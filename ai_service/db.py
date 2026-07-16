"""SQLite-backed durable job queue (spec §3.2)."""
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    call_record_id  TEXT PRIMARY KEY,
    call_record_url TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    full_text       TEXT,
    summary         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Job:
    call_record_id: str
    call_record_url: str
    status: str
    attempts: int
    error: str | None
    full_text: str | None
    summary: str | None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(value: str) -> datetime:
    """Inverse of _now(): parse a stored created_at/updated_at timestamp."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class JobStore:
    """Thread-safe: shared by the FastAPI thread and the worker thread."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def _row_to_job(self, row) -> Job:
        return Job(**{key: row[key] for key in row.keys()})

    def _fetch(self, call_record_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE call_record_id = ?", (call_record_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def _update(self, call_record_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._conn.execute(
            f"UPDATE jobs SET {assignments} WHERE call_record_id = ?",
            (*fields.values(), call_record_id),
        )
        self._conn.commit()

    def enqueue(self, call_record_id: str, call_record_url: str) -> Job:
        with self._lock:
            existing = self._fetch(call_record_id)
            if existing is None:
                now = _now()
                self._conn.execute(
                    "INSERT INTO jobs (call_record_id, call_record_url, status, attempts,"
                    " created_at, updated_at) VALUES (?, ?, 'queued', 0, ?, ?)",
                    (call_record_id, call_record_url, now, now),
                )
                self._conn.commit()
            elif existing.status == "failed":
                self._update(
                    call_record_id,
                    call_record_url=call_record_url,
                    status="queued",
                    attempts=0,
                    error=None,
                )
            return self._fetch(call_record_id)

    def get(self, call_record_id: str) -> Job | None:
        with self._lock:
            return self._fetch(call_record_id)

    def next_pending(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'processing')"
                " ORDER BY created_at, call_record_id LIMIT 1"
            ).fetchone()
            return self._row_to_job(row) if row else None

    def list_delivering(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'delivering'"
                " ORDER BY created_at, call_record_id"
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def list_jobs(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Job]:
        """Newest-first listing, optionally filtered by status (for diagnostics)."""
        clause = "WHERE status = ?" if status else ""
        params: tuple = (status,) if status else ()
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs {clause}"
                " ORDER BY created_at DESC, call_record_id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def counts_by_status(self) -> dict[str, int]:
        """Job counts per state (for the /metrics queue gauges)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
            return {row["status"]: row["n"] for row in rows}

    def oldest_queued_created_at(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(created_at) AS ts FROM jobs WHERE status = 'queued'"
            ).fetchone()
            return row["ts"]

    def set_status(self, call_record_id: str, status: str) -> None:
        with self._lock:
            self._update(call_record_id, status=status)

    def set_result(self, call_record_id: str, full_text: str, summary: str) -> None:
        with self._lock:
            self._update(
                call_record_id, full_text=full_text, summary=summary, status="delivering"
            )

    def increment_attempts(self, call_record_id: str, error: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET attempts = attempts + 1, error = ?, updated_at = ?"
                " WHERE call_record_id = ?",
                (error, _now(), call_record_id),
            )
            self._conn.commit()
            return self._fetch(call_record_id).attempts

    def mark_failed(self, call_record_id: str, error: str) -> None:
        with self._lock:
            self._update(call_record_id, status="failed", error=error)
