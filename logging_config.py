# logging_config.py
# Version 01.00.00.00 dated 20251102
# Centralized logging configuration for MemoryMate-PhotoFlow

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional
from app_env import app_path


class ColoredFormatter(logging.Formatter):
    """
    Colored console formatter for better readability during development.
    Falls back to plain formatting if terminal doesn't support colors.
    """

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'
    }

    def __init__(self, fmt: str, use_colors: bool = True):
        super().__init__(fmt)
        self.use_colors = use_colors and self._supports_color()

    def _supports_color(self) -> bool:
        """Check if terminal supports ANSI colors."""
        try:
            # Windows terminal detection
            if sys.platform == 'win32':
                return os.getenv('TERM') is not None or 'ANSICON' in os.environ
            # Unix-like systems
            return hasattr(sys.stderr, 'isatty') and sys.stderr.isatty()
        except Exception:
            return False

    def format(self, record: logging.LogRecord) -> str:
        if self.use_colors and record.levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[record.levelname]}{record.levelname}{self.COLORS['RESET']}"
            )
        return super().format(record)


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    use_colors: bool = True
) -> logging.Logger:
    """
    Configure application-wide logging.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file. If None, uses 'app_log.txt' in current directory
        console: Whether to log to console
        max_bytes: Max size of log file before rotation
        backup_count: Number of backup log files to keep
        use_colors: Whether to use colored output in console

    Returns:
        Configured root logger
    """

    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers (in case of reconfiguration)
    root_logger.handlers.clear()

    # Format strings
    detailed_format = '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s'
    simple_format = '%(asctime)s [%(levelname)s] %(message)s'

    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_formatter = ColoredFormatter(simple_format, use_colors=use_colors)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # File handler with rotation
    if log_file is None:
        log_file = app_path("app_log.txt")

    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Always log everything to file
    file_formatter = logging.Formatter(detailed_format)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Log startup info
    root_logger.info("=" * 80)
    root_logger.info(f"MemoryMate-PhotoFlow logging initialized (level={log_level})")
    root_logger.info(f"Log file: {log_file}")
    root_logger.info("=" * 80)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Usage:
        from logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened")

    Args:
        name: Logger name (typically __name__ of the module)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


def set_log_level(level: str):
    """
    Change log level at runtime.

    Args:
        level: New log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(numeric_level)
    logging.info(f"Log level changed to {level}")


def disable_external_logging():
    """
    Reduce noise from external libraries (Qt, PIL, etc.)
    Call this after setup_logging() if needed.
    """
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('PIL.PngImagePlugin').setLevel(logging.ERROR)
    logging.getLogger('PIL.TiffImagePlugin').setLevel(logging.ERROR)

    # Qt logging (if using PySide6)
    try:
        os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false")
    except Exception:
        pass


# Example usage patterns for migration:
"""
OLD CODE:
    print(f"[ScanWorker] processing {i}/{total}: {path}")

NEW CODE:
    from logging_config import get_logger
    logger = get_logger(__name__)
    logger.debug(f"Processing {i}/{total}: {path}")

OLD CODE:
    try:
        # some operation
    except Exception as e:
        print(f"Error: {e}")
        pass

NEW CODE:
    logger = get_logger(__name__)
    try:
        # some operation
    except Exception as e:
        logger.error(f"Operation failed: {e}", exc_info=True)
        # Handle error appropriately
"""
