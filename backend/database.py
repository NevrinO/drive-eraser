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

        # Phase 4: Add drive_intake_history table for SMART snapshots
        # Wrap in retry loop for concurrent migration safety (Lesson #6)
        for attempt in range(max_retries):
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS drive_intake_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        serial TEXT NOT NULL,
                        seen_at TEXT NOT NULL,
                        health_score REAL,
                        grown_defects INTEGER,
                        scan_status TEXT,
                        uncorrectable_read INTEGER,
                        uncorrectable_write INTEGER,
                        uncorrectable_verify INTEGER,
                        recommendation TEXT,
                        poh REAL,
                        manufacture_date TEXT,
                        snapshot_json TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_intake_history_serial ON drive_intake_history(serial)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_intake_history_seen_at ON drive_intake_history(seen_at)")

                # Phase 4: Add pre_wipe_smart_json and post_wipe_smart_json columns to erase_jobs
                # ensure_column handles duplicate column errors internally
                ensure_column(conn, "erase_jobs", "pre_wipe_smart_json", "pre_wipe_smart_json TEXT")
                ensure_column(conn, "erase_jobs", "post_wipe_smart_json", "post_wipe_smart_json TEXT")

                # Phase 7: Add smart_test_log table for SMART self-test audit history
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS smart_test_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device TEXT NOT NULL,
                        serial TEXT,
                        test_type TEXT NOT NULL CHECK(test_type IN ('short', 'extended', 'offline', 'conveyance')),
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        result TEXT,
                        output_json TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_test_log_device ON smart_test_log(device)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_test_log_serial ON smart_test_log(serial)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_smart_test_log_started_at ON smart_test_log(started_at)")
                break
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() or "table is locked" in str(e).lower():
                    if attempt < max_retries - 1:
                        time.sleep(backoff_delays[attempt])
                        continue
                    else:
                        # Final attempt failed, re-raise the exception
                        raise
                else:
                    # Other OperationalError, re-raise immediately
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


def close_all_connections():
    """Close all SQLite connections to prevent ResourceWarning in tests.

    This function iterates through all objects in the sqlite3 module's connection
    cache and closes any connections to the current database path. This is primarily
    intended for test cleanup to avoid ResourceWarning about unclosed connections.
    """
    try:
        db_path = get_db_path()
        # Close all connections to the test database
        for conn in list(sqlite3.connections.values()):
            try:
                if conn and not conn.closed:
                    # Check if this connection is to our database
                    if hasattr(conn, 'execute'):
                        cursor = conn.execute("PRAGMA database_list")
                        databases = cursor.fetchall()
                        for db in databases:
                            if db_path in db[2] or db[2] == db_path:
                                # Run WAL checkpoint to release file locks on Windows before closing
                                try:
                                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                                except Exception:
                                    pass
                                conn.close()
                                break
            except Exception:
                # Ignore errors during cleanup
                pass
    except Exception:
        # Ignore errors if get_db_path or connection iteration fails
        pass


# Phase 4: SMART snapshot helper functions

def record_intake_snapshot(serial, smart, recommendation, health_score=None):
    """Record a SMART snapshot at drive intake.

    Args:
        serial: Drive serial number
        smart: SMART data dict from get_smart_data()
        recommendation: Recommendation dict from get_drive_recommendation()
        health_score: Calculated health score (optional, if not provided will try to extract from smart dict)

    Returns:
        True if recorded successfully, False otherwise
    """
    if not serial:
        return False

    seen_at = datetime.now(timezone.utc).isoformat()
    # Use provided health_score or attempt to extract from smart dict for backward compatibility
    hs = health_score if health_score is not None else smart.get("health_score")
    grown_defects = smart.get("sas_grown_defect_list") or smart.get("reallocated_sectors")
    scan_status = smart.get("sas_scan_status")
    uncorrectable_read = smart.get("sas_uncorrectable_read_errors")
    uncorrectable_write = smart.get("sas_uncorrectable_write_errors")
    uncorrectable_verify = smart.get("sas_uncorrectable_verify_errors")
    rec_status = recommendation.get("status") if recommendation else None
    poh = smart.get("power_on_hours")
    manufacture_date = smart.get("manufacture_date")
    snapshot_json = json.dumps(smart)

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.execute(
                """
                INSERT INTO drive_intake_history (
                    serial, seen_at, health_score, grown_defects, scan_status,
                    uncorrectable_read, uncorrectable_write, uncorrectable_verify,
                    recommendation, poh, manufacture_date, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    serial, seen_at, hs, grown_defects, scan_status,
                    uncorrectable_read, uncorrectable_write, uncorrectable_verify,
                    rec_status, poh, manufacture_date, snapshot_json
                )
            )
            conn.commit()
            return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to record intake snapshot for serial {serial}: {e}")
        return False


def load_prior_visit(serial):
    """Load the most recent intake snapshot for a drive serial.

    Args:
        serial: Drive serial number

    Returns:
        Dict with prior visit data or None if not found
    """
    if not serial:
        return None

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT serial, seen_at, health_score, grown_defects, scan_status,
                       uncorrectable_read, uncorrectable_write, uncorrectable_verify,
                       recommendation, poh, manufacture_date, snapshot_json
                FROM drive_intake_history
                WHERE serial = ?
                ORDER BY seen_at DESC
                LIMIT 1
                """,
                (serial,)
            ).fetchone()

        if not row:
            return None

        return {
            "serial": row["serial"],
            "seen_at": row["seen_at"],
            "health_score": row["health_score"],
            "grown_defects": row["grown_defects"],
            "scan_status": row["scan_status"],
            "uncorrectable_read": row["uncorrectable_read"],
            "uncorrectable_write": row["uncorrectable_write"],
            "uncorrectable_verify": row["uncorrectable_verify"],
            "recommendation": row["recommendation"],
            "poh": row["poh"],
            "manufacture_date": row["manufacture_date"],
            "snapshot": json.loads(row["snapshot_json"]) if row["snapshot_json"] else None
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to load prior visit for serial {serial}: {e}")
        return None


def save_wipe_smart_snapshot(job_id, phase, snapshot):
    """Save a SMART snapshot at pre-wipe or post-wipe phase.

    Args:
        job_id: Job UUID
        phase: Either "pre" or "post"
        snapshot: SMART data dict from get_smart_data()

    Returns:
        True if saved successfully, False otherwise
    """
    if not job_id or not snapshot:
        return False

    column_name = f"{phase}_wipe_smart_json"
    if column_name not in ["pre_wipe_smart_json", "post_wipe_smart_json"]:
        return False

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            # Column name is validated against allowlist before use (see validation above)
            # This f-string interpolation is safe because column_name is restricted to known values
            conn.execute(
                f"UPDATE erase_jobs SET {column_name} = ? WHERE id = ?",
                (json.dumps(snapshot), job_id)
            )
            conn.commit()
            return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to save wipe SMART snapshot for job {job_id} phase {phase}: {e}")
        return False


def calculate_smart_diff(pre_smart, post_smart):
    """Calculate diff metrics between pre-wipe and post-wipe SMART snapshots.

    Args:
        pre_smart: Pre-wipe SMART data dict
        post_smart: Post-wipe SMART data dict

    Returns:
        Dict with diff metrics and worsened flags
    """
    if not pre_smart or not post_smart:
        return None

    # Extract key metrics for comparison
    metrics_to_compare = [
        "reallocated_sectors",
        "pending_sectors",
        "power_on_hours",
        "temperature",
        "sas_grown_defect_list",
        "sas_uncorrectable_read_errors",
        "sas_uncorrectable_write_errors",
        "sas_uncorrectable_verify_errors",
        "sas_scan_status",
        "sas_sticky_lba_detected",
    ]

    diff = {
        "pre": {},
        "post": {},
        "delta": {},
        "worsened": {}
    }

    for metric in metrics_to_compare:
        pre_val = pre_smart.get(metric)
        post_val = post_smart.get(metric)

        diff["pre"][metric] = pre_val
        diff["post"][metric] = post_val

        # Calculate delta for numeric metrics
        if pre_val is not None and post_val is not None:
            try:
                if isinstance(pre_val, (int, float)) and isinstance(post_val, (int, float)):
                    delta = post_val - pre_val
                    diff["delta"][metric] = delta

                    # Flag worsened metrics
                    if metric in ["reallocated_sectors", "pending_sectors", "sas_grown_defect_list",
                                  "sas_uncorrectable_read_errors", "sas_uncorrectable_write_errors",
                                  "sas_uncorrectable_verify_errors"]:
                        if delta > 0:
                            diff["worsened"][metric] = {
                                "pre": pre_val,
                                "post": post_val,
                                "delta": delta
                            }
                    elif metric == "sas_sticky_lba_detected":
                        # Sticky LBA detection is boolean - flag if went from False to True
                        if not pre_val and post_val:
                            diff["worsened"][metric] = {
                                "pre": pre_val,
                                "post": post_val,
                                "delta": "newly_detected"
                            }
                    elif metric == "sas_scan_status":
                        # Flag if scan status went from healthy to halted/failed
                        pre_status = str(pre_val).lower() if pre_val else ""
                        post_status = str(post_val).lower() if post_val else ""
                        if "halted" not in pre_status and "halted" in post_status:
                            diff["worsened"][metric] = {
                                "pre": pre_val,
                                "post": post_val,
                                "delta": "scan_halted"
                            }
            except (TypeError, ValueError):
                pass

    return diff


def record_smart_test_run(device, serial, test_type, status, result=None, output_json=None):
    """Record a SMART self-test run for audit history.

    Args:
        device: Device path (e.g., /dev/sda)
        serial: Drive serial number
        test_type: Test type (short, extended, offline, conveyance)
        status: Test status (started, in_progress, completed, failed)
        result: Test result (passed, failed, aborted, etc.)
        output_json: Full smartctl output JSON

    Returns:
        The inserted record ID if successful, None otherwise
    """
    # Defense-in-depth: validate device path (lesson #13)
    if not device or not isinstance(device, str):
        return None
    # Reject path traversal and newlines
    if ".." in device or "\n" in device or "\r" in device:
        return None
    
    if not test_type or not status:
        return None

    started_at = datetime.now(timezone.utc).isoformat()
    updated_at = started_at

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO smart_test_log (
                    device, serial, test_type, started_at, finished_at, status, result, output_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (device, serial, test_type, started_at, None, status, result, json.dumps(output_json) if output_json else None, updated_at)
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to record SMART test run for device {device}: {e}")
        return None


def get_historical_poh_for_serial(serial):
    """Get the highest power-on hours recorded for a serial from previous SMART test runs.

    Args:
        serial: Drive serial number

    Returns:
        Maximum POH found in historical test output_json, or None if no history
    """
    if not serial:
        return None

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            cursor = conn.execute(
                """
                SELECT output_json FROM smart_test_log
                WHERE serial = ? AND output_json IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 10
                """,
                (serial,)
            )
            max_poh = None
            for row in cursor:
                try:
                    output_json = json.loads(row[0])
                    # Extract POH from various possible locations in smartctl JSON
                    poh = None
                    if "power_on_time" in output_json:
                        poh = output_json["power_on_time"].get("hours")
                    elif "ata_smart_attributes" in output_json:
                        for attr in output_json["ata_smart_attributes"].get("table", []):
                            if attr.get("id") == 9:
                                poh = attr.get("raw", {}).get("value")
                                break
                    if poh is not None:
                        # Handle int, float, and string representations (e.g., "12345" or "12345.0")
                        if isinstance(poh, (int, float)):
                            poh_int = int(poh)
                        elif isinstance(poh, str):
                            # Remove decimal point for string floats, then check if numeric
                            poh_clean = poh.replace('.', '', 1)
                            poh_int = int(poh) if poh_clean.isdigit() else None
                        else:
                            poh_int = None
                        if poh_int and (max_poh is None or poh_int > max_poh):
                            max_poh = poh_int
                except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                    continue
            return max_poh
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to get historical POH for serial {serial}: {e}")
        return None


def update_smart_test_run(record_id, status, result=None, output_json=None, current_updated_at=None):
    """Update a SMART test run by record ID with optimistic locking.

    Args:
        record_id: The database record ID to update (from record_smart_test_run)
        status: New status
        result: Test result (if completed)
        output_json: Full smartctl output JSON (if completed)
        current_updated_at: Current updated_at timestamp for optimistic locking (optional)

    Returns:
        True if updated successfully, False otherwise
    """
    if not record_id or not status:
        return False

    finished_at = datetime.now(timezone.utc).isoformat() if status in ("completed", "failed") else None
    updated_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            if current_updated_at:
                # Optimistic locking: only update if record hasn't been modified since we read it
                cursor = conn.execute(
                    """
                    UPDATE smart_test_log
                    SET status = ?, finished_at = ?, result = ?, output_json = ?, updated_at = ?
                    WHERE id = ? AND updated_at = ?
                    """,
                    (status, finished_at, result, json.dumps(output_json) if output_json else None, updated_at, record_id, current_updated_at)
                )
            else:
                # No optimistic locking (backward compatibility)
                cursor = conn.execute(
                    """
                    UPDATE smart_test_log
                    SET status = ?, finished_at = ?, result = ?, output_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, finished_at, result, json.dumps(output_json) if output_json else None, updated_at, record_id)
                )
            conn.commit()
            # Check if any row was actually updated
            return cursor.rowcount > 0
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to update SMART test run for record {record_id}: {e}")
        return False


def get_smart_test_history(device=None, serial=None, limit=20):
    """Get SMART test history for a device or serial.

    Args:
        device: Device path (optional)
        serial: Serial number (optional)
        limit: Maximum number of records to return

    Returns:
        List of test run dicts or empty list
    """
    if not device and not serial:
        return []
    
    # Defense-in-depth: validate device path (lesson #13, #16, #91)
    if device:
        if not isinstance(device, str):
            return []
        # Use strict validation from smart_parsing to ensure regex patterns match
        try:
            from smart_parsing import validate_device_path
            if not validate_device_path(device):
                return []
        except ImportError:
            # Fallback to basic checks if smart_parsing unavailable
            if ".." in device or "\n" in device or "\r" in device:
                return []
    
    # Validate limit parameter (lesson #9: size limits for DoS prevention)
    try:
        limit = int(limit)
        if limit < 1 or limit > 1000:
            limit = 20  # Default to reasonable limit if out of range
    except (ValueError, TypeError):
        limit = 20

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.row_factory = sqlite3.Row
            if device:
                rows = conn.execute(
                    """
                    SELECT id, device, serial, test_type, started_at, finished_at, status, result, output_json, updated_at
                    FROM smart_test_log
                    WHERE device = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (device, limit)
                ).fetchall()
            elif serial:
                rows = conn.execute(
                    """
                    SELECT id, device, serial, test_type, started_at, finished_at, status, result, output_json, updated_at
                    FROM smart_test_log
                    WHERE serial = ?
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (serial, limit)
                ).fetchall()
            else:
                # Return all in-progress tests if no filter specified (for background thread)
                rows = conn.execute(
                    """
                    SELECT id, device, serial, test_type, started_at, finished_at, status, result, output_json, updated_at
                    FROM smart_test_log
                    WHERE status IN ('started', 'in_progress')
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()

            return [
                {
                    "id": row["id"],
                    "device": row["device"],
                    "serial": row["serial"],
                    "test_type": row["test_type"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "status": row["status"],
                    "result": row["result"],
                    "output": json.loads(row["output_json"]) if row["output_json"] else None,
                    "updated_at": row["updated_at"]
                }
                for row in rows
            ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to get SMART test history: {e}")
        return []


def cleanup_stale_smart_tests():
    """Mark stale SMART test records as failed.

    Records that have been in 'started' or 'in_progress' status for too long
    are marked as 'failed' to prevent the UI from showing tests as running
    when they actually crashed or failed to start.

    Timeout thresholds:
    - short/offline/conveyance tests: 10 minutes
    - extended tests: 3 hours (with safety margin)

    Returns:
        Number of records updated
    """
    try:
        from datetime import datetime, timezone, timedelta

        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            now = datetime.now(timezone.utc)

            # Update stale short/offline/conveyance tests (older than 10 minutes)
            short_timeout = now - timedelta(minutes=10)
            cursor1 = conn.execute(
                """
                UPDATE smart_test_log
                SET status = 'failed', finished_at = ?, result = 'Test timed out - marked as stale'
                WHERE status IN ('started', 'in_progress')
                AND test_type IN ('short', 'offline', 'conveyance')
                AND started_at < ?
                """,
                (now.isoformat(), short_timeout.isoformat())
            )

            # Update stale extended tests (older than 3 hours)
            extended_timeout = now - timedelta(hours=3)
            cursor2 = conn.execute(
                """
                UPDATE smart_test_log
                SET status = 'failed', finished_at = ?, result = 'Test timed out - marked as stale'
                WHERE status IN ('started', 'in_progress')
                AND test_type = 'extended'
                AND started_at < ?
                """,
                (now.isoformat(), extended_timeout.isoformat())
            )

            conn.commit()
            total_updated = cursor1.rowcount + cursor2.rowcount
            if total_updated > 0:
                import logging
                logging.getLogger(__name__).info(f"Cleaned up {total_updated} stale SMART test records")
            return total_updated
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to cleanup stale SMART tests: {e}")
        return 0


def get_smart_test_status_batch(devices):
    """Get latest SMART test status for multiple devices in a single query.

    Args:
        devices: List of device paths

    Returns:
        Dict mapping device path to latest test status dict, or None if no test
    """
    # Clean up stale records before querying
    cleanup_stale_smart_tests()

    if not devices or not isinstance(devices, list):
        return {}

    # Filter out invalid device paths
    valid_devices = []
    try:
        from smart_parsing import validate_device_path
        for dev in devices:
            if dev and isinstance(dev, str) and validate_device_path(dev):
                valid_devices.append(dev)
    except ImportError:
        # Fallback to basic checks if smart_parsing unavailable
        for dev in devices:
            if dev and isinstance(dev, str) and ".." not in dev and "\n" not in dev and "\r" not in dev:
                valid_devices.append(dev)

    if not valid_devices:
        return {}

    try:
        with closing(sqlite3.connect(get_db_path(), timeout=30.0)) as conn, conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join(["?"] * len(valid_devices))
            rows = conn.execute(
                f"""
                SELECT device, test_type, status, started_at, finished_at
                FROM smart_test_log
                WHERE device IN ({placeholders})
                ORDER BY started_at DESC
                """,
                valid_devices
            ).fetchall()

            # Build dict with latest test per device
            result = {}
            for row in rows:
                device = row["device"]
                # Only keep the first (latest) entry for each device
                if device not in result:
                    result[device] = {
                        "test_type": row["test_type"],
                        "status": row["status"],
                        "started_at": row["started_at"],
                        "finished_at": row["finished_at"]
                    }
            return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to get batch SMART test status: {e}")
        return {}
