"""CCDatabase adapter for RIVA.

Implements the trcore CCDatabase protocol using RIVA's db module,
allowing CCManager to operate against talkingrock.db.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from riva.db import get_connection, transaction


class RivaCCDatabase:
    """CCDatabase protocol implementation backed by RIVA's db module."""

    def get_connection(self) -> sqlite3.Connection:
        return get_connection()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with transaction() as conn:
            yield conn
