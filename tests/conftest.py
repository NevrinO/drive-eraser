"""Pytest configuration for test suite."""
import warnings
import pytest


def pytest_configure(config):
    """Configure pytest: initialize app_config and suppress false positive warnings.

    init_app() must be called before any test imports `limiter`, `app`, or
    `socketio` from app_config. It's idempotent — safe if already called
    (e.g., via app.py import chain).
    """
    from app_config import init_app
    init_app()

    # Suppress all ResourceWarnings - these are false positives from pytest's garbage collection
    warnings.filterwarnings("ignore", category=ResourceWarning)
    # Also suppress PytestUnraisableExceptionWarning for unclosed SQLite connections
    warnings.filterwarnings("ignore", category=pytest.PytestUnraisableExceptionWarning)


@pytest.fixture(autouse=True)
def stop_extended_smart_pool_after_test():
    """Stop the persistent extended SMART pool after each test to prevent thread leaks."""
    yield
    try:
        from disk_ops import stop_extended_smart_pool
        stop_extended_smart_pool(wait=False)
    except Exception:
        pass
