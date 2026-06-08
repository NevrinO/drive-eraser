import sqlite3
import json
import os
import re
import secrets
import time
from contextlib import closing
from datetime import datetime, timezone
from common import get_db_path, get_cert_dir

def ensure_column(conn, table_name, column_name, column_def):
    # Validate table_name and column_name are safe identifiers (alphanumeric + underscore)
    if not table_name.replace("_", "").isalnum():
        raise ValueError(f"Invalid table name: {table_name}")
    if not column_name.replace("_", "").isalnum():
        raise ValueError(f"Invalid column name: {column_name}")
    
    # Critical #5: Parse and validate column definition components separately
    # Allowed types: TEXT, INTEGER, REAL, BLOB
    allowed_types = {"TEXT", "INTEGER", "REAL", "BLOB"}
    
    # Split column_def into components: name, type, and optional DEFAULT clause
    # Use regex to correctly handle quoted string defaults containing spaces
    token_pattern = re.compile(r"'[^']*'|\S+")
    parts = token_pattern.findall(column_def)
    if len(parts) < 2:
        raise ValueError(f"Invalid column definition: {column_def}")
    
    def_column_name = parts[0]
    column_type = parts[1].upper()
    
    # Validate column name matches parameter
    if def_column_name != column_name:
        raise ValueError(f"Column name mismatch: definition has '{def_column_name}' but parameter is '{column_name}'")
    
    # Validate column type is in allowlist
    if column_type not in allowed_types:
        raise ValueError(f"Invalid column type: {column_type}. Must be one of {allowed_types}")
    
    # Validate DEFAULT clause if present
    # Critical #3: Use regex with word boundary to ensure DEFAULT is a separate token
    # This prevents injection attacks like "column_name TEXT DEFAULT'evil'); DROP TABLE..."
    if len(parts) >= 4 and re.match(r'\bDEFAULT\b', parts[2], re.IGNORECASE):
        if len(parts) > 4:
            raise ValueError(f"Unexpected trailing tokens in column definition: {column_def}")
        default_value = parts[3]
        # Critical #5: Validate DEFAULT value against strict allowlist
        # Allow only: NULL, numeric literals (integer/float), or string literals in single quotes
        if default_value.upper() == "NULL":
            # NULL is safe
            pass
        elif default_value.startswith("'") and default_value.endswith("'"):
            # String literal - validate it's a simple string (no nested quotes or escapes)
            # Allow only alphanumeric, spaces, and basic punctuation
            inner_value = default_value[1:-1]
            if not re.match(r'^[a-zA-Z0-9 _\-.,:]+$', inner_value):
                raise ValueError(f"Unsafe DEFAULT value: {default_value}. Only alphanumeric, spaces, and basic punctuation allowed in string literals")
        elif re.match(r'^-?\d+$', default_value):
            # Integer literal is safe
            pass
        elif re.match(r'^-?\d+\.\d+$', default_value):
            # Float literal is safe
            pass
        else:
            raise ValueError(f"Unsafe DEFAULT value: {default_value}. Only NULL, numeric literals, or simple string literals allowed")
    elif len(parts) > 2:
        raise ValueError(f"Unexpected trailing tokens in column definition: {column_def}")
    
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
    with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
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
        
        # Medium #43: Migration with retry logic and exponential backoff
        # Retry up to 3 times with 1s, 2s, 4s backoff for concurrent migration attempts
        max_retries = 3
        backoff_delays = [1, 2, 4]  # seconds
        
        for attempt in range(max_retries):
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.execute(
                    "UPDATE erase_jobs SET job_type = 'erase' WHERE job_type IS NULL"
                )
                conn.commit()
                break  # Success, exit retry loop
            except Exception as e:
                conn.rollback()
                if attempt < max_retries - 1:
                    # Retry with exponential backoff
                    delay = backoff_delays[attempt]
                    time.sleep(delay)
                else:
                    # Final attempt failed, re-raise the exception
                    raise

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
    
    # Generate friendly_id before INSERT to avoid race condition (lesson-learned #2)
    if not job.get("friendly_id"):
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        random_hex = secrets.token_hex(3).upper()  # 6 hex chars for better entropy
        friendly_id = f"CERT-{date_str}-{random_hex}"
        job["friendly_id"] = friendly_id
    
    with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
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
        conn.commit()

def load_job(job_id):
    """Load a job from the database by ID or friendly_id."""
    with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
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
