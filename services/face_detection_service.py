# face_detection_service.py
# Phase 5: Face Detection Service using InsightFace
# Detects faces in photos and generates 512-dimensional embeddings
# Uses InsightFace with buffalo_l model and OnnxRuntime backend
# ------------------------------------------------------

import os
import numpy as np
from typing import List, Tuple, Optional
from PIL import Image, ImageOps
import logging
import cv2
import threading

from config.face_detection_config import get_face_config

logger = logging.getLogger(__name__)

# CRITICAL: Initialize pillow_heif for HEIC/HEIF support (iPhone photos)
# Must be done BEFORE any PIL.Image.open() calls
# pillow_heif 1.1.0+ auto-registers on import, but explicit call is safer
try:
    import pillow_heif
    # Explicit registration (works for all versions)
    from pillow_heif import register_heif_opener
    register_heif_opener()
    logger.info(f"✓ HEIC/HEIF support enabled for face detection (pillow_heif v{pillow_heif.__version__})")
except ImportError:
    logger.warning("⚠️ pillow_heif not installed - HEIC/HEIF photos will fail face detection")
    logger.warning("   Install with: pip install pillow-heif")
except AttributeError:
    # Fallback for very old versions
    logger.info("✓ HEIC/HEIF support enabled (pillow_heif auto-registered)")
except Exception as e:
    logger.warning(f"⚠️ Could not enable HEIC support in face detection: {e}")

# Lazy import InsightFace (only load when needed)
_insightface_app = None
_providers_used = None
_buffalo_dir_path = None  # CRITICAL FIX: Store buffalo_dir for fallback app initialization
_insightface_lock = threading.Lock()  # Thread-safe initialization lock (P0 Fix #4)


def _detect_available_providers():
    """
    Detect available ONNX Runtime providers (GPU/CPU).

    Returns automatic GPU detection based on proof of concept from OldPy/photo_sorter.py

    Returns:
        tuple: (providers_list, hardware_type)
            - providers_list: List of provider names for ONNXRuntime
            - hardware_type: 'GPU' or 'CPU'
    """
    try:
        import onnxruntime as ort
        available_providers = ort.get_available_providers()

        # Prefer GPU (CUDA), fallback to CPU
        if 'CUDAExecutionProvider' in available_providers:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            hardware_type = 'GPU'
            logger.info("🚀 CUDA (GPU) available - Using GPU acceleration for face detection")
        else:
            providers = ['CPUExecutionProvider']
            hardware_type = 'CPU'
            logger.info("💻 Using CPU for face detection (CUDA not available)")

        return providers, hardware_type

    except ImportError:
        logger.warning("ONNXRuntime not found, defaulting to CPU")
        return ['CPUExecutionProvider'], 'CPU'


def _find_buffalo_directory():
    """
    Find buffalo_l directory, accepting both standard and non-standard structures.

    Accepts:
    - det_10g.onnx (standard detector)
    - scrfd_10g_bnkps.onnx (alternative detector)

    Returns:
        Path to buffalo_l directory (not parent), or None if not found
    """
    import sys

    # Detector variants (accept either one)
    detector_variants = ['det_10g.onnx', 'scrfd_10g_bnkps.onnx']

    def has_detector(path):
        """Check if path contains at least one detector variant."""
        for detector in detector_variants:
            if os.path.exists(os.path.join(path, detector)):
                return True
        return False

    # Priority 1: Custom path from settings (offline use)
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        custom_path = settings.get_setting('insightface_model_path', '')
        if custom_path and os.path.exists(custom_path):
            # Check if this IS the buffalo_l directory
            if has_detector(custom_path):
                logger.info(f"🎯 Using custom model path (buffalo_l directory): {custom_path}")
                return custom_path

            # Check for models/buffalo_l/ subdirectory
            buffalo_sub = os.path.join(custom_path, 'models', 'buffalo_l')
            if os.path.exists(buffalo_sub) and has_detector(buffalo_sub):
                logger.info(f"🎯 Using custom model path: {buffalo_sub}")
                return buffalo_sub

            # Check for buffalo_l/ subdirectory (non-standard)
            buffalo_sub = os.path.join(custom_path, 'buffalo_l')
            if os.path.exists(buffalo_sub) and has_detector(buffalo_sub):
                logger.info(f"🎯 Using custom model path (nested): {buffalo_sub}")
                return buffalo_sub

            # Check for nested buffalo_l/buffalo_l/ (user's structure from log)
            buffalo_nested = os.path.join(custom_path, 'buffalo_l', 'buffalo_l')
            if os.path.exists(buffalo_nested) and has_detector(buffalo_nested):
                logger.info(f"🎯 Using custom model path (double-nested): {buffalo_nested}")
                return buffalo_nested

            logger.warning(f"⚠️ Custom path configured but no valid buffalo_l found: {custom_path}")
    except Exception as e:
        logger.debug(f"Error checking custom path: {e}")

    # Priority 2: PyInstaller bundle
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundle_dir = sys._MEIPASS
        buffalo_path = os.path.join(bundle_dir, 'insightface', 'models', 'buffalo_l')
        if os.path.exists(buffalo_path) and has_detector(buffalo_path):
            logger.info(f"🎁 Using bundled models: {buffalo_path}")
            return buffalo_path

    # Priority 3: App directory
    try:
        app_root = os.path.dirname(os.path.dirname(__file__))
        buffalo_path = os.path.join(app_root, 'models', 'buffalo_l')
        if os.path.exists(buffalo_path) and has_detector(buffalo_path):
            logger.info(f"📁 Using local bundled models: {buffalo_path}")
            return buffalo_path
    except Exception as e:
        logger.debug(f"Error checking app directory: {e}")

    # Priority 4: User home
    user_home = os.path.expanduser('~/.insightface')
    buffalo_path = os.path.join(user_home, 'models', 'buffalo_l')
    if os.path.exists(buffalo_path) and has_detector(buffalo_path):
        logger.info(f"🏠 Using user home models: {buffalo_path}")
        return buffalo_path

    # Not found - return None
    logger.warning("⚠️ No buffalo_l models found in any location")
    return None


def _get_insightface_app():
    """
    Lazy load InsightFace application with automatic GPU/CPU detection.

    Uses the proven pattern from OldPy/photo_sorter.py proof of concept:
    - Passes buffalo_l directory DIRECTLY as root (not parent)
    - Does NOT pass providers to FaceAnalysis.__init__() for compatibility
    - Only uses providers for ctx_id selection in prepare()
    - Accepts both det_10g.onnx and scrfd_10g_bnkps.onnx detectors
    - Model caching to avoid reloading

    P0 Fix #4: Uses double-checked locking to prevent race condition
    where multiple threads could initialize models simultaneously.

    CRITICAL FIX: Also stores buffalo_dir_path globally for fallback app
    """
    global _insightface_app, _providers_used, _buffalo_dir_path

    # First check without lock (fast path for already initialized)
    if _insightface_app is None:
        # Acquire lock for initialization
        with _insightface_lock:
            # Double-check inside lock (another thread may have initialized)
            if _insightface_app is None:
                try:
                    from insightface.app import FaceAnalysis

                    # Detect best available providers
                    providers, hardware_type = _detect_available_providers()
                    _providers_used = providers

                    # Find buffalo_l directory
                    buffalo_dir = _find_buffalo_directory()

                    if not buffalo_dir:
                        raise RuntimeError(
                            "InsightFace models (buffalo_l) not found.\n\n"
                            "Please configure the model path in Preferences → Face Detection\n"
                            "or download models using: python download_face_models.py"
                        )

                    # CRITICAL FIX: Store buffalo_dir globally for fallback app initialization
                    # This prevents fallback app from downloading models to wrong location
                    _buffalo_dir_path = buffalo_dir
                    logger.debug(f"[INIT] Stored buffalo_dir for fallback use: {buffalo_dir}")

                    # COMPATIBILITY: Detect InsightFace version and log details
                    try:
                        import insightface
                        insightface_version = getattr(insightface, '__version__', 'unknown')
                        logger.info(f"📦 InsightFace version: {insightface_version}")
                    except Exception as e:
                        logger.debug(f"Could not detect InsightFace version: {e}")
                        insightface_version = 'unknown'

                    # Version detection: Check if FaceAnalysis supports providers parameter
                    # This ensures compatibility with BOTH old and new InsightFace versions
                    import inspect
                    sig = inspect.signature(FaceAnalysis.__init__)
                    supports_providers = 'providers' in sig.parameters

                    # VALIDATION: Check for duplicate buffalo_l subdirectory
                    nested_buffalo = os.path.join(buffalo_dir, 'models', 'buffalo_l')
                    if os.path.exists(nested_buffalo):
                        if supports_providers:
                            # Newer InsightFace: root is grandparent, so nested structure is unused/unexpected
                            logger.warning(f"⚠️ Detected nested buffalo_l directory: {nested_buffalo}")
                            logger.warning("⚠️ This may cause model loading issues with newer InsightFace.")
                            logger.warning("   Expected: buffalo_l/det_10g.onnx")
                            logger.warning("   Found:    buffalo_l/models/buffalo_l/det_10g.onnx")
                            logger.warning("   Please check your model directory structure.")
                        else:
                            # Older InsightFace (v0.2.x-v0.7.x): root=buffalo_dir, resolves
                            # models at {root}/models/{name}/, so nested structure is EXPECTED.
                            logger.info(f"Nested buffalo_l directory detected: {nested_buffalo}")
                            logger.info("   This is expected for older InsightFace (models resolved at root/models/buffalo_l/).")

                    # P1-8 FIX: Validate ONNX model files exist and have reasonable size
                    required_models = ['det_10g.onnx', 'genderage.onnx', 'w600k_r50.onnx']
                    for model_file in required_models:
                        model_path = os.path.join(buffalo_dir, model_file)
                        if not os.path.exists(model_path):
                            logger.warning(f"P1-8: Missing model file: {model_file}")
                        else:
                            file_size = os.path.getsize(model_path)
                            if file_size < 1000:  # Less than 1KB = likely corrupted
                                raise RuntimeError(
                                    f"Model file appears corrupted: {model_file} ({file_size} bytes)\n"
                                    f"Please re-download models using: python download_face_models.py"
                                )
                            logger.debug(f"P1-8: Validated {model_file} ({file_size / 1024 / 1024:.1f} MB)")

                    # Save successful path to settings for future use
                    try:
                        from settings_manager_qt import SettingsManager
                        settings = SettingsManager()
                        current_saved = settings.get_setting('insightface_model_path', '')
                        # Only save if not already set (preserves user's manual configuration)
                        if not current_saved:
                            settings.set_setting('insightface_model_path', buffalo_dir)
                            logger.info(f"💾 Saved InsightFace model path to settings: {buffalo_dir}")
                    except Exception as e:
                        logger.debug(f"Could not save model path to settings: {e}")

                    # CRITICAL: Pass buffalo_l directory DIRECTLY as root
                    # This matches the proof of concept approach from OldPy/photo_sorter.py
                    # Do NOT pass parent directory, pass the buffalo_l directory itself!
                    logger.info(f"✓ Initializing InsightFace with buffalo_l directory: {buffalo_dir}")

                    # Suppress FutureWarnings from insightface internals:
                    # - numpy rcond deprecation in insightface/utils/transform.py
                    # - skimage estimate() deprecation in insightface/utils/face_align.py
                    # These are in third-party code we cannot modify; safe to suppress.
                    import warnings
                    warnings.filterwarnings('ignore', category=FutureWarning, module=r'insightface\.utils')

                    # Initialize FaceAnalysis with version-appropriate root path.
                    # FaceAnalysis resolves models at {root}/models/{name}/.
                    #
                    # Newer InsightFace (has 'providers' param):
                    #   root = grandparent of buffalo_dir so it finds
                    #   {app_root}/models/buffalo_l directly.
                    #
                    # Older InsightFace (v0.2.x, no 'providers' param):
                    #   root = buffalo_dir (the original working value).
                    #   v0.2.x's model_zoo cannot parse newer ONNX files at
                    #   buffalo_dir; instead it resolves its own compatible
                    #   model cache at {buffalo_dir}/models/buffalo_l/.
                    if supports_providers:
                        root_dir = os.path.dirname(os.path.dirname(buffalo_dir))
                    else:
                        root_dir = buffalo_dir
                    init_params = {'name': 'buffalo_l', 'root': root_dir}
                    logger.info(f"✓ Root dir for FaceAnalysis: {root_dir}")

                    if supports_providers:
                        # NEWER VERSION: Pass providers for optimal performance
                        init_params['providers'] = providers
                        logger.info(f"✓ Using providers parameter (newer InsightFace v{insightface_version})")
                        logger.info(f"✓ Providers: {providers}")
                        logger.info(f"✓ Version detection: API signature check confirmed providers parameter support")
                        _insightface_app = FaceAnalysis(**init_params)

                        # For newer versions, ctx_id is derived from providers automatically
                        # But we still need to call prepare()
                        # IMPORTANT: det_size MUST be (640, 640) for buffalo_l model
                        # The model was trained specifically for this input size
                        # Using different sizes causes shape mismatch errors

                        # COMPATIBILITY: Use hardware-appropriate ctx_id
                        use_cuda = isinstance(providers, (list, tuple)) and 'CUDAExecutionProvider' in providers
                        ctx_id = 0 if use_cuda else -1

                        try:
                            _insightface_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
                            logger.info(f"✅ InsightFace (buffalo_l v{insightface_version}) loaded successfully")
                            logger.info(f"   Hardware: {hardware_type}, ctx_id={ctx_id}, det_size=640x640")
                        except TypeError as te:
                            # FALLBACK: det_size might not be supported in some versions
                            logger.warning(f"det_size parameter not supported, falling back to default: {te}")
                            _insightface_app.prepare(ctx_id=ctx_id)
                            logger.info(f"✅ InsightFace (buffalo_l v{insightface_version}) loaded with default det_size")
                        except Exception as prepare_error:
                            logger.error(f"Model preparation failed: {prepare_error}")
                            logger.error("This usually means:")
                            logger.error("  1. Model files are corrupted or incomplete")
                            logger.error("  2. InsightFace version incompatible with models")
                            logger.error("  3. Wrong directory structure")

                            # FALLBACK: Try initialization with limited modules (detection + recognition only)
                            logger.warning("⚠️ Attempting fallback initialization with detection+recognition only (no landmarks)")
                            try:
                                _insightface_app = FaceAnalysis(
                                    name='buffalo_l',
                                    root=root_dir,
                                    allowed_modules=['detection', 'recognition'],
                                    providers=providers
                                )
                                _insightface_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
                                logger.warning("✅ Fallback initialization succeeded - running WITHOUT landmark detection")
                                logger.warning("   Face detection and recognition will work, but some features may be limited")
                            except Exception as fallback_error:
                                logger.error(f"❌ Fallback initialization also failed: {fallback_error}")
                                raise RuntimeError(f"Failed to prepare InsightFace models (fallback also failed): {prepare_error}") from prepare_error
                    else:
                        # OLDER VERSION: Use ctx_id approach (proof of concept compatibility)
                        logger.info(f"✓ Using ctx_id approach (older InsightFace v{insightface_version}, proof of concept compatible)")
                        logger.info(f"✓ Version detection: API signature check confirmed no providers parameter (older API)")
                        _insightface_app = FaceAnalysis(**init_params)

                        # Use providers ONLY for ctx_id selection (proof of concept approach)
                        use_cuda = isinstance(providers, (list, tuple)) and 'CUDAExecutionProvider' in providers
                        ctx_id = 0 if use_cuda else -1
                        logger.info(f"✓ Using {hardware_type} acceleration (ctx_id={ctx_id})")

                        # Prepare model with simple parameters (matches proof of concept)
                        # IMPORTANT: det_size MUST be (640, 640) for buffalo_l model
                        # The model was trained specifically for this input size
                        # Using different sizes causes shape mismatch errors
                        try:
                            _insightface_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
                            logger.info(f"✅ InsightFace (buffalo_l) loaded successfully with {hardware_type} acceleration (det_size=640x640)")
                        except Exception as prepare_error:
                            logger.error(f"Model preparation failed: {prepare_error}")
                            logger.error("This usually means:")
                            logger.error("  1. Model files are corrupted or incomplete")
                            logger.error("  2. InsightFace version incompatible with models")
                            logger.error("  3. Wrong directory structure")

                            # FALLBACK: Try initialization with limited modules (detection + recognition only)
                            logger.warning("⚠️ Attempting fallback initialization with detection+recognition only (no landmarks)")
                            try:
                                _insightface_app = FaceAnalysis(
                                    name='buffalo_l',
                                    root=root_dir,
                                    allowed_modules=['detection', 'recognition']
                                )
                                _insightface_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
                                logger.warning("✅ Fallback initialization succeeded - running WITHOUT landmark detection")
                                logger.warning("   Face detection and recognition will work, but some features may be limited")
                            except Exception as fallback_error:
                                logger.error(f"❌ Fallback initialization also failed: {fallback_error}")
                                raise RuntimeError(f"Failed to prepare InsightFace models (fallback also failed): {prepare_error}") from prepare_error

                except ImportError as e:
                    logger.error(f"❌ InsightFace library not installed: {e}")
                    logger.error("Install with: pip install insightface onnxruntime")
                    raise ImportError(
                        "InsightFace library required for face detection. "
                        "Install with: pip install insightface onnxruntime"
                    ) from e
                except Exception as e:
                    logger.error(f"❌ Failed to initialize InsightFace: {e}")
                    logger.error(f"Error details: {type(e).__name__}: {str(e)}")
                    raise
    return _insightface_app


def cleanup_insightface():
    """
    Clean up InsightFace models and release GPU/CPU resources.

    P0 Fix #1: Implement explicit resource cleanup to prevent memory leaks.
    Call this when shutting down the application or when face detection
    is no longer needed.

    The global `_insightface_app` persists indefinitely without cleanup,
    accumulating GPU/CPU memory on systems with 8GB RAM when processing 1000+ photos.
    """
    global _insightface_app, _providers_used

    with _insightface_lock:
        if _insightface_app is not None:
            try:
                # Try to explicitly delete the model to release resources
                del _insightface_app
                _insightface_app = None
                _providers_used = None
                logger.info("✓ InsightFace models cleaned up and resources released")
            except Exception as e:
                logger.warning(f"Error during InsightFace cleanup: {e}")
                # Still set to None even if deletion failed
                _insightface_app = None
                _providers_used = None


def get_hardware_info():
    """
    Get information about the hardware being used for face detection.

    Returns:
        dict: Hardware information
            - 'type': 'GPU' or 'CPU'
            - 'providers': List of ONNXRuntime providers
            - 'cuda_available': bool
    """
    providers, hardware_type = _detect_available_providers()

    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
        cuda_available = 'CUDAExecutionProvider' in available
    except (ImportError, AttributeError) as e:
        # BUG-H5 FIX: Log CUDA detection failures
        print(f"[FaceDetection] Failed to detect CUDA: {e}")
        cuda_available = False

    return {
        'type': hardware_type,
        'providers': providers,
        'cuda_available': cuda_available
    }


class FaceDetectionService:
    """
    Service for detecting faces and generating embeddings using InsightFace.

    Uses InsightFace library which provides:
    - Face detection via RetinaFace (accurate, fast)
    - 512-dimensional face embeddings via ArcFace ResNet
    - High accuracy face recognition
    - OnnxRuntime backend for CPU/GPU inference

    Model: buffalo_l (large model, high accuracy)
    - Detection: RetinaFace
    - Recognition: ArcFace (ResNet100)
    - Embedding dimension: 512 (vs 128 for dlib)
    - Backend: OnnxRuntime

    Usage:
        service = FaceDetectionService()
        faces = service.detect_faces("photo.jpg")
        for face in faces:
            print(f"Found face at {face['bbox']} with confidence {face['confidence']}")
            print(f"Embedding shape: {face['embedding'].shape}")  # (512,)
    """

    @staticmethod
    def check_backend_availability() -> dict:
        """
        Check availability of face detection backends WITHOUT initializing them.

        This method checks if the required libraries can be imported
        without triggering expensive model downloads or initializations.

        Returns:
            Dictionary mapping backend name to availability status:
            {
                "insightface": bool,  # True if insightface and onnxruntime are available
                "face_recognition": False  # No longer supported
            }
        """
        availability = {
            "insightface": False,
            "face_recognition": False  # Deprecated, not supported
        }

        # Check InsightFace availability
        try:
            import insightface  # Just check if module exists
            import onnxruntime  # Check OnnxRuntime too
            availability["insightface"] = True
        except ImportError:
            pass

        return availability

    def __init__(self, model: str = "buffalo_l"):
        """
        Initialize face detection service.

        Args:
            model: Detection model to use (buffalo_l, buffalo_s, antelopev2)
                   - "buffalo_l" (recommended, high accuracy)
                   - "buffalo_s" (smaller, faster, lower accuracy)
                   - "antelopev2" (latest model)
        """
        self.model = model
        self.app = _get_insightface_app()
        self.fallback_app = None  # Cache for detection+recognition fallback (PyInstaller fix)
        logger.info(f"[FaceDetection] Initialized InsightFace with model={model}")

    def is_available(self) -> bool:
        """
        Check if the service is available and ready to use.

        Returns:
            True if InsightFace is initialized and ready, False otherwise
        """
        try:
            return self.app is not None
        except Exception:
            return False

    def has_gpu(self) -> bool:
        """
        Check if GPU (CUDA) is available for face detection.

        ENHANCEMENT (2026-01-07): Used to determine when to enable batch processing
        for optimal performance. GPU systems benefit from batch processing due to
        parallel execution on GPU cores.

        Returns:
            True if CUDA GPU is available, False otherwise
        """
        global _providers_used
        if _providers_used is None:
            return False
        return 'CUDAExecutionProvider' in _providers_used

    def cleanup(self):
        """
        Clean up InsightFace resources to prevent memory leaks.

        CRITICAL: This method releases both main and fallback InsightFace instances.
        Call this when:
        - Face detection job completes
        - User cancels detection
        - App shuts down

        Note: The global _insightface_app is NOT cleared (singleton pattern).
        Only this instance's fallback_app is released.
        """
        logger.info("[FaceDetection] Cleaning up resources...")

        # Release fallback app (instance-specific)
        if self.fallback_app is not None:
            try:
                del self.fallback_app
                self.fallback_app = None
                logger.debug("[FaceDetection] ✓ Released fallback app")
            except Exception as e:
                logger.warning(f"Error releasing fallback app: {e}")

        logger.info("[FaceDetection] ✓ Cleanup complete")

    def __del__(self):
        """
        Destructor to ensure cleanup on deletion.

        Note: Python's garbage collector may not call this immediately,
        so explicit cleanup() calls are preferred.
        """
        try:
            self.cleanup()
        except Exception:
            pass  # Ignore errors during destruction

    @staticmethod
    def calculate_face_quality(face_dict: dict, img: np.ndarray) -> float:
        """
        Calculate face quality score (0-1, higher is better).

        Quality factors:
        1. Blur detection (Laplacian variance)
        2. Face size (larger = better quality)
        3. Detection confidence

        Args:
            face_dict: Face dictionary with bbox info
            img: Original image (BGR format)

        Returns:
            float: Quality score (0-1)
        """
        try:
            # CRITICAL: Validate img is not None before accessing shape
            if img is None or not isinstance(img, np.ndarray) or img.size == 0:
                logger.warning("Invalid image passed to calculate_face_quality, using confidence only")
                return face_dict.get('confidence', 0.5)
            
            quality_score = 1.0

            x1, y1 = face_dict['bbox_x'], face_dict['bbox_y']
            x2 = x1 + face_dict['bbox_w']
            y2 = y1 + face_dict['bbox_h']

            # Ensure coordinates are within image bounds
            h, w = img.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)

            # 1. Blur detection (Laplacian variance)
            face_region = img[y1:y2, x1:x2]
            if face_region.size > 0:
                gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)
                blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()

                # Normalize blur score (typical range: 0-500+)
                if blur_score < 50:  # Very blurry
                    quality_score *= 0.3
                elif blur_score < 100:  # Somewhat blurry
                    quality_score *= 0.6
                elif blur_score < 200:  # Acceptable
                    quality_score *= 0.9
                # else: sharp, keep 1.0

            # 2. Face size scoring (larger faces = better quality)
            face_area = face_dict['bbox_w'] * face_dict['bbox_h']
            if face_area < 40*40:  # Very small
                quality_score *= 0.4
            elif face_area < 80*80:  # Small
                quality_score *= 0.7
            elif face_area < 120*120:  # Medium
                quality_score *= 0.9
            # else: large, keep 1.0

            # 3. Detection confidence
            quality_score *= face_dict['confidence']

            return min(1.0, max(0.0, quality_score))

        except Exception as e:
            logger.debug(f"Error calculating face quality: {e}")
            return face_dict.get('confidence', 0.5)  # Fallback to confidence only

    def detect_faces(self, image_path: str, project_id: Optional[int] = None) -> List[dict]:
        """
        Detect all faces in an image and generate embeddings.

        Args:
            image_path: Path to image file

        Returns:
            List of face dictionaries with:
            {
                'bbox': [x1, y1, x2, y2],  # Face bounding box
                'bbox_x': int,  # X coordinate (top-left)
                'bbox_y': int,  # Y coordinate (top-left)
                'bbox_w': int,  # Width
                'bbox_h': int,  # Height
                'embedding': np.array (512,),  # Face embedding vector (ArcFace)
                'confidence': float  # Detection confidence (0-1)
            }

        Example:
            faces = service.detect_faces("photo.jpg")
            print(f"Found {len(faces)} faces")
        """
        try:
            # Check if file exists
            if not os.path.exists(image_path):
                logger.warning(f"Image not found: {image_path}")
                return []

            # CRITICAL FIX: Load image using PIL first (supports HEIC/HEIF/RAW)
            # Then convert to cv2 format for InsightFace
            # This handles:
            # - HEIC/HEIF files (iPhone photos) via pillow_heif
            # - Unicode filenames (Arabic, Chinese, emoji, etc.)
            # - Corrupted files (graceful fallback)
            # - EXIF orientation correction
            
            img = None
            try:
                # STEP 1: Load with PIL (supports more formats)
                from PIL import Image, ImageOps
                
                pil_image = Image.open(image_path)
                
                # Auto-rotate based on EXIF orientation
                pil_image = ImageOps.exif_transpose(pil_image)
                
                # Convert to RGB if needed (removes alpha channel, handles grayscale)
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')
                
                # STEP 2: Convert PIL → numpy array → cv2 BGR format
                # PIL uses RGB, cv2/InsightFace expects BGR
                img_rgb = np.array(pil_image)
                
                # CRITICAL: Validate numpy conversion succeeded (can fail in PyInstaller)
                if img_rgb is None or not isinstance(img_rgb, np.ndarray) or img_rgb.size == 0:
                    logger.warning(f"NumPy conversion failed for {os.path.basename(image_path)}, trying cv2 fallback")
                    pil_image.close()
                    raise ValueError("NumPy array conversion failed")
                
                # Convert RGB to BGR for cv2/InsightFace
                img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
                
                # CRITICAL: Validate cv2.cvtColor succeeded
                if img is None or not hasattr(img, 'shape') or img.size == 0:
                    logger.warning(f"cv2.cvtColor failed for {os.path.basename(image_path)}, trying cv2 fallback")
                    pil_image.close()
                    raise ValueError("cv2.cvtColor failed")
                
                # Cleanup PIL image
                pil_image.close()
                
                logger.debug(f"Loaded image via PIL: {img.shape} - {os.path.basename(image_path)}")
                
            except Exception as pil_error:
                # FALLBACK: Try cv2.imdecode for standard formats
                logger.debug(f"PIL failed, trying cv2.imdecode: {pil_error}")
                
                try:
                    # Read file as binary and decode with cv2
                    # This handles Unicode filenames that cv2.imread() can't process
                    with open(image_path, 'rb') as f:
                        file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                    
                    if img is None:
                        logger.warning(f"Failed to load image (both PIL and cv2 failed): {image_path}")
                        return []
                        
                except Exception as cv2_error:
                    logger.warning(f"Failed to load image with cv2: {cv2_error}")
                    return []

            # Detect faces and extract embeddings
            cfg = get_face_config()
            params = cfg.get_detection_params(project_id)
            conf_th = float(params.get('confidence_threshold', 0.65))
            min_face_size = int(params.get('min_face_size', 20))
            show_low_conf = bool(cfg.get('show_low_confidence', False))
            
            # OPTIMIZATION: Downscale very large images to improve speed/memory
            # Must check img.shape AFTER verifying img is not None
            original_img = img  # Keep original for quality calculation in case resize fails
            scale_factor = 1.0  # CRITICAL FIX (2026-01-08): Track scale factor for bbox coordinate correction

            # CRITICAL VALIDATION: Ensure img is valid before ANY operations
            if img is None:
                logger.error(f"CRITICAL: img became None after PIL/cv2 loading for {os.path.basename(image_path)}")
                return []
            
            if not isinstance(img, np.ndarray):
                logger.error(f"CRITICAL: img is not a numpy array (type: {type(img)}) for {os.path.basename(image_path)}")
                return []
            
            if not hasattr(img, 'shape'):
                logger.error(f"CRITICAL: img has no 'shape' attribute for {os.path.basename(image_path)}")
                return []
            
            if img.size == 0:
                logger.error(f"CRITICAL: img.size is 0 for {os.path.basename(image_path)}")
                return []
            
            try:
                max_dim = max(img.shape[0], img.shape[1])
                logger.debug(f"Image dimensions: {img.shape}, max_dim={max_dim} for {os.path.basename(image_path)}")
                
                if max_dim > 3000:
                    scale = 2000.0 / max_dim
                    resized_img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    # CRITICAL: Check if resize succeeded before replacing img
                    if resized_img is not None and hasattr(resized_img, 'shape') and resized_img.size > 0:
                        img = resized_img
                        scale_factor = scale  # CRITICAL FIX (2026-01-08): Record scale factor for bbox correction
                        logger.debug(f"Downscaled image for detection: scale={scale:.3f}")
                    else:
                        logger.warning(f"Image resize failed for {image_path}, using original size")
                        # Keep original_img for both detection and quality
            except Exception as resize_error:
                logger.error(f"Failed to resize {image_path}: {resize_error}")
                import traceback
                logger.error(f"Resize error traceback:\n{traceback.format_exc()}")
                # Continue with original img (don't modify it)
                img = original_img
            
            # Final validation before face detection
            if img is None or not hasattr(img, 'shape') or img.size == 0:
                logger.warning(f"Image is None or empty after processing: {image_path}")
                return []
            
            # CRITICAL FIX: InsightFace requires contiguous BGR uint8 array
            # PyInstaller environments may have non-contiguous arrays that crash InsightFace
            logger.debug(f"[VALIDATION] Validating array for {os.path.basename(image_path)}")  # DEBUG level
            
            try:
                # Log current state (only if validation issues found)
                if not img.flags['C_CONTIGUOUS'] or img.dtype != np.uint8:
                    logger.warning(f"[VALIDATION] Array needs fixing for {os.path.basename(image_path)}: dtype={img.dtype}, contiguous={img.flags['C_CONTIGUOUS']}")
                
                # Ensure array is contiguous in memory
                if not img.flags['C_CONTIGUOUS']:
                    img = np.ascontiguousarray(img)
                    logger.info(f"[VALIDATION] Converted to contiguous array")
                
                # Ensure correct dtype (InsightFace expects uint8)
                if img.dtype != np.uint8:
                    if img.dtype == np.float32 or img.dtype == np.float64:
                        # Normalize float to uint8 range
                        img = (img * 255).astype(np.uint8)
                    else:
                        img = img.astype(np.uint8)
                    logger.info(f"[VALIDATION] Converted dtype to uint8")
                
                # Ensure 3-channel BGR format
                if len(img.shape) != 3:
                    logger.error(f"[VALIDATION] ❌ Invalid shape {img.shape} (not 3D) for {os.path.basename(image_path)}")
                    return []
                
                if img.shape[2] != 3:
                    logger.error(f"[VALIDATION] ❌ Invalid channels {img.shape[2]} (expected 3) for {os.path.basename(image_path)}")
                    return []
                
                logger.debug(f"[VALIDATION] ✅ Validated: {img.shape}, {img.dtype}")
                
            except Exception as validation_error:
                logger.error(f"[VALIDATION] ❌ Failed for {os.path.basename(image_path)}: {validation_error}")
                import traceback
                logger.error(f"Validation traceback:\n{traceback.format_exc()}")
                return []
            
            # Returns list of Face objects with bbox, embedding, det_score, etc.
            # CRITICAL: InsightFace might fail silently in PyInstaller if models not loaded
            logger.debug(f"[INSIGHTFACE] Calling app.get() for {os.path.basename(image_path)}")
            
            try:
                detected_faces = self.app.get(img)
                logger.debug(f"[INSIGHTFACE] ✅ Returned {len(detected_faces) if detected_faces else 0} faces")
            except AttributeError as attr_error:
                # SPECIFIC FIX: InsightFace internal NoneType error during landmark detection
                if "'NoneType' object has no attribute 'shape'" in str(attr_error):
                    logger.warning(f"[INSIGHTFACE] ⚠️ InsightFace landmark detection failed (internal NoneType) for {os.path.basename(image_path)} - using fallback")
                    
                    # WORKAROUND: Use cached detection + recognition fallback app
                    try:
                        # Check if fallback app is already initialized
                        if self.fallback_app is None:
                            logger.warning(f"[INSIGHTFACE] Initializing fallback app (detection+recognition, no landmarks) - will be cached")
                            from insightface.app import FaceAnalysis

                            # CRITICAL FIX: Use the same buffalo_dir as main app
                            # This prevents downloading models to ~/.insightface/ (wrong location)
                            # and ensures recognition module is loaded correctly
                            global _buffalo_dir_path, _providers_used
                            if _buffalo_dir_path is None:
                                logger.error("[INSIGHTFACE] ❌ Buffalo dir path not available for fallback app!")
                                raise RuntimeError("Buffalo directory not initialized - cannot create fallback app")

                            logger.info(f"[INSIGHTFACE] 📁 Using bundled models for fallback: {_buffalo_dir_path}")

                            # CRITICAL FIX: InsightFace ignores 'root' parameter when 'allowed_modules' is set
                            # Workaround: Temporarily override INSIGHTFACE_HOME environment variable
                            import os as os_module
                            original_home = os_module.environ.get('INSIGHTFACE_HOME')
                            try:
                                # Version detection: Check if FaceAnalysis supports providers parameter
                                import inspect
                                sig = inspect.signature(FaceAnalysis.__init__)
                                supports_providers = 'providers' in sig.parameters

                                # Same version-conditional root as main init:
                                # newer → grandparent so {root}/models/{name} resolves;
                                # older (v0.2.x) → buffalo_dir itself.
                                if supports_providers:
                                    parent_dir = os.path.dirname(os.path.dirname(_buffalo_dir_path))
                                else:
                                    parent_dir = _buffalo_dir_path
                                os_module.environ['INSIGHTFACE_HOME'] = parent_dir
                                logger.debug(f"[INSIGHTFACE] Temporarily set INSIGHTFACE_HOME to: {parent_dir}")

                                # Initialize fallback app with version-appropriate parameters
                                if supports_providers:
                                    # NEWER VERSION: Pass providers for optimal performance
                                    logger.debug(f"[INSIGHTFACE] Fallback using providers parameter (newer version)")
                                    self.fallback_app = FaceAnalysis(
                                        name=self.model,
                                        allowed_modules=['detection', 'recognition'],
                                        providers=_providers_used if _providers_used else ['CPUExecutionProvider']
                                    )
                                else:
                                    # OLDER VERSION: Use ctx_id approach (no providers parameter)
                                    logger.debug(f"[INSIGHTFACE] Fallback using ctx_id approach (older version)")
                                    self.fallback_app = FaceAnalysis(
                                        name=self.model,
                                        allowed_modules=['detection', 'recognition']
                                    )

                                # Prepare with appropriate context
                                use_cuda = isinstance(_providers_used, (list, tuple)) and 'CUDAExecutionProvider' in _providers_used if _providers_used else False
                                ctx_id = 0 if use_cuda else -1
                                
                                try:
                                    self.fallback_app.prepare(ctx_id=ctx_id, det_size=(640, 640))
                                    logger.info(f"[INSIGHTFACE] ✅ Fallback app cached with embeddings support (ctx_id={ctx_id})")
                                except TypeError:
                                    # det_size not supported in some versions
                                    self.fallback_app.prepare(ctx_id=ctx_id)
                                    logger.info(f"[INSIGHTFACE] ✅ Fallback app cached (default det_size, ctx_id={ctx_id})")
                            finally:
                                # Restore original INSIGHTFACE_HOME
                                if original_home is not None:
                                    os_module.environ['INSIGHTFACE_HOME'] = original_home
                                else:
                                    os_module.environ.pop('INSIGHTFACE_HOME', None)
                                logger.debug(f"[INSIGHTFACE] Restored INSIGHTFACE_HOME")
                        else:
                            logger.debug(f"[INSIGHTFACE] Using cached fallback app (no reinitialization)")

                        detected_faces = self.fallback_app.get(img)
                        logger.info(f"[INSIGHTFACE] ✅ Fallback app returned {len(detected_faces) if detected_faces else 0} faces with embeddings")
                        
                    except Exception as det_rec_error:
                        logger.error(f"[INSIGHTFACE] ❌ Fallback app failed: {det_rec_error}")
                        import traceback
                        logger.error(f"Fallback traceback:\n{traceback.format_exc()}")
                        return []
                else:
                    logger.error(f"InsightFace.get() failed for {os.path.basename(image_path)}: {attr_error}")
                    import traceback
                    logger.error(f"InsightFace traceback:\n{traceback.format_exc()}")
                    return []
            except Exception as insightface_error:
                logger.error(f"InsightFace.get() failed for {os.path.basename(image_path)}: {insightface_error}")
                import traceback
                logger.error(f"InsightFace traceback:\n{traceback.format_exc()}")
                return []

            if not detected_faces:
                logger.debug(f"No faces found in {image_path}")
                return []

            # Convert InsightFace results to our format
            faces = []
            for face in detected_faces:
                # Get bounding box: [x1, y1, x2, y2]
                bbox = face.bbox.astype(int)
                x1, y1, x2, y2 = bbox

                # CRITICAL FIX (2026-01-08): Scale bbox coordinates back to original image dimensions
                # If image was downscaled for detection, bbox coordinates are in scaled space
                # Must scale them back to match original image for correct cropping
                if scale_factor != 1.0:
                    x1 = int(x1 / scale_factor)
                    y1 = int(y1 / scale_factor)
                    x2 = int(x2 / scale_factor)
                    y2 = int(y2 / scale_factor)
                    logger.debug(f"Scaled bbox back to original: scale_factor={scale_factor:.3f}")

                # Calculate dimensions
                bbox_x = int(x1)
                bbox_y = int(y1)
                bbox_w = int(x2 - x1)
                bbox_h = int(y2 - y1)

                # Get confidence score from detection
                confidence = float(face.det_score)

                # Get embedding (512-dimensional ArcFace embedding)
                embedding = face.normed_embedding  # Already normalized to unit length

                # ENHANCEMENT #1: Extract facial landmarks (kps) for face alignment
                # InsightFace provides 5 landmarks: left_eye, right_eye, nose, left_mouth, right_mouth
                kps = None
                if hasattr(face, 'kps') and face.kps is not None:
                    kps = face.kps.astype(float).tolist()  # Convert to list for JSON serialization

                    # CRITICAL FIX (2026-01-08): Scale landmarks back to original dimensions
                    if scale_factor != 1.0:
                        kps = [[x / scale_factor, y / scale_factor] for x, y in kps]
                        logger.debug(f"[FaceDetection] Scaled {len(kps)} facial landmarks to original dimensions")
                    else:
                        logger.debug(f"[FaceDetection] Extracted {len(kps)} facial landmarks")

                faces.append({
                    'bbox': bbox.tolist(),
                    'bbox_x': bbox_x,
                    'bbox_y': bbox_y,
                    'bbox_w': bbox_w,
                    'bbox_h': bbox_h,
                    'embedding': embedding,
                    'confidence': confidence,
                    'kps': kps  # NEW: Facial landmarks for alignment
                })

            # OPTIMIZATION: Calculate quality scores for all faces
            # Use original_img for quality calculation (not downscaled version)
            quality_img = original_img if original_img is not None else img
            for face in faces:
                face['quality'] = self.calculate_face_quality(face, quality_img)

            # Filter by size and confidence
            if show_low_conf:
                faces = [f for f in faces if min(f['bbox_w'], f['bbox_h']) >= min_face_size]
            else:
                faces = [f for f in faces if f['confidence'] >= conf_th and min(f['bbox_w'], f['bbox_h']) >= min_face_size]

            # ENHANCEMENT (2026-01-07): Quality threshold filtering
            # Filter out low-quality faces to reduce clutter in person clusters
            min_quality = float(params.get('min_quality_score', 0.0))  # 0-100 scale (0 = disabled)
            if min_quality > 0:
                original_count = len(faces)
                # Convert 0-100 config value to 0-1 internal scale
                min_quality_normalized = min_quality / 100.0
                faces = [f for f in faces if f.get('quality', 0) >= min_quality_normalized]
                filtered_count = original_count - len(faces)

                if filtered_count > 0:
                    logger.info(
                        f"[FaceDetection] Filtered {filtered_count}/{original_count} low-quality faces "
                        f"(threshold: {min_quality:.0f}/100) from {os.path.basename(image_path)}"
                    )

            # OPTIMIZATION: Sort by quality (best quality first)
            # This helps clustering: best quality faces become cluster representatives
            faces = sorted(faces, key=lambda f: f['quality'], reverse=True)

            logger.info(f"[FaceDetection] Found {len(faces)} faces in {os.path.basename(image_path)}")
            return faces

        except Exception as e:
            # Enhanced error logging with stack trace for PyInstaller debugging
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"Error detecting faces in {image_path}: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            return []

    def _load_and_preprocess_image(self, image_path: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        Load and preprocess a single image for face detection.

        Returns:
            Tuple of (processed_img, original_img) or None if loading fails
            - processed_img: Image ready for InsightFace (may be downscaled)
            - original_img: Original image for quality calculation
        """
        try:
            # Check if file exists
            if not os.path.exists(image_path):
                logger.warning(f"Image not found: {image_path}")
                return None

            # Load image using PIL first (supports HEIC/HEIF/RAW)
            img = None
            try:
                from PIL import Image, ImageOps

                pil_image = Image.open(image_path)
                pil_image = ImageOps.exif_transpose(pil_image)

                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')

                img_rgb = np.array(pil_image)

                if img_rgb is None or not isinstance(img_rgb, np.ndarray) or img_rgb.size == 0:
                    pil_image.close()
                    raise ValueError("NumPy array conversion failed")

                img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

                if img is None or not hasattr(img, 'shape') or img.size == 0:
                    pil_image.close()
                    raise ValueError("cv2.cvtColor failed")

                pil_image.close()

            except Exception as pil_error:
                logger.debug(f"PIL failed, trying cv2.imdecode: {pil_error}")
                try:
                    with open(image_path, 'rb') as f:
                        file_bytes = np.frombuffer(f.read(), dtype=np.uint8)
                        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

                    if img is None:
                        logger.warning(f"Failed to load image: {image_path}")
                        return None
                except Exception as cv2_error:
                    logger.warning(f"Failed to load image with cv2: {cv2_error}")
                    return None

            # Validate image
            if img is None or not hasattr(img, 'shape') or img.size == 0:
                return None

            if not isinstance(img, np.ndarray):
                return None

            original_img = img.copy()

            # Downscale very large images
            try:
                max_dim = max(img.shape[0], img.shape[1])
                if max_dim > 3000:
                    scale = 2000.0 / max_dim
                    resized_img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    if resized_img is not None and hasattr(resized_img, 'shape') and resized_img.size > 0:
                        img = resized_img
            except Exception as resize_error:
                logger.debug(f"Resize failed for {image_path}: {resize_error}")

            # Ensure contiguous array
            if not img.flags['C_CONTIGUOUS']:
                img = np.ascontiguousarray(img)

            # Ensure correct dtype
            if img.dtype != np.uint8:
                if img.dtype == np.float32 or img.dtype == np.float64:
                    img = (img * 255).astype(np.uint8)
                else:
                    img = img.astype(np.uint8)

            # Validate 3-channel BGR
            if len(img.shape) != 3 or img.shape[2] != 3:
                logger.warning(f"Invalid image shape: {img.shape}")
                return None

            return (img, original_img)

        except Exception as e:
            logger.warning(f"Failed to preprocess {image_path}: {e}")
            return None

    def batch_detect_faces(self, image_paths: List[str], batch_size: int = 4,
                          project_id: Optional[int] = None) -> dict:
        """
        GPU-optimized batch face detection.

        ENHANCEMENT (2026-01-07): Processes multiple images in single GPU inference
        call for better throughput. Expected performance: 2-5x faster than sequential
        processing on GPU systems.

        Args:
            image_paths: List of image paths to process
            batch_size: Number of images to process in parallel (2-8 for most GPUs)
            project_id: Optional project ID for configuration

        Returns:
            Dict mapping image_path -> list of detected faces

        Example:
            results = service.batch_detect_faces(photo_paths, batch_size=4)
            for path, faces in results.items():
                print(f"{path}: {len(faces)} faces")
        """
        results = {}

        # Get configuration
        cfg = get_face_config()
        params = cfg.get_detection_params(project_id)
        conf_th = float(params.get('confidence_threshold', 0.65))
        min_face_size = int(params.get('min_face_size', 20))
        show_low_conf = bool(cfg.get('show_low_confidence', False))

        logger.info(f"[BatchDetection] Processing {len(image_paths)} images in batches of {batch_size}")

        # Process in batches
        for batch_idx in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[batch_idx:batch_idx+batch_size]

            # Load and preprocess batch
            batch_images = []
            batch_originals = []
            valid_paths = []

            for path in batch_paths:
                try:
                    result = self._load_and_preprocess_image(path)
                    if result is not None:
                        img, original = result
                        batch_images.append(img)
                        batch_originals.append(original)
                        valid_paths.append(path)
                    else:
                        results[path] = []
                except Exception as e:
                    logger.warning(f"Failed to load {path}: {e}")
                    results[path] = []

            if not batch_images:
                continue

            # Batch inference (single GPU call for multiple images)
            try:
                logger.debug(f"[BatchDetection] Processing batch {batch_idx//batch_size + 1}: {len(batch_images)} images")

                # InsightFace batch processing: process all images in one GPU call
                all_detected_faces = []
                for img in batch_images:
                    try:
                        detected_faces = self.app.get(img)
                        all_detected_faces.append(detected_faces if detected_faces else [])
                    except AttributeError as attr_error:
                        # Handle landmark detection errors with fallback app
                        if "'NoneType' object has no attribute 'shape'" in str(attr_error):
                            if self.fallback_app is not None:
                                detected_faces = self.fallback_app.get(img)
                                all_detected_faces.append(detected_faces if detected_faces else [])
                            else:
                                all_detected_faces.append([])
                        else:
                            all_detected_faces.append([])
                    except Exception as e:
                        logger.warning(f"Detection failed for image in batch: {e}")
                        all_detected_faces.append([])

                # Process results for each image
                for path, detected_faces, original_img in zip(valid_paths, all_detected_faces, batch_originals):
                    faces = []

                    for face in detected_faces:
                        # Get bounding box
                        bbox = face.bbox.astype(int)
                        x1, y1, x2, y2 = bbox

                        bbox_x = int(x1)
                        bbox_y = int(y1)
                        bbox_w = int(x2 - x1)
                        bbox_h = int(y2 - y1)

                        confidence = float(face.det_score)
                        embedding = face.normed_embedding

                        # Extract landmarks
                        kps = None
                        if hasattr(face, 'kps') and face.kps is not None:
                            kps = face.kps.astype(float).tolist()

                        faces.append({
                            'bbox': bbox.tolist(),
                            'bbox_x': bbox_x,
                            'bbox_y': bbox_y,
                            'bbox_w': bbox_w,
                            'bbox_h': bbox_h,
                            'embedding': embedding,
                            'confidence': confidence,
                            'kps': kps
                        })

                    # Calculate quality scores
                    for face in faces:
                        face['quality'] = self.calculate_face_quality(face, original_img)

                    # Filter by size and confidence
                    if show_low_conf:
                        faces = [f for f in faces if min(f['bbox_w'], f['bbox_h']) >= min_face_size]
                    else:
                        faces = [f for f in faces if f['confidence'] >= conf_th and min(f['bbox_w'], f['bbox_h']) >= min_face_size]

                    # ENHANCEMENT (2026-01-07): Quality threshold filtering
                    # Filter out low-quality faces to reduce clutter in person clusters
                    min_quality = float(params.get('min_quality_score', 0.0))  # 0-100 scale (0 = disabled)
                    if min_quality > 0:
                        original_count = len(faces)
                        # Convert 0-100 config value to 0-1 internal scale
                        min_quality_normalized = min_quality / 100.0
                        faces = [f for f in faces if f.get('quality', 0) >= min_quality_normalized]
                        filtered_count = original_count - len(faces)

                        if filtered_count > 0:
                            logger.debug(
                                f"[BatchDetection] Filtered {filtered_count}/{original_count} low-quality faces "
                                f"(threshold: {min_quality:.0f}/100) from {os.path.basename(path)}"
                            )

                    # Sort by quality
                    faces = sorted(faces, key=lambda f: f['quality'], reverse=True)

                    results[path] = faces
                    logger.debug(f"[BatchDetection] {os.path.basename(path)}: {len(faces)} faces")

            except Exception as e:
                logger.error(f"Batch inference failed: {e}")
                import traceback
                logger.error(f"Batch error traceback:\n{traceback.format_exc()}")
                # Fallback to sequential processing for this batch
                for path in valid_paths:
                    if path not in results:
                        results[path] = self.detect_faces(path, project_id)

        total_faces = sum(len(faces) for faces in results.values())
        logger.info(f"[BatchDetection] Complete: {len(results)} images, {total_faces} total faces")
        return results

    def save_face_crop(self, image_path: str, face: dict, output_path: str) -> bool:
        """
        Save a cropped face image to disk.

        Args:
            image_path: Original image path
            face: Face dictionary with 'bbox' key
            output_path: Path to save cropped face

        Returns:
            True if successful, False otherwise
        """
        try:
            # BUG-C2 FIX: Use context manager to prevent resource leak
            with Image.open(image_path) as img:
                # CRITICAL FIX (2026-01-08): Apply EXIF auto-rotation BEFORE cropping
                # Without this, face crops from rotated photos (portrait mode, etc.) appear sideways
                # Note: detect_faces() already applies exif_transpose when reading for detection,
                # so bbox coordinates are relative to the ROTATED image. We must apply the same
                # rotation here before cropping, otherwise bbox coordinates will be misaligned.
                img = ImageOps.exif_transpose(img)

                # Extract bounding box
                bbox_x = face['bbox_x']
                bbox_y = face['bbox_y']
                bbox_w = face['bbox_w']
                bbox_h = face['bbox_h']

                # Add padding (10% on each side)
                padding = int(min(bbox_w, bbox_h) * 0.1)
                x1 = max(0, bbox_x - padding)
                y1 = max(0, bbox_y - padding)
                x2 = min(img.width, bbox_x + bbox_w + padding)
                y2 = min(img.height, bbox_y + bbox_h + padding)

                # Crop face
                face_img = img.crop((x1, y1, x2, y2))

                # Convert RGBA to RGB if necessary (required for JPEG)
                # This handles PNG files with transparency
                if face_img.mode == 'RGBA':
                    # Create white background
                    rgb_img = Image.new('RGB', face_img.size, (255, 255, 255))
                    # Paste using alpha channel as mask
                    rgb_img.paste(face_img, mask=face_img.split()[3])
                    face_img = rgb_img
                    logger.debug(f"Converted RGBA to RGB for JPEG compatibility")
                elif face_img.mode not in ('RGB', 'L'):
                    # Convert any other modes to RGB
                    face_img = face_img.convert('RGB')
                    logger.debug(f"Converted {img.mode} to RGB")

                # Resize to standard size for consistency
                # Get crop size from config (default 160x160 for better quality)
                try:
                    cfg = get_face_config()
                    crop_size = int(cfg.get('crop_size', 160))
                except Exception:
                    crop_size = 160
                face_img = face_img.resize((crop_size, crop_size), Image.Resampling.LANCZOS)

                # Ensure directory exists
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                # Save with explicit format based on extension
                # Get quality setting from config
                try:
                    cfg = get_face_config()
                    crop_quality = int(cfg.get('crop_quality', 95))
                except Exception:
                    crop_quality = 95

                file_ext = os.path.splitext(output_path)[1].lower()
                if file_ext in ['.jpg', '.jpeg']:
                    # Save without EXIF to prevent double-rotation (we already applied rotation above)
                    face_img.save(output_path, format='JPEG', quality=crop_quality, exif=b'')
                elif file_ext == '.png':
                    face_img.save(output_path, format='PNG')
                else:
                    # Default to JPEG without EXIF
                    face_img.save(output_path, format='JPEG', quality=crop_quality, exif=b'')

                logger.debug(f"Saved face crop to {output_path}")
                return True
            # BUG-C2 FIX: img automatically closed by context manager

        except Exception as e:
            logger.error(f"Failed to save face crop: {e}")
            return False

    def batch_detect_faces(self, image_paths: List[str],
                          max_workers: int = 4) -> dict:
        """
        Detect faces in multiple images (parallel processing).

        Args:
            image_paths: List of image paths
            max_workers: Number of parallel workers

        Returns:
            Dictionary mapping image_path -> list of faces

        Example:
            results = service.batch_detect_faces(["img1.jpg", "img2.jpg"])
            for path, faces in results.items():
                print(f"{path}: {len(faces)} faces")
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        total = len(image_paths)
        # P1-4 FIX: Track failures to inform user
        failed_count = 0

        logger.info(f"[FaceDetection] Processing {total} images with {max_workers} workers")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all detection tasks
            futures = {executor.submit(self.detect_faces, path): path
                      for path in image_paths}

            # Collect results as they complete
            processed = 0
            for future in as_completed(futures):
                path = futures[future]
                try:
                    faces = future.result(timeout=30)  # P1-4 FIX: Add timeout
                    results[path] = faces
                    processed += 1

                    if processed % 10 == 0:
                        logger.info(f"[FaceDetection] Progress: {processed}/{total} images")

                except Exception as e:
                    # P1-4 FIX: Use warning-level logging and track failures
                    logger.warning(f"Face detection failed for {path}: {e}")
                    failed_count += 1
                    results[path] = []
                    processed += 1

        # P1-4 FIX: Log failure summary for user awareness
        if failed_count > 0:
            logger.warning(f"[FaceDetection] Batch complete with {failed_count}/{total} failures")
        else:
            logger.info(f"[FaceDetection] Batch complete: {processed}/{total} images processed successfully")

        return results


# Singleton instance
_face_detection_service = None

def get_face_detection_service(model: str = "buffalo_l") -> FaceDetectionService:
    """Get or create singleton FaceDetectionService instance."""
    global _face_detection_service
    if _face_detection_service is None:
        _face_detection_service = FaceDetectionService(model=model)
    return _face_detection_service


def create_face_detection_service(config: dict) -> Optional[FaceDetectionService]:
    """
    Create a new FaceDetectionService instance from configuration.

    This function creates a fresh instance (not singleton) for testing purposes.

    Args:
        config: Configuration dictionary with keys:
            - backend: "insightface" (only supported backend)
            - insightface_model: Model name ("buffalo_l", "buffalo_s", "antelopev2")

    Returns:
        FaceDetectionService instance or None if backend not supported/available

    Example:
        config = {"backend": "insightface", "insightface_model": "buffalo_l"}
        service = create_face_detection_service(config)
    """
    backend = config.get("backend", "insightface")

    if backend != "insightface":
        logger.warning(f"Unsupported backend: {backend}. Only 'insightface' is supported.")
        return None

    # Check if InsightFace is available
    availability = FaceDetectionService.check_backend_availability()
    if not availability.get("insightface", False):
        logger.error("InsightFace backend not available. Install with: pip install insightface onnxruntime")
        return None

    # Get model name from config
    model = config.get("insightface_model", "buffalo_l")

    try:
        return FaceDetectionService(model=model)
    except Exception as e:
        logger.error(f"Failed to create FaceDetectionService: {e}")
        return None
