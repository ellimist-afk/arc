"""Write a file without a window where it exists but is empty.

`open(path, 'w')` and `Path.write_text()` truncate first and write second.
Anything that interrupts between the two -- a killed process, a full disk, an
exception while serialising -- leaves the file empty, permanently. For a
config file that is the whole configuration gone, and the next start comes up
on defaults with no clue why.

That risk is real here rather than theoretical: bot_settings.json is written
from chat (!persona) and from the API, while a health monitor polls and reads
it every few seconds.

The fix is the standard one: write a sibling temp file, flush it to disk,
then rename over the target. os.replace is atomic on Windows and POSIX
alike, so a reader sees either the whole old file or the whole new one.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def write_text_atomic(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """Replace `path` with `text`, or leave it exactly as it was.

    Raises on failure, having cleaned up the temp file. Callers that must not
    fail should catch -- the point is that a failure never damages the
    existing file.
    """
    target = Path(path)
    # A sibling, so the rename stays within one filesystem and therefore
    # atomic. A temp in the system temp dir can land on another volume, where
    # os.replace degrades to a copy and the guarantee is lost.
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def write_json_atomic(path: PathLike, data: Any, indent: int = 2,
                      encoding: str = "utf-8") -> None:
    """Same, for JSON. Serialising BEFORE touching the file matters: an
    unserialisable value must fail without the target having been opened."""
    payload = json.dumps(data, indent=indent) + "\n"
    write_text_atomic(path, payload, encoding=encoding)
