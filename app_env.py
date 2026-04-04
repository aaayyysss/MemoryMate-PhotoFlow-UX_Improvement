"""
Application Environment Detection
===================================

Resolves the application root directory reliably for both:
  - Portable Python (e.g., WinPython on a non-admin PC)
  - Full system Python installation

The key problem: os.getcwd() can differ from the actual app directory
when launched via a shortcut, script, or portable interpreter from
another location.  This module anchors every app-relative path
(database, settings, logs) to the directory that contains *this file*,
which is always the project root.

Usage:
    from app_env import APP_DIR, app_path

    db   = app_path("reference_data.db")
    logs = app_path("app_log.txt")
"""

import os
import sys
from pathlib import Path


def _resolve_app_dir() -> str:
    """
    Return the absolute path to the application root directory.

    Resolution order:
      1. MEMORYMATE_APP_DIR environment variable (explicit override)
      2. Directory containing this file (app_env.py lives in project root)
      3. Fallback: current working directory (legacy behaviour)
    """
    # 1. Explicit override via environment variable
    env_dir = os.environ.get("MEMORYMATE_APP_DIR")
    if env_dir and os.path.isdir(env_dir):
        return os.path.abspath(env_dir)

    # 2. Directory of this source file (reliable for both portable & full Python)
    try:
        src_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.isdir(src_dir):
            return src_dir
    except NameError:
        pass  # __file__ not defined (e.g., interactive interpreter)

    # 3. Fallback
    return os.path.abspath(os.getcwd())


# Resolved once at import time — every module sees the same value.
APP_DIR: str = _resolve_app_dir()


def app_path(*parts: str) -> str:
    """
    Join path segments relative to APP_DIR.

    Examples:
        app_path("reference_data.db")        -> "/path/to/app/reference_data.db"
        app_path("models", "buffalo_l")       -> "/path/to/app/models/buffalo_l"
    """
    return os.path.join(APP_DIR, *parts)


def is_portable_python() -> bool:
    """
    Heuristic: detect whether we are running under a portable Python.

    Indicators:
      - sys.prefix is inside the app directory tree
      - No 'site-packages' in a system-wide location
      - VIRTUAL_ENV or a portable marker file exists
    """
    prefix = os.path.abspath(sys.prefix)
    # Portable Python is typically co-located with (or near) the app
    if prefix.startswith(APP_DIR) or APP_DIR.startswith(prefix):
        return True
    # Check for common portable Python markers
    for marker in ("python.bat", "WPy64", "portable"):
        if marker.lower() in prefix.lower():
            return True
    return False


def is_writable(directory: str) -> bool:
    """Check whether *directory* is writable without requiring admin."""
    try:
        test_file = os.path.join(directory, ".memorymate_write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True
    except (OSError, IOError):
        return False


def get_data_dir() -> str:
    """
    Return a writable directory for user data (session state, caches).

    Tries in order:
      1. APP_DIR  (keeps everything together — ideal for portable setups)
      2. ~/.memorymate  (standard user-data location)
      3. System temp directory (last resort)
    """
    # Prefer keeping data with the app (portable-friendly)
    if is_writable(APP_DIR):
        return APP_DIR

    # Fallback to home directory
    home_data = os.path.join(os.path.expanduser("~"), ".memorymate")
    try:
        os.makedirs(home_data, exist_ok=True)
        if is_writable(home_data):
            return home_data
    except OSError:
        pass

    # Last resort
    import tempfile
    return tempfile.gettempdir()
