"""Cold storage writer — async batch persistence to SQLite or PostgreSQL.

Provides a write buffer that accumulates records and flushes them
periodically or when the buffer exceeds a threshold.  Supports
automatic schema migrations from ``storage/migrations/``.

Usage::

    writer = ColdWriter(dsn="sqlite:///data.db")
    await writer.start()
    await writer.write("fills", {"market_id": "0xabc", "side": "BUY", ...})
    await writer.stop()  # final flush
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("storage.cold_writer")

__all__ = ["ColdWriter"]

# Migrations directory relative to this file
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass
class BufferedRecord:
    """A single record waiting to be flushed."""

    table: str
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ColdWriter:
    """Async batch writer with periodic flush.

    Parameters
    ----------
    dsn:
        Database connection string.
        - ``sqlite:///path/to/file.db`` — SQLite (default)
        - ``postgresql://...`` — PostgreSQL (requires asyncpg)
    flush_interval_seconds:
        How often to flush the buffer.
    buffer_max_size:
        Maximum records in buffer before forced flush.
    batch_size:
        Max records per INSERT batch.
    """

    def __init__(
        self,
        dsn: str = "sqlite:///data/cold_storage.db",
        flush_interval_seconds: float = 10.0,
        buffer_max_size: int = 5000,
        batch_size: int = 500,
    ) -> None:
        self._dsn = dsn
        self._flush_interval = flush_interval_seconds
        self._buffer_max = buffer_max_size
        self._batch_size = batch_size

        self._buffer: list[BufferedRecord] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._running = False

        # Stats
        self._stats_written: int = 0
        self._stats_flushed: int = 0
        self._stats_errors: int = 0

        # Backend
        self._is_sqlite = dsn.startswith("sqlite")
        self._sqlite_conn: sqlite3.Connection | None = None
        self._pg_pool: Any = None  # asyncpg.Pool when using PostgreSQL

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise the backend connection and start the flush loop."""
        if self._is_sqlite:
            await self._init_sqlite()
        else:
            await self._init_postgres()

        await self._run_migrations()

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())

        logger.info(
            "cold_writer.started",
            dsn=self._dsn.split("@")[-1] if "@" in self._dsn else self._dsn,
            flush_interval=self._flush_interval,
        )

    async def stop(self) -> None:
        """Stop the flush loop and perform a final flush."""
        self._running = False

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self.flush()

        if self._sqlite_conn:
            self._sqlite_conn.close()
            self._sqlite_conn = None

        if self._pg_pool:
            await self._pg_pool.close()
            self._pg_pool = None

        logger.info(
            "cold_writer.stopped",
            total_written=self._stats_written,
            total_flushes=self._stats_flushed,
            errors=self._stats_errors,
        )

    # ── Write API ────────────────────────────────────────────────

    async def write(self, table: str, data: dict[str, Any]) -> None:
        """Buffer a single record for later batch insert.

        Parameters
        ----------
        table:
            Target table name.
        data:
            Column name → value mapping.
        """
        record = BufferedRecord(table=table, data=data)

        async with self._lock:
            self._buffer.append(record)

        # Force flush if buffer is too large
        if len(self._buffer) >= self._buffer_max:
            await self.flush()

    async def write_many(self, table: str, records: list[dict[str, Any]]) -> None:
        """Buffer multiple records at once."""
        now = datetime.now(timezone.utc)
        buffered = [BufferedRecord(table=table, data=d, timestamp=now) for d in records]

        async with self._lock:
            self._buffer.extend(buffered)

        if len(self._buffer) >= self._buffer_max:
            await self.flush()

    # ── Flush ────────────────────────────────────────────────────

    async def flush(self) -> int:
        """Flush all buffered records to the database.

        Returns the number of records written.
        """
        async with self._lock:
            if not self._buffer:
                return 0

            to_flush = list(self._buffer)
            self._buffer.clear()

        # Group by table
        by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in to_flush:
            by_table[record.table].append({
                **record.data,
                "_inserted_at": record.timestamp.isoformat(),
            })

        total_written = 0
        for table, rows in by_table.items():
            for i in range(0, len(rows), self._batch_size):
                batch = rows[i : i + self._batch_size]
                try:
                    if self._is_sqlite:
                        await self._write_sqlite(table, batch)
                    else:
                        await self._write_postgres(table, batch)
                    total_written += len(batch)
                except Exception:
                    self._stats_errors += 1
                    logger.exception(
                        "cold_writer.flush_error",
                        table=table,
                        batch_size=len(batch),
                    )
                    # Re-buffer failed records
                    async with self._lock:
                        for row in batch:
                            ts_str = row.pop("_inserted_at", None)
                            self._buffer.append(BufferedRecord(
                                table=table, data=row,
                            ))

        self._stats_written += total_written
        self._stats_flushed += 1
        logger.debug("cold_writer.flushed", records=total_written)
        return total_written

    # ── Stats ────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, int]:
        """Return writer statistics."""
        return {
            "total_written": self._stats_written,
            "total_flushes": self._stats_flushed,
            "errors": self._stats_errors,
            "buffer_size": len(self._buffer),
        }

    @property
    def buffer_size(self) -> int:
        """Current number of buffered records."""
        return len(self._buffer)

    # ── SQLite backend ───────────────────────────────────────────

    async def _init_sqlite(self) -> None:
        """Initialise SQLite connection."""
        # Parse path from DSN: sqlite:///path/to/file.db
        db_path = self._dsn.replace("sqlite:///", "")
        if not db_path:
            db_path = "data/cold_storage.db"

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        loop = asyncio.get_running_loop()
        self._sqlite_conn = await loop.run_in_executor(
            None, lambda: sqlite3.connect(db_path, check_same_thread=False)
        )
        self._sqlite_conn.execute("PRAGMA journal_mode=WAL")
        self._sqlite_conn.execute("PRAGMA synchronous=NORMAL")

    async def _write_sqlite(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Batch insert into SQLite."""
        if not rows or not self._sqlite_conn:
            return

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)

        # Ensure table exists with dynamic schema
        create_sql = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(f'{c} TEXT' for c in columns)})"

        loop = asyncio.get_running_loop()

        def _do_insert() -> None:
            assert self._sqlite_conn is not None
            self._sqlite_conn.execute(create_sql)
            values = [
                tuple(json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values())
                for row in rows
            ]
            self._sqlite_conn.executemany(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
                values,
            )
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_insert)

    # ── PostgreSQL backend ───────────────────────────────────────

    async def _init_postgres(self) -> None:
        """Initialise asyncpg connection pool."""
        try:
            import asyncpg  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL support. "
                "Install with: pip install asyncpg"
            ) from exc

        self._pg_pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)

    async def _write_postgres(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Batch insert into PostgreSQL using asyncpg."""
        if not rows or not self._pg_pool:
            return

        columns = list(rows[0].keys())
        col_names = ", ".join(columns)
        placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))

        create_sql = (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            + ", ".join(f"{c} TEXT" for c in columns)
            + ")"
        )

        async with self._pg_pool.acquire() as conn:
            await conn.execute(create_sql)
            for row in rows:
                values = [
                    json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else None
                    for v in row.values()
                ]
                await conn.execute(
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
                    *values,
                )

    # ── Migrations ───────────────────────────────────────────────

    async def _run_migrations(self) -> None:
        """Run SQL migration files from storage/migrations/."""
        if not _MIGRATIONS_DIR.exists():
            return

        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            return

        for mf in migration_files:
            sql = mf.read_text()
            if not sql.strip():
                continue

            try:
                if self._is_sqlite:
                    await self._run_sqlite_migration(sql, mf.name)
                else:
                    await self._run_postgres_migration(sql, mf.name)
                logger.info("cold_writer.migration_applied", file=mf.name)
            except Exception:
                logger.exception("cold_writer.migration_failed", file=mf.name)

    async def _run_sqlite_migration(self, sql: str, name: str) -> None:
        """Apply a migration to SQLite."""
        if not self._sqlite_conn:
            return

        loop = asyncio.get_running_loop()

        def _do_migration() -> None:
            assert self._sqlite_conn is not None
            # Ensure migration tracking table exists
            self._sqlite_conn.execute(
                "CREATE TABLE IF NOT EXISTS _migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT)"
            )
            # Skip if already applied
            cursor = self._sqlite_conn.execute(
                "SELECT 1 FROM _migrations WHERE name = ?", (name,)
            )
            if cursor.fetchone():
                return
            self._sqlite_conn.executescript(sql)
            self._sqlite_conn.execute(
                "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_migration)

    async def _run_postgres_migration(self, sql: str, name: str) -> None:
        """Apply a migration to PostgreSQL."""
        if not self._pg_pool:
            return

        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS _migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ)"
            )
            row = await conn.fetchrow(
                "SELECT 1 FROM _migrations WHERE name = $1", name
            )
            if row:
                return
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (name, applied_at) VALUES ($1, $2)",
                name, datetime.now(timezone.utc),
            )

    # ── Flush loop ───────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        """Periodic flush loop running in the background."""
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval)
                if self._buffer:
                    await self.flush()
        except asyncio.CancelledError:
            pass
