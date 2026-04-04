# ui/performance_analytics_dialog.py
# Performance Analytics Dashboard
# Phase 2C: Historical Performance Tracking
# Visualizes historical performance data and insights

"""
Performance Analytics Dashboard

Displays:
- Performance statistics and trends
- Quality metrics over time
- Regression detection
- Optimization recommendations
- Recent workflow runs

Provides insights into face detection/clustering performance.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QWidget, QTextEdit, QComboBox, QScrollArea,
    QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
import logging
from typing import Optional

from services.performance_tracking_db import PerformanceTrackingDB
from services.performance_analytics import PerformanceAnalytics

logger = logging.getLogger(__name__)


class MetricCardWidget(QFrame):
    """Widget for displaying a single metric card."""

    def __init__(self, title: str, value: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background: #ffffff;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                padding: 16px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #6c757d; font-size: 12px; font-weight: bold; border: none; background: transparent;")
        layout.addWidget(title_label)

        # Value
        value_label = QLabel(value)
        value_label.setStyleSheet("color: #212529; font-size: 24px; font-weight: bold; border: none; background: transparent;")
        layout.addWidget(value_label)

        # Subtitle
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setStyleSheet("color: #6c757d; font-size: 11px; border: none; background: transparent;")
            layout.addWidget(subtitle_label)

        layout.addStretch()


class TrendIndicatorWidget(QWidget):
    """Widget for displaying trend indicator."""

    def __init__(self, trend_direction: str, change_percent: float, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Arrow
        if trend_direction == 'improving':
            arrow = "↑"
            color = "#28a745"
        elif trend_direction == 'declining':
            arrow = "↓"
            color = "#dc3545"
        else:
            arrow = "→"
            color = "#6c757d"

        arrow_label = QLabel(arrow)
        arrow_label.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold;")
        layout.addWidget(arrow_label)

        # Change percent
        change_label = QLabel(f"{abs(change_percent):.1f}%")
        change_label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
        layout.addWidget(change_label)

        layout.addStretch()


class PerformanceAnalyticsDialog(QDialog):
    """
    Performance Analytics Dashboard.

    Displays comprehensive analytics on face detection/clustering performance.
    """

    def __init__(self, parent=None, project_id: Optional[int] = None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = PerformanceTrackingDB()
        self.analytics = PerformanceAnalytics(self.db)

        self._setup_ui()
        self._load_data()

        self.setWindowTitle("Performance Analytics")
        self.resize(900, 700)

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()

        title_label = QLabel("<h2>Performance Analytics</h2>")
        title_label.setStyleSheet("color: #212529;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # Time range selector
        self.time_range_combo = QComboBox()
        self.time_range_combo.addItems(["Last 7 days", "Last 30 days", "Last 90 days", "All time"])
        self.time_range_combo.setCurrentIndex(1)  # Default: 30 days
        self.time_range_combo.currentIndexChanged.connect(self._on_time_range_changed)
        header_layout.addWidget(QLabel("Time Range:"))
        header_layout.addWidget(self.time_range_combo)

        # Refresh button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_data)
        header_layout.addWidget(refresh_btn)

        layout.addLayout(header_layout)

        # Tabs
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Overview tab
        overview_tab = self._create_overview_tab()
        tabs.addTab(overview_tab, "Overview")

        # Trends tab
        trends_tab = self._create_trends_tab()
        tabs.addTab(trends_tab, "Trends")

        # Insights tab
        insights_tab = self._create_insights_tab()
        tabs.addTab(insights_tab, "Insights")

        # History tab
        history_tab = self._create_history_tab()
        tabs.addTab(history_tab, "History")

        layout.addWidget(tabs)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _create_overview_tab(self) -> QWidget:
        """Create overview tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Metric cards
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.total_runs_card = MetricCardWidget("Total Runs", "0")
        cards_layout.addWidget(self.total_runs_card)

        self.success_rate_card = MetricCardWidget("Success Rate", "0%")
        cards_layout.addWidget(self.success_rate_card)

        self.avg_quality_card = MetricCardWidget("Avg Quality", "0/100")
        cards_layout.addWidget(self.avg_quality_card)

        self.avg_throughput_card = MetricCardWidget("Avg Throughput", "0 photos/s")
        cards_layout.addWidget(self.avg_throughput_card)

        layout.addLayout(cards_layout)

        # Statistics group
        stats_group = QGroupBox("Statistics")
        stats_layout = QVBoxLayout(stats_group)

        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(200)
        self.stats_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        stats_layout.addWidget(self.stats_text)

        layout.addWidget(stats_group)

        layout.addStretch()

        return widget

    def _create_trends_tab(self) -> QWidget:
        """Create trends tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Quality trend group
        quality_group = QGroupBox("Quality Trend")
        quality_layout = QVBoxLayout(quality_group)

        self.quality_trend_layout = QVBoxLayout()
        quality_layout.addLayout(self.quality_trend_layout)

        layout.addWidget(quality_group)

        # Throughput trend group
        throughput_group = QGroupBox("Throughput Trend")
        throughput_layout = QVBoxLayout(throughput_group)

        self.throughput_trend_layout = QVBoxLayout()
        throughput_layout.addLayout(self.throughput_trend_layout)

        layout.addWidget(throughput_group)

        layout.addStretch()

        return widget

    def _create_insights_tab(self) -> QWidget:
        """Create insights tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Regressions group
        regressions_group = QGroupBox("Regressions & Warnings")
        regressions_layout = QVBoxLayout(regressions_group)

        self.regressions_text = QTextEdit()
        self.regressions_text.setReadOnly(True)
        self.regressions_text.setMaximumHeight(250)
        regressions_layout.addWidget(self.regressions_text)

        layout.addWidget(regressions_group)

        # Recommendations group
        recommendations_group = QGroupBox("Optimization Recommendations")
        recommendations_layout = QVBoxLayout(recommendations_group)

        self.recommendations_text = QTextEdit()
        self.recommendations_text.setReadOnly(True)
        self.recommendations_text.setMaximumHeight(250)
        recommendations_layout.addWidget(self.recommendations_text)

        layout.addWidget(recommendations_group)

        layout.addStretch()

        return widget

    def _create_history_tab(self) -> QWidget:
        """Create history tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Recent runs table
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "Timestamp", "Type", "State", "Duration", "Photos", "Faces", "Quality"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout.addWidget(self.history_table)

        return widget

    def _load_data(self):
        """Load analytics data."""
        days = self._get_selected_days()

        # Get performance summary
        summary = self.analytics.get_performance_summary(self.project_id, days)

        # Update overview cards
        stats = summary['statistics']
        self.total_runs_card.findChildren(QLabel)[1].setText(str(stats['total_runs']))
        self.success_rate_card.findChildren(QLabel)[1].setText(f"{stats['success_rate']:.1%}")
        self.avg_quality_card.findChildren(QLabel)[1].setText(f"{stats['avg_quality_score']:.1f}/100")
        self.avg_throughput_card.findChildren(QLabel)[1].setText(f"{stats['avg_throughput_photos_per_second']:.2f} photos/s")

        # Update statistics text
        stats_text = f"""
Total Runs: {stats['total_runs']}
Completed: {stats['completed_runs']}
Failed: {stats['failed_runs']}
Success Rate: {stats['success_rate']:.1%}

Average Duration: {stats['avg_duration_seconds']:.1f}s
Average Throughput: {stats['avg_throughput_photos_per_second']:.2f} photos/sec

Quality Scores:
  Average: {stats['avg_quality_score']:.1f}/100
  Maximum: {stats['max_quality_score']:.1f}/100
  Minimum: {stats['min_quality_score']:.1f}/100
        """.strip()
        self.stats_text.setPlainText(stats_text)

        # Update trends
        self._update_trends(summary['trends'])

        # Update insights
        self._update_insights(summary['regressions'], summary['recommendations'])

        # Update history table
        self._update_history_table()

        logger.info(f"[PerformanceAnalyticsDialog] Loaded data for {days} days")

    def _update_trends(self, trends: dict):
        """Update trends display."""
        # Clear existing widgets
        for i in reversed(range(self.quality_trend_layout.count())):
            self.quality_trend_layout.itemAt(i).widget().setParent(None)

        for i in reversed(range(self.throughput_trend_layout.count())):
            self.throughput_trend_layout.itemAt(i).widget().setParent(None)

        # Quality trend
        if trends['quality']:
            quality_trend = trends['quality']

            trend_layout = QHBoxLayout()

            # Current value
            value_label = QLabel(f"Current: {quality_trend['current_value']:.1f}/100")
            value_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            trend_layout.addWidget(value_label)

            # Trend indicator
            trend_indicator = TrendIndicatorWidget(
                quality_trend['trend_direction'],
                quality_trend['change_percent']
            )
            trend_layout.addWidget(trend_indicator)

            trend_layout.addStretch()

            self.quality_trend_layout.addLayout(trend_layout)

            # Details
            details_label = QLabel(
                f"Average: {quality_trend['avg_value']:.1f} | "
                f"Range: {quality_trend['min_value']:.1f}-{quality_trend['max_value']:.1f} | "
                f"Data points: {quality_trend['data_points']}"
            )
            details_label.setStyleSheet("color: #6c757d; font-size: 11px;")
            self.quality_trend_layout.addWidget(details_label)
        else:
            no_data_label = QLabel("No quality data available")
            no_data_label.setStyleSheet("color: #6c757d; font-style: italic;")
            self.quality_trend_layout.addWidget(no_data_label)

        # Throughput trend
        throughput_trend = trends['throughput']

        trend_layout = QHBoxLayout()

        # Current value
        value_label = QLabel(f"Current: {throughput_trend['current_value']:.2f} photos/s")
        value_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        trend_layout.addWidget(value_label)

        # Trend indicator
        trend_indicator = TrendIndicatorWidget(
            throughput_trend['trend_direction'],
            throughput_trend['change_percent']
        )
        trend_layout.addWidget(trend_indicator)

        trend_layout.addStretch()

        self.throughput_trend_layout.addLayout(trend_layout)

        # Details
        details_label = QLabel(
            f"Average: {throughput_trend['avg_value']:.2f} | "
            f"Range: {throughput_trend['min_value']:.2f}-{throughput_trend['max_value']:.2f} | "
            f"Data points: {throughput_trend['data_points']}"
        )
        details_label.setStyleSheet("color: #6c757d; font-size: 11px;")
        self.throughput_trend_layout.addWidget(details_label)

    def _update_insights(self, regressions: list, recommendations: list):
        """Update insights display."""
        # Format regressions
        if regressions:
            regressions_html = "<h3>Detected Issues:</h3><ul>"
            for reg in regressions:
                severity_color = {
                    'high': '#dc3545',
                    'medium': '#ffc107',
                    'low': '#17a2b8'
                }.get(reg['severity'], '#6c757d')

                regressions_html += f"""
                <li style="margin-bottom: 12px;">
                    <b style="color: {severity_color};">[{reg['severity'].upper()}] {reg['title']}</b><br>
                    {reg['description']}<br>
                    <i>Recommendation: {reg['recommendation']}</i>
                </li>
                """
            regressions_html += "</ul>"
            self.regressions_text.setHtml(regressions_html)
        else:
            self.regressions_text.setHtml("<p style='color: #28a745;'><b>✓ No regressions detected</b></p><p>Performance is stable.</p>")

        # Format recommendations
        if recommendations:
            recommendations_html = "<h3>Suggestions:</h3><ul>"
            for rec in recommendations:
                impact_color = {
                    'high': '#28a745',
                    'medium': '#17a2b8',
                    'low': '#6c757d'
                }.get(rec['impact'], '#6c757d')

                recommendations_html += f"""
                <li style="margin-bottom: 12px;">
                    <b>{rec['title']}</b> <span style="color: {impact_color};">(Impact: {rec['impact']})</span><br>
                    {rec['description']}<br>
                    <i>→ {rec['recommendation']}</i>
                </li>
                """
            recommendations_html += "</ul>"
            self.recommendations_text.setHtml(recommendations_html)
        else:
            self.recommendations_text.setHtml("<p style='color: #28a745;'><b>✓ No optimization recommendations</b></p><p>Configuration appears optimal.</p>")

    def _update_history_table(self):
        """Update history table."""
        runs = self.db.get_recent_runs(self.project_id, limit=50)

        self.history_table.setRowCount(len(runs))

        for i, run in enumerate(runs):
            # Timestamp
            timestamp = run['run_timestamp'][:19].replace('T', ' ')  # Format: YYYY-MM-DD HH:MM:SS
            self.history_table.setItem(i, 0, QTableWidgetItem(timestamp))

            # Type
            self.history_table.setItem(i, 1, QTableWidgetItem(run['workflow_type']))

            # State
            state_item = QTableWidgetItem(run['workflow_state'])
            if run['workflow_state'] == 'completed':
                state_item.setForeground(QColor('#28a745'))
            elif run['workflow_state'] == 'failed':
                state_item.setForeground(QColor('#dc3545'))
            self.history_table.setItem(i, 2, state_item)

            # Duration
            duration = run['duration_seconds'] or 0
            self.history_table.setItem(i, 3, QTableWidgetItem(f"{duration:.1f}s"))

            # Photos
            self.history_table.setItem(i, 4, QTableWidgetItem(str(run['photos_processed'])))

            # Faces
            self.history_table.setItem(i, 5, QTableWidgetItem(str(run['faces_detected'])))

            # Quality
            quality = run['overall_quality_score'] or 0
            self.history_table.setItem(i, 6, QTableWidgetItem(f"{quality:.1f}/100"))

    def _get_selected_days(self) -> int:
        """Get selected time range in days."""
        time_range_map = {
            "Last 7 days": 7,
            "Last 30 days": 30,
            "Last 90 days": 90,
            "All time": 365 * 10  # 10 years
        }
        return time_range_map[self.time_range_combo.currentText()]

    def _on_time_range_changed(self):
        """Handle time range change."""
        self._load_data()


def show_performance_analytics(parent=None, project_id: Optional[int] = None):
    """
    Show performance analytics dialog.

    Args:
        parent: Parent widget
        project_id: Optional project ID filter

    Returns:
        Dialog result
    """
    dialog = PerformanceAnalyticsDialog(parent, project_id)
    return dialog.exec()
