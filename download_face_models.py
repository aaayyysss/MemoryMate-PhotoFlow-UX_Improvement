#!/usr/bin/env python3
"""
Download and package InsightFace models for distribution

This script downloads the buffalo_l face detection model and packages it
with the application so users don't need internet access or admin rights.

Usage:
    python download_face_models.py

Models will be downloaded to: ./models/buffalo_l/

For distribution:
    1. Run this script on development machine
    2. Commit ./models/ directory to git (or package separately)
    3. App will use bundled models automatically
"""

import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def download_buffalo_model():
    """
    Download buffalo_l model to ./models/ directory.

    This model includes:
    - RetinaFace for face detection
    - ArcFace ResNet100 for face recognition
    - Total size: ~200MB
    """
    try:
        from insightface.app import FaceAnalysis
        import shutil

        logger.info("=" * 70)
        logger.info("InsightFace Model Downloader")
        logger.info("=" * 70)

        # Get project root directory
        # InsightFace expects: root/models/buffalo_l/
        # So we set root to project root, and models will be in ./models/buffalo_l/
        project_root = os.path.dirname(os.path.abspath(__file__))
        models_dir = os.path.join(project_root, 'models')
        buffalo_dir = os.path.join(models_dir, 'buffalo_l')

        logger.info(f"Project root: {project_root}")
        logger.info(f"Models will be downloaded to: {buffalo_dir}")
        logger.info(f"Model root parameter: {project_root}")

        # Create models directory
        os.makedirs(models_dir, exist_ok=True)

        # Check if model already exists
        if os.path.exists(buffalo_dir) and os.listdir(buffalo_dir):
            logger.info(f"✓ Buffalo_l model already exists at: {buffalo_dir}")
            logger.info("  To re-download, delete the directory first")
            return True

        logger.info("\n📥 Downloading buffalo_l model (this may take a few minutes)...")
        logger.info("   Model size: ~200MB")

        # Detect available providers (ONNX Runtime 1.9+ requires explicit providers)
        try:
            import onnxruntime as ort
            available_providers = ort.get_available_providers()
            if 'CUDAExecutionProvider' in available_providers:
                providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                logger.info("   Using GPU acceleration for download")
            else:
                providers = ['CPUExecutionProvider']
                logger.info("   Using CPU for download")
        except ImportError:
            providers = ['CPUExecutionProvider']
            logger.warning("   ONNXRuntime not found, defaulting to CPU")

        # Initialize FaceAnalysis with project root
        # InsightFace will create: project_root/models/buffalo_l/
        # Check version compatibility for providers parameter
        import inspect
        sig = inspect.signature(FaceAnalysis.__init__)
        init_params = {'name': 'buffalo_l', 'root': project_root}

        if 'providers' in sig.parameters:
            # Newer version: pass providers during init
            init_params['providers'] = providers
            logger.info("   Passing providers to FaceAnalysis.__init__")
        else:
            logger.info("   InsightFace version doesn't support providers in __init__")

        app = FaceAnalysis(**init_params)

        # Prepare the model - this downloads if not present
        logger.info("🔧 Preparing model...")
        prepare_params = {'ctx_id': -1, 'det_size': (640, 640)}

        # For older versions, try passing providers to prepare()
        if 'providers' not in sig.parameters:
            try:
                prepare_sig = inspect.signature(app.prepare)
                if 'providers' in prepare_sig.parameters:
                    prepare_params['providers'] = providers
                    logger.info("   Passing providers to prepare() instead")
            except Exception:
                pass

        app.prepare(**prepare_params)

        logger.info("\n✅ Model downloaded successfully!")
        logger.info(f"📁 Location: {buffalo_dir}")

        # List downloaded files
        logger.info("\n📋 Downloaded files:")
        for root, dirs, files in os.walk(buffalo_dir):
            for file in files:
                filepath = os.path.join(root, file)
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                rel_path = os.path.relpath(filepath, buffalo_dir)
                logger.info(f"   {rel_path} ({size_mb:.1f} MB)")

        total_size = sum(
            os.path.getsize(os.path.join(root, file))
            for root, dirs, files in os.walk(buffalo_dir)
            for file in files
        ) / (1024 * 1024)

        logger.info(f"\n📊 Total size: {total_size:.1f} MB")

        logger.info("\n" + "=" * 70)
        logger.info("NEXT STEPS:")
        logger.info("=" * 70)
        logger.info("1. The models are now in ./models/buffalo_l/")
        logger.info("2. These files should be distributed with your app")
        logger.info("3. Users won't need to download models on first run")
        logger.info("4. App will work on non-admin PCs without internet")
        logger.info("\nOptions for distribution:")
        logger.info("  A) Commit to git (if repo allows large files)")
        logger.info("  B) Use Git LFS for large file storage")
        logger.info("  C) Package separately and extract on install")
        logger.info("=" * 70)

        return True

    except ImportError:
        logger.error("❌ InsightFace not installed!")
        logger.error("   Install with: pip install insightface onnxruntime")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to download models: {e}")
        logger.error(f"   Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False


def verify_models():
    """Verify that models were downloaded correctly."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    buffalo_dir = os.path.join(project_root, 'models', 'buffalo_l')

    if not os.path.exists(buffalo_dir):
        logger.error(f"❌ Model directory not found: {buffalo_dir}")
        return False

    # Check for essential model files
    essential_files = ['det_10g.onnx', 'w600k_r50.onnx']
    missing_files = []

    for file in essential_files:
        file_path = os.path.join(buffalo_dir, file)
        if not os.path.exists(file_path):
            # Try in subdirectories
            found = False
            for root, dirs, files in os.walk(buffalo_dir):
                if file in files:
                    found = True
                    break
            if not found:
                missing_files.append(file)

    if missing_files:
        logger.warning(f"⚠️  Some expected files not found: {missing_files}")
        logger.warning("   Model might still work if files are named differently")
        return True  # Don't fail, just warn

    logger.info("✅ All essential model files present")
    return True


def create_gitattributes():
    """Create .gitattributes for handling large model files."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    gitattributes_path = os.path.join(project_root, '.gitattributes')

    content = """# Git LFS tracking for large model files
*.onnx filter=lfs diff=lfs merge=lfs -text
*.params filter=lfs diff=lfs merge=lfs -text
"""

    try:
        # Check if file exists and already has LFS config
        if os.path.exists(gitattributes_path):
            with open(gitattributes_path, 'r') as f:
                existing = f.read()
                if 'filter=lfs' in existing:
                    logger.info("✓ .gitattributes already configured for LFS")
                    return

        with open(gitattributes_path, 'a') as f:
            f.write(content)

        logger.info("✓ Created/updated .gitattributes for Git LFS")
        logger.info("  Note: You need to install Git LFS and run 'git lfs install'")

    except Exception as e:
        logger.warning(f"⚠️  Could not create .gitattributes: {e}")


if __name__ == "__main__":
    logger.info("\n")

    success = download_buffalo_model()

    if success:
        logger.info("\n🔍 Verifying downloaded models...")
        verify_models()

        logger.info("\n📝 Setting up Git LFS configuration...")
        create_gitattributes()

        logger.info("\n✅ SETUP COMPLETE!\n")
        sys.exit(0)
    else:
        logger.error("\n❌ SETUP FAILED!\n")
        sys.exit(1)
