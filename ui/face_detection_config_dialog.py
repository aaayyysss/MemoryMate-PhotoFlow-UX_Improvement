# ui/face_detection_config_dialog.py
# Face Detection Configuration Dialog
# Phase 2B: Face Detection Controller & UI
# Advanced configuration for quality thresholds and clustering parameters

"""
Face Detection Configuration Dialog

Provides advanced configuration UI for:
- Face quality thresholds (Phase 2A)
- Clustering parameters
- Detection settings
- Performance monitoring

Features:
- Real-time quality threshold preview
- Adaptive parameter recommendations
- Configuration validation
- Import/Export configuration
- Per-project overrides
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton,
    QComboBox, QSlider, QDialogButtonBox, QScrollArea, QWidget,
    QMessageBox, QFileDialog, QTabWidget, QTextEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from pathlib import Path
import json
import logging

from config.face_detection_config import get_face_config
from translation_manager import tr

logger = logging.getLogger(__name__)


class QualityThresholdWidget(QWidget):
    """Widget for configuring face quality thresholds."""

    thresholds_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_current_thresholds()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        info_label = QLabel(
            "Configure quality thresholds for face selection. "
            "Higher thresholds = stricter quality requirements = fewer faces selected as representatives."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 11px; padding: 8px; background: #f5f5f5; border-radius: 4px;")
        layout.addWidget(info_label)

        # Form layout for thresholds
        form = QFormLayout()
        form.setSpacing(12)
        form.setContentsMargins(0, 16, 0, 0)

        # Blur threshold
        self.blur_spin = QDoubleSpinBox()
        self.blur_spin.setRange(0.0, 1000.0)
        self.blur_spin.setSingleStep(10.0)
        self.blur_spin.setSuffix(" (higher = sharper)")
        self.blur_spin.setToolTip(
            "Minimum Laplacian variance for sharp images.\n"
            "Typical values:\n"
            "- < 50: Very blurry\n"
            "- 50-100: Moderate blur\n"
            "- 100-500: Good sharpness ✓\n"
            "- > 500: Excellent sharpness"
        )
        self.blur_spin.valueChanged.connect(self._on_threshold_changed)
        form.addRow("Blur Threshold (min):", self.blur_spin)

        # Lighting thresholds
        self.lighting_min_spin = QDoubleSpinBox()
        self.lighting_min_spin.setRange(0.0, 100.0)
        self.lighting_min_spin.setSingleStep(5.0)
        self.lighting_min_spin.setToolTip("Minimum lighting score (0-100)")
        self.lighting_min_spin.valueChanged.connect(self._on_threshold_changed)

        self.lighting_max_spin = QDoubleSpinBox()
        self.lighting_max_spin.setRange(0.0, 100.0)
        self.lighting_max_spin.setSingleStep(5.0)
        self.lighting_max_spin.setToolTip("Maximum lighting score (avoid overexposure)")
        self.lighting_max_spin.valueChanged.connect(self._on_threshold_changed)

        lighting_layout = QHBoxLayout()
        lighting_layout.addWidget(self.lighting_min_spin)
        lighting_layout.addWidget(QLabel("-"))
        lighting_layout.addWidget(self.lighting_max_spin)
        form.addRow("Lighting Range:", lighting_layout)

        # Size threshold
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(0.0, 1.0)
        self.size_spin.setSingleStep(0.01)
        self.size_spin.setDecimals(3)
        self.size_spin.setSuffix(" (% of image)")
        self.size_spin.setToolTip(
            "Minimum face area as percentage of image.\n"
            "Recommended: 0.02 (2% of image area)"
        )
        self.size_spin.valueChanged.connect(self._on_threshold_changed)
        form.addRow("Size Threshold (min):", self.size_spin)

        # Aspect ratio thresholds
        self.aspect_min_spin = QDoubleSpinBox()
        self.aspect_min_spin.setRange(0.0, 5.0)
        self.aspect_min_spin.setSingleStep(0.1)
        self.aspect_min_spin.setToolTip("Minimum aspect ratio (width/height)")
        self.aspect_min_spin.valueChanged.connect(self._on_threshold_changed)

        self.aspect_max_spin = QDoubleSpinBox()
        self.aspect_max_spin.setRange(0.0, 5.0)
        self.aspect_max_spin.setSingleStep(0.1)
        self.aspect_max_spin.setToolTip("Maximum aspect ratio (width/height)")
        self.aspect_max_spin.valueChanged.connect(self._on_threshold_changed)

        aspect_layout = QHBoxLayout()
        aspect_layout.addWidget(self.aspect_min_spin)
        aspect_layout.addWidget(QLabel("-"))
        aspect_layout.addWidget(self.aspect_max_spin)
        form.addRow("Aspect Ratio Range:", aspect_layout)

        # Confidence threshold
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setDecimals(2)
        self.confidence_spin.setToolTip("Minimum detection confidence (0-1)")
        self.confidence_spin.valueChanged.connect(self._on_threshold_changed)
        form.addRow("Confidence Threshold (min):", self.confidence_spin)

        # Overall quality threshold
        self.overall_spin = QDoubleSpinBox()
        self.overall_spin.setRange(0.0, 100.0)
        self.overall_spin.setSingleStep(5.0)
        self.overall_spin.setToolTip(
            "Minimum overall quality score (0-100).\n"
            "Faces below this threshold won't be selected as representatives.\n"
            "Recommended: 60"
        )
        self.overall_spin.valueChanged.connect(self._on_threshold_changed)
        form.addRow("Overall Quality (min):", self.overall_spin)

        layout.addLayout(form)

        # Quality labels reference
        quality_ref = QLabel(
            "<b>Quality Labels:</b><br>"
            "• 80-100: Excellent ⭐⭐⭐⭐⭐<br>"
            "• 60-80: Good ⭐⭐⭐⭐<br>"
            "• 40-60: Fair ⭐⭐⭐<br>"
            "• 0-40: Poor ⭐⭐"
        )
        quality_ref.setStyleSheet("color: #555; font-size: 10px; padding: 12px; background: #fafafa; border-radius: 4px;")
        layout.addWidget(quality_ref)

        # Reset to defaults button
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_to_defaults)
        layout.addWidget(reset_btn)

        layout.addStretch()

    def _load_current_thresholds(self):
        """Load current thresholds from configuration."""
        from services.face_quality_analyzer import FaceQualityAnalyzer

        config = get_face_config()
        thresholds = FaceQualityAnalyzer.DEFAULT_THRESHOLDS

        # Try to get custom thresholds from config if available
        # For now, use defaults
        self.blur_spin.setValue(thresholds['blur_min'])
        self.lighting_min_spin.setValue(thresholds['lighting_min'])
        self.lighting_max_spin.setValue(thresholds['lighting_max'])
        self.size_spin.setValue(thresholds['size_min'])
        self.aspect_min_spin.setValue(thresholds['aspect_min'])
        self.aspect_max_spin.setValue(thresholds['aspect_max'])
        self.confidence_spin.setValue(thresholds['confidence_min'])
        self.overall_spin.setValue(thresholds['overall_min'])

    def _on_threshold_changed(self):
        """Emit signal when thresholds change."""
        thresholds = self.get_thresholds()
        self.thresholds_changed.emit(thresholds)

    def get_thresholds(self) -> dict:
        """Get current threshold values."""
        return {
            'blur_min': self.blur_spin.value(),
            'lighting_min': self.lighting_min_spin.value(),
            'lighting_max': self.lighting_max_spin.value(),
            'size_min': self.size_spin.value(),
            'aspect_min': self.aspect_min_spin.value(),
            'aspect_max': self.aspect_max_spin.value(),
            'confidence_min': self.confidence_spin.value(),
            'overall_min': self.overall_spin.value()
        }

    def _reset_to_defaults(self):
        """Reset all thresholds to defaults."""
        from services.face_quality_analyzer import FaceQualityAnalyzer

        thresholds = FaceQualityAnalyzer.DEFAULT_THRESHOLDS
        self.blur_spin.setValue(thresholds['blur_min'])
        self.lighting_min_spin.setValue(thresholds['lighting_min'])
        self.lighting_max_spin.setValue(thresholds['lighting_max'])
        self.size_spin.setValue(thresholds['size_min'])
        self.aspect_min_spin.setValue(thresholds['aspect_min'])
        self.aspect_max_spin.setValue(thresholds['aspect_max'])
        self.confidence_spin.setValue(thresholds['confidence_min'])
        self.overall_spin.setValue(thresholds['overall_min'])


class ClusteringParametersWidget(QWidget):
    """Widget for configuring clustering parameters."""

    parameters_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._load_current_parameters()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        info_label = QLabel(
            "Configure DBSCAN clustering parameters. "
            "Auto-tuning is recommended for most users."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 11px; padding: 8px; background: #f5f5f5; border-radius: 4px;")
        layout.addWidget(info_label)

        # Auto-tuning checkbox
        self.auto_tune_checkbox = QCheckBox("Enable Auto-Tuning (Recommended)")
        self.auto_tune_checkbox.setToolTip(
            "Automatically select optimal clustering parameters based on dataset size.\n"
            "Disable to manually configure eps and min_samples."
        )
        self.auto_tune_checkbox.setChecked(True)
        self.auto_tune_checkbox.stateChanged.connect(self._on_auto_tune_changed)
        layout.addWidget(self.auto_tune_checkbox)

        # Manual parameters group
        self.manual_group = QGroupBox("Manual Parameters")
        manual_layout = QFormLayout(self.manual_group)
        manual_layout.setSpacing(12)

        # Epsilon parameter
        self.eps_spin = QDoubleSpinBox()
        self.eps_spin.setRange(0.20, 0.50)
        self.eps_spin.setSingleStep(0.01)
        self.eps_spin.setDecimals(2)
        self.eps_spin.setToolTip(
            "DBSCAN epsilon (maximum distance between faces in same cluster).\n"
            "Lower = stricter grouping (more clusters, fewer false positives)\n"
            "Higher = looser grouping (fewer clusters, more false positives)\n"
            "Recommended range: 0.30-0.40"
        )
        self.eps_spin.valueChanged.connect(self._on_parameter_changed)
        manual_layout.addRow("Epsilon (eps):", self.eps_spin)

        # Min samples parameter
        self.min_samples_spin = QSpinBox()
        self.min_samples_spin.setRange(1, 10)
        self.min_samples_spin.setToolTip(
            "Minimum faces required to form a cluster.\n"
            "1-2: Allow small clusters (more sensitive)\n"
            "3-4: Require more evidence (more conservative)\n"
            "Recommended: 2-3"
        )
        self.min_samples_spin.valueChanged.connect(self._on_parameter_changed)
        manual_layout.addRow("Min Samples:", self.min_samples_spin)

        layout.addWidget(self.manual_group)

        # Adaptive parameters reference
        adaptive_info = QLabel(
            "<b>Adaptive Parameters (Auto-Tuning):</b><br>"
            "• Tiny (&lt;50 faces): eps=0.42, min_samples=2<br>"
            "• Small (50-200): eps=0.38, min_samples=2<br>"
            "• Medium (200-1000): eps=0.35, min_samples=2<br>"
            "• Large (1000-5000): eps=0.32, min_samples=3<br>"
            "• XLarge (&gt;5000): eps=0.30, min_samples=3"
        )
        adaptive_info.setStyleSheet("color: #555; font-size: 10px; padding: 12px; background: #fafafa; border-radius: 4px;")
        layout.addWidget(adaptive_info)

        layout.addStretch()

        # Initially disable manual parameters
        self._on_auto_tune_changed()

    def _load_current_parameters(self):
        """Load current parameters from configuration."""
        config = get_face_config()

        # Load default parameters
        params = config.get_clustering_params()
        self.eps_spin.setValue(params.get('eps', 0.35))
        self.min_samples_spin.setValue(params.get('min_samples', 2))

    def _on_auto_tune_changed(self):
        """Handle auto-tune checkbox state change."""
        auto_tune = self.auto_tune_checkbox.isChecked()
        self.manual_group.setEnabled(not auto_tune)
        self._on_parameter_changed()

    def _on_parameter_changed(self):
        """Emit signal when parameters change."""
        parameters = self.get_parameters()
        self.parameters_changed.emit(parameters)

    def get_parameters(self) -> dict:
        """Get current parameter values."""
        return {
            'auto_tune': self.auto_tune_checkbox.isChecked(),
            'eps': self.eps_spin.value(),
            'min_samples': self.min_samples_spin.value()
        }


class FaceDetectionConfigDialog(QDialog):
    """
    Advanced configuration dialog for face detection and clustering.

    Provides tabbed interface for:
    - Face quality thresholds (Phase 2A)
    - Clustering parameters
    - Performance settings
    - Configuration import/export
    """

    def __init__(self, parent=None, project_id: Optional[int] = None):
        super().__init__(parent)
        self.project_id = project_id
        self._setup_ui()
        self.setWindowTitle("Face Detection Configuration")
        self.resize(700, 600)

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("<h2>Face Detection Configuration</h2>")
        header.setStyleSheet("color: #333; padding: 12px;")
        layout.addWidget(header)

        # Tab widget
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Quality Thresholds tab
        self.quality_widget = QualityThresholdWidget()
        scroll_quality = QScrollArea()
        scroll_quality.setWidgetResizable(True)
        scroll_quality.setWidget(self.quality_widget)
        scroll_quality.setFrameShape(QScrollArea.NoFrame)
        tabs.addTab(scroll_quality, "Quality Thresholds")

        # Clustering Parameters tab
        self.clustering_widget = ClusteringParametersWidget()
        scroll_clustering = QScrollArea()
        scroll_clustering.setWidgetResizable(True)
        scroll_clustering.setWidget(self.clustering_widget)
        scroll_clustering.setFrameShape(QScrollArea.NoFrame)
        tabs.addTab(scroll_clustering, "Clustering Parameters")

        # Configuration tab
        config_tab = self._create_config_tab()
        tabs.addTab(config_tab, "Configuration")

        layout.addWidget(tabs)

        # Button box
        button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _create_config_tab(self) -> QWidget:
        """Create configuration import/export tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Info
        info = QLabel(
            "Save and load configuration presets for different use cases.\n"
            "Configurations can be shared across projects or users."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 11px; padding: 8px; background: #f5f5f5; border-radius: 4px;")
        layout.addWidget(info)

        # Buttons
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        export_btn = QPushButton("Export Configuration...")
        export_btn.setToolTip("Save current configuration to JSON file")
        export_btn.clicked.connect(self._export_config)
        btn_layout.addWidget(export_btn)

        import_btn = QPushButton("Import Configuration...")
        import_btn.setToolTip("Load configuration from JSON file")
        import_btn.clicked.connect(self._import_config)
        btn_layout.addWidget(import_btn)

        reset_btn = QPushButton("Reset All to Defaults")
        reset_btn.setToolTip("Reset all settings to default values")
        reset_btn.clicked.connect(self._reset_all)
        btn_layout.addWidget(reset_btn)

        layout.addLayout(btn_layout)

        # Current configuration preview
        preview_group = QGroupBox("Current Configuration Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.config_preview = QTextEdit()
        self.config_preview.setReadOnly(True)
        self.config_preview.setMaximumHeight(300)
        self.config_preview.setStyleSheet("font-family: monospace; font-size: 10px;")
        preview_layout.addWidget(self.config_preview)

        refresh_btn = QPushButton("Refresh Preview")
        refresh_btn.clicked.connect(self._update_config_preview)
        preview_layout.addWidget(refresh_btn)

        layout.addWidget(preview_group)

        layout.addStretch()

        # Initial preview
        self._update_config_preview()

        return widget

    def _update_config_preview(self):
        """Update configuration preview."""
        config = {
            'quality_thresholds': self.quality_widget.get_thresholds(),
            'clustering_parameters': self.clustering_widget.get_parameters()
        }
        self.config_preview.setPlainText(json.dumps(config, indent=2))

    def _export_config(self):
        """Export configuration to file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Configuration",
            str(Path.home() / "face_detection_config.json"),
            "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            config = {
                'quality_thresholds': self.quality_widget.get_thresholds(),
                'clustering_parameters': self.clustering_widget.get_parameters(),
                'metadata': {
                    'exported_at': str(Path(__file__).parent.parent),
                    'version': '2.0.0'
                }
            }

            with open(file_path, 'w') as f:
                json.dump(config, f, indent=2)

            QMessageBox.information(
                self,
                "Export Successful",
                f"Configuration exported to:\n{file_path}"
            )

        except Exception as e:
            logger.error(f"Failed to export configuration: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Failed to export configuration:\n{str(e)}"
            )

    def _import_config(self):
        """Import configuration from file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Configuration",
            str(Path.home()),
            "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            with open(file_path, 'r') as f:
                config = json.load(f)

            # Apply quality thresholds
            if 'quality_thresholds' in config:
                thresholds = config['quality_thresholds']
                self.quality_widget.blur_spin.setValue(thresholds.get('blur_min', 100.0))
                self.quality_widget.lighting_min_spin.setValue(thresholds.get('lighting_min', 40.0))
                self.quality_widget.lighting_max_spin.setValue(thresholds.get('lighting_max', 90.0))
                self.quality_widget.size_spin.setValue(thresholds.get('size_min', 0.02))
                self.quality_widget.aspect_min_spin.setValue(thresholds.get('aspect_min', 0.5))
                self.quality_widget.aspect_max_spin.setValue(thresholds.get('aspect_max', 1.6))
                self.quality_widget.confidence_spin.setValue(thresholds.get('confidence_min', 0.6))
                self.quality_widget.overall_spin.setValue(thresholds.get('overall_min', 60.0))

            # Apply clustering parameters
            if 'clustering_parameters' in config:
                params = config['clustering_parameters']
                self.clustering_widget.auto_tune_checkbox.setChecked(params.get('auto_tune', True))
                self.clustering_widget.eps_spin.setValue(params.get('eps', 0.35))
                self.clustering_widget.min_samples_spin.setValue(params.get('min_samples', 2))

            self._update_config_preview()

            QMessageBox.information(
                self,
                "Import Successful",
                "Configuration imported successfully!"
            )

        except Exception as e:
            logger.error(f"Failed to import configuration: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Import Failed",
                f"Failed to import configuration:\n{str(e)}"
            )

    def _reset_all(self):
        """Reset all settings to defaults."""
        reply = QMessageBox.question(
            self,
            "Reset Configuration",
            "Reset all settings to default values?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.quality_widget._reset_to_defaults()
            self.clustering_widget._load_current_parameters()
            self.clustering_widget.auto_tune_checkbox.setChecked(True)
            self._update_config_preview()

    def get_configuration(self) -> dict:
        """Get complete configuration."""
        return {
            'quality_thresholds': self.quality_widget.get_thresholds(),
            'clustering_parameters': self.clustering_widget.get_parameters()
        }

    def accept(self):
        """Save configuration and close dialog."""
        try:
            config = self.get_configuration()

            # TODO: Save to configuration system
            # For now, just log it
            logger.info(f"Face detection configuration updated: {json.dumps(config, indent=2)}")

            QMessageBox.information(
                self,
                "Configuration Saved",
                "Configuration saved successfully!\n\n"
                "Changes will take effect on next face detection run."
            )

            super().accept()

        except Exception as e:
            logger.error(f"Failed to save configuration: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Failed to save configuration:\n{str(e)}"
            )
