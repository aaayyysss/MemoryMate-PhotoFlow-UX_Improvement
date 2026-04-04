"""
UX-11: Side-by-side person comparison dialog for merge review.

Shows left vs right cluster cards with representative faces, counts,
time hints, confidence scoring, and merge / reject / skip actions.
Includes auto-advance, decision log, undo toast, and badge support.
"""

import base64
import os
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QDialog, QDialogButtonBox, QFrame
)
from ui.search.review_state_badges import ReviewStateBadgeFactory


class ClusterCompareCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
            }
        """)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-weight: 600; font-size: 14px;")

        self.lbl_face = QLabel()
        self.lbl_face.setFixedSize(220, 220)
        self.lbl_face.setAlignment(Qt.AlignCenter)
        self.lbl_face.setStyleSheet(
            "border: 1px solid #e0e0e0; background: #fafafa; border-radius: 6px;"
        )

        self.lbl_label = QLabel("")
        self.lbl_label.setWordWrap(True)
        self.lbl_label.setStyleSheet("color: #202124;")

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color: #5f6368;")

        self.lbl_time = QLabel("")
        self.lbl_time.setStyleSheet("color: #5f6368; font-size: 11px;")

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #9aa0a6; font-size: 11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_face, 0, Qt.AlignCenter)
        layout.addWidget(self.lbl_label)
        layout.addWidget(self.lbl_count)
        layout.addWidget(self.lbl_time)
        layout.addWidget(self.lbl_status)

    def set_cluster_data(self, payload: dict):
        label = payload.get("label") or payload.get("id") or "Unknown"
        count = payload.get("count", 0)
        time_hint = payload.get("time_hint", "")

        is_unnamed = str(label).startswith("face_") or str(label).startswith("unnamed")
        self.lbl_label.setText(f"Identity: {label}")
        self.lbl_count.setText(f"Photos: {count}")
        self.lbl_time.setText(f"Last seen: {time_hint}" if time_hint else "")
        self.lbl_status.setText("Unnamed cluster" if is_unnamed else "Named identity")

        pix = self._load_pixmap(payload)
        if pix and not pix.isNull():
            self.lbl_face.setPixmap(
                pix.scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.lbl_face.setText("No preview")

    def _load_pixmap(self, payload: dict):
        rep_thumb = payload.get("rep_thumb_png")
        rep_path = payload.get("rep_path")

        try:
            if rep_thumb:
                data = base64.b64decode(rep_thumb) if isinstance(rep_thumb, str) else rep_thumb
                pix = QPixmap()
                pix.loadFromData(data)
                if not pix.isNull():
                    return pix
        except Exception:
            pass

        try:
            if rep_path and os.path.exists(rep_path):
                pix = QPixmap(rep_path)
                if not pix.isNull():
                    return pix
        except Exception:
            pass

        return None


def _confidence_color(score):
    """Return (bg_color, text_color) based on merge confidence score."""
    if score is None:
        return "#f1f3f4", "#5f6368"
    if score >= 0.85:
        return "#e6f4ea", "#188038"  # green — high confidence
    if score >= 0.70:
        return "#e8f0fe", "#1a73e8"  # blue — moderate
    return "#fef7e0", "#f9ab00"       # amber — review carefully


class PeopleMergeReviewDialog(QDialog):
    reviewRequested = Signal(str, str)
    mergeAccepted = Signal(str, str)
    mergeRejected = Signal(str, str)
    mergePostponed = Signal(str, str)
    undoLastMerge = Signal(str, str)  # UX-11B: undo last accepted merge

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("People Merge Review")
        self.resize(1020, 660)

        self._current_pair = None
        self._last_merged_pair = None  # UX-11B: for undo support
        self._decision_counts = {"merged": 0, "rejected": 0, "skipped": 0}

        # Title row
        self.lbl_title = QLabel("People Merge Review")
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: 600; color: #202124;")

        self.lbl_subtitle = QLabel("")
        self.lbl_subtitle.setStyleSheet("color: #5f6368; font-size: 12px;")

        # Candidate list (left pane)
        self.list_widget = QListWidget()
        self.list_widget.setMaximumWidth(300)
        self.list_widget.setStyleSheet("""
            QListWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #f1f3f4;
            }
            QListWidget::item:selected {
                background: #e8f0fe;
            }
        """)

        # Confidence + rationale labels
        self.lbl_confidence = QLabel("")
        self.lbl_confidence.setAlignment(Qt.AlignCenter)
        self.lbl_confidence.setStyleSheet("font-size: 13px; padding: 4px;")

        self.lbl_rationale = QLabel("")
        self.lbl_rationale.setAlignment(Qt.AlignCenter)
        self.lbl_rationale.setWordWrap(True)
        self.lbl_rationale.setStyleSheet("color: #5f6368; font-size: 11px; padding: 4px;")

        # Comparison cards (right pane)
        self.left_card = ClusterCompareCard("Left Cluster")
        self.right_card = ClusterCompareCard("Right Cluster")

        compare_row = QHBoxLayout()
        compare_row.addWidget(self.left_card, 1)
        compare_row.addWidget(self.right_card, 1)

        # Action buttons — Merge (primary), Not Same (warning), Skip (neutral)
        self.btn_merge = QPushButton("Merge")
        self.btn_merge.setStyleSheet("""
            QPushButton {
                background: #1a73e8; color: white;
                border: none; border-radius: 6px;
                padding: 8px 20px; font-weight: 600;
            }
            QPushButton:hover { background: #1967d2; }
        """)

        self.btn_reject = QPushButton("Not Same")
        self.btn_reject.setStyleSheet("""
            QPushButton {
                background: #fff3e0; color: #e65100;
                border: 1px solid #f9ab00; border-radius: 6px;
                padding: 8px 20px;
            }
            QPushButton:hover { background: #fce8e6; }
        """)

        self.btn_skip = QPushButton("Skip")
        self.btn_skip.setStyleSheet("""
            QPushButton {
                background: #f1f3f4; color: #5f6368;
                border: none; border-radius: 6px;
                padding: 8px 20px;
            }
            QPushButton:hover { background: #e0e0e0; }
        """)

        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.btn_merge)
        actions.addWidget(self.btn_reject)
        actions.addWidget(self.btn_skip)

        # Decision log
        self.lbl_decisions = QLabel("")
        self.lbl_decisions.setStyleSheet("color: #5f6368; font-size: 11px; padding: 4px 0;")
        self._update_decision_log()

        # UX-11B: Undo toast
        self.undo_toast = QFrame(self)
        self.undo_toast.setStyleSheet("""
            QFrame {
                background: #323232; border-radius: 6px;
                padding: 8px 12px;
            }
        """)
        undo_layout = QHBoxLayout(self.undo_toast)
        undo_layout.setContentsMargins(12, 6, 12, 6)
        self.undo_toast_label = QLabel("")
        self.undo_toast_label.setStyleSheet("color: white; font-size: 12px;")
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setStyleSheet("""
            QPushButton {
                background: transparent; color: #8ab4f8;
                border: none; font-weight: 600; font-size: 12px;
            }
            QPushButton:hover { color: #aecbfa; }
        """)
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        undo_layout.addWidget(self.undo_toast_label, 1)
        undo_layout.addWidget(self.btn_undo)
        self.undo_toast.hide()

        # Layout assembly
        right_pane = QVBoxLayout()
        right_pane.addWidget(self.lbl_confidence)
        right_pane.addLayout(compare_row, 1)
        right_pane.addWidget(self.lbl_rationale)
        right_pane.addLayout(actions)

        main_row = QHBoxLayout()
        main_row.addWidget(self.list_widget, 0)
        main_row.addLayout(right_pane, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)

        header = QHBoxLayout()
        header.addWidget(self.lbl_title)
        header.addStretch()
        header.addWidget(self.lbl_subtitle)

        outer = QVBoxLayout(self)
        outer.addLayout(header)
        outer.addLayout(main_row, 1)
        outer.addWidget(self.lbl_decisions)
        outer.addWidget(self.undo_toast)
        outer.addWidget(buttons)

        # Signal wiring
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.btn_merge.clicked.connect(self._emit_merge)
        self.btn_reject.clicked.connect(self._emit_reject)
        self.btn_skip.clicked.connect(self._emit_skip)

    # ── UX-11 service-driven mode ──────────────────────────────────

    def set_services(self, people_review_service, identity_resolution_service):
        """UX-11: Inject services for service-driven merge review."""
        self._people_review_service = people_review_service
        self._identity_resolution_service = identity_resolution_service

    def reload_queue(self):
        """UX-11: Reload the candidate list directly from PeopleReviewService."""
        svc = getattr(self, '_people_review_service', None)
        if not svc:
            return

        queue_items = svc.get_merge_review_queue(include_reviewed=False, limit=None)
        self._queue_items = queue_items

        # Populate list directly from service queue items — no legacy conversion
        self.list_widget.clear()
        self.lbl_subtitle.setText(f"Possible merges: {len(queue_items)}")

        for item in queue_items:
            left_id = item.get("cluster_a_id", "")
            right_id = item.get("cluster_b_id", "")
            score = item.get("confidence_score")
            left_label = item.get("left_label", left_id)
            right_label = item.get("right_label", right_id)

            score_txt = f"{score:.2f}" if isinstance(score, (float, int)) else "?"
            label = f"{left_label} \u2194 {right_label}\nscore {score_txt}"

            list_item = QListWidgetItem(label)
            list_item.setData(256, (left_id, right_id))
            list_item.setData(257, item)  # store raw queue item

            bg_color, text_color = _confidence_color(score)
            list_item.setBackground(QColor(bg_color))
            list_item.setForeground(QColor(text_color))

            self.list_widget.addItem(list_item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _find_candidate_id_for_pair(self, left_id, right_id):
        """UX-11: Find candidate_id for a left/right pair from queue items."""
        for item in getattr(self, '_queue_items', []):
            if (item.get("cluster_a_id") == left_id and item.get("cluster_b_id") == right_id):
                return item.get("candidate_id")
            if (item.get("cluster_a_id") == right_id and item.get("cluster_b_id") == left_id):
                return item.get("candidate_id")
        return None

    def refresh_from_event(self, payload=None):
        """UX-11: Refresh from service event, preserving current selection."""
        svc = getattr(self, '_people_review_service', None)
        if not svc:
            return
        saved_pair = self._current_pair
        self.reload_queue()
        # Try to restore selection
        if saved_pair and self.list_widget.count() > 0:
            for i in range(self.list_widget.count()):
                item = self.list_widget.item(i)
                pair = item.data(256)
                if pair == saved_pair:
                    self.list_widget.setCurrentRow(i)
                    return

    def set_suggestions(self, suggestions):
        self.list_widget.clear()
        suggestions = list(suggestions or [])

        self.lbl_subtitle.setText(f"Possible merges: {len(suggestions)}")

        for item in suggestions:
            left_id = str(item.get("left_id", ""))
            right_id = str(item.get("right_id", ""))
            score = item.get("score")
            left_count = item.get("left_count", "?")
            right_count = item.get("right_count", "?")

            score_txt = f"{score:.2f}" if isinstance(score, (float, int)) else "?"
            label = f"{left_id} \u2194 {right_id}\nscore {score_txt}  |  {left_count} vs {right_count}"

            list_item = QListWidgetItem(label)
            list_item.setData(256, (left_id, right_id))
            list_item.setData(257, item)  # store full suggestion data

            # Confidence color coding
            bg_color, text_color = _confidence_color(score)
            list_item.setBackground(QColor(bg_color))
            list_item.setForeground(QColor(text_color))

            self.list_widget.addItem(list_item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def set_comparison_payload(self, payload: dict):
        left = payload.get("left", {})
        right = payload.get("right", {})
        self.left_card.set_cluster_data(left)
        self.right_card.set_cluster_data(right)

    def _on_selection_changed(self, current, previous):
        if not current:
            self._current_pair = None
            return

        left_id, right_id = current.data(256)
        self._current_pair = (left_id, right_id)

        # Show confidence info from stored item data (works with both queue items and suggestions)
        item_data = current.data(257)
        if isinstance(item_data, dict):
            # Support both "score" (legacy) and "confidence_score" (service) keys
            score = item_data.get("confidence_score") or item_data.get("score")
            if isinstance(score, (float, int)):
                _, text_color = _confidence_color(score)
                confidence_text = f"Merge confidence: {score:.2f}"
                if score >= 0.85:
                    confidence_text += " (high)"
                elif score >= 0.70:
                    confidence_text += " (moderate)"
                else:
                    confidence_text += " (review carefully)"
                self.lbl_confidence.setText(confidence_text)
                self.lbl_confidence.setStyleSheet(
                    f"font-size: 13px; padding: 4px; color: {text_color}; font-weight: 600;"
                )
            else:
                self.lbl_confidence.setText("")

            rationale = item_data.get("rationale", {})
            if isinstance(rationale, dict) and rationale:
                parts = [f"{k}: {v}" for k, v in rationale.items()]
                self.lbl_rationale.setText(" | ".join(parts))
            else:
                self.lbl_rationale.setText("")
        else:
            self.lbl_confidence.setText("")
            self.lbl_rationale.setText("")

        # Load comparison payload from service if available, otherwise fall back to signal
        svc = getattr(self, '_people_review_service', None)
        if svc:
            try:
                compare = svc.get_merge_compare_payload(left_id, right_id)
                if compare:
                    self.set_comparison_payload(compare)
                    return
            except Exception:
                pass
        self.reviewRequested.emit(left_id, right_id)

    def _advance_to_next(self):
        """Auto-advance to next pair after action."""
        row = self.list_widget.currentRow()
        self.list_widget.takeItem(row)
        self.lbl_subtitle.setText(f"Possible merges: {self.list_widget.count()}")
        if self.list_widget.count() > 0:
            next_row = min(row, self.list_widget.count() - 1)
            self.list_widget.setCurrentRow(next_row)
        else:
            self._current_pair = None
            self.lbl_confidence.setText("All pairs reviewed!")
            self.lbl_rationale.setText("")

    def _update_decision_log(self):
        c = self._decision_counts
        self.lbl_decisions.setText(
            f"Decisions: merged {c['merged']}, rejected {c['rejected']}, "
            f"skipped {c['skipped']}"
        )

    def _emit_merge(self):
        if self._current_pair:
            svc = getattr(self, '_identity_resolution_service', None)
            candidate_id = self._find_candidate_id_for_pair(*self._current_pair) if svc else None

            if svc and candidate_id:
                try:
                    result = svc.accept_merge_candidate(
                        candidate_id=candidate_id,
                        reviewed_by="user",
                    )
                    self._last_merge_identity_id = result.get("identity_id")
                except Exception as e:
                    print(f"[UX-11] Service accept failed, falling back to signal: {e}")
                    svc = None

            self._decision_counts["merged"] += 1
            self._last_merged_pair = self._current_pair
            self._update_decision_log()

            if not (svc and candidate_id):
                self.mergeAccepted.emit(*self._current_pair)

            self._show_undo_toast(
                f"Merged {self._current_pair[0]} and {self._current_pair[1]}"
            )
            self._advance_to_next()

    def _emit_reject(self):
        if self._current_pair:
            svc = getattr(self, '_identity_resolution_service', None)
            candidate_id = self._find_candidate_id_for_pair(*self._current_pair) if svc else None

            if svc and candidate_id:
                try:
                    svc.reject_merge_candidate(
                        candidate_id=candidate_id,
                        reviewed_by="user",
                    )
                except Exception as e:
                    print(f"[UX-11] Service reject failed, falling back to signal: {e}")
                    svc = None

            self._decision_counts["rejected"] += 1
            self._update_decision_log()

            if not (svc and candidate_id):
                self.mergeRejected.emit(*self._current_pair)

            self._advance_to_next()

    def _emit_skip(self):
        if self._current_pair:
            svc = getattr(self, '_identity_resolution_service', None)
            candidate_id = self._find_candidate_id_for_pair(*self._current_pair) if svc else None

            if svc and candidate_id:
                try:
                    svc.skip_merge_candidate(
                        candidate_id=candidate_id,
                        reviewed_by="user",
                    )
                except Exception as e:
                    print(f"[UX-11] Service skip failed, falling back to signal: {e}")
                    svc = None

            self._decision_counts["skipped"] += 1
            self._update_decision_log()

            if not (svc and candidate_id):
                self.mergePostponed.emit(*self._current_pair)

            self._advance_to_next()

    # UX-11B: Undo toast support

    def _show_undo_toast(self, message: str):
        """Show a temporary undo toast for 5 seconds."""
        self.undo_toast_label.setText(message)
        self.undo_toast.show()
        QTimer.singleShot(5000, self._hide_undo_toast)

    def _hide_undo_toast(self):
        self.undo_toast.hide()
        self._last_merged_pair = None

    def _on_undo_clicked(self):
        """Emit undo signal for the last merged pair. Uses service if available."""
        if self._last_merged_pair:
            left_id, right_id = self._last_merged_pair

            # UX-11: Try service-driven undo first
            svc = getattr(self, '_identity_resolution_service', None)
            identity_id = getattr(self, '_last_merge_identity_id', None)
            if svc and identity_id:
                try:
                    svc.reverse_last_merge_for_identity(
                        identity_id=identity_id,
                        performed_by="user",
                    )
                except Exception as e:
                    print(f"[UX-11] Service undo failed, falling back to signal: {e}")
                    self.undoLastMerge.emit(left_id, right_id)
            else:
                self.undoLastMerge.emit(left_id, right_id)

            self._decision_counts["merged"] = max(0, self._decision_counts["merged"] - 1)
            self._update_decision_log()
            self._hide_undo_toast()
