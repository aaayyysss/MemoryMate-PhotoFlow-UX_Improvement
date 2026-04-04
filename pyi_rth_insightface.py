"""
PyInstaller runtime hook for InsightFace model path resolution.

This hook ensures InsightFace finds the bundled buffalo_l models
when running from a PyInstaller-packaged executable.

It also adds the bundle directory to sys.path so root-level project
modules (main_window_qt, sidebar_qt, etc.) can be imported.
"""

import os
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    bundle_dir = sys._MEIPASS

    # ------------------------------------------------------------------
    # 1. Ensure bundle root is on sys.path for root-level module imports
    # ------------------------------------------------------------------
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

    # ------------------------------------------------------------------
    # 2. Set INSIGHTFACE_HOME so InsightFace finds bundled models.
    #    InsightFace resolves models at {INSIGHTFACE_HOME}/models/{name}/
    #    Models are bundled at {bundle_dir}/insightface/models/buffalo_l/
    # ------------------------------------------------------------------
    insightface_home = os.path.join(bundle_dir, 'insightface')
    os.environ['INSIGHTFACE_HOME'] = insightface_home

    bundled_models = os.path.join(insightface_home, 'models')
    buffalo_path = os.path.join(bundled_models, 'buffalo_l')

    print(f"[PyInstaller Hook] INSIGHTFACE_HOME = {insightface_home}")

    if os.path.isdir(buffalo_path):
        onnx_count = len([f for f in os.listdir(buffalo_path) if f.endswith('.onnx')])
        print(f"[PyInstaller Hook] Found bundled buffalo_l models ({onnx_count} .onnx files)")
    else:
        print(f"[PyInstaller Hook] WARNING: buffalo_l models not found at {buffalo_path}")

    # Also check the app-root fallback path (models/buffalo_l)
    alt_path = os.path.join(bundle_dir, 'models', 'buffalo_l')
    if os.path.isdir(alt_path):
        print(f"[PyInstaller Hook] Also found models at fallback path: {alt_path}")
