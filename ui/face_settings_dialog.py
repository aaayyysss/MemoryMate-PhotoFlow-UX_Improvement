"""
Face Detection Settings Dialog
Allows users to configure face detection and recognition settings.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QPushButton, QMessageBox, QTabWidget, QWidget, QSlider
)
from PySide6.QtCore import Qt
from typing import Optional

from config.face_detection_config import get_face_config
from services.face_detection_service import FaceDetectionService


class FaceSettingsDialog(QDialog):
    """Dialog for configuring face detection settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_face_config()
        self.setWindowTitle("Face Detection Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)

        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        """Setup the user interface."""
        layout = QVBoxLayout(self)

        # Create tab widget
        tabs = QTabWidget()
        tabs.addTab(self.create_general_tab(), "General")
        tabs.addTab(self.create_detection_tab(), "Detection")
        tabs.addTab(self.create_clustering_tab(), "Clustering")
        tabs.addTab(self.create_advanced_tab(), "Advanced")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.test_btn = QPushButton("Test Detection")
        self.test_btn.clicked.connect(self.test_backend)
        button_layout.addWidget(self.test_btn)

        self.defaults_btn = QPushButton("Reset to Defaults")
        self.defaults_btn.clicked.connect(self.reset_to_defaults)
        button_layout.addWidget(self.defaults_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setDefault(True)
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

    def create_general_tab(self) -> QWidget:
        """Create general settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Enable/disable face detection
        enable_group = QGroupBox("Face Detection")
        enable_layout = QVBoxLayout(enable_group)

        self.enabled_check = QCheckBox("Enable face detection")
        self.enabled_check.setToolTip("Enable or disable face detection feature")
        enable_layout.addWidget(self.enabled_check)

        self.auto_cluster_check = QCheckBox("Automatically cluster faces after scan")
        self.auto_cluster_check.setToolTip("Run face clustering immediately after scanning")
        enable_layout.addWidget(self.auto_cluster_check)

        self.require_confirm_check = QCheckBox("Ask for confirmation before detection")
        self.require_confirm_check.setToolTip("Show confirmation dialog before starting face detection")
        enable_layout.addWidget(self.require_confirm_check)

        layout.addWidget(enable_group)

        # Backend selection
        backend_group = QGroupBox("Detection Backend")
        backend_layout = QFormLayout(backend_group)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["insightface"])  # Only InsightFace supported (buffalo_l + OnnxRuntime)
        self.backend_combo.currentTextChanged.connect(self.on_backend_changed)
        backend_layout.addRow("Backend:", self.backend_combo)

        # Backend status
        self.backend_status_label = QLabel()
        backend_layout.addRow("Status:", self.backend_status_label)

        # Backend info
        self.backend_info_label = QLabel()
        self.backend_info_label.setWordWrap(True)
        backend_layout.addRow("Info:", self.backend_info_label)

        layout.addWidget(backend_group)

        # Storage settings
        storage_group = QGroupBox("Storage")
        storage_layout = QFormLayout(storage_group)

        self.save_crops_check = QCheckBox("Save face crops to disk")
        storage_layout.addRow("", self.save_crops_check)

        self.crop_size_spin = QSpinBox()
        self.crop_size_spin.setRange(64, 512)
        self.crop_size_spin.setValue(160)
        self.crop_size_spin.setSuffix(" px")
        storage_layout.addRow("Crop size:", self.crop_size_spin)

        self.crop_quality_spin = QSpinBox()
        self.crop_quality_spin.setRange(50, 100)
        self.crop_quality_spin.setValue(95)
        self.crop_quality_spin.setSuffix("%")
        storage_layout.addRow("JPEG quality:", self.crop_quality_spin)

        layout.addWidget(storage_group)

        layout.addStretch()
        return widget

    def create_detection_tab(self) -> QWidget:
        """Create detection settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # InsightFace settings (only backend supported)
        if_group = QGroupBox("InsightFace Backend (buffalo_l + OnnxRuntime)")
        if_layout = QFormLayout(if_group)

        self.if_model_combo = QComboBox()
        self.if_model_combo.addItems(["buffalo_s", "buffalo_l", "antelopev2"])
        self.if_model_combo.setToolTip(
            "buffalo_s: Small, fast\n"
            "buffalo_l: Large, more accurate (recommended)\n"
            "antelopev2: Latest model"
        )
        if_layout.addRow("Model:", self.if_model_combo)

        layout.addWidget(if_group)

        # General detection settings
        detection_group = QGroupBox("Detection Parameters")
        detection_layout = QFormLayout(detection_group)

        self.min_face_spin = QSpinBox()
        self.min_face_spin.setRange(10, 200)
        self.min_face_spin.setValue(20)
        self.min_face_spin.setSuffix(" px")
        self.min_face_spin.setToolTip("Minimum face size to detect")
        detection_layout.addRow("Min face size:", self.min_face_spin)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(0.6)
        self.confidence_spin.setToolTip("Minimum confidence threshold (0.0-1.0)")
        detection_layout.addRow("Confidence threshold:", self.confidence_spin)

        # Quality filtering (ENHANCEMENT 2026-01-07)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 100)
        self.quality_spin.setValue(0)
        self.quality_spin.setSuffix("/100")
        self.quality_spin.setToolTip(
            "Minimum quality score for faces (0 = disabled)\n"
            "0 = All faces (default)\n"
            "40 = Fair quality and above\n"
            "60 = Good quality (recommended for cleaner clusters)\n"
            "80 = Excellent quality only"
        )
        detection_layout.addRow("Min quality score:", self.quality_spin)

        # Quality slider (more user-friendly)
        quality_slider = QSlider(Qt.Horizontal)
        quality_slider.setRange(0, 100)
        quality_slider.setValue(0)
        quality_slider.valueChanged.connect(self.quality_spin.setValue)
        self.quality_spin.valueChanged.connect(quality_slider.setValue)
        detection_layout.addRow("", quality_slider)

        # Quality help text
        quality_help = QLabel(
            "<b>Quality Filtering:</b> Filters out blurry, poorly lit, or low-resolution faces.<br>"
            "• <b>0 (Disabled):</b> Keep all faces (default, backward compatible)<br>"
            "• <b>40-60:</b> Remove very poor quality faces (20-30% reduction)<br>"
            "• <b>60-80:</b> Keep only good quality faces (recommended)<br>"
            "• <b>80-100:</b> Keep only excellent quality faces (strict)<br>"
            "<i>Quality based on: blur, lighting, size, aspect ratio, confidence</i>"
        )
        quality_help.setWordWrap(True)
        quality_help.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 8px; border-radius: 5px; }")
        detection_layout.addRow(quality_help)

        layout.addWidget(detection_group)

        layout.addStretch()
        return widget

    def create_clustering_tab(self) -> QWidget:
        """Create clustering settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Clustering settings
        cluster_group = QGroupBox("DBSCAN Clustering")
        cluster_layout = QFormLayout(cluster_group)

        self.clustering_enabled_check = QCheckBox("Enable clustering")
        cluster_layout.addRow("", self.clustering_enabled_check)

        self.eps_spin = QDoubleSpinBox()
        self.eps_spin.setRange(0.1, 1.0)
        self.eps_spin.setSingleStep(0.01)
        self.eps_spin.setValue(0.42)
        self.eps_spin.setDecimals(2)
        self.eps_spin.setToolTip("Distance threshold for clustering (lower = stricter)")
        cluster_layout.addRow("Epsilon (eps):", self.eps_spin)

        # Add slider for epsilon (more user-friendly)
        eps_slider = QSlider(Qt.Horizontal)
        eps_slider.setRange(10, 100)  # 0.1 to 1.0
        eps_slider.setValue(int(self.eps_spin.value() * 100))
        eps_slider.valueChanged.connect(lambda v: self.eps_spin.setValue(v / 100.0))
        self.eps_spin.valueChanged.connect(lambda v: eps_slider.setValue(int(v * 100)))
        cluster_layout.addRow("", eps_slider)

        self.min_samples_spin = QSpinBox()
        self.min_samples_spin.setRange(1, 20)
        self.min_samples_spin.setValue(3)
        self.min_samples_spin.setToolTip("Minimum faces needed to form a cluster")
        cluster_layout.addRow("Min samples:", self.min_samples_spin)

        # Help text
        help_label = QLabel(
            "<b>Clustering Tips:</b><br>"
            "• <b>Epsilon:</b> Controls how similar faces must be to cluster together.<br>"
            "  - Lower (0.3-0.4): Stricter, fewer false positives<br>"
            "  - Higher (0.5-0.6): More lenient, may group different people<br>"
            "• <b>Min samples:</b> Minimum faces to form a person cluster.<br>"
            "  - 2-3: Good for small collections<br>"
            "  - 5-10: Better for large collections"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 10px; border-radius: 5px; }")
        cluster_layout.addRow(help_label)

        layout.addWidget(cluster_group)
        layout.addStretch()
        return widget

    def create_advanced_tab(self) -> QWidget:
        """Create advanced settings tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Performance settings
        perf_group = QGroupBox("Performance")
        perf_layout = QFormLayout(perf_group)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(1, 500)
        self.batch_size_spin.setValue(50)
        self.batch_size_spin.setToolTip("Number of images to process before committing to database")
        perf_layout.addRow("Batch size:", self.batch_size_spin)

        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, 16)
        self.max_workers_spin.setValue(4)
        self.max_workers_spin.setToolTip("Maximum parallel face detection workers")
        perf_layout.addRow("Max workers:", self.max_workers_spin)

        self.skip_detected_check = QCheckBox("Skip images with existing detections")
        perf_layout.addRow("", self.skip_detected_check)

        # GPU Batch Processing (ENHANCEMENT 2026-01-07)
        self.enable_gpu_batch_check = QCheckBox("Enable GPU batch processing")
        self.enable_gpu_batch_check.setToolTip(
            "Process multiple images in parallel on GPU for 2-5x speedup\n"
            "Only effective on systems with CUDA GPU"
        )
        perf_layout.addRow("", self.enable_gpu_batch_check)

        self.gpu_batch_size_spin = QSpinBox()
        self.gpu_batch_size_spin.setRange(1, 16)
        self.gpu_batch_size_spin.setValue(4)
        self.gpu_batch_size_spin.setToolTip(
            "Number of images to process in single GPU call\n"
            "4 is optimal for most consumer GPUs (6-8GB VRAM)\n"
            "Higher = better GPU utilization but more VRAM usage"
        )
        perf_layout.addRow("GPU batch size:", self.gpu_batch_size_spin)

        self.gpu_batch_min_photos_spin = QSpinBox()
        self.gpu_batch_min_photos_spin.setRange(1, 100)
        self.gpu_batch_min_photos_spin.setValue(10)
        self.gpu_batch_min_photos_spin.setToolTip(
            "Minimum photos to enable batch processing\n"
            "Batch overhead not worth it for small jobs"
        )
        perf_layout.addRow("GPU batch threshold:", self.gpu_batch_min_photos_spin)

        layout.addWidget(perf_group)

        # UI settings
        ui_group = QGroupBox("User Interface")
        ui_layout = QFormLayout(ui_group)

        self.show_boxes_check = QCheckBox("Show face bounding boxes")
        ui_layout.addRow("", self.show_boxes_check)

        self.show_confidence_check = QCheckBox("Show detection confidence")
        ui_layout.addRow("", self.show_confidence_check)

        self.show_low_confidence_check = QCheckBox("Show low-confidence detections")
        self.show_low_confidence_check.setToolTip("Include faces with confidence below threshold in results")
        ui_layout.addRow("", self.show_low_confidence_check)

        self.thumbnail_size_spin = QSpinBox()
        self.thumbnail_size_spin.setRange(64, 256)
        self.thumbnail_size_spin.setValue(128)
        self.thumbnail_size_spin.setSuffix(" px")
        ui_layout.addRow("Thumbnail size:", self.thumbnail_size_spin)

        layout.addWidget(ui_group)

        layout.addStretch()
        return widget

    def load_settings(self):
        """Load settings from configuration."""
        # General
        self.enabled_check.setChecked(self.config.get("enabled", False))
        self.auto_cluster_check.setChecked(self.config.get("auto_cluster_after_scan", True))
        self.require_confirm_check.setChecked(self.config.get("require_confirmation", True))

        backend = self.config.get("backend", "insightface")
        self.backend_combo.setCurrentText(backend)

        self.save_crops_check.setChecked(self.config.get("save_face_crops", True))
        self.crop_size_spin.setValue(self.config.get("crop_size", 160))
        self.crop_quality_spin.setValue(self.config.get("crop_quality", 95))

        # Detection (InsightFace only)
        self.if_model_combo.setCurrentText(self.config.get("insightface_model", "buffalo_l"))
        self.min_face_spin.setValue(self.config.get("min_face_size", 20))
        self.confidence_spin.setValue(self.config.get("confidence_threshold", 0.6))
        self.quality_spin.setValue(int(self.config.get("min_quality_score", 0.0)))

        # Clustering
        self.clustering_enabled_check.setChecked(self.config.get("clustering_enabled", True))
        self.eps_spin.setValue(self.config.get("clustering_eps", 0.42))
        self.min_samples_spin.setValue(self.config.get("clustering_min_samples", 3))

        # Advanced
        self.batch_size_spin.setValue(self.config.get("batch_size", 50))
        self.max_workers_spin.setValue(self.config.get("max_workers", 4))
        self.skip_detected_check.setChecked(self.config.get("skip_detected", True))
        self.enable_gpu_batch_check.setChecked(self.config.get("enable_gpu_batch", True))
        self.gpu_batch_size_spin.setValue(self.config.get("gpu_batch_size", 4))
        self.gpu_batch_min_photos_spin.setValue(self.config.get("gpu_batch_min_photos", 10))
        self.show_boxes_check.setChecked(self.config.get("show_face_boxes", True))
        self.show_confidence_check.setChecked(self.config.get("show_confidence", False))
        self.show_low_confidence_check.setChecked(self.config.get("show_low_confidence", False))
        self.thumbnail_size_spin.setValue(self.config.get("thumbnail_size", 128))

        # Update backend status
        self.update_backend_status()

    def save_settings(self):
        """Save settings to configuration."""
        # General
        self.config.set("enabled", self.enabled_check.isChecked())
        self.config.set("auto_cluster_after_scan", self.auto_cluster_check.isChecked())
        self.config.set("require_confirmation", self.require_confirm_check.isChecked())
        self.config.set("backend", self.backend_combo.currentText())
        self.config.set("save_face_crops", self.save_crops_check.isChecked())
        self.config.set("crop_size", self.crop_size_spin.value())
        self.config.set("crop_quality", self.crop_quality_spin.value())

        # Detection (InsightFace only)
        self.config.set("insightface_model", self.if_model_combo.currentText())
        self.config.set("min_face_size", self.min_face_spin.value())
        self.config.set("confidence_threshold", self.confidence_spin.value())
        self.config.set("min_quality_score", float(self.quality_spin.value()))

        # Clustering
        self.config.set("clustering_enabled", self.clustering_enabled_check.isChecked())
        self.config.set("clustering_eps", self.eps_spin.value())
        self.config.set("clustering_min_samples", self.min_samples_spin.value())

        # Advanced
        self.config.set("batch_size", self.batch_size_spin.value())
        self.config.set("max_workers", self.max_workers_spin.value())
        self.config.set("skip_detected", self.skip_detected_check.isChecked())
        self.config.set("enable_gpu_batch", self.enable_gpu_batch_check.isChecked())
        self.config.set("gpu_batch_size", self.gpu_batch_size_spin.value())
        self.config.set("gpu_batch_min_photos", self.gpu_batch_min_photos_spin.value())
        self.config.set("show_face_boxes", self.show_boxes_check.isChecked())
        self.config.set("show_confidence", self.show_confidence_check.isChecked())
        self.config.set("show_low_confidence", self.show_low_confidence_check.isChecked())
        self.config.set("thumbnail_size", self.thumbnail_size_spin.value())

        QMessageBox.information(self, "Settings Saved", "Face detection settings have been saved successfully!")
        self.accept()

    def reset_to_defaults(self):
        """Reset all settings to defaults."""
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Are you sure you want to reset all face detection settings to defaults?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.config.reset_to_defaults()
            self.load_settings()
            QMessageBox.information(self, "Reset Complete", "Settings have been reset to defaults.")

    def on_backend_changed(self, backend: str):
        """Handle backend selection change."""
        self.update_backend_status()

    def update_backend_status(self):
        """Update backend availability status."""
        availability = FaceDetectionService.check_backend_availability()
        backend = self.backend_combo.currentText()

        if availability.get(backend, False):
            self.backend_status_label.setText("✅ Available")
            self.backend_status_label.setStyleSheet("QLabel { color: green; }")
        else:
            self.backend_status_label.setText("❌ Not installed")
            self.backend_status_label.setStyleSheet("QLabel { color: red; }")

        # Update info text
        if backend == "face_recognition":
            info = (
                "Uses dlib for face detection and recognition.<br>"
                "• <b>Pros:</b> Accurate, well-tested, 128-D embeddings<br>"
                "• <b>Cons:</b> Can be slow without GPU<br>"
                "• <b>Install:</b> pip install face-recognition"
            )
        else:  # insightface
            info = (
                "State-of-the-art face analysis using ONNX models.<br>"
                "• <b>Pros:</b> Very accurate, fast, 512-D embeddings, age/gender detection<br>"
                "• <b>Cons:</b> Larger model files<br>"
                "• <b>Install:</b> pip install insightface onnxruntime"
            )

        self.backend_info_label.setText(info)

    def test_backend(self):
        """Test the selected backend."""
        backend = self.backend_combo.currentText()

        try:
            from services.face_detection_service import create_face_detection_service

            # Create test service (InsightFace only)
            test_config = {
                "backend": backend,
                "insightface_model": self.if_model_combo.currentText(),
            }

            service = create_face_detection_service(test_config)

            if service and service.is_available():
                QMessageBox.information(
                    self,
                    "Backend Test",
                    f"✅ Backend '{backend}' is working correctly!\n\n"
                    f"Model: {self.if_model_combo.currentText()}\n"
                    f"Embedding size: 512-D (InsightFace ArcFace)"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Backend Test",
                    f"❌ Backend '{backend}' is not available.\n\n"
                    f"Please install the required libraries."
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Backend Test Failed",
                f"Failed to test backend '{backend}':\n\n{str(e)}"
            )
