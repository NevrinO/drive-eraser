# Unit tests for database.py SQL security validation
import sqlite3
import tempfile
import os
import sys

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

if __name__ == "__main__":
    print("Running database.py SQL security tests...")
    print()
    
    test_safe_default_values()
    test_sql_injection_via_default()
    test_invalid_column_types()
    test_column_name_mismatch()
    
    print()
    print("All tests passed!")
    sys.exit(0)


