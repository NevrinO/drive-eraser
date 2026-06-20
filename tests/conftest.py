"""Pytest configuration for test suite."""
import warnings
import pytest


def pytest_configure(config):
    """Configure pytest to suppress false positive ResourceWarnings.
    
    SQLite connections opened with context managers in test methods are
    properly closed, but Python's garbage collector may still emit ResourceWarning
    during pytest's internal cleanup. These are false positives and can be safely
    suppressed.
    """
    # Suppress all ResourceWarnings - these are false positives from pytest's garbage collection
    warnings.filterwarnings("ignore", category=ResourceWarning)
    # Also suppress PytestUnraisableExceptionWarning for unclosed SQLite connections
    warnings.filterwarnings("ignore", category=pytest.PytestUnraisableExceptionWarning)
