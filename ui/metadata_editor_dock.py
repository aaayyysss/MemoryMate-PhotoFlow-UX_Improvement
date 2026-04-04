# ui/metadata_editor_dock.py
# Version: 1.0.0 dated 2026-02-06
"""
Metadata Editor Dock - Lightroom-style Info Panel

A right-side dock panel for editing photo metadata with:
- DB-first storage (changes go to database, not original files)
- Optional XMP sidecar export
- Non-destructive editing (original files untouched)

Best Practices (Adobe Lightroom / Capture One / Google Photos):
1. Changes stored in catalog/database
2. Optional XMP sidecar files for portability
3. "Write to file" is explicit user action, not automatic
4. Original files remain pristine

Fields:
- Title / Caption
- Keywords (Tags)
- Rating (0-5 stars)
- Flag (Pick / Reject / None)
- Date taken (display, with override option)
- Location (GPS coordinates + name, read-only or override)
- People (detected faces with names)
- File info (read-only: dimensions, size, format, camera)
"""

from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QFrame, QScrollArea,
    QGridLayout, QSpinBox, QComboBox, QGroupBox, QSizePolicy,
    QToolButton, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QFont, QPixmap
from typing import Optional, Dict, Any, List
import os
from logging_config import get_logger

logger = get_logger(__name__)


class StarRatingWidget(QWidget):
    """5-star rating widget with clickable stars."""

    ratingChanged = Signal(int)  # 0-5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._max_stars = 5
        self._buttons: List[QToolButton] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        for i in range(self._max_stars):
            btn = QToolButton()
            btn.setFixedSize(24, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QToolButton {
                    border: none;
                    background: transparent;
                    font-size: 16px;
                }
                QToolButton:hover {
                    background: rgba(255, 193, 7, 0.2);
                    border-radius: 4px;
                }
            """)
            btn.clicked.connect(lambda checked, idx=i: self._on_star_clicked(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        # Clear rating button
        btn_clear = QToolButton()
        btn_clear.setText("✕")
        btn_clear.setToolTip("Clear rating")
        btn_clear.setFixedSize(20, 20)
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet("""
            QToolButton {
                border: none;
                color: #999;
                font-size: 12px;
            }
            QToolButton:hover {
                color: #333;
            }
        """)
        btn_clear.clicked.connect(lambda: self.set_rating(0))
        layout.addWidget(btn_clear)

        layout.addStretch()
        self._update_stars()

    def _on_star_clicked(self, idx: int):
        new_rating = idx + 1
        # Toggle: clicking same star clears it
        if new_rating == self._rating:
            new_rating = 0
        self.set_rating(new_rating)

    def _update_stars(self):
        for i, btn in enumerate(self._buttons):
            if i < self._rating:
                btn.setText("★")
                btn.setStyleSheet(btn.styleSheet() + "color: #FFC107;")  # Gold
            else:
                btn.setText("☆")
                btn.setStyleSheet(btn.styleSheet() + "color: #CCC;")

    def rating(self) -> int:
        return self._rating

    def set_rating(self, rating: int):
        rating = max(0, min(5, rating))
        if rating != self._rating:
            self._rating = rating
            self._update_stars()
            self.ratingChanged.emit(rating)


class FlagWidget(QWidget):
    """Pick/Reject/None flag widget."""

    flagChanged = Signal(str)  # "pick", "reject", "none"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flag = "none"
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.btn_pick = QToolButton()
        self.btn_pick.setText("🏳️")
        self.btn_pick.setToolTip("Pick (P)")
        self.btn_pick.setCheckable(True)
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.clicked.connect(lambda: self._set_flag("pick"))

        self.btn_reject = QToolButton()
        self.btn_reject.setText("⛔")
        self.btn_reject.setToolTip("Reject (X)")
        self.btn_reject.setCheckable(True)
        self.btn_reject.setCursor(Qt.PointingHandCursor)
        self.btn_reject.clicked.connect(lambda: self._set_flag("reject"))

        for btn in [self.btn_pick, self.btn_reject]:
            btn.setFixedSize(28, 28)
            btn.setStyleSheet("""
                QToolButton {
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    background: white;
                    font-size: 14px;
                }
                QToolButton:hover {
                    background: #f5f5f5;
                }
                QToolButton:checked {
                    background: #e3f2fd;
                    border-color: #2196F3;
                }
            """)

        layout.addWidget(self.btn_pick)
        layout.addWidget(self.btn_reject)
        layout.addStretch()

    def _set_flag(self, flag: str):
        if self._flag == flag:
            # Toggle off
            flag = "none"
        self._flag = flag
        self.btn_pick.setChecked(flag == "pick")
        self.btn_reject.setChecked(flag == "reject")
        self.flagChanged.emit(flag)

    def flag(self) -> str:
        return self._flag

    def set_flag(self, flag: str):
        self._flag = flag
        self.btn_pick.setChecked(flag == "pick")
        self.btn_reject.setChecked(flag == "reject")


class MetadataEditorDock(QDockWidget):
    """
    Right-side dock for editing photo metadata.

    Signals:
        metadataChanged(photo_id, field, value) - Emitted when any field changes
        xmpExportRequested(photo_id) - Emitted when user wants to export XMP
        writeToFileRequested(photo_id) - Emitted when user wants to write to file
    """

    metadataChanged = Signal(int, str, object)  # photo_id, field_name, new_value
    xmpExportRequested = Signal(int)  # photo_id
    writeToFileRequested = Signal(int)  # photo_id

    def __init__(self, parent=None):
        super().__init__("Info", parent)
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.setMinimumWidth(280)
        self.setMaximumWidth(400)

        self._current_photo_id: Optional[int] = None
        self._current_photo_path: Optional[str] = None
        self._loading = False
        self._dirty_fields: set = set()

        # Debounce timer for text fields (title, caption, tags).
        # Saves happen 500ms after the last keystroke instead of per-character.
        from PySide6.QtCore import QTimer
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._flush_pending_save)
        self._pending_save: dict | None = None  # {"field": ..., "value": ...}

        self._setup_ui()

    def _setup_ui(self):
        """Build the dock UI."""
        # Main container with scroll
        container = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: #fafafa;
            }
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        # === Header with photo name ===
        self.lbl_filename = QLabel("No photo selected")
        self.lbl_filename.setStyleSheet("""
            font-size: 13px;
            font-weight: bold;
            color: #333;
            padding-bottom: 8px;
            border-bottom: 1px solid #e0e0e0;
        """)
        self.lbl_filename.setWordWrap(True)
        layout.addWidget(self.lbl_filename)

        # === Quick Actions ===
        actions_layout = QHBoxLayout()

        self.btn_export_xmp = QToolButton()
        self.btn_export_xmp.setText("XMP")
        self.btn_export_xmp.setToolTip("Export metadata to XMP sidecar file")
        self.btn_export_xmp.clicked.connect(self._on_export_xmp)

        self.btn_write_file = QToolButton()
        self.btn_write_file.setText("Write")
        self.btn_write_file.setToolTip("Write metadata to original file (destructive)")
        self.btn_write_file.clicked.connect(self._on_write_to_file)

        for btn in [self.btn_export_xmp, self.btn_write_file]:
            btn.setStyleSheet("""
                QToolButton {
                    padding: 4px 8px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    background: white;
                }
                QToolButton:hover {
                    background: #f0f0f0;
                }
            """)

        actions_layout.addWidget(self.btn_export_xmp)
        actions_layout.addWidget(self.btn_write_file)
        actions_layout.addStretch()
        layout.addLayout(actions_layout)

        # === Rating & Flag ===
        rating_group = QGroupBox("Rating & Flag")
        rating_group.setStyleSheet(self._group_style())
        rating_layout = QVBoxLayout(rating_group)

        # Rating
        rating_row = QHBoxLayout()
        rating_row.addWidget(QLabel("Rating:"))
        self.rating_widget = StarRatingWidget()
        self.rating_widget.ratingChanged.connect(
            lambda r: self._on_field_changed("rating", r))
        rating_row.addWidget(self.rating_widget)
        rating_layout.addLayout(rating_row)

        # Flag
        flag_row = QHBoxLayout()
        flag_row.addWidget(QLabel("Flag:"))
        self.flag_widget = FlagWidget()
        self.flag_widget.flagChanged.connect(
            lambda f: self._on_field_changed("flag", f))
        flag_row.addWidget(self.flag_widget)
        rating_layout.addLayout(flag_row)

        layout.addWidget(rating_group)

        # === Title & Caption ===
        text_group = QGroupBox("Title & Caption")
        text_group.setStyleSheet(self._group_style())
        text_layout = QVBoxLayout(text_group)

        text_layout.addWidget(QLabel("Title:"))
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("Add a title...")
        self.edit_title.editingFinished.connect(
            lambda: self._on_field_changed("title", self.edit_title.text()))
        text_layout.addWidget(self.edit_title)

        text_layout.addWidget(QLabel("Caption:"))
        self.edit_caption = QTextEdit()
        self.edit_caption.setPlaceholderText("Add a description...")
        self.edit_caption.setMaximumHeight(80)
        self.edit_caption.textChanged.connect(
            lambda: self._on_field_changed("caption", self.edit_caption.toPlainText()))
        text_layout.addWidget(self.edit_caption)

        layout.addWidget(text_group)

        # === Keywords (Tags) ===
        tags_group = QGroupBox("Keywords")
        tags_group.setStyleSheet(self._group_style())
        tags_layout = QVBoxLayout(tags_group)

        self.edit_tags = QLineEdit()
        self.edit_tags.setPlaceholderText("tag1, tag2, tag3...")
        self.edit_tags.editingFinished.connect(
            lambda: self._on_field_changed("tags", self.edit_tags.text()))
        tags_layout.addWidget(self.edit_tags)

        layout.addWidget(tags_group)

        # === Date & Location (Read-only + override) ===
        datetime_group = QGroupBox("Date & Location")
        datetime_group.setStyleSheet(self._group_style())
        datetime_layout = QGridLayout(datetime_group)

        datetime_layout.addWidget(QLabel("Date Taken:"), 0, 0)
        self.lbl_date_taken = QLabel("-")
        self.lbl_date_taken.setStyleSheet("color: #666;")
        datetime_layout.addWidget(self.lbl_date_taken, 0, 1)

        datetime_layout.addWidget(QLabel("Location:"), 1, 0)
        self.lbl_location = QLabel("-")
        self.lbl_location.setStyleSheet("color: #666;")
        self.lbl_location.setWordWrap(True)
        datetime_layout.addWidget(self.lbl_location, 1, 1)

        datetime_layout.addWidget(QLabel("GPS:"), 2, 0)
        self.lbl_gps = QLabel("-")
        self.lbl_gps.setStyleSheet("color: #888; font-size: 11px;")
        datetime_layout.addWidget(self.lbl_gps, 2, 1)

        layout.addWidget(datetime_group)

        # === People (Faces) ===
        people_group = QGroupBox("People")
        people_group.setStyleSheet(self._group_style())
        people_layout = QVBoxLayout(people_group)

        self.people_container = QWidget()
        self.people_flow = QVBoxLayout(self.people_container)
        self.people_flow.setContentsMargins(0, 0, 0, 0)
        self.people_flow.setSpacing(4)
        people_layout.addWidget(self.people_container)

        self.lbl_no_people = QLabel("No faces detected")
        self.lbl_no_people.setStyleSheet("color: #999; font-style: italic;")
        people_layout.addWidget(self.lbl_no_people)

        layout.addWidget(people_group)

        # === File Info (Read-only) ===
        file_group = QGroupBox("File Info")
        file_group.setStyleSheet(self._group_style())
        file_layout = QGridLayout(file_group)

        labels = ["Dimensions:", "File Size:", "Format:", "Camera:"]
        self.file_info_labels = {}

        for i, label in enumerate(labels):
            file_layout.addWidget(QLabel(label), i, 0)
            value_label = QLabel("-")
            value_label.setStyleSheet("color: #666;")
            value_label.setWordWrap(True)
            self.file_info_labels[label.replace(":", "").lower()] = value_label
            file_layout.addWidget(value_label, i, 1)

        layout.addWidget(file_group)

        # Spacer
        layout.addStretch()

        # Save indicator
        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setStyleSheet("color: #4CAF50; font-size: 11px;")
        self.lbl_save_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_save_status)

        scroll.setWidget(content)
        self.setWidget(scroll)

    def _group_style(self) -> str:
        return """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                margin-top: 8px;
                padding: 12px 8px 8px 8px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #555;
            }
        """

    def load_photo(self, photo_id: int, photo_path: str, metadata: Dict[str, Any] = None):
        """
        Load a photo's metadata into the editor.

        Args:
            photo_id: Database photo ID
            photo_path: File path for display
            metadata: Optional pre-loaded metadata dict
        """
        self._loading = True
        self._current_photo_id = photo_id
        self._current_photo_path = photo_path
        self._dirty_fields.clear()

        # Update filename display
        filename = os.path.basename(photo_path) if photo_path else "Unknown"
        self.lbl_filename.setText(filename)

        if metadata is None:
            metadata = self._load_metadata_from_db(photo_id)

        # Populate fields
        self.rating_widget.set_rating(metadata.get("rating", 0) or 0)
        self.flag_widget.set_flag(metadata.get("flag", "none") or "none")
        self.edit_title.setText(metadata.get("title", "") or "")
        self.edit_caption.setPlainText(metadata.get("caption", "") or "")
        self.edit_tags.setText(metadata.get("tags", "") or "")

        # Date & Location
        date_taken = metadata.get("date_taken", "-")
        self.lbl_date_taken.setText(date_taken if date_taken else "-")

        location = metadata.get("location_name", "")
        self.lbl_location.setText(location if location else "-")

        lat = metadata.get("gps_latitude")
        lon = metadata.get("gps_longitude")
        if lat is not None and lon is not None:
            self.lbl_gps.setText(f"{lat:.6f}, {lon:.6f}")
        else:
            self.lbl_gps.setText("-")

        # File info
        width = metadata.get("width")
        height = metadata.get("height")
        if width and height:
            self.file_info_labels["dimensions"].setText(f"{width} × {height}")
        else:
            self.file_info_labels["dimensions"].setText("-")

        size_kb = metadata.get("size_kb")
        if size_kb:
            if size_kb >= 1024:
                self.file_info_labels["file size"].setText(f"{size_kb/1024:.1f} MB")
            else:
                self.file_info_labels["file size"].setText(f"{size_kb:.0f} KB")
        else:
            self.file_info_labels["file size"].setText("-")

        # Format from extension
        ext = os.path.splitext(photo_path)[1].upper().replace(".", "") if photo_path else "-"
        self.file_info_labels["format"].setText(ext)

        camera = metadata.get("camera_model", "-")
        self.file_info_labels["camera"].setText(camera if camera else "-")

        # People
        self._load_people(photo_id, metadata.get("people", []))

        self._loading = False
        self.lbl_save_status.setText("")

        logger.debug(f"[MetadataEditorDock] Loaded metadata for photo {photo_id}")

    def _load_metadata_from_db(self, photo_id: int) -> Dict[str, Any]:
        """Load metadata from database."""
        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            with db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT
                        pm.path, pm.date_taken, pm.width, pm.height, pm.size_kb,
                        pm.tags, pm.gps_latitude, pm.gps_longitude, pm.location_name,
                        pm.rating, pm.flag, pm.title, pm.caption
                    FROM photo_metadata pm
                    WHERE pm.id = ?
                """, (photo_id,))
                row = cursor.fetchone()

                if row:
                    return {
                        "path": row["path"],
                        "date_taken": row["date_taken"],
                        "width": row["width"],
                        "height": row["height"],
                        "size_kb": row["size_kb"],
                        "tags": row["tags"],
                        "gps_latitude": row["gps_latitude"],
                        "gps_longitude": row["gps_longitude"],
                        "location_name": row["location_name"],
                        "rating": row["rating"],
                        "flag": row["flag"],
                        "title": row["title"],
                        "caption": row["caption"],
                    }
        except Exception as e:
            logger.warning(f"[MetadataEditorDock] Failed to load metadata: {e}")

        return {}

    def _load_people(self, photo_id: int, people: List[Dict] = None):
        """Load detected people/faces for photo."""
        # Clear existing
        while self.people_flow.count():
            item = self.people_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if people is None:
            # Load from database
            try:
                from reference_db import ReferenceDB
                db = ReferenceDB()

                with db.get_connection() as conn:
                    cursor = conn.execute("""
                        SELECT DISTINCT fc.branch_key, fc.label
                        FROM face_instances fi
                        JOIN face_clusters fc ON fi.cluster_id = fc.id
                        WHERE fi.photo_id = ?
                    """, (photo_id,))
                    people = [{"branch_key": r["branch_key"], "label": r["label"]}
                              for r in cursor.fetchall()]
            except Exception as e:
                logger.debug(f"[MetadataEditorDock] Failed to load people: {e}")
                people = []

        if people:
            self.lbl_no_people.hide()
            for person in people:
                label = person.get("label") or person.get("branch_key", "Unknown")
                person_label = QLabel(f"👤 {label}")
                person_label.setStyleSheet("""
                    padding: 4px 8px;
                    background: #e3f2fd;
                    border-radius: 4px;
                    color: #1565C0;
                """)
                self.people_flow.addWidget(person_label)
        else:
            self.lbl_no_people.show()

    def _on_field_changed(self, field: str, value):
        """Handle field value changes.

        Text fields (title, caption, tags) are debounced — the DB write
        happens 500 ms after the last keystroke.  Discrete fields (rating,
        flag) are saved immediately.
        """
        if self._loading:
            return

        if self._current_photo_id is None:
            return

        self._dirty_fields.add(field)

        if field in ("title", "caption", "tags"):
            # Debounced save — restart timer on every keystroke
            self._pending_save = {"field": field, "value": value}
            self._save_timer.start()
            self.lbl_save_status.setText("…")
        else:
            # Discrete fields — save immediately
            self._save_field_to_db(field, value)
            self.metadataChanged.emit(self._current_photo_id, field, value)
            self.lbl_save_status.setText("✓ Saved")
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(2000, lambda: self.lbl_save_status.setText(""))

    def _flush_pending_save(self):
        """Timer callback — write the last pending text-field value to DB."""
        pending = self._pending_save
        if pending is None:
            return
        self._pending_save = None
        self._save_field_to_db(pending["field"], pending["value"])
        if self._current_photo_id is not None:
            self.metadataChanged.emit(self._current_photo_id, pending["field"], pending["value"])
        self.lbl_save_status.setText("✓ Saved")
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(2000, lambda: self.lbl_save_status.setText(""))

    def _save_field_to_db(self, field: str, value):
        """Save a single field to database (DB-first approach)."""
        if self._current_photo_id is None:
            return

        try:
            from reference_db import ReferenceDB
            db = ReferenceDB()

            # Map field names to DB columns
            column_map = {
                "rating": "rating",
                "flag": "flag",
                "title": "title",
                "caption": "caption",
                "tags": "tags",
            }

            column = column_map.get(field)
            if column is None:
                logger.warning(f"[MetadataEditorDock] Unknown field: {field}")
                return

            with db.get_connection() as conn:
                # Check if columns exist (defensive)
                cursor = conn.execute("PRAGMA table_info(photo_metadata)")
                existing_cols = {row["name"] for row in cursor.fetchall()}

                if column not in existing_cols:
                    # Add column if missing
                    col_type = "INTEGER" if field == "rating" else "TEXT"
                    conn.execute(f"ALTER TABLE photo_metadata ADD COLUMN {column} {col_type}")
                    logger.info(f"[MetadataEditorDock] Added missing column: {column}")

                # Update value
                conn.execute(f"""
                    UPDATE photo_metadata
                    SET {column} = ?, updated_at = datetime('now')
                    WHERE id = ?
                """, (value, self._current_photo_id))
                conn.commit()

            logger.debug(f"[MetadataEditorDock] Saved {field}={value} for photo {self._current_photo_id}")

        except Exception as e:
            logger.error(f"[MetadataEditorDock] Failed to save {field}: {e}")

    def _on_export_xmp(self):
        """Export metadata to XMP sidecar file."""
        if self._current_photo_id is None or self._current_photo_path is None:
            return

        try:
            xmp_path = self._current_photo_path + ".xmp"
            self._write_xmp_sidecar(xmp_path)

            QMessageBox.information(
                self,
                "XMP Export",
                f"Metadata exported to:\n{xmp_path}"
            )
            self.xmpExportRequested.emit(self._current_photo_id)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Export Failed",
                f"Failed to export XMP:\n{str(e)}"
            )

    def _write_xmp_sidecar(self, xmp_path: str):
        """Write XMP sidecar file with current metadata."""
        rating = self.rating_widget.rating()
        title = self.edit_title.text()
        caption = self.edit_caption.toPlainText()
        tags = [t.strip() for t in self.edit_tags.text().split(",") if t.strip()]

        # Simple XMP template (Adobe/Lightroom compatible)
        xmp_content = f'''<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
        xmlns:xmp="http://ns.adobe.com/xap/1.0/"
        xmlns:dc="http://purl.org/dc/elements/1.1/"
        xmlns:xmpRights="http://ns.adobe.com/xap/1.0/rights/"
        xmp:Rating="{rating}">
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">{title}</rdf:li>
        </rdf:Alt>
      </dc:title>
      <dc:description>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">{caption}</rdf:li>
        </rdf:Alt>
      </dc:description>
      <dc:subject>
        <rdf:Bag>
          {"".join(f'<rdf:li>{tag}</rdf:li>' for tag in tags)}
        </rdf:Bag>
      </dc:subject>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''

        with open(xmp_path, 'w', encoding='utf-8') as f:
            f.write(xmp_content)

        logger.info(f"[MetadataEditorDock] Wrote XMP sidecar: {xmp_path}")

    def _on_write_to_file(self):
        """Write metadata to original file (destructive operation)."""
        if self._current_photo_id is None or self._current_photo_path is None:
            return

        reply = QMessageBox.warning(
            self,
            "Write to File",
            "This will modify the original file.\n\n"
            "Changes cannot be undone.\n\n"
            "Are you sure you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.writeToFileRequested.emit(self._current_photo_id)
            # Actual file writing would be handled by the parent/controller
            # to use appropriate library (piexif, exiftool, etc.)

    def clear(self):
        """Clear the editor (no photo selected)."""
        self._current_photo_id = None
        self._current_photo_path = None
        self._dirty_fields.clear()
        self._loading = True

        self.lbl_filename.setText("No photo selected")
        self.rating_widget.set_rating(0)
        self.flag_widget.set_flag("none")
        self.edit_title.clear()
        self.edit_caption.clear()
        self.edit_tags.clear()
        self.lbl_date_taken.setText("-")
        self.lbl_location.setText("-")
        self.lbl_gps.setText("-")

        for label in self.file_info_labels.values():
            label.setText("-")

        # Clear people
        while self.people_flow.count():
            item = self.people_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.lbl_no_people.show()

        self.lbl_save_status.setText("")
        self._loading = False
