"""
Model Selection Helper - User-Friendly CLIP Model Selection

This module provides functions to check for the best CLIP model availability
and prompt users if the large model is not available offline.

Features:
- Check if clip-vit-large-patch14 is available offline
- Show user-friendly dialog if not available
- Offer to download, choose alternative, or continue with available model
"""

from typing import Optional, Tuple
from PySide6.QtWidgets import QMessageBox, QWidget
from logging_config import get_logger

logger = get_logger(__name__)


def check_and_select_model(parent: Optional[QWidget] = None) -> Tuple[str, bool]:
    """
    Check if large-patch14 is available offline, prompt user if not.

    This function:
    1. Checks if clip-vit-large-patch14 is available offline
    2. If available: returns it and continues
    3. If not available: shows dialog asking user what to do
       - Download large model now
       - Continue with available model
       - Cancel operation

    Args:
        parent: Parent widget for dialog (None for standalone)

    Returns:
        Tuple of (model_variant, should_continue)
        - model_variant: The CLIP model to use
        - should_continue: False if user cancelled, True otherwise

    Example:
        variant, continue_op = check_and_select_model(self)
        if not continue_op:
            return  # User cancelled
        # Use variant for embedding extraction
    """
    from utils.clip_check import check_clip_availability, get_available_variants, get_recommended_variant

    # Check if large-patch14 is available
    large_model = 'openai/clip-vit-large-patch14'
    is_available, message = check_clip_availability(large_model)

    if is_available:
        # Large model found! Use it
        logger.info(f"[ModelSelection] Large model found offline: {large_model}")
        return large_model, True

    # Large model NOT available - check what IS available
    available_variants = get_available_variants()
    available_models = [
        variant for variant, available in available_variants.items()
        if available
    ]

    if not available_models:
        # NO models available at all - must download
        result = QMessageBox.critical(
            parent,
            "No CLIP Models Found",
            "No CLIP models are available offline.\n\n"
            "The large model (clip-vit-large-patch14) is recommended for best search quality.\n\n"
            "Would you like to download it now?\n"
            "• Size: ~1.7GB\n"
            "• Quality: 40-60% search scores\n\n"
            "Click 'Yes' to open download preferences, or 'No' to cancel.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if result == QMessageBox.Yes:
            # User wants to download - return signal to open preferences
            logger.info("[ModelSelection] User chose to download large model")
            return 'OPEN_DOWNLOAD_DIALOG', True
        else:
            # User cancelled
            logger.info("[ModelSelection] User cancelled - no models available")
            return '', False

    # Some models available, but not large-patch14
    best_available = get_recommended_variant()

    # Map model names to friendly descriptions
    model_info = {
        'openai/clip-vit-base-patch32': 'Base model (512-D, fast, 19-26% quality)',
        'openai/clip-vit-base-patch16': 'Base-16 model (512-D, better, 30-40% quality)',
        'openai/clip-vit-large-patch14': 'Large model (768-D, best, 40-60% quality)',
    }

    available_desc = model_info.get(best_available, best_available)

    # Show dialog asking user what to do
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Warning)
    msg.setWindowTitle("Large Model Not Available")
    msg.setText(
        f"The large CLIP model (clip-vit-large-patch14) is not available offline.\n\n"
        f"Best available model: {available_desc}\n\n"
        f"The large model provides 2-3x better search quality.\n"
        f"What would you like to do?"
    )

    msg.addButton("Download Large Model", QMessageBox.YesRole)
    msg.addButton(f"Continue with {best_available.split('/')[-1]}", QMessageBox.NoRole)
    msg.addButton("Cancel", QMessageBox.RejectRole)

    msg.setDefaultButton(msg.buttons()[0])  # Default to download

    result = msg.exec()
    clicked_button = msg.clickedButton()
    button_role = msg.buttonRole(clicked_button)

    if button_role == QMessageBox.YesRole:
        # User wants to download large model
        logger.info("[ModelSelection] User chose to download large model")
        return 'OPEN_DOWNLOAD_DIALOG', True
    elif button_role == QMessageBox.NoRole:
        # Continue with best available
        logger.info(f"[ModelSelection] User chose to continue with: {best_available}")
        return best_available, True
    else:
        # Cancel
        logger.info("[ModelSelection] User cancelled operation")
        return '', False


def open_model_download_preferences(parent: QWidget) -> bool:
    """
    Open the preferences dialog to the CLIP model download section.

    Args:
        parent: Parent widget (should be MainWindow)

    Returns:
        True if download dialog was opened, False otherwise
    """
    try:
        # Import here to avoid circular dependencies
        from preferences_dialog import PreferencesDialog

        # Create and show preferences dialog
        dialog = PreferencesDialog(parent)

        # TODO: Navigate to Visual Embeddings section automatically
        # For now, just open the dialog and user can navigate

        dialog.exec()

        logger.info("[ModelSelection] Preferences dialog opened for model download")
        return True

    except Exception as e:
        logger.error(f"[ModelSelection] Failed to open preferences: {e}")
        QMessageBox.critical(
            parent,
            "Error",
            f"Failed to open download preferences:\n{str(e)}\n\n"
            f"Please manually open Preferences → Visual Embeddings to download the model."
        )
        return False
