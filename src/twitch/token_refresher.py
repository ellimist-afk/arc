"""
Twitch OAuth token auto-refresh system.

Prevents token expiry by refreshing access tokens before they expire,
updating both .env and token files, and live-updating running components.
"""

import os
import asyncio
import logging
from typing import Dict, Optional, Callable
from datetime import datetime, timedelta
import aiohttp

logger = logging.getLogger(__name__)


class TwitchTokenRefresher:
    """Auto-refresh Twitch OAuth tokens before expiry."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.accounts: Dict[str, Dict] = {}
        self.callback: Optional[Callable] = None
        self.running = False
        self.task: Optional[asyncio.Task] = None

    def register_account(
        self,
        account_name: str,
        env_var_name: str,
        token_file_path: str
    ) -> bool:
        """
        Register an account for auto-refresh.

        Args:
            account_name: Account identifier
            env_var_name: .env variable name to update
            token_file_path: Path to token .txt file

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            if not os.path.exists(token_file_path):
                logger.error(f"Token file not found: {token_file_path}")
                return False

            with open(token_file_path, 'r') as f:
                content = f.read()

            access_token = None
            refresh_token = None

            for line in content.splitlines():
                line = line.strip()
                if line.startswith('ACCESS_TOKEN='):
                    access_token = line.split('=', 1)[1].strip()
                elif line.startswith('REFRESH_TOKEN='):
                    refresh_token = line.split('=', 1)[1].strip()

            if not refresh_token:
                logger.error(f"No REFRESH_TOKEN found in {token_file_path}")
                return False

            self.accounts[account_name] = {
                'env_var_name': env_var_name,
                'token_file_path': token_file_path,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_at': None
            }

            logger.info(f"Registered account {account_name} for token refresh")
            return True

        except Exception as e:
            logger.error(f"Failed to register account {account_name}: {e}")
            return False

    def on_refresh_callback(self, callback: Callable[[str, str], None]) -> None:
        """
        Register callback to fire after successful refresh.

        Args:
            callback: Function(account_name, new_access_token)
        """
        self.callback = callback

    async def refresh(self, account_name: str) -> bool:
        """
        Refresh tokens for a specific account.

        Args:
            account_name: Account to refresh

        Returns:
            True on success, False on failure (never raises)
        """
        if account_name not in self.accounts:
            logger.error(f"Account {account_name} not registered")
            return False

        account = self.accounts[account_name]

        try:
            # Make refresh request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://id.twitch.tv/oauth2/token',
                    data={
                        'grant_type': 'refresh_token',
                        'refresh_token': account['refresh_token'],
                        'client_id': self.client_id,
                        'client_secret': self.client_secret
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(
                            f"Token refresh failed for {account_name}: "
                            f"HTTP {resp.status} - {error_text}"
                        )
                        return False

                    data = await resp.json()

            new_access_token = data.get('access_token')
            new_refresh_token = data.get('refresh_token')
            expires_in = data.get('expires_in', 14400)  # Default 4 hours

            if not new_access_token or not new_refresh_token:
                logger.error(f"Invalid refresh response for {account_name}")
                return False

            # Update internal state
            account['access_token'] = new_access_token
            account['refresh_token'] = new_refresh_token
            account['expires_at'] = datetime.now() + timedelta(seconds=expires_in)

            # Atomically update .env file FIRST - if this fails, .txt stays old (consistent state)
            if not self._update_env_file(
                account['env_var_name'],
                new_access_token
            ):
                return False

            # Then atomically update .txt file
            if not self._update_token_file(
                account['token_file_path'],
                new_access_token,
                new_refresh_token
            ):
                return False

            logger.info(
                f"Successfully refreshed tokens for {account_name} "
                f"(expires in {expires_in}s)"
            )

            # Fire callback
            if self.callback:
                try:
                    self.callback(account_name, new_access_token)
                except Exception as e:
                    logger.error(f"Refresh callback failed: {e}")

            return True

        except asyncio.TimeoutError:
            logger.error(f"Token refresh timeout for {account_name}")
            return False
        except Exception as e:
            logger.error(f"Token refresh error for {account_name}: {e}")
            return False

    def _update_token_file(
        self,
        file_path: str,
        new_access_token: str,
        new_refresh_token: str
    ) -> bool:
        """Atomically update .txt file with new tokens."""
        tmp_path = f"{file_path}.tmp"

        try:
            # Read current content
            with open(file_path, 'r') as f:
                lines = f.readlines()

            # Build new content, preserving comments and structure
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('ACCESS_TOKEN='):
                    new_lines.append(f"ACCESS_TOKEN={new_access_token}\n")
                elif stripped.startswith('REFRESH_TOKEN='):
                    new_lines.append(f"REFRESH_TOKEN={new_refresh_token}\n")
                else:
                    new_lines.append(line)

            # Write to temp file
            with open(tmp_path, 'w') as f:
                f.writelines(new_lines)

            # Atomic replace
            os.replace(tmp_path, file_path)
            return True

        except Exception as e:
            logger.error(f"Failed to update {file_path}: {e}")
            # Clean up temp file if it exists
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            return False

    def _update_env_file(self, env_var_name: str, new_value: str) -> bool:
        """Atomically update .env file with new token value."""
        env_path = '.env'
        tmp_path = '.env.tmp'

        try:
            # Read current .env
            if not os.path.exists(env_path):
                logger.error(".env file not found")
                return False

            with open(env_path, 'r') as f:
                lines = f.readlines()

            # Build new content, only changing the target variable
            new_lines = []
            found = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(f"{env_var_name}="):
                    new_lines.append(f"{env_var_name}={new_value}\n")
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                logger.error(f"{env_var_name} not found in .env")
                return False

            # Write to temp file
            with open(tmp_path, 'w') as f:
                f.writelines(new_lines)

            # Atomic replace
            os.replace(tmp_path, env_path)
            return True

        except Exception as e:
            logger.error(f"Failed to update .env: {e}")
            # Clean up temp file if it exists
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            return False

    async def refresh_all(self) -> Dict[str, bool]:
        """Refresh all registered accounts."""
        results = {}
        for account_name in self.accounts.keys():
            results[account_name] = await self.refresh(account_name)
        return results

    async def start(self) -> None:
        """Start background refresh loop."""
        if self.running:
            logger.warning("Token refresher already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._refresh_loop())
        logger.info("Token refresher started")

    async def _refresh_loop(self) -> None:
        """Background loop that refreshes tokens before expiry."""
        while self.running:
            try:
                # Calculate next refresh time (30 min before soonest expiry)
                next_refresh_in = self._calculate_next_refresh()

                logger.debug(f"Next token refresh in {next_refresh_in}s")

                # Sleep until refresh time
                await asyncio.sleep(next_refresh_in)

                # Refresh all accounts
                if self.running:
                    logger.info("Starting scheduled token refresh...")
                    await self.refresh_all()

            except asyncio.CancelledError:
                logger.info("Token refresh loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in token refresh loop: {e}")
                # Continue running despite errors
                await asyncio.sleep(60)  # Wait 1 min before retry

    def _calculate_next_refresh(self) -> float:
        """Calculate seconds until next refresh needed."""
        safety_margin = timedelta(minutes=30)
        default_interval = timedelta(hours=3)

        earliest_expiry = None

        for account in self.accounts.values():
            if account['expires_at']:
                refresh_at = account['expires_at'] - safety_margin
                if earliest_expiry is None or refresh_at < earliest_expiry:
                    earliest_expiry = refresh_at

        if earliest_expiry:
            now = datetime.now()
            seconds_until = (earliest_expiry - now).total_seconds()
            return max(60, seconds_until)  # At least 1 minute
        else:
            # No expiry info yet, use default interval
            return default_interval.total_seconds()

    async def stop(self) -> None:
        """Stop background refresh loop."""
        if not self.running:
            return

        self.running = False

        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        logger.info("Token refresher stopped")
