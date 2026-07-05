"""DB "Connection lost" churn fix.

The bug: ResilientDatabaseConnection holds a single asyncpg connection, which
permits exactly one in-flight operation. Concurrent coroutines (e.g. two
memory ops during voice handling) raised InterfaceError ("another operation
is in progress"), which the reconnect handlers misread as a lost connection
-- logging "Connection lost, attempting to reconnect..." and opening a fresh
connection per collision, leaking the old one.

No real database is touched: asyncpg.connect is monkeypatched to hand out
fake connections that enforce asyncpg's one-operation-at-a-time rule.
"""
import asyncio
from pathlib import Path

import asyncpg

import database.session as session_mod
from database.session import ResilientDatabaseConnection

SRC_ROOT = Path(__file__).resolve().parents[1] / 'src'


class FakeConnection:
    """Enforces asyncpg's single-in-flight-operation rule."""

    def __init__(self):
        self.busy = False
        self.closed = False
        self.completed_ops = 0

    async def _op(self, result):
        if self.busy:
            raise asyncpg.InterfaceError(
                'cannot perform operation: another operation is in progress'
            )
        self.busy = True
        try:
            await asyncio.sleep(0.01)
            self.completed_ops += 1
            return result
        finally:
            self.busy = False

    async def execute(self, query, *args, **kwargs):
        return await self._op('OK')

    async def fetchval(self, query, *args, **kwargs):
        return await self._op(1)

    async def close(self, timeout=None):
        self.closed = True

    def terminate(self):
        self.closed = True


class DyingConnection(FakeConnection):
    """First operation fails like a genuinely dropped connection."""

    def __init__(self):
        super().__init__()
        self.died = False

    async def execute(self, query, *args, **kwargs):
        if not self.died:
            self.died = True
            raise asyncpg.ConnectionDoesNotExistError('connection was closed')
        return await super().execute(query, *args, **kwargs)


async def test_concurrent_ops_serialize_without_reconnect(monkeypatch):
    connections = []

    async def fake_connect(url, **kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(session_mod.asyncpg, 'connect', fake_connect)

    db = ResilientDatabaseConnection('postgresql://fake/db')
    assert await db.connect()

    results = await asyncio.gather(
        *[db.execute('INSERT INTO t VALUES ($1)', i) for i in range(10)],
        db.fetchval('SELECT 1'),
    )

    assert results[:10] == ['OK'] * 10
    assert results[10] == 1
    # The whole point: collisions used to force a reconnect per operation
    assert len(connections) == 1, "concurrent ops must not trigger reconnects"
    assert connections[0].completed_ops == 11


async def test_reconnect_closes_previous_connection(monkeypatch):
    connections = []

    async def fake_connect(url, **kwargs):
        conn = DyingConnection() if not connections else FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(session_mod.asyncpg, 'connect', fake_connect)

    db = ResilientDatabaseConnection('postgresql://fake/db')
    assert await db.connect()

    # Genuine connection loss: op fails once, reconnects, retries, succeeds
    assert await db.execute('INSERT INTO t VALUES (1)') == 'OK'

    assert len(connections) == 2
    assert connections[0].closed, "old connection must be closed, not leaked"
    assert not connections[1].closed


async def test_disconnect_closes_connection(monkeypatch):
    connections = []

    async def fake_connect(url, **kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(session_mod.asyncpg, 'connect', fake_connect)

    db = ResilientDatabaseConnection('postgresql://fake/db')
    assert await db.connect()
    await db.disconnect()

    assert connections[0].closed
    assert db.connection is None
    assert not db.is_connected


def test_no_wrapper_calls_inside_transaction_blocks():
    """Lock re-entrancy guard: _op_lock is held for a transaction() block and
    is not re-entrant, so code inside `async with db.transaction()` must use
    the yielded raw connection -- calling the wrapper's execute/fetch* there
    would deadlock. Scans all of src/ for offending call sites."""
    offenders = []
    for py in SRC_ROOT.rglob('*.py'):
        if py.name == 'session.py':
            continue
        lines = py.read_text(encoding='utf-8', errors='ignore').splitlines()
        for i, line in enumerate(lines):
            if '.transaction()' not in line or 'with' not in line:
                continue
            indent = len(line) - len(line.lstrip())
            for j in range(i + 1, len(lines)):
                block_line = lines[j]
                if block_line.strip() and (len(block_line) - len(block_line.lstrip())) <= indent:
                    break
                if any(f'.db.{op}(' in block_line or f'db_session.{op}(' in block_line
                       for op in ('execute', 'fetch', 'fetchrow', 'fetchval')):
                    offenders.append(f'{py.relative_to(SRC_ROOT)}:{j + 1}')
    assert not offenders, (
        'wrapper execute/fetch* used inside a transaction() block '
        f'(deadlocks on _op_lock): {offenders}'
    )
