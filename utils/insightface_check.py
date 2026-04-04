"""
InsightFace model availability checker with user-friendly notifications.

Provides clear guidance when face detection models are not installed.
"""

import os
import logging
from pathlib import Path
from typing import Tuple, Dict

logger = logging.getLogger(__name__)


def check_insightface_availability() -> Tuple[bool, str]:
    """
    Check if InsightFace library and models are available.

    Returns:
        Tuple[bool, str]: (available, message)
            - available: True if InsightFace and buffalo_l models are ready
            - message: Status message for user display
    """
    # Check if InsightFace library is installed
    try:
        import insightface
        from insightface.app import FaceAnalysis
    except ImportError:
        message = _get_install_message(library_missing=True)
        return False, message
    except AttributeError as e:
        # NumPy 2.x incompatibility: onnxruntime compiled against NumPy 1.x
        # raises "_ARRAY_API not found" when loaded with NumPy 2.x
        message = (
            "⚠️ NumPy version incompatibility detected!\n\n"
            f"Error: {e}\n\n"
            "onnxruntime (required by InsightFace) was compiled against NumPy 1.x\n"
            "but NumPy 2.x is installed. Fix:\n\n"
            "  pip install \"numpy<2\"\n\n"
            "Then restart the application."
        )
        logger.error("NumPy 2.x incompatibility: %s", e)
        return False, message

    # Check if models exist in any of the standard locations
    model_locations = _get_model_search_paths()
    models_found = False
    model_path = None

    for location in model_locations:
        buffalo_path = os.path.join(location, 'models', 'buffalo_l')
        if os.path.exists(buffalo_path) and _verify_model_files(buffalo_path):
            models_found = True
            model_path = buffalo_path
            break

    if models_found:
        message = f"✅ InsightFace detected with buffalo_l models\n   Location: {model_path}"
        return True, message
    else:
        message = _get_install_message(models_missing=True)
        return False, message


def _get_model_search_paths() -> list:
    """
    Get list of paths to search for InsightFace models.

    Priority order:
    1. Custom path from settings (for offline use)
    2. App directory (./models/buffalo_l/)
    3. PyInstaller bundle (sys._MEIPASS/insightface/)
    4. User home (~/.insightface/)
    """
    import sys

    paths = []

    # 1. Custom path from settings (offline use)
    try:
        from settings_manager_qt import get_settings
        settings = get_settings()
        custom_path = settings.get_setting('insightface_model_path', '')
        if custom_path:
            custom_path = Path(custom_path)
            if custom_path.exists():
                # Check if this is the buffalo_l directory itself
                if (custom_path / 'det_10g.onnx').exists():
                    # This is buffalo_l, use parent as root
                    paths.append(str(custom_path.parent))
                elif (custom_path / 'models' / 'buffalo_l').exists():
                    # This is the parent directory
                    paths.append(str(custom_path))
                else:
                    # Add it anyway, might have different structure
                    paths.append(str(custom_path))
    except Exception:
        pass

    # 2. App directory
    try:
        app_root = Path(__file__).parent.parent
        paths.append(str(app_root))
    except Exception:
        pass

    # 3. PyInstaller bundle
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_dir = Path(sys._MEIPASS) / 'insightface'
        paths.append(str(bundle_dir))

    # 4. User home
    user_home = Path.home() / '.insightface'
    paths.append(str(user_home))

    return paths


def _verify_model_files(buffalo_path: str) -> bool:
    """
    Verify that essential model files exist in buffalo_l directory.

    Args:
        buffalo_path: Path to buffalo_l directory

    Returns:
        True if essential files are present, False otherwise
    """
    # Accept EITHER detector variant (matches proof of concept approach)
    detector_variants = ['det_10g.onnx', 'scrfd_10g_bnkps.onnx']
    recognition_model = 'w600k_r50.onnx'

    # Check for recognition model (required)
    recognition_found = False
    for root, dirs, files in os.walk(buffalo_path):
        if recognition_model in files:
            recognition_found = True
            break

    if not recognition_found:
        return False

    # Check for at least ONE detector variant
    detector_found = False
    for detector in detector_variants:
        for root, dirs, files in os.walk(buffalo_path):
            if detector in files:
                detector_found = True
                break
        if detector_found:
            break

    if not detector_found:
        return False

    return True


def get_model_download_status() -> Dict[str, any]:
    """
    Get detailed status of model installation.

    Returns:
        Dictionary with:
            - 'library_installed': bool
            - 'models_available': bool
            - 'model_path': str or None
            - 'can_download': bool
            - 'message': str
    """
    status = {
        'library_installed': False,
        'models_available': False,
        'model_path': None,
        'can_download': False,
        'message': ''
    }

    # Check library
    try:
        import insightface
        status['library_installed'] = True
        status['can_download'] = True
    except ImportError:
        status['message'] = "InsightFace library not installed"
        return status
    except AttributeError as e:
        # NumPy 2.x incompatibility with onnxruntime
        status['message'] = (
            f"NumPy version conflict: {e}. "
            "Fix: pip install \"numpy<2\" and restart."
        )
        logger.error("NumPy 2.x incompatibility in get_model_download_status: %s", e)
        return status

    # Check models
    model_locations = _get_model_search_paths()
    for location in model_locations:
        buffalo_path = os.path.join(location, 'models', 'buffalo_l')
        if os.path.exists(buffalo_path) and _verify_model_files(buffalo_path):
            status['models_available'] = True
            status['model_path'] = buffalo_path
            status['message'] = f"Models found at: {buffalo_path}"
            break

    if not status['models_available']:
        status['message'] = "Models not found - download needed"

    return status


def _get_install_message(library_missing: bool = False, models_missing: bool = False) -> str:
    """
    Get user-friendly installation message based on what's missing.

    Args:
        library_missing: InsightFace library is not installed
        models_missing: Models are not downloaded

    Returns:
        Formatted message with installation instructions
    """
    if library_missing:
        return """
═══════════════════════════════════════════════════════════════════
⚠️  InsightFace Library Not Found
═══════════════════════════════════════════════════════════════════

Face detection requires the InsightFace library.

⚠️ Impact:
  ✅ Photos can still be viewed and organized
  ❌ Face detection won't work
  ❌ People sidebar will be empty

📦 Installation:
  pip install insightface onnxruntime

After installation:
  1. Restart the application
  2. Go to Preferences (Ctrl+,) → 🧑 Face Detection
  3. Click "Download Models" to get face detection models
  4. Face detection will be enabled

═══════════════════════════════════════════════════════════════════
"""

    elif models_missing:
        return """
═══════════════════════════════════════════════════════════════════
⚠️  InsightFace Models Not Found
═══════════════════════════════════════════════════════════════════

Face detection models (buffalo_l) are not installed.

⚠️ Impact:
  ✅ Photos can still be viewed and organized
  ❌ Face detection won't work until models are downloaded
  ❌ People sidebar will be empty

📥 Download Models:
  Option 1: Use the application preferences
    1. Go to Preferences (Ctrl+,)
    2. Navigate to "🧑 Face Detection" section
    3. Click "Download Models"

  Option 2: Run the download script
    python download_face_models.py

💡 Model Size: ~200MB
   Models will be downloaded to: ./models/buffalo_l/

After download:
  1. Restart the application (or re-scan photos)
  2. Face detection will be automatically enabled
  3. Faces will be detected during the next scan

═══════════════════════════════════════════════════════════════════
"""

    return "Unknown error checking InsightFace availability"


def show_insightface_status_once() -> str:
    """
    Show InsightFace status message once per session.

    Returns:
        Message string if this is the first check, None otherwise
    """
    flag_file = Path('.insightface_check_done')

    # Check availability
    available, message = check_insightface_availability()

    # If available, create flag and return success message
    if available:
        if not flag_file.exists():
            flag_file.touch()
        return message

    # If something is missing and we haven't shown the message yet, show it
    if not flag_file.exists():
        # Don't create flag file when models are missing
        # This ensures the message shows every session until models are installed
        return message

    # Models are missing but we've already shown the message this session
    return None


if __name__ == '__main__':
    # Test the checker
    message = show_insightface_status_once()
    if message:
        print(message)
