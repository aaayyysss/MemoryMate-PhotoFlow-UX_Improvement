"""UI safety helpers.

Goals:
- Prevent crashes when background workers emit signals during shutdown/restart.
- Provide a cheap validity gate for PySide6 widgets (shiboken6.isValid).
- Provide generation guards to ignore stale callbacks after restart.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

try:
    from shiboken6 import isValid as _isValid  # type: ignore
except Exception:  # pragma: no cover
    _isValid = None

T = TypeVar("T")


def is_alive(obj: Any) -> bool:
    """Return True if obj is a still-valid PySide6 wrapper.

    If shiboken6 is unavailable, fall back to a conservative non-None check.
    """
    if obj is None:
        return False
    if _isValid is None:
        return True
    try:
        return bool(_isValid(obj))
    except Exception:
        return False


def generation_ok(owner: Any, expected_generation: Optional[int]) -> bool:
    """Check that owner's ui_generation matches expected_generation (if provided)."""
    if expected_generation is None:
        return True
    try:
        current = owner.ui_generation()  # expected to exist on MainWindow
    except Exception:
        return False
    return current == expected_generation


def guarded(owner: Any, expected_generation: Optional[int], fn: Callable[..., T], *args: Any, **kwargs: Any) -> Optional[T]:
    """Call fn only if owner is alive and generation matches.

    Returns fn result, or None if skipped.
    """
    if not is_alive(owner):
        return None
    if not generation_ok(owner, expected_generation):
        return None
    try:
        return fn(*args, **kwargs)
    except RuntimeError:
        # Common during shutdown when Qt objects are already being deleted.
        return None
