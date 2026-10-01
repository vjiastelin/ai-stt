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
    updated_at      TEXT NOT NULL,
    delivered_to    TEXT NOT NULL DEFAULT '',
    route           TEXT NOT NULL DEFAULT '',
    route_by        TEXT NOT NULL DEFAULT '',
    emailed_to      TEXT NOT NULL DEFAULT '',
    routed_at       TEXT NOT NULL DEFAULT ''
)
"""

# objects the bucket scanner has already turned into jobs (keeps scans incremental:
# a key is enqueued once, so a failed job is never silently re-queued by a rescan)
_S3_OBJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS s3_objects (
    bucket          TEXT NOT NULL,
    key             TEXT NOT NULL,
    etag            TEXT,
    call_record_id  TEXT NOT NULL,
    discovered_at   TEXT NOT NULL,
    PRIMARY KEY (bucket, key)
)
"""

# columns added after the first release; ALTERed into pre-existing databases on open
_MIGRATIONS = {
    "delivered_to": "ALTER TABLE jobs ADD COLUMN delivered_to TEXT NOT NULL DEFAULT ''",
    # which team the delivered result was attributed to, by which rule, and where
    # the e-mail actually went — recorded once delivery finishes (routing stats)
    "route": "ALTER TABLE jobs ADD COLUMN route TEXT NOT NULL DEFAULT ''",
    "route_by": "ALTER TABLE jobs ADD COLUMN route_by TEXT NOT NULL DEFAULT ''",
    "emailed_to": "ALTER TABLE jobs ADD COLUMN emailed_to TEXT NOT NULL DEFAULT ''",
    "routed_at": "ALTER TABLE jobs ADD COLUMN routed_at TEXT NOT NULL DEFAULT ''",
}


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
    # comma-separated delivery channels ("bpm", "email") that already accepted the
    # current result, so a retry resends only to the channels that failed
    delivered_to: str = ""
    route: str = ""
    route_by: str = ""        # file | domain | company | default; "" = not routed yet
    emailed_to: str = ""      # comma-separated recipients of the result e-mail
    routed_at: str = ""

    @property
    def delivered_channels(self) -> set[str]:
        return {c for c in self.delivered_to.split(",") if c}


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
            self._conn.execute(_S3_OBJECTS_SCHEMA)
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")}
            for column, ddl in _MIGRATIONS.items():
                if column not in columns:
                    self._conn.execute(ddl)
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

    def known_s3_keys(self, bucket: str, prefix: str = "") -> set[str]:
        """Keys under bucket/prefix the scanner has already enqueued."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM s3_objects WHERE bucket = ? AND substr(key, 1, ?) = ?",
                (bucket, len(prefix), prefix),
            ).fetchall()
            return {row["key"] for row in rows}

    def enqueue_discovered(
        self, bucket: str, key: str, etag: str | None, call_record_id: str, call_record_url: str
    ) -> bool:
        """Atomically remember a scanned object and queue its job.

        Returns False when the object was already known. A job that already
        exists under this id (e.g. POSTed by hand) is left as it is.
        """
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO s3_objects (bucket, key, etag, call_record_id,"
                " discovered_at) VALUES (?, ?, ?, ?, ?)",
                (bucket, key, etag, call_record_id, now),
            )
            if cur.rowcount == 0:
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT OR IGNORE INTO jobs (call_record_id, call_record_url, status, attempts,"
                " created_at, updated_at) VALUES (?, ?, 'queued', 0, ?, ?)",
                (call_record_id, call_record_url, now, now),
            )
            self._conn.commit()
            return True

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
                call_record_id,
                full_text=full_text,
                summary=summary,
                error=None,  # clear any error left by an earlier transient retry
                delivered_to="",
                route="", route_by="", emailed_to="", routed_at="",
                status="delivering",
            )

    def set_failed_result(self, call_record_id: str, error: str) -> None:
        """Route a permanently-failed job to delivering so BPM is notified (Error:true).

        full_text stays NULL — the delivery loop uses that to tell a failure
        callback from a success one — and the job becomes terminal `failed`
        only once BPM has acknowledged the error.
        """
        with self._lock:
            self._update(call_record_id, error=error, delivered_to="", status="delivering")

    def mark_delivered_to(self, call_record_id: str, channel: str) -> None:
        """Record that one delivery channel accepted the job's current result."""
        with self._lock:
            channels = self._fetch(call_record_id).delivered_channels | {channel}
            self._update(call_record_id, delivered_to=",".join(sorted(channels)))

    def increment_attempts(self, call_record_id: str, error: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET attempts = attempts + 1, error = ?, updated_at = ?"
                " WHERE call_record_id = ?",
                (error, _now(), call_record_id),
            )
            self._conn.commit()
            return self._fetch(call_record_id).attempts

    def set_routing(self, call_record_id: str, route: str, route_by: str, emailed_to: str) -> None:
        with self._lock:
            self._update(call_record_id, route=route, route_by=route_by,
                         emailed_to=emailed_to, routed_at=_now())

    def list_routed_since(self, since: str) -> list[Job]:
        """Delivered results attributed to a route at or after `since`."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE route_by != '' AND routed_at >= ?"
                " ORDER BY routed_at, call_record_id",
                (since,),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def list_summaries_since(self, since: str) -> list[Job]:
        """Processed jobs (with a summary) created at or after `since` (a stored timestamp)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE created_at >= ? AND full_text IS NOT NULL"
                " AND summary IS NOT NULL AND summary != ''"
                " ORDER BY created_at, call_record_id",
                (since,),
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def mark_failed(self, call_record_id: str, error: str) -> None:
        with self._lock:
            self._update(call_record_id, status="failed", error=error)
