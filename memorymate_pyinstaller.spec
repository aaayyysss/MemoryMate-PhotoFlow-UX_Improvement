# pyinstaller memorymate_pyinstaller.spec
# Version: v-11_01.01.04-16 dated 20260221
# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for MemoryMate-PhotoFlow
Includes proper packaging of InsightFace models and dependencies

BUILD COMMAND:
    pyinstaller memorymate_pyinstaller.spec

PREREQUISITES:
    1. pip install -r requirements.txt
    2. Run face detection once so InsightFace downloads compatible models
       (creates models/buffalo_l/models/buffalo_l/ for v0.2.x compat)
    3. Optionally install FFmpeg and add to PATH (for video features)

OUTPUT:
    dist/MemoryMate-PhotoFlow-v-11_01.01.04-16/  (ONEDIR bundle)
"""

import os
import sys
from pathlib import Path

# PyInstaller executes spec via exec(); __file__ may be undefined.
# SPECPATH is provided by PyInstaller and points to the spec directory.
project_root = Path(SPECPATH).resolve()
insightface_models_dir = project_root / 'models' / 'buffalo_l'

# --------------------------------------------------------------------------
# Collect InsightFace model files
# --------------------------------------------------------------------------
# Walks the entire models/buffalo_l tree.  If InsightFace v0.2.x has
# previously downloaded its own compatible models into a nested
# models/buffalo_l/models/buffalo_l/ directory, those are captured too.
#
# Destination inside the bundle:  insightface/models/buffalo_l/...
# This matches _find_buffalo_directory() Priority 2 (PyInstaller check)
# and the pyi_rth_insightface.py runtime hook.
# --------------------------------------------------------------------------
model_datas = []
if insightface_models_dir.exists():
    for root, dirs, files in os.walk(insightface_models_dir):
        for file in files:
            src = os.path.join(root, file)
            rel_path = os.path.relpath(src, os.path.dirname(str(insightface_models_dir)))
            dst = os.path.join('insightface', 'models', os.path.dirname(rel_path))
            model_datas.append((src, dst))
    print(f"✓ Found {len(model_datas)} model files in {insightface_models_dir}")

    # Also bundle to models/buffalo_l (app-root relative) for
    # _find_buffalo_directory() Priority 3 fallback.
    for root, dirs, files in os.walk(insightface_models_dir):
        for file in files:
            src = os.path.join(root, file)
            rel_path = os.path.relpath(src, str(project_root))
            dst = os.path.dirname(rel_path)
            model_datas.append((src, dst))
    print(f"✓ Also bundled to models/buffalo_l for Priority 3 fallback")
else:
    print(f"⚠ WARNING: InsightFace models not found at {insightface_models_dir}")
    print("  Please run face detection once to download models before packaging")

# --------------------------------------------------------------------------
# CLIP Model Bundling - DISABLED
# --------------------------------------------------------------------------
# CLIP models are NOT bundled to keep the package size small.
# Transfer CLIP models separately to the target machine.
# Models should be placed in: models/clip-vit-base-patch32, etc.
# Run: python download_clip_large.py on the target machine if needed.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Additional data files
# --------------------------------------------------------------------------
datas = [
    # Language / translation files
    ('lang', 'lang'),
    ('locales', 'locales'),

    # Configuration package (Python + JSON)
    ('config', 'config'),

    # Layout files
    ('layouts', 'layouts'),

    # SQL / Python migration files
    ('migrations', 'migrations'),

    # Core architecture (state_bus, actions)
    ('core', 'core'),

    # Python package directories (required for dynamic imports)
    ('controllers', 'controllers'),
    ('repository', 'repository'),
    ('services', 'services'),
    ('workers', 'workers'),
    ('ui', 'ui'),
    ('utils', 'utils'),

    # Google Photos components (PhotoButton, MediaLightbox, etc.)
    ('google_components', 'google_components'),

    # Application icons and images
    ('app_icon.ico', '.'),
    ('MemoryMate-PhotoFlow-logo.png', '.'),
    ('MemoryMate-PhotoFlow-logo.jpg', '.'),

    # Configuration JSON files
    ('photo_app_settings.json', '.'),
    ('FeatureList.json', '.'),

    # Note: Databases (reference_data.db, thumb_cache_db, etc.) are excluded.
    # They are created automatically on first run.
]

# Append InsightFace model files
datas.extend(model_datas)

# NOTE: CLIP models are NOT bundled (user transfers them separately)

# --------------------------------------------------------------------------
# Bundle FFmpeg / FFprobe binaries (optional — for video support)
# --------------------------------------------------------------------------
import shutil
ffmpeg_exe = shutil.which('ffmpeg')
ffprobe_exe = shutil.which('ffprobe')

if ffmpeg_exe and ffprobe_exe:
    datas.append((ffmpeg_exe, '.'))
    datas.append((ffprobe_exe, '.'))
    print(f"✓ Bundled ffmpeg:  {ffmpeg_exe}")
    print(f"✓ Bundled ffprobe: {ffprobe_exe}")
else:
    print("⚠ WARNING: FFmpeg not found on PATH")
    print("  Video thumbnails and metadata will not work!")
    print("  Install FFmpeg and add to PATH, then re-run PyInstaller")

# --------------------------------------------------------------------------
# Hidden imports
# --------------------------------------------------------------------------
# Comprehensive audit: 2026-02-21
# Covers all project modules + third-party libraries that are lazy-loaded,
# dynamically imported, or otherwise invisible to PyInstaller's analysis.
# --------------------------------------------------------------------------
hiddenimports = [
    # === ML / AI — Core ===
    'insightface',
    'insightface.app',
    'insightface.model_zoo',
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'numpy',
    'numpy.core',
    'numpy.core._methods',
    'numpy.lib',
    'numpy.lib.format',
    'cv2',
    'cv2.cv2',
    'sklearn',
    'sklearn.cluster',
    'sklearn.preprocessing',
    'sklearn.__check_build',
    'sklearn.__check_build._check_build',
    'sklearn.utils',
    'sklearn.utils._cython_blas',
    'sklearn.neighbors',
    'sklearn.neighbors._partition_nodes',

    # === Deep Learning & Transformers (CLIP semantic search) ===
    'torch',
    'torch.nn',
    'torch.nn.functional',
    'torch.nn.modules',
    'torch.nn.modules.activation',
    'torch.nn.modules.container',
    'torch.nn.modules.linear',
    'torch.optim',
    'torch.autograd',
    'torch.autograd.function',
    'torch.cuda',
    'torch.backends',
    'torch.backends.cudnn',
    'torch.backends.mps',             # Apple Metal support
    'torch.utils',
    'torch.utils.data',
    'torch._C',
    'torch._C._distributed_c10d',
    'transformers',
    'transformers.models',
    'transformers.models.clip',
    'transformers.models.clip.modeling_clip',
    'transformers.models.clip.processing_clip',
    'transformers.models.clip.configuration_clip',
    'transformers.models.clip.image_processing_clip',
    'transformers.models.clip.tokenization_clip',
    'transformers.models.clip.tokenization_clip_fast',
    'transformers.processing_utils',
    'transformers.feature_extraction_utils',
    'transformers.image_processing_utils',
    'transformers.image_utils',
    'transformers.tokenization_utils',
    'transformers.tokenization_utils_base',
    'transformers.tokenization_utils_fast',
    'transformers.utils',
    'transformers.utils.hub',
    'transformers.dynamic_module_utils',
    'tokenizers',
    'huggingface_hub',                # Required by transformers for .from_pretrained()
    'huggingface_hub.utils',
    'safetensors',                    # Modern model format
    'safetensors.torch',
    'filelock',                       # Transformers file locking
    'regex',                          # Tokenizer dependency
    'packaging',                      # Version checking
    'packaging.version',

    # === RAW image support (DSLR: CR2, NEF, ARW, DNG) ===
    'rawpy',

    # === PIL / Pillow ===
    'PIL',
    'PIL.Image',
    'PIL.ImageOps',
    'PIL.ImageQt',
    'PIL.ImageDraw',
    'PIL.ImageFilter',
    'PIL.ExifTags',
    'PIL.ImageEnhance',

    # === PySide6 / Qt ===
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebChannel',
    'PySide6.QtSvg',
    'shiboken6',  # C++ wrapper validation (used by utils.ui_safety)

    # === Windows COM / pywin32 ===
    'win32com',
    'win32com.client',
    'win32com.shell',
    'win32api',
    'win32con',
    'win32timezone',
    'pythoncom',
    'pywintypes',
    'ctypes',
    'ctypes.wintypes',

    # === HEIF / HEIC (iPhone photos) ===
    'pillow_heif',
    'pillow_heif.heif',

    # === EXIF metadata (GPS persistence) ===
    'piexif',
    'piexif.helper',

    # === Caching ===
    'cachetools',
    'cachetools.func',

    # === Matplotlib (InsightFace dependency) ===
    'matplotlib',
    'matplotlib.pyplot',
    'matplotlib.backends',
    'matplotlib.backends.backend_agg',

    # === HTTP / Networking (for CLIP model downloads, geocoding) ===
    'requests',
    'requests.adapters',
    'requests.models',
    'urllib3',

    # === System utilities ===
    'psutil',

    # === FAISS (optional - fast ANN search, graceful fallback if missing) ===
    'faiss',

    # ======================================================================
    # PROJECT MODULES
    # ======================================================================

    # --- config ---
    'config',
    'config.face_detection_config',
    'config.embedding_config',
    'config.google_layout_config',
    'config.search_config',           # NEW 2026-03-01: Smart Find / search orchestrator config
    'config.similarity_config',

    # --- controllers ---
    'controllers',
    'controllers.photo_operations_controller',
    'controllers.project_controller',
    'controllers.scan_controller',
    'controllers.sidebar_controller',

    # --- layouts ---
    'layouts',
    'layouts.apple_layout',
    'layouts.base_layout',
    'layouts.current_layout',
    'layouts.google_layout',
    'layouts.lightroom_layout',
    'layouts.layout_manager',
    'layouts.layout_protocol',
    'layouts.video_editor_mixin',

    # --- layouts.google_components ---
    'layouts.google_components',
    'layouts.google_components.duplicate_badge_widget',
    'layouts.google_components.duplicates_dialog',
    'layouts.google_components.stack_badge_widget',
    'layouts.google_components.stack_view_dialog',

    # --- google_components (root-level) ---
    'google_components',
    'google_components.widgets',
    'google_components.media_lightbox',
    'google_components.photo_helpers',
    'google_components.dialogs',

    # --- migrations (SQL + Python migration scripts) ---
    'migrations',
    'migrations.migration_v6_visual_semantics',
    'migrations.migration_v9_1_semantic_model',

    # --- repository ---
    'repository',
    'repository.asset_repository',
    'repository.base_repository',
    'repository.folder_repository',
    'repository.job_history_repository',
    'repository.photo_repository',
    'repository.project_repository',
    'repository.stack_repository',
    'repository.tag_repository',
    'repository.video_repository',
    'repository.migrations',
    'repository.schema',

    # --- services (comprehensive — includes all 40+ modules) ---
    'services',
    'services.asset_service',
    'services.batch_iterator',
    'services.clustering_quality_analyzer',
    'services.device_id_extractor',
    'services.device_import_service',
    'services.device_monitor',
    'services.device_sources',
    'services.embedding_service',
    'services.exif_gps_writer',
    'services.exif_parser',
    'services.face_detection_benchmark',
    'services.face_detection_controller',
    'services.face_detection_service',
    'services.face_pipeline_service',
    'services.face_quality_analyzer',
    'services.geocoding_service',
    'services.group_service',
    'services.incremental_updates',
    'services.job_manager',
    'services.job_service',
    'services.library_detector',
    'services.metadata_service',
    'services.mtp_import_adapter',
    'services.people_group_service',
    'services.performance_analytics',
    'services.performance_monitor',
    'services.performance_tracking_db',
    'services.person_stack_service',
    'services.photo_deletion_service',
    'services.photo_query_service',
    'services.photo_scan_service',
    'services.photo_similarity_service',
    'services.reranking_service',
    'services.safe_image_loader',
    'services.scan_worker_adapter',
    'services.search_history_service',
    'services.search_orchestrator',      # NEW 2026-03-01: Smart Find search engine
    'services.search_service',
    'services.semantic_embedding_service',
    'services.semantic_search_service',
    'services.smart_find_service',       # NEW 2026-03-01: Smart Find preset & NL search
    'services.stack_generation_service',
    'services.tag_service',
    'services.thumbnail_manager',
    'services.thumbnail_service',
    'services.ui_refresh_mediator',
    'services.video_metadata_service',
    'services.video_service',
    'services.video_thumbnail_service',

    # --- workers (comprehensive — includes all 21 modules) ---
    'workers',
    'workers.duplicate_loading_worker',
    'workers.embedding_worker',
    'workers.face_cluster_worker',
    'workers.face_detection_worker',
    'workers.face_pipeline_worker',
    'workers.ffmpeg_detection_worker',
    'workers.group_compute_worker',
    'workers.group_index_worker',
    'workers.hash_backfill_worker',
    'workers.meta_backfill_pool',
    'workers.meta_backfill_single',
    'workers.model_warmup_worker',       # NEW 2026-02-08: Async CLIP model loading
    'workers.mtp_copy_worker',
    'workers.photo_page_worker',
    'workers.post_scan_pipeline_worker',
    'workers.progress_writer',
    'workers.semantic_embedding_worker',
    'workers.semantic_search_worker',    # NEW 2026-02-08: Async semantic search
    'workers.similar_shot_stack_worker',
    'workers.startup_maintenance_worker',
    'workers.video_metadata_worker',
    'workers.video_thumbnail_worker',

    # --- ui (comprehensive — includes all 35+ modules) ---
    'ui',
    'ui.advanced_filters_widget',
    'ui.activity_center',
    'ui.background_activity_panel',
    'ui.clip_model_dialog',
    'ui.cluster_face_selector',
    'ui.create_group_dialog',
    'ui.device_import_dialog',
    'ui.duplicate_detection_dialog',
    'ui.duplicate_scope_dialog',
    'ui.embedding_progress_dialog',
    'ui.embedding_scope_widget',
    'ui.embedding_stats_dashboard',
    'ui.face_crop_editor',
    'ui.face_detection_config_dialog',
    'ui.face_detection_progress_dialog',
    'ui.face_detection_scope_dialog',
    'ui.face_naming_dialog',
    'ui.face_quality_dashboard',
    'ui.face_quality_scorer',
    'ui.face_settings_dialog',
    'ui.hash_backfill_progress_dialog',
    'ui.location_editor_dialog',
    'ui.location_editor_integration',
    'ui.metadata_editor_dock',
    'ui.mtp_deep_scan_dialog',
    'ui.mtp_import_dialog',
    'ui.people_list_view',
    'ui.people_manager_dialog',
    'ui.performance_analytics_dialog',
    'ui.prescan_options_dialog',
    'ui.semantic_search_dialog',
    'ui.semantic_search_widget',
    'ui.similar_photo_dialog',
    'ui.similar_photos_dialog',
    'ui.similar_shot_progress_dialog',
    'ui.ui_builder',
    'ui.visual_photo_browser',

    # --- ui.panels ---
    'ui.panels',
    'ui.panels.backfill_status_panel',
    'ui.panels.details_panel',

    # --- ui.widgets ---
    'ui.widgets',
    'ui.widgets.backfill_indicator',
    'ui.widgets.breadcrumb_navigation',
    'ui.widgets.selection_toolbar',

    # --- ui.accordion_sidebar ---
    'ui.accordion_sidebar',
    'ui.accordion_sidebar.base_section',
    'ui.accordion_sidebar.dates_section',
    'ui.accordion_sidebar.devices_section',
    'ui.accordion_sidebar.duplicates_section',
    'ui.accordion_sidebar.find_section',  # NEW 2026-03-01: Smart Find sidebar section
    'ui.accordion_sidebar.folders_section',
    'ui.accordion_sidebar.groups_section',
    'ui.accordion_sidebar.locations_section',
    'ui.accordion_sidebar.people_section',
    'ui.accordion_sidebar.quick_section',
    'ui.accordion_sidebar.section_widgets',
    'ui.accordion_sidebar.videos_section',

    # --- ui.dialogs ---
    'ui.dialogs',
    'ui.dialogs.new_group_dialog',

    # --- utils ---
    'utils',
    'utils.clip_check',              # CLIP model availability checks (used by 6+ modules)
    'utils.clip_model_registry',
    'utils.dpi_helper',
    'utils.face_detection_logger',
    'utils.insightface_check',       # InsightFace status (used by main_qt, preferences_dialog)
    'utils.model_selection_helper',
    'utils.test_insightface_models', # InsightFace model tests (used by preferences_dialog)
    'utils.translation_manager',
    'utils.ui_safety',               # Shutdown/generation guards (used by scan_controller)
    'utils.qt_guards',               # Guarded signal connects (used by 10+ modules)
    'utils.qt_role',

    # --- Core architecture modules ---
    'core',                           # Core package
    'core.state_bus',                 # ProjectState store, actions, Qt bridge

    # --- Core database/migration modules ---
    'apply_migrations',
    'apply_performance_optimizations',

    # --- Core app modules (root-level) ---
    'logging_config',
    'db_config',
    'db_performance_optimizations',
    'db_writer',
    'session_state_manager',
    'settings_manager_qt',
    'app_services',
    'reference_db',
    'thumb_cache_db',
    'main_window_qt',
    'sidebar_qt',
    'accordion_sidebar',
    'search_widget_qt',
    'thumbnail_grid_qt',
    'preview_panel_qt',
    'video_player_qt',
    'splash_qt',
    'preferences_dialog',
    'video_backfill_dialog',
    'translation_manager',
]

# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
a = Analysis(
    ['main_qt.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_insightface.py'],
    excludes=[
        # Not used by app
        'tkinter',
        'pytest',
        'tests',

        # Prevent PyInstaller from collecting multiple Qt bindings
        'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.sip',
        'sip',
        'PyQt6', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets',
        'PySide2',

        # Debug / diagnostic utilities (not needed at runtime)
        # NOTE: insightface_check, clip_check, test_insightface_models are NOT
        # excluded — they are imported at runtime by main_qt.py, preferences_dialog,
        # embedding_worker, model_selection_helper, and others.
        'utils.diagnose_insightface',
        'utils.ffmpeg_check',
        'utils.cleanup_face_crops',
        'utils.fix_missing_project_images',
        'IPython',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

# --------------------------------------------------------------------------
# PYZ archive (bytecode)
# --------------------------------------------------------------------------
pyz = PYZ(
    a.pure,
    a.zipped_data,
)

# --------------------------------------------------------------------------
# Executable  (ONEDIR mode — best for ML apps with large native deps)
# --------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemoryMate-PhotoFlow-v-11_01.01.04-16',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # Set to False for release builds (hides console window)
    disable_windowing_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)

# --------------------------------------------------------------------------
# COLLECT — gathers exe + binaries + data into one folder
# --------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MemoryMate-PhotoFlow-v-11_01.01.04-16',
)
