"""
Semantic Search Widget - Natural Language Photo Search

Version: 1.0.1
Date: 2026-01-29

Widget for searching photos by natural language descriptions using
CLIP/SigLIP visual embeddings.

Features:
- Text input for natural language queries
- Real-time search as user types
- Display results in main grid
- Integration with EmbeddingService
- Project-aware context support (v1.0.1)

Usage:
    widget = SemanticSearchWidget(parent)
    widget.set_project_id(current_project_id)  # Set project for canonical model
    widget.searchTriggered.connect(on_semantic_search)
    toolbar.addWidget(widget)
"""

from PySide6.QtWidgets import (
    QWidget, QLineEdit, QPushButton, QHBoxLayout, QLabel,
    QMessageBox, QProgressDialog, QFileDialog, QSlider, QVBoxLayout
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap

from typing import Optional, List, Tuple, Dict, Any
import numpy as np
import time
from pathlib import Path
import re
from hashlib import md5

# Caching support
try:
    from cachetools import TTLCache
    CACHING_AVAILABLE = True
except ImportError:
    CACHING_AVAILABLE = False
    logger = None  # Will be set below

# FIX 2026-02-08: Removed direct get_embedding_service import to prevent UI-thread blocking
# Now uses ModelWarmupWorker for async model loading
from services.search_history_service import get_search_history_service
from repository.photo_repository import PhotoRepository
from logging_config import get_logger
from translation_manager import tr

logger = get_logger(__name__)

# Log caching availability
if CACHING_AVAILABLE:
    logger.info("[SemanticSearch] Result caching ENABLED (cachetools available)")
else:
    logger.warning("[SemanticSearch] Result caching DISABLED (install cachetools: pip install cachetools)")


def expand_query(query: str) -> str:
    """
    Expand simple queries into more descriptive phrases for better CLIP matching.

    CLIP models are trained on image captions, so 'eyes' → 'close-up photo of
    person's eyes' produces much better results.

    Args:
        query: Original user query

    Returns:
        Expanded query if pattern matches, otherwise original query
    """
    query_lower = query.lower().strip()

    # Skip expansion if query is already descriptive (3+ words)
    if len(query_lower.split()) >= 3:
        return query

    # Try to match and expand single-word or 2-word queries
    for pattern, expansion in QUERY_EXPANSIONS.items():
        if re.search(pattern, query_lower, re.IGNORECASE):
            expanded = re.sub(pattern, expansion, query_lower, count=1, flags=re.IGNORECASE)
            logger.info(f"[SemanticSearch] Query expansion: '{query}' → '{expanded}'")
            return expanded

    # No expansion matched, return original
    return query


# Query expansion mapping for common terms
# CLIP works best with natural language descriptions, like image captions
QUERY_EXPANSIONS = {
    # Body parts - expand to contextualized descriptions
    r'\b(eye|eyes)\b': 'close-up of eyes',
    r'\b(mouth|lips)\b': 'close-up of mouth and lips',
    r'\b(nose)\b': 'close-up of nose',
    r'\b(face|faces)\b': 'portrait of face',
    r'\b(hand|hands)\b': 'hands in view',
    r'\b(finger|fingers)\b': 'fingers visible',
    r'\b(head|heads)\b': 'person head visible',
    r'\b(hair)\b': 'hair visible',
    r'\b(ear|ears)\b': 'ears visible',

    # Color + clothing combos (most specific) - handle both singular and plural
    r'\b(blue|red|green|yellow|black|white|pink|purple|orange|brown)\s+(shirts?|t-shirts?|tshirts?)\b': r'person wearing \1 shirt',
    r'\b(blue|red|green|yellow|black|white|pink|purple|orange|brown)\s+(pants|jeans|trousers)\b': r'person wearing \1 pants',
    r'\b(blue|red|green|yellow|black|white|pink|purple|orange|brown)\s+(dresses?|skirts?)\b': r'person wearing \1 dress',
    r'\b(blue|red|green|yellow|black|white|pink|purple|orange|brown)\s+(jackets?|coats?)\b': r'person wearing \1 jacket',

    # Colors alone - keep simple
    r'\b(blue)\b': 'blue colored',
    r'\b(red)\b': 'red colored',
    r'\b(green)\b': 'green colored',
    r'\b(yellow)\b': 'yellow colored',
    r'\b(black)\b': 'black colored',
    r'\b(white)\b': 'white colored',

    # Common objects
    r'\b(window|windows)\b': 'building with windows',
    r'\b(door|doors)\b': 'door entrance',
    r'\b(car|cars)\b': 'car vehicle',
    r'\b(tree|trees)\b': 'trees nature',
    r'\b(sky)\b': 'sky visible',
    r'\b(cloud|clouds)\b': 'clouds in sky',
    r'\b(building|buildings)\b': 'building architecture',

    # Activities - keep natural
    r'\b(smile|smiling)\b': 'person smiling',
    r'\b(laugh|laughing)\b': 'person laughing',
    r'\b(walk|walking)\b': 'person walking',
    r'\b(run|running)\b': 'person running',
    r'\b(sitting)\b': 'person sitting',
    r'\b(standing)\b': 'person standing',
}


class SemanticSearchWidget(QWidget):
    """
    Semantic search bar for natural language photo search.

    Emits signals when search is triggered with results.
    """

    # Signal: (photo_ids, query_text, scores)
    # scores is a list of (photo_id, similarity_score) tuples
    searchTriggered = Signal(list, str, list)

    # Signal: () - emitted when search is cleared
    searchCleared = Signal()

    # Signal: (error_message)
    errorOccurred = Signal(str)

    def __init__(self, parent=None, project_id: Optional[int] = None):
        """
        Initialize semantic search widget.

        Args:
            parent: Parent widget
            project_id: Optional project ID for canonical model context
        """
        super().__init__(parent)
        self.embedding_service = None  # Legacy service for multi-modal search
        self.semantic_service = None   # New service for async text search
        self.photo_repo = PhotoRepository()
        self.search_history_service = get_search_history_service()
        self._project_id = project_id
        self._last_query = ""
        self._query_image_path = None  # Path to uploaded query image
        self._query_image_embedding = None  # Cached image embedding
        self._search_start_time = None  # For timing searches
        # Load search parameters from centralized config
        try:
            from config.search_config import SearchConfig
            self._min_similarity = SearchConfig.get_semantic_min_similarity()
            self._semantic_top_k = SearchConfig.get_semantic_top_k()
        except Exception:
            self._min_similarity = 0.30
            self._semantic_top_k = 20
        self._slider_debounce_timer = None  # Timer for debouncing slider changes

        # FIX 2026-02-08: Async model loading state
        self._model_loading = False  # True while background worker is loading model
        self._model_ready = False  # True when model is loaded and ready for search
        self._model_id = None  # ID of loaded model
        self._model_variant = None  # Variant name of loaded model
        self._warmup_worker = None  # Reference to active ModelWarmupWorker
        self._pending_search = None  # Queued search request while model is loading
        self._pending_image_path = None  # Queued image upload path while model is loading

        # FIX 2026-02-08: Async search state (Google Photos-style debounced, cancellable search)
        self._active_search_worker = None  # Reference to active SemanticSearchWorker
        self._search_debounce_timer = None  # Timer for debouncing text input (250ms)

        # Result caching (Phase 1 improvement)
        if CACHING_AVAILABLE:
            self._result_cache = TTLCache(maxsize=100, ttl=300)  # 100 queries, 5 min TTL
            self._cache_hits = 0
            self._cache_misses = 0
            logger.info("[SemanticSearch] Result cache initialized (100 entries, 5min TTL)")
        else:
            self._result_cache = None
            self._cache_hits = 0
            self._cache_misses = 0

        self._setup_ui()

        # Proactively check for available CLIP models at startup
        self._check_available_models()

    def set_project_id(self, project_id: Optional[int]):
        """
        Set the current project context.

        Call this when the active project changes to ensure searches use
        the correct canonical model for the project.

        Args:
            project_id: Project ID, or None to clear project context
        """
        if self._project_id != project_id:
            old_project = self._project_id
            self._project_id = project_id

            # Clear cached results when project changes
            if self._result_cache is not None:
                self._result_cache.clear()
                logger.info(
                    f"[SemanticSearch] Project changed {old_project} -> {project_id}, "
                    f"cleared result cache"
                )

            # Clear embedding services and model state to force reload with new project model
            self.embedding_service = None
            self.semantic_service = None
            self._model_ready = False
            self._model_loading = False
            self._model_id = None
            self._model_variant = None
            self._pending_search = None

            # Cancel any in-progress warmup worker
            if self._warmup_worker is not None:
                self._warmup_worker.cancel()
                self._warmup_worker = None

            # Cancel any in-progress search worker
            if self._active_search_worker is not None:
                self._active_search_worker.cancel()
                self._active_search_worker = None

            logger.info(f"[SemanticSearch] Project context set to: {project_id}")

    def _resolve_clip_variant(self) -> str:
        """
        Resolve which CLIP model variant to use.

        Priority:
        1. Project's canonical model (projects.semantic_model)
        2. Hard default from clip_model_registry — never 'best available'

        Uses centralized clip_model_registry for all name resolution.
        """
        from utils.clip_model_registry import normalize_model_id, DEFAULT_MODEL

        if self._project_id:
            try:
                from repository.project_repository import ProjectRepository
                from repository.base_repository import DatabaseConnection
                db = DatabaseConnection()
                repo = ProjectRepository(db)
                canonical = repo.get_semantic_model(self._project_id)
                if canonical:
                    logger.info("[SemanticSearch] Using project canonical model: %s", canonical)
                    return canonical  # Already normalized by repo
            except Exception as e:
                logger.warning("[SemanticSearch] Could not resolve project model: %s", e)

        default = normalize_model_id(DEFAULT_MODEL)
        logger.info("[SemanticSearch] No project model set, using default: %s", default)
        return default

    @property
    def project_id(self) -> Optional[int]:
        """Get the current project ID."""
        return self._project_id

    def _check_available_models(self):
        """
        Check which CLIP models are available in models/ directory.
        Logs findings and warns if better models are available.
        """
        try:
            from utils.clip_check import get_available_variants, MODEL_CONFIGS

            # Get all available models
            available = get_available_variants()

            # Use project canonical model (single source of truth)
            recommended = self._resolve_clip_variant()
            recommended_config = MODEL_CONFIGS.get(recommended, {})

            # Log findings
            available_list = [name for name, is_available in available.items() if is_available]

            if available_list:
                logger.info(f"[SemanticSearch] 🔍 Available CLIP models found: {len(available_list)}")
                for variant in available_list:
                    config = MODEL_CONFIGS.get(variant, {})
                    is_recommended = "← WILL BE USED" if variant == recommended else ""
                    logger.info(
                        f"  ✓ {variant}: {config.get('description', 'unknown')} "
                        f"({config.get('dimension', '???')}-D, {config.get('size_mb', '???')}MB) {is_recommended}"
                    )

                logger.info(
                    f"[SemanticSearch] 🎯 Will use: {recommended} - {recommended_config.get('description', 'unknown')}"
                )

                # Warn if using base-patch32 but large is available
                if recommended == 'openai/clip-vit-base-patch32' and available.get('openai/clip-vit-large-patch14', False):
                    logger.warning(
                        "[SemanticSearch] ⚠️  Better model available but not being used!\n"
                        "  clip-vit-large-patch14 is installed but has embeddings from base-patch32.\n"
                        "  To use the large model: Tools → Extract Embeddings (will auto-use large model)"
                    )
            else:
                logger.warning(
                    "[SemanticSearch] ⚠️  No CLIP models found in models/ directory!\n"
                    "  Semantic search will not work until models are downloaded.\n"
                    "  Run: python download_clip_large.py"
                )
        except Exception as e:
            logger.warning(f"[SemanticSearch] Could not check available models: {e}")

    def _start_model_loading(self, pending_action: Optional[str] = None):
        """
        Start async model loading in background thread.

        FIX 2026-02-08: Replaced UI-thread blocking model loading with background worker.

        Args:
            pending_action: Action to perform when model is ready ('search' or 'upload_image')
        """
        if self._model_loading:
            logger.info("[SemanticSearch] Model already loading, waiting...")
            return

        if self._model_ready and self.embedding_service is not None:
            logger.info("[SemanticSearch] Model already loaded, executing action immediately")
            self._execute_pending_action(pending_action)
            return

        logger.info("[SemanticSearch] Starting async model loading...")
        self._model_loading = True
        self._pending_search = pending_action

        # Update UI to show loading state
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Loading...")
        self.image_btn.setEnabled(False)
        self.status_label.setText("Loading AI model in background...")
        self.status_label.setVisible(True)

        # Launch background worker
        from workers.model_warmup_worker import ModelWarmupWorker
        from PySide6.QtCore import QThreadPool

        variant = self._resolve_clip_variant()

        self._warmup_worker = ModelWarmupWorker(
            model_variant=variant,
            device='auto',
            project_id=self._project_id
        )

        # Connect signals
        self._warmup_worker.signals.finished.connect(self._on_model_loaded)
        self._warmup_worker.signals.progress.connect(self._on_model_progress)
        self._warmup_worker.signals.error.connect(self._on_model_error)

        QThreadPool.globalInstance().start(self._warmup_worker)

    def _on_model_loaded(self, model_id: int, model_variant: str):
        """
        Handle model loaded signal from background worker.

        Args:
            model_id: ID of loaded model
            model_variant: Variant name of loaded model
        """
        logger.info(f"[SemanticSearch] Model loaded: {model_variant} (id={model_id})")

        self._model_loading = False
        self._model_ready = True
        self._model_id = model_id
        self._model_variant = model_variant
        self._warmup_worker = None

        # FIX 2026-02-08: Use semantic_embedding_service for async text search
        # Keep embedding_service for legacy multi-modal (image + text) search path
        from services.semantic_embedding_service import get_semantic_embedding_service
        from services.embedding_service import get_embedding_service
        self.semantic_service = get_semantic_embedding_service(model_name=model_variant)
        self.embedding_service = get_embedding_service()  # For multi-modal sync path

        # Update UI
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Search")
        self.image_btn.setEnabled(True)
        self.status_label.setText("AI model ready")
        self.status_label.setVisible(True)

        # Hide status after a moment
        QTimer.singleShot(2000, lambda: self.status_label.setVisible(False) if not self._last_query else None)

        # Execute pending action
        pending = self._pending_search
        self._pending_search = None
        self._execute_pending_action(pending)

    def _on_model_progress(self, message: str):
        """Handle progress updates from model warmup worker."""
        logger.debug(f"[SemanticSearch] Model loading: {message}")
        self.status_label.setText(message)

    def _on_model_error(self, error_message: str):
        """Handle error from model warmup worker."""
        logger.error(f"[SemanticSearch] Model loading failed: {error_message}")

        self._model_loading = False
        self._warmup_worker = None

        # Update UI
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Search")
        self.image_btn.setEnabled(True)
        self.status_label.setText("Model loading failed")
        self.status_label.setStyleSheet("color: #cc0000; font-style: italic; font-size: 9pt;")
        self.status_label.setVisible(True)

        # Show error dialog
        QMessageBox.critical(
            self,
            "Model Loading Failed",
            f"Failed to load AI model:\n{error_message}\n\n"
            "Check console for details."
        )

        # Reset status style
        QTimer.singleShot(3000, lambda: self.status_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;"))

        self.errorOccurred.emit(error_message)

    def _execute_pending_action(self, action: Optional[str]):
        """Execute the pending action after model is loaded."""
        if action == 'search':
            self._on_search()
        elif action == 'upload_image':
            # Process the pending image path
            if hasattr(self, '_pending_image_path') and self._pending_image_path:
                file_path = self._pending_image_path
                self._pending_image_path = None
                self._process_uploaded_image(file_path)

    def _ensure_model_ready(self, pending_action: str) -> bool:
        """
        Ensure model is ready for use. Starts async loading if needed.

        Args:
            pending_action: Action to perform when ready ('search' or 'upload_image')

        Returns:
            True if model is ready NOW, False if loading was started
        """
        if self._model_ready and self.embedding_service is not None:
            return True

        # Start async loading
        self._start_model_loading(pending_action)
        return False

    def _setup_ui(self):
        """Setup the semantic search UI with 2-row layout for better organization."""
        # Main vertical layout to stack two rows
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # ROW 1: Search input and primary actions
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(4)

        # Semantic search icon/label
        search_label = QLabel("🔍✨")
        search_label.setToolTip("Semantic Search - Describe what you're looking for")
        row1_layout.addWidget(search_label)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Describe the photo (e.g., 'sunset beach', 'dog playing in park')..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(300)
        self.search_input.returnPressed.connect(self._on_search)
        self.search_input.textChanged.connect(self._on_text_changed)
        row1_layout.addWidget(self.search_input, 1)

        # Search button
        self.search_btn = QPushButton("Search")
        self.search_btn.setToolTip("Search photos by description using AI")
        self.search_btn.clicked.connect(self._on_search)
        row1_layout.addWidget(self.search_btn)

        # Image query button (multi-modal search)
        self.image_btn = QPushButton("📷 +Image")
        self.image_btn.setToolTip("Add an image to your search query (multi-modal search)")
        self.image_btn.clicked.connect(self._on_upload_image)
        row1_layout.addWidget(self.image_btn)

        # Clear button
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Show all photos")
        self.clear_btn.clicked.connect(self._on_clear)
        self.clear_btn.setVisible(False)  # Hidden until search is active
        row1_layout.addWidget(self.clear_btn)

        main_layout.addLayout(row1_layout)

        # ROW 2: Threshold controls and status
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(8)

        # Threshold label
        threshold_label_static = QLabel("Similarity:")
        threshold_label_static.setStyleSheet("font-size: 9pt; color: #666;")
        row2_layout.addWidget(threshold_label_static)

        # Similarity threshold slider
        threshold_slider_layout = QVBoxLayout()
        threshold_slider_layout.setSpacing(0)

        self.threshold_label = QLabel(f"{int(self._min_similarity * 100)}%")
        self.threshold_label.setToolTip("Minimum similarity threshold - higher = stricter matching")
        self.threshold_label.setStyleSheet("font-size: 9pt; color: #666; font-weight: bold;")
        self.threshold_label.setAlignment(Qt.AlignCenter)
        threshold_slider_layout.addWidget(self.threshold_label)

        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(10)  # 10% = 0.10
        self.threshold_slider.setMaximum(50)  # 50% = 0.50
        self.threshold_slider.setValue(int(self._min_similarity * 100))  # Default 30%
        self.threshold_slider.setTickPosition(QSlider.TicksBelow)
        self.threshold_slider.setTickInterval(10)
        self.threshold_slider.setMaximumWidth(100)
        self.threshold_slider.setToolTip(
            "Adjust similarity threshold:\n"
            "• 10-25%: Very permissive (may include unrelated photos)\n"
            "• 30-35%: Balanced (recommended for base CLIP models)\n"
            "• 40-50%: Strict (only close matches)"
        )
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        threshold_slider_layout.addWidget(self.threshold_slider)

        row2_layout.addLayout(threshold_slider_layout)

        # Preset buttons for quick threshold selection (optimized for base CLIP models)
        self.lenient_btn = QPushButton("Lenient")
        self.lenient_btn.setToolTip("Show more results (25% threshold)")
        self.lenient_btn.setMaximumWidth(65)
        self.lenient_btn.clicked.connect(lambda: self._set_preset_threshold(25))
        row2_layout.addWidget(self.lenient_btn)

        self.balanced_btn = QPushButton("Balanced")
        self.balanced_btn.setToolTip("Recommended setting (30% threshold)")
        self.balanced_btn.setMaximumWidth(70)
        self.balanced_btn.setStyleSheet("font-weight: bold;")  # Default preset
        self.balanced_btn.clicked.connect(lambda: self._set_preset_threshold(30))
        row2_layout.addWidget(self.balanced_btn)

        self.strict_btn = QPushButton("Strict")
        self.strict_btn.setToolTip("Only close matches (40% threshold)")
        self.strict_btn.setMaximumWidth(60)
        self.strict_btn.clicked.connect(lambda: self._set_preset_threshold(40))
        row2_layout.addWidget(self.strict_btn)

        # Spacer
        row2_layout.addSpacing(10)

        # History button
        self.history_btn = QPushButton("📜")
        self.history_btn.setToolTip("View recent searches")
        self.history_btn.setMaximumWidth(35)
        self.history_btn.clicked.connect(self._on_show_history)
        row2_layout.addWidget(self.history_btn)

        # Status label (shows result count)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;")
        self.status_label.setVisible(False)
        row2_layout.addWidget(self.status_label, 1)

        main_layout.addLayout(row2_layout)

        # Set size policy - don't force height as it can cause toolbar hiding issues
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Debounce timer for live search (optional)
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._on_search)

        # FIX 2026-02-08: Search debounce timer (Google Photos-style 300ms delay)
        self._search_debounce_timer = QTimer()
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.setInterval(300)  # 300ms debounce like Google Photos
        self._search_debounce_timer.timeout.connect(self._start_async_search)

        # Debounce timer for slider changes
        self._slider_debounce_timer = QTimer()
        self._slider_debounce_timer.setSingleShot(True)
        self._slider_debounce_timer.setInterval(500)  # 500ms delay

        # Initialize preset button highlighting (Balanced is default at 30%)
        self._update_preset_buttons(30)

    def sizeHint(self):
        """Override sizeHint to suggest proper size for 2-row layout without forcing it."""
        from PySide6.QtCore import QSize
        # Suggest a comfortable size for 2 rows, but allow Qt to adjust if needed
        return QSize(600, 90)  # width, height

    def _on_text_changed(self, text: str):
        """
        Handle text change with debounced async search.

        FIX 2026-02-08: Implement Google Photos-style live search with:
        - 300ms debounce to avoid excessive API calls
        - Cancel any in-progress search when user types
        - Clear results if query too short
        """
        query = text.strip()

        # Cancel any pending debounce timer
        if self._search_debounce_timer and self._search_debounce_timer.isActive():
            self._search_debounce_timer.stop()

        # Query too short - clear and cancel
        if len(query) < 2:
            # Cancel any active search worker
            if self._active_search_worker is not None:
                logger.debug("[SemanticSearch] Cancelling search - query too short")
                self._active_search_worker.cancel()
                self._active_search_worker = None

            # Clear status but don't emit searchCleared (user is still typing)
            if query == "":
                self.status_label.setVisible(False)
            return

        # Start debounce timer (will trigger _start_async_search after 300ms)
        self._search_debounce_timer.start()

    def _start_async_search(self):
        """
        Start async search using SemanticSearchWorker.

        FIX 2026-02-08: Implements Google Photos-style async, cancellable search:
        - Runs search in background QThread
        - Cancels previous search if still running
        - Reports progress via signals
        - Integrates with JobManager for visibility
        """
        query = self.search_input.text().strip()

        # Validate query
        if len(query) < 2:
            return

        # Skip if same query (no image query)
        if query == self._last_query and self._query_image_embedding is None:
            logger.debug(f"[SemanticSearch] Same query, skipping async search: {query}")
            return

        # Cancel any active search worker
        if self._active_search_worker is not None:
            logger.info(f"[SemanticSearch] Cancelling previous search for new query")
            self._active_search_worker.cancel()
            self._active_search_worker.wait(500)  # Wait up to 500ms for graceful stop
            self._active_search_worker = None

        # Check project context
        if self._project_id is None:
            logger.warning("[SemanticSearch] No project context set, cannot search")
            return

        logger.info(f"[SemanticSearch] Starting async search: '{query}' (project={self._project_id})")

        # Show searching state
        self.status_label.setText("Searching...")
        self.status_label.setVisible(True)

        # Create and start worker
        from workers.semantic_search_worker import SemanticSearchWorker

        self._active_search_worker = SemanticSearchWorker(
            project_id=self._project_id,
            query=query,
            limit=self._semantic_top_k,
            threshold=self._min_similarity,
            model_name=self._resolve_clip_variant()
        )

        # Connect signals
        self._active_search_worker.signals.status.connect(self._on_search_status)
        self._active_search_worker.signals.results_ready.connect(self._on_search_results)
        self._active_search_worker.signals.error.connect(self._on_search_error)
        self._active_search_worker.signals.finished.connect(self._on_search_finished)
        self._active_search_worker.signals.progress.connect(self._on_search_progress)

        # Start the worker
        self._active_search_worker.start()

    def _on_search_status(self, message: str):
        """Handle status updates from search worker."""
        self.status_label.setText(message)

    def _on_search_progress(self, percent: int, message: str):
        """Handle progress updates from search worker."""
        self.status_label.setText(f"{message} ({percent}%)")

    def _on_search_results(self, response):
        """
        Handle search results from async worker.

        Args:
            response: SearchResponse from SemanticSearchWorker
        """
        from workers.semantic_search_worker import SearchResponse

        if not isinstance(response, SearchResponse):
            logger.warning(f"[SemanticSearch] Unexpected response type: {type(response)}")
            return

        query = response.query
        results = response.results
        stats = response.stats

        # Check for no-embeddings state
        if stats.get('no_embeddings'):
            self.status_label.setText(stats.get('message', 'No embeddings found'))
            self.status_label.setStyleSheet("color: #FF9800; font-style: italic; font-size: 9pt;")
            self.status_label.setVisible(True)
            # Reset style after delay
            QTimer.singleShot(5000, lambda: self.status_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;"))
            return

        # Update last query
        self._last_query = query

        if response.is_empty:
            self.status_label.setText(f"No results ≥{self._min_similarity:.0%}")
            self.status_label.setVisible(True)
            return

        # Extract photo IDs and scores
        photo_ids = [r.photo_id for r in results]
        scores = [r.score for r in results]
        score_tuples = [(r.photo_id, r.score) for r in results]

        # Calculate score statistics
        top_score = scores[0] if scores else 0
        avg_score = sum(scores) / len(scores) if scores else 0

        logger.info(
            f"[SemanticSearch] Async search complete: '{query}' → {len(results)} results "
            f"(top={top_score:.3f}, avg={avg_score:.3f}, time={stats.get('query_time_ms', 0)}ms)"
        )

        # Update UI state
        self.clear_btn.setVisible(True)

        # Create detailed status message
        status_parts = [
            f"Found {len(results)} matches ≥{self._min_similarity:.0%}",
            f"Top: {top_score:.1%}",
            f"Avg: {avg_score:.1%}"
        ]

        # Add quality indicator
        if top_score >= 0.40:
            status_parts.append("🟢 Excellent")
        elif top_score >= 0.30:
            status_parts.append("🟡 Good")
        elif top_score >= 0.20:
            status_parts.append("🟠 Fair")
        else:
            status_parts.append("🔴 Weak")

        self.status_label.setText(" | ".join(status_parts))
        self.status_label.setVisible(True)

        # Record in search history
        try:
            self.search_history_service.record_search(
                query_type='semantic_text',
                query_text=query,
                result_count=len(results),
                top_photo_ids=photo_ids[:10],
                execution_time_ms=stats.get('query_time_ms', 0),
                model_id=0  # Async worker doesn't track model ID
            )
        except Exception as e:
            logger.warning(f"[SemanticSearch] Failed to record search history: {e}")

        # Emit signal with results
        self.searchTriggered.emit(photo_ids, query, score_tuples)

    def _on_search_error(self, error_message: str):
        """Handle error from search worker."""
        logger.error(f"[SemanticSearch] Async search error: {error_message}")
        self.status_label.setText(f"Search error: {error_message[:50]}...")
        self.status_label.setStyleSheet("color: #cc0000; font-style: italic; font-size: 9pt;")
        self.status_label.setVisible(True)

        # Reset style after delay
        QTimer.singleShot(3000, lambda: self.status_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;"))

        self.errorOccurred.emit(error_message)

    def _on_search_finished(self):
        """Handle search worker completion."""
        logger.debug("[SemanticSearch] Async search worker finished")
        # FIX 2026-03-09: Clear the worker reference so the next search
        # creates a fresh QThread instead of reusing a finished one.
        # QThread objects should not be restarted after finishing.
        self._active_search_worker = None

    def _on_threshold_changed(self, value: int):
        """Handle similarity threshold slider change with debouncing."""
        self._min_similarity = value / 100.0
        self.threshold_label.setText(f"Min: {value}%")

        # Update preset button highlighting
        self._update_preset_buttons(value)

        # Debounce: only log after user stops dragging for 500ms
        if self._slider_debounce_timer.isActive():
            self._slider_debounce_timer.stop()

        self._slider_debounce_timer.timeout.disconnect()
        self._slider_debounce_timer.timeout.connect(
            lambda: logger.info(f"[SemanticSearch] Threshold set to {self._min_similarity:.0%}")
        )
        self._slider_debounce_timer.start()

    def _set_preset_threshold(self, value: int):
        """Set threshold to preset value (Lenient=25%, Balanced=30%, Strict=40%)."""
        self.threshold_slider.setValue(value)
        logger.info(f"[SemanticSearch] Preset threshold applied: {value}%")

    def _update_preset_buttons(self, value: int):
        """Update visual highlighting of preset buttons based on current threshold."""
        # Clear all button highlighting
        self.lenient_btn.setStyleSheet("")
        self.balanced_btn.setStyleSheet("")
        self.strict_btn.setStyleSheet("")

        # Highlight the active preset (with tolerance of ±2%)
        if abs(value - 25) <= 2:
            self.lenient_btn.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white;")
        elif abs(value - 30) <= 2:
            self.balanced_btn.setStyleSheet("font-weight: bold; background-color: #2196F3; color: white;")
        elif abs(value - 40) <= 2:
            self.strict_btn.setStyleSheet("font-weight: bold; background-color: #FF9800; color: white;")

    def _suggest_threshold(self, scores: List[float]) -> Optional[str]:
        """
        Analyze score distribution and suggest optimal threshold.

        Args:
            scores: List of similarity scores from search results

        Returns:
            Suggestion message or None if current threshold is optimal
        """
        if not scores:
            return None

        top_score = scores[0]
        avg_score = sum(scores) / len(scores)
        current_threshold = self._min_similarity

        # Case 1: Very low scores (< 0.25) - might need query expansion or different search terms
        if top_score < 0.25:
            return (
                f"💡 Suggestion: Low match scores detected (top: {top_score:.1%}). "
                "Try more descriptive search terms or check if embeddings are extracted."
            )

        # Case 2: Top score is good but current threshold too strict
        if top_score > 0.35 and current_threshold > 0.30 and len(scores) < 5:
            return (
                f"💡 Suggestion: Good matches found (top: {top_score:.1%}), but only {len(scores)} results. "
                f"Try lowering threshold to ~{int(avg_score * 100 - 5)}% to see more relevant photos."
            )

        # Case 3: Many results with low average - threshold too lenient
        if len(scores) > 50 and avg_score < current_threshold + 0.05:
            return (
                f"💡 Suggestion: Many results ({len(scores)}) with low average similarity ({avg_score:.1%}). "
                f"Try raising threshold to ~{int(avg_score * 100 + 10)}% for better quality."
            )

        # Case 4: Perfect range - no suggestion needed
        if 10 <= len(scores) <= 30 and avg_score > current_threshold + 0.05:
            return None  # Good results, no suggestion

        return None

    def _show_suggestion_toast(self, message: str):
        """Show threshold suggestion as a non-blocking message."""
        # Use status label for now (could be upgraded to toast notification)
        original_text = self.status_label.text()
        self.status_label.setText(f"{original_text} | {message}")
        self.status_label.setStyleSheet("color: #FF9800; font-style: italic; font-weight: bold;")

        # Reset style after 5 seconds
        QTimer.singleShot(5000, lambda: self.status_label.setStyleSheet("color: #666; font-style: italic;"))

    def _on_upload_image(self):
        """Handle image upload for multi-modal search."""
        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Query Image",
            "",
            "Images (*.jpg *.jpeg *.png *.heic *.bmp);;All Files (*)"
        )

        if not file_path:
            return

        # Store the file path for later processing
        self._pending_image_path = file_path

        # FIX 2026-02-08: Use async model loading instead of blocking UI thread
        if not self._ensure_model_ready('upload_image'):
            # Model is loading, _pending_image_path will be processed when ready
            logger.info(f"[SemanticSearch] Model loading, will process image when ready: {file_path}")
            return

        # Model is ready, process the image immediately
        self._process_uploaded_image(file_path)

    def _process_uploaded_image(self, file_path: str):
        """
        Process an uploaded image after model is ready.

        FIX 2026-02-08: Extracted from _on_upload_image to support async model loading.

        Args:
            file_path: Path to the uploaded image
        """
        try:
            logger.info(f"[SemanticSearch] Loading query image: {file_path}")

            # Model should already be loaded at this point
            if self.semantic_service is None or not self._model_ready:
                logger.error("[SemanticSearch] _process_uploaded_image called but model not ready")
                return

            # FIX 2026-02-08: Use semantic_embedding_service API
            if not self.semantic_service._available:
                QMessageBox.warning(
                    self,
                    "Feature Unavailable",
                    "Multi-modal search requires PyTorch and Transformers.\n\n"
                    "Install dependencies:\n"
                    "pip install torch transformers pillow"
                )
                return

            # Extract embedding from image using semantic_embedding_service API
            self._query_image_embedding = self.semantic_service.encode_image(
                file_path
            )
            self._query_image_path = file_path

            # Update UI to show image is loaded
            image_name = Path(file_path).name
            self.image_btn.setText(f"📷 {image_name[:15]}...")
            self.image_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                }
            """)

            logger.info(f"[SemanticSearch] Query image loaded: {image_name}")

            # Show info
            QMessageBox.information(
                self,
                "Image Loaded",
                f"Query image loaded: {image_name}\n\n"
                "You can now:\n"
                "• Search with just the image (leave text empty)\n"
                "• Combine image + text for multi-modal search\n"
                "• Click 'Clear' to remove the image"
            )

        except Exception as e:
            logger.error(f"[SemanticSearch] Failed to load query image: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Image Load Failed",
                f"Failed to load query image:\n{e}"
            )

    def _on_search(self):
        """Trigger semantic search (supports multi-modal: text + image)."""
        query = self.search_input.text().strip()
        has_text = bool(query)
        has_image = self._query_image_embedding is not None

        # Must have at least one query type
        if not has_text and not has_image:
            self._on_clear()
            return

        # Skip if same text query and no image
        if has_text and query == self._last_query and not has_image:
            logger.info(f"[SemanticSearch] Same query, skipping: {query}")
            return

        query_desc = []
        if has_text:
            query_desc.append(f"text: '{query}'")
        if has_image:
            query_desc.append(f"image: {Path(self._query_image_path).name}")
        logger.info(f"[SemanticSearch] Searching for: {', '.join(query_desc)}")

        # FIX 2026-02-08: Use async search for text-only queries
        # Multi-modal (image) queries still use sync path since SemanticSearchWorker
        # doesn't support image embeddings
        if has_text and not has_image:
            self._start_async_search()
            return

        # FIX 2026-02-08: Use async model loading instead of blocking UI thread
        if not self._ensure_model_ready('search'):
            # Model is loading, search will be triggered when ready
            logger.info("[SemanticSearch] Model loading, will search when ready")
            # Store query for logging later but don't update _last_query yet
            return

        self._last_query = query

        # Start timing
        self._search_start_time = time.time()

        try:
            # Model should already be loaded at this point
            if self.embedding_service is None or not self._model_ready:
                logger.error("[SemanticSearch] _on_search called but model not ready")
                return

            if not self.embedding_service.available:
                QMessageBox.warning(
                    self,
                    "Semantic Search Unavailable",
                    "Semantic search requires PyTorch and Transformers.\n\n"
                    "Install dependencies:\n"
                    "pip install torch transformers pillow"
                )
                self.errorOccurred.emit("Dependencies not available")
                return

            # Log which model is in use
            model_variant = getattr(self.embedding_service, '_clip_variant', self._model_variant or 'unknown')
            logger.info(f"[SemanticSearch] Using CLIP model: {model_variant}")

            # Apply query expansion for better CLIP matching
            expanded_query = query
            if has_text:
                expanded_query = expand_query(query)

            # Extract query embedding (multi-modal support)
            query_embedding = None

            if has_text and has_image:
                # Multi-modal: combine text + image embeddings
                logger.info("[SemanticSearch] Extracting multi-modal query embedding (text + image)...")
                text_embedding = self.embedding_service.extract_text_embedding(expanded_query)
                image_embedding = self._query_image_embedding

                # Weighted average (50/50 by default)
                # Can be adjusted: 0.7 text + 0.3 image, etc.
                text_weight = 0.5
                image_weight = 0.5

                query_embedding = (text_weight * text_embedding + image_weight * image_embedding)

                # Normalize the combined embedding
                query_embedding = query_embedding / np.linalg.norm(query_embedding)

                logger.info(f"[SemanticSearch] Combined embeddings: {text_weight}*text + {image_weight}*image")

            elif has_text:
                # Text-only search
                logger.info("[SemanticSearch] Extracting text query embedding...")
                query_embedding = self.embedding_service.extract_text_embedding(expanded_query)

            elif has_image:
                # Image-only search
                logger.info("[SemanticSearch] Using image query embedding...")
                query_embedding = self._query_image_embedding

            else:
                raise ValueError("No query provided (neither text nor image)")

            # Search for similar images (with caching and progress reporting)
            model_id = self.embedding_service._clip_model_id

            # Get embedding count for progress dialog decision
            total_embeddings = self.embedding_service.get_embedding_count(model_id)

            # Generate cache key
            cache_key = None
            if self._result_cache is not None:
                cache_key = self._get_cache_key(
                    expanded_query,
                    self._min_similarity,
                    model_id,
                    has_image=has_image
                )

                # Check cache
                if cache_key in self._result_cache:
                    results = self._result_cache[cache_key]
                    self._cache_hits += 1
                    logger.info(
                        f"[SemanticSearch] ✓ Cache HIT for '{expanded_query}' "
                        f"(hits={self._cache_hits}, misses={self._cache_misses}, "
                        f"hit_rate={self._cache_hits/(self._cache_hits+self._cache_misses):.1%})"
                    )
                else:
                    # Cache miss - perform search with progress reporting
                    logger.info(f"[SemanticSearch] Cache MISS - searching database (min_similarity={self._min_similarity:.2f})...")

                    # Show progress dialog for large collections
                    if total_embeddings > 5000:
                        progress_dialog = QProgressDialog(
                            "Searching...",
                            "Cancel",
                            0, total_embeddings,
                            self
                        )
                        progress_dialog.setWindowTitle("Semantic Search")
                        progress_dialog.setWindowModality(Qt.WindowModal)
                        progress_dialog.show()

                        # FIX 2026-02-08: Removed processEvents() call - modal dialog handles its own events
                        # Using processEvents() causes re-entrancy risks
                        def on_progress(current, total, message):
                            progress_dialog.setValue(current)
                            progress_dialog.setLabelText(message)

                        results = self.embedding_service.search_similar(
                            query_embedding,
                            top_k=self._semantic_top_k,
                            model_id=model_id,
                            min_similarity=self._min_similarity,
                            progress_callback=on_progress,
                            query_text=expanded_query  # For metrics logging
                        )

                        progress_dialog.close()
                    else:
                        # Small collection - no progress dialog needed
                        results = self.embedding_service.search_similar(
                            query_embedding,
                            top_k=self._semantic_top_k,
                            model_id=model_id,
                            min_similarity=self._min_similarity,
                            query_text=expanded_query  # For metrics logging
                        )

                    # Store in cache
                    self._result_cache[cache_key] = results
                    self._cache_misses += 1
                    logger.info(
                        f"[SemanticSearch] Result cached "
                        f"(hits={self._cache_hits}, misses={self._cache_misses}, "
                        f"hit_rate={self._cache_hits/(self._cache_hits+self._cache_misses):.1%})"
                    )
            else:
                # Caching disabled - perform search directly with progress reporting
                logger.info(f"[SemanticSearch] Searching database (min_similarity={self._min_similarity:.2f})...")

                # Show progress dialog for large collections
                if total_embeddings > 5000:
                    progress_dialog = QProgressDialog(
                        "Searching...",
                        "Cancel",
                        0, total_embeddings,
                        self
                    )
                    progress_dialog.setWindowTitle("Semantic Search")
                    progress_dialog.setWindowModality(Qt.WindowModal)
                    progress_dialog.show()

                    # FIX 2026-02-08: Removed processEvents() call - modal dialog handles its own events
                    # Using processEvents() causes re-entrancy risks
                    def on_progress(current, total, message):
                        progress_dialog.setValue(current)
                        progress_dialog.setLabelText(message)

                    results = self.embedding_service.search_similar(
                        query_embedding,
                        top_k=100,  # Get top 100 results
                        model_id=model_id,
                        min_similarity=self._min_similarity,
                        progress_callback=on_progress,
                        query_text=expanded_query  # For metrics logging
                    )

                    progress_dialog.close()
                else:
                    # Small collection - no progress dialog needed
                    results = self.embedding_service.search_similar(
                        query_embedding,
                        top_k=100,  # Get top 100 results
                        model_id=model_id,
                        min_similarity=self._min_similarity,
                        query_text=expanded_query  # For metrics logging
                    )

            if not results:
                # Build context-aware no-results message
                suggestions = []

                # Check if query was expanded
                if expanded_query != query:
                    suggestions.append(
                        f"✓ Query expanded: '{query}' → '{expanded_query}'\n"
                        "  (Still no matches - try different terms)"
                    )

                # Check threshold
                if self._min_similarity >= 0.30:
                    suggestions.append(
                        f"• Lower threshold: Currently {self._min_similarity:.0%} (Strict)\n"
                        "  Try clicking 'Balanced' (25%) or 'Lenient' (15%)"
                    )
                else:
                    suggestions.append(
                        f"• Try different search terms\n"
                        "  Current threshold ({self._min_similarity:.0%}) is already lenient"
                    )

                # Check embedding count
                try:
                    embedding_count = self.embedding_service.get_embedding_count()
                    if embedding_count == 0:
                        suggestions.append(
                            "⚠ No embeddings found in database!\n"
                            "  Run: Scan → Extract Embeddings first"
                        )
                    else:
                        suggestions.append(
                            f"✓ {embedding_count} embeddings in database\n"
                            "  Try more descriptive queries (e.g., 'sunset beach' instead of 'sunset')"
                        )
                except Exception:
                    suggestions.append(
                        "• Make sure embeddings have been extracted\n"
                        "  (Scan → Extract Embeddings)"
                    )

                QMessageBox.information(
                    self,
                    "No Search Results",
                    f"No photos found matching '{query}' (similarity ≥ {self._min_similarity:.0%}).\n\n"
                    + "\n".join(suggestions)
                )
                self.status_label.setText(f"No results ≥{self._min_similarity:.0%}")
                self.status_label.setVisible(True)
                return

            # Extract photo IDs and analyze score distribution
            photo_ids = [photo_id for photo_id, score in results]
            scores = [score for _, score in results]

            # Calculate score statistics for smart suggestions
            top_score = scores[0]
            avg_score = sum(scores) / len(scores)
            min_score = scores[-1]

            logger.info(
                f"[SemanticSearch] Found {len(results)} results, "
                f"top score: {top_score:.3f}, avg: {avg_score:.3f}, min: {min_score:.3f}"
            )

            # Smart threshold suggestion based on score distribution
            threshold_suggestion = self._suggest_threshold(scores)
            if threshold_suggestion:
                logger.info(f"[SemanticSearch] {threshold_suggestion}")

            # Update UI state with score distribution
            self.clear_btn.setVisible(True)

            # Create detailed status message with score distribution
            status_parts = [
                f"Found {len(results)} matches ≥{self._min_similarity:.0%}",
                f"Top: {top_score:.1%}",
                f"Avg: {avg_score:.1%}"
            ]

            # Add quality indicator
            if top_score >= 0.40:
                status_parts.append("🟢 Excellent")
            elif top_score >= 0.30:
                status_parts.append("🟡 Good")
            elif top_score >= 0.20:
                status_parts.append("🟠 Fair")
            else:
                status_parts.append("🔴 Weak")

            self.status_label.setText(" | ".join(status_parts))
            self.status_label.setVisible(True)

            # Show suggestion if available
            if threshold_suggestion:
                QTimer.singleShot(1000, lambda: self._show_suggestion_toast(threshold_suggestion))

            # Calculate execution time
            execution_time_ms = (time.time() - self._search_start_time) * 1000

            # Record search in history
            query_type = 'semantic_text' if (has_text and not has_image) else \
                        'semantic_image' if (has_image and not has_text) else \
                        'semantic_multi'

            self.search_history_service.record_search(
                query_type=query_type,
                query_text=query if has_text else None,
                query_image_path=self._query_image_path if has_image else None,
                result_count=len(results),
                top_photo_ids=photo_ids[:10],  # Store top 10
                execution_time_ms=execution_time_ms,
                model_id=self.embedding_service._clip_model_id
            )

            # Emit signal with results and scores
            # results is already a list of (photo_id, score) tuples
            self.searchTriggered.emit(photo_ids, query, results)

        except Exception as e:
            logger.error(f"[SemanticSearch] Search failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Search Failed",
                f"Semantic search failed:\n{e}\n\n"
                "Check console for details."
            )
            self.errorOccurred.emit(str(e))

    def _on_clear(self):
        """Clear search and show all photos."""
        self.search_input.clear()
        self._last_query = ""
        self._query_image_path = None
        self._query_image_embedding = None

        # Cancel any pending debounce timer
        if self._search_debounce_timer and self._search_debounce_timer.isActive():
            self._search_debounce_timer.stop()

        # Cancel any active search worker
        if self._active_search_worker is not None:
            self._active_search_worker.cancel()
            self._active_search_worker = None

        # Reset image button
        self.image_btn.setText("📷 +Image")
        self.image_btn.setStyleSheet("")

        self.clear_btn.setVisible(False)
        self.status_label.setVisible(False)
        self.searchCleared.emit()
        logger.info("[SemanticSearch] Search cleared")

    def get_query(self) -> str:
        """Get current query text."""
        return self.search_input.text().strip()

    def set_enabled(self, enabled: bool):
        """Enable/disable the search widget."""
        self.search_input.setEnabled(enabled)
        self.search_btn.setEnabled(enabled)

    def _get_cache_key(self, query: str, threshold: float, model_id: int, has_image: bool = False) -> str:
        """
        Generate cache key from search parameters.

        Args:
            query: Query text (expanded)
            threshold: Similarity threshold
            model_id: CLIP model ID
            has_image: Whether image query is included

        Returns:
            MD5 hash of cache key string
        """
        # Include image flag to differentiate text-only from multi-modal searches
        cache_str = f"{query}|{threshold:.3f}|{model_id}|{has_image}"
        return md5(cache_str.encode()).hexdigest()

    def clear_cache(self):
        """
        Clear result cache.

        Call this when embeddings are re-extracted to invalidate stale results.
        """
        if self._result_cache is not None:
            self._result_cache.clear()
            logger.info("[SemanticSearch] Result cache cleared")
        else:
            logger.debug("[SemanticSearch] No cache to clear (caching disabled)")

    def get_cache_statistics(self) -> dict:
        """
        Get cache performance statistics.

        Returns:
            Dictionary with cache stats (hits, misses, hit rate, size)
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0

        return {
            'enabled': self._result_cache is not None,
            'hits': self._cache_hits,
            'misses': self._cache_misses,
            'total_searches': total,
            'hit_rate': hit_rate,
            'cache_size': len(self._result_cache) if self._result_cache else 0,
            'max_size': 100 if self._result_cache else 0,
        }

    def _on_show_history(self):
        """Show search history dialog."""
        from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem, QVBoxLayout, QDialogButtonBox

        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Search History")
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(400)

        layout = QVBoxLayout(dialog)

        # Info label
        info = QLabel("Click on a search to re-run it")
        info.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(info)

        # List widget
        list_widget = QListWidget()
        list_widget.setAlternatingRowColors(True)

        # Load recent searches
        recent_searches = self.search_history_service.get_recent_searches(limit=50)

        if not recent_searches:
            no_results = QLabel("No search history yet.\n\nYour searches will appear here.")
            no_results.setAlignment(Qt.AlignCenter)
            no_results.setStyleSheet("color: #999; padding: 40px;")
            layout.addWidget(no_results)
        else:
            for search in recent_searches:
                # Format display text
                if search.query_type == 'semantic_text':
                    text = f"🔍 Text: \"{search.query_text}\""
                elif search.query_type == 'semantic_image':
                    image_name = Path(search.query_image_path).name if search.query_image_path else "Unknown"
                    text = f"📷 Image: {image_name}"
                elif search.query_type == 'semantic_multi':
                    image_name = Path(search.query_image_path).name if search.query_image_path else "Unknown"
                    text = f"✨ Multi: \"{search.query_text}\" + {image_name}"
                else:
                    text = f"❓ {search.query_type}"

                # Add metadata
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(search.created_at)
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    time_str = search.created_at

                text += f"\n   {time_str} • {search.result_count} results • {search.execution_time_ms:.0f}ms"

                # Create item
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, search)  # Store search record
                list_widget.addItem(item)

            # Handle item click
            def on_item_clicked(item):
                search = item.data(Qt.UserRole)

                # Restore search state
                if search.query_text:
                    self.search_input.setText(search.query_text)

                # TODO: Handle image query restoration
                # Would need to check if image still exists

                # Re-run search
                dialog.accept()
                self._on_search()

            list_widget.itemClicked.connect(on_item_clicked)
            layout.addWidget(list_widget)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        dialog.exec()
