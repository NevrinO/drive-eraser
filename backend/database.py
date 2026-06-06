import sqlite3
import json
import os
import re
from common import get_db_path, get_cert_dir

def ensure_column(conn, table_name, column_name, column_def):
    # Validate table_name and column_name are safe identifiers (alphanumeric + underscore)
    if not table_name.replace("_", "").isalnum():
        raise ValueError(f"Invalid table name: {table_name}")
    if not column_name.replace("_", "").isalnum():
        raise ValueError(f"Invalid column name: {column_name}")
    # Validate column_def matches expected pattern: "column_name TYPE [DEFAULT 'value']"
    # Allowed types: TEXT, INTEGER, REAL, BLOB
    allowed_types = {"TEXT", "INTEGER", "REAL", "BLOB"}
    column_def_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*\s+(TEXT|INTEGER|REAL|BLOB)(\s+DEFAULT\s+\'[^\']*\')?$')
    if not column_def_pattern.match(column_def):
        raise ValueError(f"Invalid column definition: {column_def}")
    # Ensure column_name in column_def matches the provided column_name parameter
    def_column_name = column_def.split()[0]
    if def_column_name != column_name:
        raise ValueError(f"Column name mismatch: definition has '{def_column_name}' but parameter is '{column_name}'")
    
    columns = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    names = {col[1] for col in columns}
    if column_name not in names:
        try:
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_def}')
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

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
                certificate_json TEXT,
                job_type TEXT DEFAULT 'erase'
            )
            """
        )
        ensure_column(conn, "erase_jobs", "friendly_id", "friendly_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_erase_jobs_friendly_id ON erase_jobs(friendly_id)")
        ensure_column(conn, "erase_jobs", "verification_json", "verification_json TEXT")
        ensure_column(conn, "erase_jobs", "marker_json", "marker_json TEXT")
        ensure_column(conn, "erase_jobs", "certificate_json", "certificate_json TEXT")
        ensure_column(conn, "erase_jobs", "job_type", "job_type TEXT DEFAULT 'erase'")
        
        # Migration: Set default job_type for existing jobs
        conn.execute(
            "UPDATE erase_jobs SET job_type = 'erase' WHERE job_type IS NULL"
        )
        conn.commit()

# Valid job types - allowlist for validation
# job_type distinguishes between different job types:
# - "erase": Standard drive sanitization jobs (default)
# - "bulk_cert": Bulk certificate generation jobs (future use)
VALID_JOB_TYPES = {"erase", "bulk_cert"}

def persist_job(job):
    # Validate job_type if present
    job_type = job.get("job_type") or "erase"
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"Invalid job_type: {job_type}. Must be one of {VALID_JOB_TYPES}")
    
    with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO erase_jobs (
                id, friendly_id, status, created_at, started_at, finished_at,
                error, request_json, result_json, verification_json, marker_json, certificate_json, job_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                certificate_json=excluded.certificate_json,
                job_type=excluded.job_type
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
                job_type,
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

def load_job(job_id):
    """Load a job from the database by ID or friendly_id."""
    with sqlite3.connect(get_db_path(), timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT job_number, id, friendly_id, status, created_at, started_at, finished_at, error,
                   request_json, result_json, verification_json, marker_json, certificate_json, job_type
            FROM erase_jobs WHERE id = ? OR friendly_id = ?
            """,
            (job_id, job_id),
        ).fetchone()

    if not row:
        return None

    def safe_json_load(json_str, field_name):
        """Safely load JSON with error handling."""
        try:
            return json.loads(json_str or "{}")
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger = __import__("logging").getLogger("app")
            logger.warning(f"Failed to parse JSON for field '{field_name}' in job {row['id']}: {str(e)}")
            return {}

    return {
        "id": row["id"],
        "friendly_id": row["friendly_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error": row["error"],
        "request": safe_json_load(row["request_json"], "request_json"),
        "result": safe_json_load(row["result_json"], "result_json"),
        "verification": safe_json_load(row["verification_json"], "verification_json"),
        "marker": safe_json_load(row["marker_json"], "marker_json"),
        "certificate": safe_json_load(row["certificate_json"], "certificate_json"),
        "job_type": row["job_type"],
    }
