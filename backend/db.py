# Database connection helper with SQLite-compatible execute API over Postgres or SQLite.

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .config import settings

_PG_POOL = None


def _adapt_sql_for_postgres(sql: str) -> str:
    text = sql
    ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", text, flags=re.IGNORECASE))
    if ignore:
        text = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO",
            "INSERT INTO",
            text,
            flags=re.IGNORECASE,
        )
    text = text.replace("?", "%s")
    text = re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"%(\1)s", text)
    if ignore and "ON CONFLICT" not in text.upper():
        text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return text


class CursorResult:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = -1):
        self._rows = rows
        self.rowcount = rowcount
        self._index = 0

    def fetchone(self) -> dict[str, Any] | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[dict[str, Any]]:
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return remaining


class DbConnection:
    """Thin wrapper so call sites can keep sqlite-style ? placeholders."""

    def __init__(self, raw: Any, backend: str):
        self._raw = raw
        self.backend = backend

    def execute(self, sql: str, params: Any = None) -> CursorResult:
        params = () if params is None else params
        if self.backend == "postgres":
            adapted = _adapt_sql_for_postgres(sql)
            cur = self._raw.execute(adapted, params)
            if cur.description:
                fetched = cur.fetchall()
                rows = []
                for row in fetched:
                    if isinstance(row, dict):
                        rows.append(dict(row))
                    else:
                        cols = [col.name for col in cur.description]
                        rows.append(dict(zip(cols, row)))
            else:
                rows = []
            return CursorResult(rows, rowcount=cur.rowcount)
        cur = self._raw.execute(sql, params)
        if cur.description:
            rows = [dict(row) for row in cur.fetchall()]
        else:
            rows = []
        return CursorResult(rows, rowcount=cur.rowcount)

    def executescript(self, script: str) -> None:
        if self.backend == "postgres":
            for statement in script.split(";"):
                stmt = statement.strip()
                if stmt:
                    self._raw.execute(stmt)
            return
        self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> "DbConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _sqlite_connect(db_path: Path) -> DbConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DbConnection(conn, "sqlite")


def _postgres_connect() -> DbConnection:
    """Direct short-timeout connection (avoids pool hang when Neon is unreachable)."""
    from psycopg import Connection
    from psycopg.rows import dict_row

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    raw = Connection.connect(
        settings.database_url,
        row_factory=dict_row,
        autocommit=False,
        connect_timeout=10,
    )
    return DbConnection(raw, "postgres")


def connect(db_path: Path | None = None) -> DbConnection:
    """Open a DB connection. Neon when DATABASE_URL is set; else SQLite."""
    if settings.use_postgres:
        if db_path is None:
            return _postgres_connect()
        try:
            if Path(db_path).resolve() == settings.db_path.resolve():
                return _postgres_connect()
        except OSError:
            pass
        return _sqlite_connect(Path(db_path))
    path = Path(db_path) if db_path is not None else settings.db_path
    return _sqlite_connect(path)
