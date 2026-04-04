# duplicate_scope_dialog.py
# Version 2.00.00.00 dated 20260129
# DEPRECATED: This module is a compatibility wrapper.
# Use ui.duplicate_detection_dialog.DuplicateDetectionDialog directly.

"""
Duplicate Detection Scope Selection Dialog - COMPATIBILITY WRAPPER

This module provides backward compatibility for code that imports DuplicateScopeDialog.
The functionality has been unified into DuplicateDetectionDialog which provides:
- Scope selection (all/folders/dates/recent/quantity)
- Detection methods (exact duplicates, similar photos)
- Embedded worker for background processing
- Progress tracking
- System readiness checking

Migration Guide:
    # Old code:
    from ui.duplicate_scope_dialog import DuplicateScopeDialog
    dialog = DuplicateScopeDialog(project_id, parent)
    dialog.scopeSelected.connect(handler)

    # New code (recommended):
    from ui.duplicate_detection_dialog import DuplicateDetectionDialog
    dialog = DuplicateDetectionDialog(project_id, parent)
    # No signal connection needed - dialog handles detection internally
"""

import warnings
from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

# Import the unified dialog
from ui.duplicate_detection_dialog import DuplicateDetectionDialog

__all__ = ['DuplicateScopeDialog', 'DuplicateDetectionDialog']


class DuplicateScopeDialog(DuplicateDetectionDialog):
    """
    DEPRECATED: Use DuplicateDetectionDialog instead.

    This class provides backward compatibility for existing code that uses
    DuplicateScopeDialog. It inherits from DuplicateDetectionDialog and adds
    the scopeSelected signal for compatibility with old signal-based workflows.

    The dialog now handles detection internally, so connecting to scopeSelected
    is optional - the detection will run when user clicks "Start Detection".
    """

    # Compatibility signal - emits (photo_ids, options) when detection starts
    # This is for backward compatibility with code that connected to this signal
    scopeSelected = Signal(list, dict)

    def __init__(self, project_id: int, parent: Optional[QWidget] = None):
        """
        Initialize the duplicate scope dialog.

        Args:
            project_id: The project ID to detect duplicates in
            parent: Optional parent widget
        """
        # Emit deprecation warning
        warnings.warn(
            "DuplicateScopeDialog is deprecated. Use DuplicateDetectionDialog instead. "
            "The new dialog handles detection internally without requiring signal connections.",
            DeprecationWarning,
            stacklevel=2
        )

        super().__init__(project_id, parent)

        # Update window title to match old behavior
        self.setWindowTitle("Detect Duplicates & Similar Photos")

    def _start_detection(self):
        """
        Override to emit scopeSelected signal for backward compatibility.

        The signal is emitted before starting detection, allowing old code
        that connected to this signal to still work.
        """
        # Get selected photo IDs
        photo_ids = self.scope_widget.get_selected_photo_ids()

        if not photo_ids:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "No Photos Selected",
                "Please select photos to scan using the options above."
            )
            return

        # Build options dict for backward compatibility
        options = {
            'detect_exact': self.chk_exact.isChecked(),
            'detect_similar': self.chk_similar.isChecked(),
            'generate_embeddings': self.chk_generate_embeddings.isChecked(),
            'similarity_threshold': self.spin_similarity.value(),
            'time_window_seconds': self.spin_time_window.value(),
            'scope_description': self.scope_widget.get_scope_description(),
            'processing_order': self.scope_widget.get_processing_order(),
            'scope_mode': self.scope_widget.scope_mode
        }

        # Emit signal for backward compatibility
        self.scopeSelected.emit(photo_ids, options)

        # Call parent's detection method to actually run detection
        super()._start_detection()


# For convenience, also expose the main dialog
__doc__ = """
Duplicate Detection Dialog Module (Compatibility Wrapper)

This module provides backward compatibility. Use the following imports:

Recommended (new code):
    from ui.duplicate_detection_dialog import DuplicateDetectionDialog

Legacy (backward compatible):
    from ui.duplicate_scope_dialog import DuplicateScopeDialog

Both dialogs are now functionally equivalent, with DuplicateScopeDialog
being a thin wrapper around DuplicateDetectionDialog.
"""
