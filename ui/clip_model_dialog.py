# ui/clip_model_dialog.py
# Version 01.00.00.00 dated 20260118
# CLIP Model Selection and Download Dialog
#
# Follows best practices from professional photo management applications:
# - Offline-first: Check for local models before attempting download
# - User choice: Let user select model variant based on their needs
# - Explicit consent: Never download without user permission
# - Preference storage: Remember user's choice to avoid repeated prompts

"""
CLIPModelDialog - User-friendly CLIP model management

This dialog appears when CLIP models are needed but not found locally.
It provides clear information about model options and manages downloads.

Best Practices:
1. Check offline availability first
2. Inform user about missing models
3. Offer model selection (balanced vs. fast vs. high-quality)
4. Request explicit download consent
5. Store preferences for future use
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QButtonGroup, QGroupBox, QProgressBar,
    QTextEdit, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont, QPixmap
from pathlib import Path
from typing import Optional, Tuple
import requests
from logging_config import get_logger

logger = get_logger(__name__)


class ModelDownloadWorker(QThread):
    """Background worker for downloading CLIP models."""

    progress = Signal(int, str)  # percentage, status message
    finished = Signal(bool, str)  # success, message

    def __init__(self, model_name: str, destination_path: Path):
        super().__init__()
        self.model_name = model_name
        self.destination_path = destination_path
        self._is_cancelled = False

    def run(self):
        """Download model using HuggingFace transformers."""
        try:
            self.progress.emit(10, "Importing libraries...")
            from transformers import CLIPProcessor, CLIPModel

            # Map model names
            model_map = {
                'clip-vit-b32': 'openai/clip-vit-base-patch32',
                'clip-vit-b16': 'openai/clip-vit-base-patch16',
                'clip-vit-l14': 'openai/clip-vit-large-patch14',
            }
            hf_model = model_map.get(self.model_name, self.model_name)

            self.progress.emit(20, f"Downloading {hf_model}...")
            logger.info(f"[ModelDownloadWorker] Downloading {hf_model} to {self.destination_path}")

            # Create destination directory
            self.destination_path.mkdir(parents=True, exist_ok=True)

            # Download processor
            if self._is_cancelled:
                self.finished.emit(False, "Download cancelled")
                return

            self.progress.emit(40, "Downloading processor...")
            processor = CLIPProcessor.from_pretrained(
                hf_model,
                cache_dir=str(self.destination_path.parent),
                local_files_only=False
            )
            processor.save_pretrained(str(self.destination_path))

            # Download model
            if self._is_cancelled:
                self.finished.emit(False, "Download cancelled")
                return

            self.progress.emit(70, "Downloading model weights...")
            model = CLIPModel.from_pretrained(
                hf_model,
                cache_dir=str(self.destination_path.parent),
                local_files_only=False
            )
            model.save_pretrained(str(self.destination_path))

            self.progress.emit(100, "Download complete!")
            logger.info(f"[ModelDownloadWorker] Download complete: {self.destination_path}")
            self.finished.emit(True, f"Model downloaded successfully to:\n{self.destination_path}")

        except Exception as e:
            logger.error(f"[ModelDownloadWorker] Download failed: {e}", exc_info=True)
            self.finished.emit(False, f"Download failed:\n{str(e)}")

    def cancel(self):
        """Cancel download."""
        self._is_cancelled = True


class CLIPModelDialog(QDialog):
    """
    Dialog for CLIP model selection and download.

    IMPORTANT: Model selection is PROJECT-LEVEL metadata (Google Photos/Lightroom best practice).
    When project_id is provided, model changes are treated as project migrations that
    require reindexing all embeddings.

    Workflow:
    1. Check for offline models
    2. Show available options
    3. Let user choose model variant
    4. Download with explicit consent
    5. Store as project canonical model (if project_id provided) or global preference
    6. Trigger reindex job if model changed
    """

    model_selected = Signal(str, str)  # model_name, model_path
    reindex_required = Signal(int, str, str)  # project_id, old_model, new_model

    def __init__(self, parent=None, project_id: Optional[int] = None):
        """
        Initialize CLIP model dialog.

        Args:
            parent: Parent widget
            project_id: Optional project ID for project-level model binding.
                       When provided, model selection becomes project metadata
                       and triggers reindexing workflow on change.
        """
        super().__init__(parent)
        self.selected_model = None
        self.model_path = None
        self.download_worker = None
        self.project_id = project_id
        self.current_project_model = None

        self.setWindowTitle("CLIP Model Setup")
        self.setMinimumSize(700, 600)
        self.setModal(True)

        self._init_ui()
        self._check_offline_availability()
        self._load_project_model()

    def _init_ui(self):
        """Initialize user interface."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("🤖 Visual Embedding Model Setup")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            "MemoryMate uses AI vision models (CLIP) to understand photo content "
            "for intelligent search and similar photo detection.\n\n"
            "These models run locally on your computer - no cloud processing."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(desc)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Model selection group
        model_group = QGroupBox("Select Model")
        model_layout = QVBoxLayout(model_group)
        model_layout.setSpacing(12)

        self.model_button_group = QButtonGroup(self)

        # Fast model (B/32)
        self.radio_b32 = QRadioButton("⚡ Fast (Recommended)")
        self.radio_b32.setChecked(True)  # Default
        desc_b32 = QLabel(
            "  • Size: ~600 MB\n"
            "  • Speed: Fast on most PCs\n"
            "  • Quality: Good for most photos\n"
            "  • Best for: General use, older hardware"
        )
        desc_b32.setStyleSheet("color: #666; font-size: 11px; margin-left: 20px;")
        model_layout.addWidget(self.radio_b32)
        model_layout.addWidget(desc_b32)
        self.model_button_group.addButton(self.radio_b32, 0)

        # Balanced model (B/16)
        self.radio_b16 = QRadioButton("⚖️ Balanced")
        desc_b16 = QLabel(
            "  • Size: ~800 MB\n"
            "  • Speed: Moderate\n"
            "  • Quality: Better accuracy\n"
            "  • Best for: Users who want better results"
        )
        desc_b16.setStyleSheet("color: #666; font-size: 11px; margin-left: 20px;")
        model_layout.addWidget(self.radio_b16)
        model_layout.addWidget(desc_b16)
        self.model_button_group.addButton(self.radio_b16, 1)

        # High quality model (L/14)
        self.radio_l14 = QRadioButton("🎯 High Quality")
        desc_l14 = QLabel(
            "  • Size: ~1.7 GB\n"
            "  • Speed: Slow (requires good GPU)\n"
            "  • Quality: Best accuracy\n"
            "  • Best for: Professional use, powerful hardware"
        )
        desc_l14.setStyleSheet("color: #666; font-size: 11px; margin-left: 20px;")
        model_layout.addWidget(self.radio_l14)
        model_layout.addWidget(desc_l14)
        self.model_button_group.addButton(self.radio_l14, 2)

        layout.addWidget(model_group)

        # Offline status info
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "background-color: #fff3cd; "
            "border: 1px solid #ffc107; "
            "border-radius: 4px; "
            "padding: 12px; "
            "color: #856404;"
        )
        layout.addWidget(self.status_label)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Progress message
        self.progress_label = QLabel()
        self.progress_label.setVisible(False)
        self.progress_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.progress_label)

        layout.addStretch()

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.HLine)
        separator2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator2)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_use_existing = QPushButton("📁 Use Existing Model")
        self.btn_use_existing.clicked.connect(self._on_use_existing)
        self.btn_use_existing.setEnabled(False)  # Enabled when offline model found
        button_layout.addWidget(self.btn_use_existing)

        self.btn_download = QPushButton("⬇️ Download Model")
        self.btn_download.clicked.connect(self._on_download)
        self.btn_download.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #ccc;
                color: #999;
            }
        """)
        button_layout.addWidget(self.btn_download)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _check_offline_availability(self):
        """Check if models are available offline."""
        app_root = Path(__file__).parent.parent.absolute()
        models_found = []

        # Check for each model
        model_variants = {
            'clip-vit-b32': 'openai--clip-vit-base-patch32',
            'clip-vit-b16': 'openai--clip-vit-base-patch16',
            'clip-vit-l14': 'openai--clip-vit-large-patch14',
        }

        for model_key, folder_name in model_variants.items():
            # Bare model name without org prefix (e.g. 'clip-vit-base-patch32')
            bare_name = folder_name.split('--', 1)[-1] if '--' in folder_name else folder_name

            found = False
            # Check all folder name variants × directory name variants
            for name in (folder_name, bare_name):
                for dir_name in ('Model', 'models', 'model'):
                    model_path = app_root / dir_name / name
                    if model_path.exists() and (model_path / 'config.json').exists():
                        models_found.append((model_key, str(model_path)))
                        logger.info(f"[CLIPModelDialog] Found offline model: {model_path}")
                        found = True
                        break
                if found:
                    break

        if models_found:
            # Found at least one offline model
            model_names = [m[0] for m in models_found]
            self.status_label.setText(
                f"✅ Found {len(models_found)} model(s) available offline:\n"
                f"{', '.join(model_names)}\n\n"
                f"You can use these models without downloading."
            )
            self.status_label.setStyleSheet(
                "background-color: #d4edda; "
                "border: 1px solid #c3e6cb; "
                "border-radius: 4px; "
                "padding: 12px; "
                "color: #155724;"
            )
            self.btn_use_existing.setEnabled(True)
            self.offline_models = dict(models_found)
        else:
            # No offline models found
            self.status_label.setText(
                "⚠️ No models found offline.\n\n"
                "To use visual embedding features, you need to download a model.\n"
                "The model will be stored locally and reused for future sessions."
            )
            self.offline_models = {}

    def _get_selected_model(self) -> Tuple[str, str]:
        """Get selected model name and HuggingFace ID."""
        checked_id = self.model_button_group.checkedId()

        model_map = {
            0: ('clip-vit-b32', 'openai/clip-vit-base-patch32'),
            1: ('clip-vit-b16', 'openai/clip-vit-base-patch16'),
            2: ('clip-vit-l14', 'openai/clip-vit-large-patch14'),
        }

        return model_map.get(checked_id, model_map[0])

    def _confirm_model_change(self, new_model: str) -> bool:
        """
        Confirm model change when it requires reindexing.

        Returns True if user confirms, False to cancel.
        """
        if self.project_id is None or self.current_project_model is None:
            return True  # No project context, no confirmation needed

        if self.current_project_model == new_model:
            return True  # Same model, no change

        # Count photos that need reindexing
        try:
            from repository.project_repository import ProjectRepository
            project_repo = ProjectRepository()
            mismatch = project_repo.get_embedding_model_mismatch_count(self.project_id)
            total = mismatch['total_embeddings']
        except Exception:
            total = "unknown number of"

        reply = QMessageBox.warning(
            self,
            "Model Change Requires Reindexing",
            f"Changing from '{self.current_project_model}' to '{new_model}' requires "
            f"reindexing all embeddings for this project.\n\n"
            f"This will:\n"
            f"  - Regenerate embeddings for {total} photos\n"
            f"  - Take some time depending on your hardware\n"
            f"  - Keep old embeddings until reindex completes\n\n"
            f"Similar photo search and semantic search will use the new model "
            f"after reindexing is complete.\n\n"
            f"Do you want to proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        return reply == QMessageBox.Yes

    def _on_use_existing(self):
        """Use existing offline model."""
        model_name, _ = self._get_selected_model()

        if model_name in self.offline_models:
            # Confirm model change if needed
            if not self._confirm_model_change(model_name):
                return

            model_path = self.offline_models[model_name]
            logger.info(f"[CLIPModelDialog] Using existing model: {model_path}")

            # Store preference
            self._store_preference(model_name, model_path)

            # Emit signal and close
            self.model_selected.emit(model_name, model_path)
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "Model Not Found",
                f"Selected model '{model_name}' is not available offline.\n"
                f"Please download it first."
            )

    def _on_download(self):
        """Download selected model."""
        model_name, hf_model = self._get_selected_model()

        # Confirm model change if needed (before download)
        if not self._confirm_model_change(model_name):
            return

        # Confirm download
        reply = QMessageBox.question(
            self,
            "Download Confirmation",
            f"Download {model_name}?\n\n"
            f"Model: {hf_model}\n"
            f"Size: ~{self._get_model_size(model_name)}\n\n"
            f"The model will be downloaded from HuggingFace and stored locally.\n"
            f"This is a one-time download and will be reused for all future sessions.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # Prepare destination
        app_root = Path(__file__).parent.parent.absolute()
        destination = app_root / 'Model' / hf_model.replace('/', '--')

        # Show progress UI
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_bar.setValue(0)

        # Disable buttons during download
        self.btn_download.setEnabled(False)
        self.btn_use_existing.setEnabled(False)
        self.btn_cancel.setText("Cancel Download")

        # Start download worker
        self.download_worker = ModelDownloadWorker(model_name, destination)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()

        logger.info(f"[CLIPModelDialog] Starting download: {model_name} → {destination}")

    def _on_download_progress(self, percentage: int, message: str):
        """Update download progress."""
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(message)

    def _on_download_finished(self, success: bool, message: str):
        """Handle download completion."""
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)

        # Re-enable buttons
        self.btn_download.setEnabled(True)
        self.btn_cancel.setText("Cancel")

        if success:
            model_name, _ = self._get_selected_model()
            model_path = str(self.download_worker.destination_path)

            logger.info(f"[CLIPModelDialog] Download successful: {model_path}")

            # Store preference
            self._store_preference(model_name, model_path)

            QMessageBox.information(
                self,
                "Download Complete",
                f"Model downloaded successfully!\n\n{message}"
            )

            # Emit signal and close
            self.model_selected.emit(model_name, model_path)
            self.accept()
        else:
            logger.error(f"[CLIPModelDialog] Download failed: {message}")
            QMessageBox.critical(
                self,
                "Download Failed",
                f"Failed to download model:\n\n{message}\n\n"
                f"Please check your internet connection and try again."
            )

    def _load_project_model(self):
        """Load current project's canonical model if project_id is provided."""
        if self.project_id is None:
            return

        try:
            from repository.project_repository import ProjectRepository
            project_repo = ProjectRepository()
            self.current_project_model = project_repo.get_semantic_model(self.project_id)

            # Pre-select the current model in the UI
            model_to_radio = {
                'clip-vit-b32': self.radio_b32,
                'clip-vit-b16': self.radio_b16,
                'clip-vit-l14': self.radio_l14,
            }
            if self.current_project_model in model_to_radio:
                model_to_radio[self.current_project_model].setChecked(True)

            logger.info(
                f"[CLIPModelDialog] Project {self.project_id} current model: {self.current_project_model}"
            )
        except Exception as e:
            logger.warning(f"[CLIPModelDialog] Could not load project model: {e}")

    def _store_preference(self, model_name: str, model_path: str):
        """
        Store model preference.

        If project_id is provided, stores as project canonical model and
        triggers reindexing workflow. Otherwise stores as global preference.
        """
        try:
            # Always store global preference for model path
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            settings.set("clip_model_name", model_name)
            settings.set("clip_model_path", model_path)
            logger.info(f"[CLIPModelDialog] Stored global preference: {model_name} → {model_path}")

            # If project_id is provided, update project's canonical model
            if self.project_id is not None:
                from repository.project_repository import ProjectRepository
                project_repo = ProjectRepository()

                old_model = self.current_project_model or project_repo.get_semantic_model(self.project_id)

                if old_model != model_name:
                    # This is a MODEL MIGRATION - important workflow!
                    logger.info(
                        f"[CLIPModelDialog] PROJECT MODEL CHANGE: "
                        f"Project {self.project_id}: {old_model} → {model_name}"
                    )

                    # Change the project's canonical model
                    change_result = project_repo.change_semantic_model(
                        project_id=self.project_id,
                        new_model=model_name,
                        keep_old_embeddings=True  # Keep for comparison/rollback
                    )

                    # Emit signal to trigger reindex job
                    self.reindex_required.emit(self.project_id, old_model, model_name)

                    logger.info(
                        f"[CLIPModelDialog] Project migration initiated: "
                        f"{change_result['photos_to_reindex']} photos need reindexing"
                    )
                else:
                    logger.info(
                        f"[CLIPModelDialog] Project {self.project_id} already uses model {model_name}"
                    )

        except Exception as e:
            logger.warning(f"[CLIPModelDialog] Could not store preference: {e}")

    def _get_model_size(self, model_name: str) -> str:
        """Get approximate model size."""
        sizes = {
            'clip-vit-b32': '600 MB',
            'clip-vit-b16': '800 MB',
            'clip-vit-l14': '1.7 GB',
        }
        return sizes.get(model_name, 'Unknown')

    def closeEvent(self, event):
        """Handle dialog close."""
        # Cancel download if in progress
        if self.download_worker and self.download_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Download?",
                "Download is in progress. Are you sure you want to cancel?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self.download_worker.cancel()
                self.download_worker.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def show_clip_model_dialog(
    parent=None,
    project_id: Optional[int] = None,
    on_reindex_required=None
) -> Optional[Tuple[str, str]]:
    """
    Show CLIP model selection dialog.

    Args:
        parent: Parent widget
        project_id: Optional project ID for project-level model binding.
                   When provided, model changes trigger reindexing workflow.
        on_reindex_required: Optional callback(project_id, old_model, new_model)
                            called when model change requires reindexing.

    Returns:
        Tuple of (model_name, model_path) if selected, None if cancelled

    Example:
        def handle_reindex(project_id, old_model, new_model):
            # Enqueue reindex job
            from workers.semantic_embedding_worker import SemanticEmbeddingWorker
            photo_ids = get_photo_ids_for_project(project_id)
            worker = SemanticEmbeddingWorker(photo_ids=photo_ids, project_id=project_id)
            QThreadPool.globalInstance().start(worker)

        result = show_clip_model_dialog(
            parent=self,
            project_id=current_project_id,
            on_reindex_required=handle_reindex
        )
    """
    dialog = CLIPModelDialog(parent, project_id=project_id)

    result = [None]  # Use list to capture result from signal

    def on_model_selected(model_name, model_path):
        result[0] = (model_name, model_path)

    dialog.model_selected.connect(on_model_selected)

    if on_reindex_required is not None:
        dialog.reindex_required.connect(on_reindex_required)

    if dialog.exec() == QDialog.Accepted:
        return result[0]

    return None
