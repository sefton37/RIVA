"""RIVA database access.

Uses the shared talkingrock.db (same as Cairn) via direct sqlite3.
RIVA tables are prefixed with `riva_` to avoid namespace collisions.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from cairn.settings import settings

logger = logging.getLogger(__name__)

_DB_FILENAME = "talkingrock.db"


def get_db_path() -> Path:
    """Return the path to the shared talkingrock.db."""
    return settings.data_dir / _DB_FILENAME


def get_connection(*, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection to talkingrock.db with WAL mode.

    Args:
        readonly: If True, open in read-only mode (uri=file:...?mode=ro).

    Returns:
        A sqlite3.Connection configured for WAL mode with row_factory.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(db_path))

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for a database transaction.

    Commits on success, rolls back on exception.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
