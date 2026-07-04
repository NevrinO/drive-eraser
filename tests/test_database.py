# Unit tests for database.py SQL security validation
import sqlite3
import tempfile
import os
import sys
import json

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from database import ensure_column

def test_safe_default_values():
    """Test that safe DEFAULT values are accepted."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        
        # Test NULL default
        ensure_column(conn, "test_table", "col1", "col1 TEXT DEFAULT NULL")
        
        # Test numeric defaults
        ensure_column(conn, "test_table", "col2", "col2 INTEGER DEFAULT 0")
        ensure_column(conn, "test_table", "col3", "col3 INTEGER DEFAULT 42")
        ensure_column(conn, "test_table", "col4", "col4 REAL DEFAULT 3.14")
        ensure_column(conn, "test_table", "col5", "col5 REAL DEFAULT -10.5")
        
        # Test simple string defaults
        ensure_column(conn, "test_table", "col6", "col6 TEXT DEFAULT 'erase'")
        ensure_column(conn, "test_table", "col7", "col7 TEXT DEFAULT 'hello world'")
        ensure_column(conn, "test_table", "col8", "col8 TEXT DEFAULT 'test-value:123'")
        
        conn.close()
        print("[PASS] Safe DEFAULT values accepted")
    except Exception as e:
        print(f"✗ DEFAULT test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

def test_sql_injection_via_default():
    """Test that SQL injection attempts via DEFAULT clause are blocked."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        
        # Test SQL injection via nested quotes
        try:
            ensure_column(conn, "test_table", "col1", "col1 TEXT DEFAULT ''); DROP TABLE test_table; --'")
            print("[FAIL] SQL injection via nested quotes NOT blocked")
            assert False, "SQL injection via nested quotes NOT blocked"
        except ValueError as e:
            if "Unsafe DEFAULT value" in str(e) or "Unexpected trailing tokens" in str(e):
                print("[PASS] SQL injection via nested quotes blocked")
            else:
                print(f"[FAIL] Wrong error for nested quotes: {e}")
                raise
        
        # Test SQL injection via function call
        try:
            ensure_column(conn, "test_table", "col2", "col2 TEXT DEFAULT (SELECT version())")
            print("✗ SQL injection via function call NOT blocked")
            assert False, "SQL injection via function call NOT blocked"
        except ValueError as e:
            if "Unsafe DEFAULT value" in str(e) or "Unexpected trailing tokens" in str(e):
                print("[PASS] SQL injection via function call blocked")
            else:
                print(f"[FAIL] Wrong error for function call: {e}")
                raise
        
        # Test SQL injection via expression
        try:
            ensure_column(conn, "test_table", "col3", "col3 INTEGER DEFAULT 1+1")
            print("✗injection via expression NOT blocked")
            assert False, "SQL injection via expression NOT blocked"
        except ValueError as e:
            if "Unsafe DEFAULT value" in str(e) or "Unexpected trailing tokens" in str(e):
                print("[PASS] SQL injection via expression blocked")
            else:
                print(f"[FAIL] Wrong error for expression: {e}")
                raise
        
        # Test SQL injection via semicolon
        try:
            ensure_column(conn, "test_table", "col4", "col4 TEXT DEFAULT 'test'; DROP TABLE test_table; --")
            print("[FAIL] SQL injection via semicolon NOT blocked")
            assert False, "SQL injection via semicolon NOT blocked"
        except ValueError as e:
            if "Unsafe DEFAULT value" in str(e) or "Unexpected trailing tokens" in str(e):
                print("[PASS] SQL injection via semicolon blocked")
            else:
                print(f"[FAIL] Wrong error for semicolon: {e}")
                raise
        
        conn.close()
    except Exception as e:
        print(f"[FAIL] SQL injection test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

def test_invalid_column_types():
    """Test that invalid column types are rejected."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        
        # Test invalid type
        try:
            ensure_column(conn, "test_table", "col1", "col1 VARCHAR(255)")
            print("[FAIL] Invalid column type NOT blocked")
            assert False, "Invalid column type NOT blocked"
        except ValueError as e:
            if "Invalid column type" in str(e):
                print("[PASS] Invalid column type blocked")
            else:
                print(f"[FAIL] Wrong error for invalid type: {e}")
                raise
        
        conn.close()
    except Exception as e:
        print(f"✗lid column type test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

def test_column_name_mismatch():
    """Test that column name mismatch is detected."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY)")
        
        # Test column name mismatch
        try:
            ensure_column(conn, "test_table", "col1", "col2 TEXT")
            print("[FAIL] Column name mismatch NOT detected")
            assert False, "Column name mismatch NOT detected"
        except ValueError as e:
            if "Column name mismatch" in str(e):
                print("[PASS] Column name mismatch detected")
            else:
                print(f"[FAIL] Wrong error for name mismatch: {e}")
                raise
        
        conn.close()
    except Exception as e:
        print(f"[FAIL] Column name mismatch test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)

def test_record_intake_snapshot():
    """Test recording a drive intake snapshot."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name

    try:
        from database import record_intake_snapshot, get_db_path
        import database
        import smart_db

        # Patch get_db_path to use our test database
        original_get_db_path = database.get_db_path
        original_smart_db_path = smart_db.get_db_path
        database.get_db_path = lambda: db_path
        smart_db.get_db_path = lambda: db_path

        # Initialize database with required tables using init_wipe_db
        from database import init_wipe_db
        init_wipe_db()

        # Test recording a snapshot
        smart_data = {
            "serial": "TEST123",
            "health_score": 85,
            "sas_grown_defect_list": 10,
            "sas_scan_status": "completed",
            "sas_uncorrectable_read_errors": 0,
            "sas_uncorrectable_write_errors": 0,
            "sas_uncorrectable_verify_errors": 0,
            "power_on_hours": 10000,
            "manufacture_date": "2020-01-01"
        }
        recommendation = {"status": "USED_GOOD"}

        result = record_intake_snapshot("TEST123", smart_data, recommendation, health_score=85)

        assert result is True

        # Verify record was inserted
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM drive_intake_history WHERE serial = ?", ("TEST123",)).fetchone()
        conn.close()

        assert row is not None
        assert row["serial"] == "TEST123"
        assert row["seen_at"] is not None
        assert row["health_score"] == 85
        assert row["grown_defects"] == 10

        print("[PASS] record_intake_snapshot test passed")

        # Restore original function
        database.get_db_path = original_get_db_path
        smart_db.get_db_path = original_smart_db_path

    except Exception as e:
        print(f"[FAIL] record_intake_snapshot test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_load_prior_visit():
    """Test loading prior visit data."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name

    try:
        from database import record_intake_snapshot, load_prior_visit, get_db_path
        import database
        import smart_db

        # Patch get_db_path to use our test database
        original_get_db_path = database.get_db_path
        original_smart_db_path = smart_db.get_db_path
        database.get_db_path = lambda: db_path
        smart_db.get_db_path = lambda: db_path

        # Initialize database using init_wipe_db
        from database import init_wipe_db
        init_wipe_db()

        # Record a snapshot
        smart_data = {
            "serial": "TEST123",
            "health_score": 85,
            "sas_grown_defect_list": 10,
            "power_on_hours": 10000
        }
        recommendation = {"status": "USED_GOOD"}
        record_intake_snapshot("TEST123", smart_data, recommendation, health_score=85)

        # Load prior visit
        prior = load_prior_visit("TEST123")

        assert prior is not None
        assert prior["serial"] == "TEST123"
        assert prior["health_score"] == 85
        assert prior["grown_defects"] == 10
        assert prior["recommendation"] == "USED_GOOD"

        print("[PASS] load_prior_visit test passed")

        # Test non-existent serial
        prior_none = load_prior_visit("NONEXISTENT")
        assert prior_none is None

        print("[PASS] load_prior_visit (non-existent) test passed")

        # Restore original function
        database.get_db_path = original_get_db_path
        smart_db.get_db_path = original_smart_db_path

    except Exception as e:
        print(f"[FAIL] load_prior_visit test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_save_wipe_smart_snapshot():
    """Test saving pre/post-wipe SMART snapshots."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name

    try:
        from database import save_wipe_smart_snapshot, get_db_path
        import database
        import smart_db

        # Patch get_db_path to use our test database
        original_get_db_path = database.get_db_path
        original_smart_db_path = smart_db.get_db_path
        database.get_db_path = lambda: db_path
        smart_db.get_db_path = lambda: db_path

        # Initialize database using init_wipe_db
        from database import init_wipe_db
        init_wipe_db()

        # Insert test job
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO erase_jobs (id, status, created_at, request_json) VALUES (?, ?, ?, ?)",
                     ("test-job-1", "running", "2024-01-01T00:00:00Z", "{}"))
        conn.commit()
        conn.close()

        # Test pre-wipe snapshot
        pre_smart = {"serial": "TEST123", "health_score": 85}
        result = save_wipe_smart_snapshot("test-job-1", "pre", pre_smart)

        assert result is True

        # Verify pre-wipe snapshot was saved
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT pre_wipe_smart_json FROM erase_jobs WHERE id = ?", ("test-job-1",)).fetchone()
        conn.close()

        assert row is not None
        assert row[0] is not None
        saved_data = json.loads(row[0])
        assert saved_data["serial"] == "TEST123"

        print("[PASS] save_wipe_smart_snapshot (pre) test passed")

        # Test post-wipe snapshot
        post_smart = {"serial": "TEST123", "health_score": 90}
        result = save_wipe_smart_snapshot("test-job-1", "post", post_smart)

        assert result is True

        # Verify post-wipe snapshot was saved
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT post_wipe_smart_json FROM erase_jobs WHERE id = ?", ("test-job-1",)).fetchone()
        conn.close()

        assert row is not None
        assert row[0] is not None
        saved_data = json.loads(row[0])
        assert saved_data["serial"] == "TEST123"

        print("[PASS] save_wipe_smart_snapshot (post) test passed")

        # Test invalid phase
        result = save_wipe_smart_snapshot("test-job-1", "invalid", pre_smart)
        assert result is False

        print("[PASS] save_wipe_smart_snapshot (invalid phase) test passed")

        # Restore original function
        database.get_db_path = original_get_db_path
        smart_db.get_db_path = original_smart_db_path

    except Exception as e:
        print(f"[FAIL] save_wipe_smart_snapshot test failed: {e}")
        raise
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    print("Running database.py SQL security tests...")
    print()

    test_safe_default_values()
    test_sql_injection_via_default()
    test_invalid_column_types()
    test_column_name_mismatch()

    print()
    print("Running Phase 8 DB snapshot helper tests...")
    print()

    test_record_intake_snapshot()
    test_load_prior_visit()
    test_save_wipe_smart_snapshot()

    print()
    print("All tests passed!")
    sys.exit(0)


