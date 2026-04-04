# utils/qt_role.py
"""
Shiboken-safe helpers for storing structured data in Qt item roles.

CONTRACT: Qt.UserRole must only store JSON strings, never Python dicts,
lists, or arbitrary objects. QTreeWidgetItem.setData() crosses the
Python→C++ boundary via Shiboken, which cannot reliably marshal Python
containers as QVariant. Violating this causes:
  - Shiboken::Conversions::_pythonToCppCopy warnings
  - Undefined behavior / silent data loss
  - Access violations on Windows

Usage:
    from utils.qt_role import role_set_json, role_get_json

    # Write (always serializes to JSON string)
    role_set_json(item, {"type": "month", "year": 2026}, role=Qt.UserRole)

    # Read (handles JSON string, legacy dict, and invalid data)
    data = role_get_json(item, role=Qt.UserRole)
    if data:
        item_type = data.get("type")

    # Writeback after mutation (safe)
    data = role_get_json(item, role=Qt.UserRole)
    data["label"] = new_name
    role_set_json(item, data, role=Qt.UserRole)

Ref: https://doc.qt.io/qt-6/qtreewidgetitem.html (item data storage)
"""

import json
from typing import Any, Dict, Optional


def role_set_json(
    item,
    payload: Dict[str, Any],
    column: int = 0,
    role=None,
) -> None:
    """Store a dict as a JSON string in a Qt item's data role.

    Args:
        item: QTreeWidgetItem (or any item with setData)
        payload: Dict to serialize — must be JSON-compatible
        column: Column index (default 0)
        role: Qt role constant (e.g. Qt.UserRole). Must be passed explicitly.
    """
    item.setData(column, role, json.dumps(payload, ensure_ascii=False))


def role_get_json(
    item,
    column: int = 0,
    role=None,
) -> Optional[Dict[str, Any]]:
    """Read structured data from a Qt item's data role.

    Handles:
      - JSON string (correct path) → deserialized dict
      - Python dict (legacy/backward compat) → returned as-is, not written back
      - None / empty / invalid → returns None

    Args:
        item: QTreeWidgetItem (or any item with data())
        column: Column index (default 0)
        role: Qt role constant (e.g. Qt.UserRole). Must be passed explicitly.

    Returns:
        Deserialized dict, or None if data is missing/invalid.
    """
    raw = item.data(column, role)
    if not raw:
        return None
    if isinstance(raw, dict):
        # Backward compatibility — legacy code stored dicts directly.
        # Return as-is but do NOT write back (caller should migrate).
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None
