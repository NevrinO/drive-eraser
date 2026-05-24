import sqlite3
import json
import os
from common import get_db_path, get_cert_dir

def ensure_column(conn, table_name, column_name, column_def):
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = {col[1] for col in columns}
    if column_name not in names:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")

def init_wipe_db():
    os.makedirs(os.path.dirname(get_db_path()), exist_ok=True)
    os.makedirs(get_cert_dir(), exist_ok=True)
    with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
        # Enable Write-Ahead Logging to keep UI reads non-blocking against background writers
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS erase_jobs (
                job_number INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE,
                friendly_id TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                request_json TEXT NOT NULL,
                result_json TEXT,
                verification_json TEXT,
                marker_json TEXT,
                certificate_json TEXT
            )
            """
        )
        ensure_column(conn, "erase_jobs", "friendly_id", "friendly_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_erase_jobs_friendly_id ON erase_jobs(friendly_id)")
        ensure_column(conn, "erase_jobs", "verification_json", "verification_json TEXT")
        ensure_column(conn, "erase_jobs", "marker_json", "marker_json TEXT")
        ensure_column(conn, "erase_jobs", "certificate_json", "certificate_json TEXT")
        conn.commit()

def persist_job(job):
    with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO erase_jobs (
                id, friendly_id, status, created_at, started_at, finished_at,
                error, request_json, result_json, verification_json, marker_json, certificate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                friendly_id=COALESCE(excluded.friendly_id, friendly_id),
                status=excluded.status,
                created_at=excluded.created_at,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at,
                error=excluded.error,
                request_json=excluded.request_json,
                result_json=excluded.result_json,
                verification_json=excluded.verification_json,
                marker_json=excluded.marker_json,
                certificate_json=excluded.certificate_json
            """,
            (
                job.get("id"),
                job.get("friendly_id"),
                job.get("status"),
                job.get("created_at"),
                job.get("started_at"),
                job.get("finished_at"),
                job.get("error"),
                json.dumps(job.get("request") or {}),
                json.dumps(job.get("result") or {}),
                json.dumps(job.get("verification") or {}),
                json.dumps(job.get("marker") or {}),
                json.dumps(job.get("certificate") or {}),
            ),
        )
        if not job.get("friendly_id"):
            job_number = cursor.lastrowid
            friendly_id = f"SANI-{job_number:06d}"
            job["friendly_id"] = friendly_id
            cursor.execute(
                "UPDATE erase_jobs SET friendly_id = ? WHERE id = ?",
                (friendly_id, job.get("id")),
            )
        conn.commit()