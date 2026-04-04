# services/performance_analytics.py
# Performance Analytics Service
# Phase 2C: Historical Performance Tracking
# Analyzes historical performance data and provides insights

"""
Performance Analytics Service

Provides analytics and insights on historical performance data:
- Trend analysis (quality, throughput over time)
- Performance regression detection
- Configuration impact analysis
- Recommendations for optimization
- Statistical summaries

Uses data from PerformanceTrackingDB to generate actionable insights.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
import statistics

from services.performance_tracking_db import PerformanceTrackingDB

logger = logging.getLogger(__name__)


@dataclass
class TrendAnalysis:
    """
    Trend analysis results.

    Attributes:
        metric_name: Name of metric being analyzed
        trend_direction: 'improving', 'declining', or 'stable'
        trend_strength: 0-1 (how strong the trend is)
        current_value: Most recent value
        avg_value: Average value over period
        min_value: Minimum value
        max_value: Maximum value
        change_percent: Percent change from first to last
        data_points: Number of data points analyzed
    """
    metric_name: str
    trend_direction: str
    trend_strength: float
    current_value: float
    avg_value: float
    min_value: float
    max_value: float
    change_percent: float
    data_points: int


@dataclass
class PerformanceInsight:
    """
    Performance insight or recommendation.

    Attributes:
        insight_type: Type ('improvement', 'regression', 'optimization', 'warning')
        severity: 'low', 'medium', 'high'
        title: Short title
        description: Detailed description
        recommendation: Recommended action
        impact: Estimated impact ('low', 'medium', 'high')
        related_metrics: Related metric names
    """
    insight_type: str
    severity: str
    title: str
    description: str
    recommendation: str
    impact: str
    related_metrics: List[str]


class PerformanceAnalytics:
    """
    Analytics service for performance data.

    Provides:
    - Trend analysis for quality/performance metrics
    - Regression detection
    - Configuration impact analysis
    - Optimization recommendations
    - Statistical summaries
    """

    def __init__(self, db: Optional[PerformanceTrackingDB] = None):
        """
        Initialize analytics service.

        Args:
            db: Optional PerformanceTrackingDB instance
        """
        self.db = db or PerformanceTrackingDB()
        logger.info("[PerformanceAnalytics] Initialized")

    def analyze_quality_trend(self,
                             project_id: int,
                             days: int = 30) -> TrendAnalysis:
        """
        Analyze quality score trend over time.

        Args:
            project_id: Project ID
            days: Number of days to analyze

        Returns:
            TrendAnalysis for quality scores
        """
        quality_history = self.db.get_quality_trend(project_id, days)

        if not quality_history:
            return TrendAnalysis(
                metric_name="Overall Quality",
                trend_direction="stable",
                trend_strength=0.0,
                current_value=0.0,
                avg_value=0.0,
                min_value=0.0,
                max_value=0.0,
                change_percent=0.0,
                data_points=0
            )

        # Extract quality scores
        scores = [h['overall_quality'] for h in quality_history]

        # Calculate statistics
        current_value = scores[-1]
        avg_value = statistics.mean(scores)
        min_value = min(scores)
        max_value = max(scores)

        # Calculate trend
        trend_direction, trend_strength = self._calculate_trend(scores)

        # Calculate change percent
        change_percent = 0.0
        if len(scores) >= 2 and scores[0] != 0:
            change_percent = ((scores[-1] - scores[0]) / scores[0]) * 100

        return TrendAnalysis(
            metric_name="Overall Quality",
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            current_value=current_value,
            avg_value=avg_value,
            min_value=min_value,
            max_value=max_value,
            change_percent=change_percent,
            data_points=len(scores)
        )

    def analyze_throughput_trend(self,
                                project_id: Optional[int] = None,
                                days: int = 30) -> TrendAnalysis:
        """
        Analyze processing throughput trend.

        Args:
            project_id: Optional project filter
            days: Number of days to analyze

        Returns:
            TrendAnalysis for throughput
        """
        runs = self.db.get_recent_runs(project_id, limit=100)

        # Filter by date range
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        runs = [r for r in runs if r['run_timestamp'] >= cutoff_date]

        if not runs:
            return TrendAnalysis(
                metric_name="Throughput (photos/sec)",
                trend_direction="stable",
                trend_strength=0.0,
                current_value=0.0,
                avg_value=0.0,
                min_value=0.0,
                max_value=0.0,
                change_percent=0.0,
                data_points=0
            )

        # Extract throughput values
        throughputs = [r['photos_per_second'] for r in runs if r['photos_per_second'] > 0]

        if not throughputs:
            return TrendAnalysis(
                metric_name="Throughput (photos/sec)",
                trend_direction="stable",
                trend_strength=0.0,
                current_value=0.0,
                avg_value=0.0,
                min_value=0.0,
                max_value=0.0,
                change_percent=0.0,
                data_points=0
            )

        # Calculate statistics
        current_value = throughputs[-1]
        avg_value = statistics.mean(throughputs)
        min_value = min(throughputs)
        max_value = max(throughputs)

        # Calculate trend
        trend_direction, trend_strength = self._calculate_trend(throughputs)

        # Calculate change percent
        change_percent = 0.0
        if len(throughputs) >= 2 and throughputs[0] != 0:
            change_percent = ((throughputs[-1] - throughputs[0]) / throughputs[0]) * 100

        return TrendAnalysis(
            metric_name="Throughput (photos/sec)",
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            current_value=current_value,
            avg_value=avg_value,
            min_value=min_value,
            max_value=max_value,
            change_percent=change_percent,
            data_points=len(throughputs)
        )

    def detect_regressions(self,
                          project_id: Optional[int] = None,
                          days: int = 7) -> List[PerformanceInsight]:
        """
        Detect performance regressions.

        Compares recent performance to historical baseline.

        Args:
            project_id: Optional project filter
            days: Number of days to compare

        Returns:
            List of regression insights
        """
        insights = []

        # Get performance stats for recent and historical periods
        recent_stats = self.db.get_performance_stats(project_id, days=days)
        historical_stats = self.db.get_performance_stats(project_id, days=30)

        # Check quality regression
        recent_quality = recent_stats['avg_quality_score']
        historical_quality = historical_stats['avg_quality_score']

        if historical_quality > 0:
            quality_change = ((recent_quality - historical_quality) / historical_quality) * 100

            if quality_change < -10:  # 10% drop
                insights.append(PerformanceInsight(
                    insight_type='regression',
                    severity='high' if quality_change < -20 else 'medium',
                    title='Quality Regression Detected',
                    description=f"Quality dropped {abs(quality_change):.1f}% in last {days} days "
                               f"(from {historical_quality:.1f} to {recent_quality:.1f}).",
                    recommendation="Review recent configuration changes or data quality. "
                                 "Consider reverting to previous quality thresholds.",
                    impact='high',
                    related_metrics=['overall_quality_score']
                ))

        # Check throughput regression
        recent_throughput = recent_stats['avg_throughput_photos_per_second']
        historical_throughput = historical_stats['avg_throughput_photos_per_second']

        if historical_throughput > 0:
            throughput_change = ((recent_throughput - historical_throughput) / historical_throughput) * 100

            if throughput_change < -20:  # 20% drop
                insights.append(PerformanceInsight(
                    insight_type='regression',
                    severity='medium',
                    title='Throughput Regression Detected',
                    description=f"Processing speed dropped {abs(throughput_change):.1f}% in last {days} days "
                               f"(from {historical_throughput:.2f} to {recent_throughput:.2f} photos/sec).",
                    recommendation="Check system resources (CPU, memory). "
                                 "Consider optimizing batch sizes or worker count.",
                    impact='medium',
                    related_metrics=['photos_per_second']
                ))

        # Check failure rate
        recent_success_rate = recent_stats['success_rate']
        historical_success_rate = historical_stats['success_rate']

        if recent_success_rate < historical_success_rate - 0.1:  # 10% drop
            insights.append(PerformanceInsight(
                insight_type='warning',
                severity='high',
                title='Increased Failure Rate',
                description=f"Success rate dropped from {historical_success_rate:.1%} to {recent_success_rate:.1%}.",
                recommendation="Investigate error logs for patterns. "
                             "Check for data quality issues or configuration problems.",
                impact='high',
                related_metrics=['success_rate']
            ))

        return insights

    def generate_optimization_recommendations(self,
                                             project_id: int) -> List[PerformanceInsight]:
        """
        Generate optimization recommendations based on historical data.

        Args:
            project_id: Project ID

        Returns:
            List of optimization insights
        """
        insights = []

        # Get recent runs
        runs = self.db.get_recent_runs(project_id, limit=20)
        if not runs:
            return insights

        # Analyze quality distribution
        quality_scores = [r['overall_quality_score'] for r in runs if r['overall_quality_score'] > 0]
        if quality_scores:
            avg_quality = statistics.mean(quality_scores)

            if avg_quality < 60:
                insights.append(PerformanceInsight(
                    insight_type='optimization',
                    severity='medium',
                    title='Low Average Quality Score',
                    description=f"Average quality score is {avg_quality:.1f}/100, below recommended threshold.",
                    recommendation="Review and adjust quality thresholds. "
                                 "Consider lowering blur/lighting thresholds or improving source image quality.",
                    impact='high',
                    related_metrics=['overall_quality_score']
                ))

        # Analyze noise ratio
        noise_ratios = [r['noise_ratio'] for r in runs if r['noise_ratio'] is not None and r['noise_ratio'] > 0]
        if noise_ratios:
            avg_noise = statistics.mean(noise_ratios)

            if avg_noise > 0.3:  # > 30% noise
                insights.append(PerformanceInsight(
                    insight_type='optimization',
                    severity='medium',
                    title='High Noise Ratio',
                    description=f"Average noise ratio is {avg_noise:.1%}, indicating many unassigned faces.",
                    recommendation="Consider decreasing clustering epsilon (eps) or min_samples "
                                 "to include more faces in clusters.",
                    impact='medium',
                    related_metrics=['noise_ratio']
                ))
            elif avg_noise < 0.05:  # < 5% noise with many clusters
                avg_clusters = statistics.mean([r['clusters_found'] for r in runs if r['clusters_found'] > 0])
                if avg_clusters > 50:
                    insights.append(PerformanceInsight(
                        insight_type='optimization',
                        severity='low',
                        title='Possible Over-Clustering',
                        description=f"Very low noise ratio ({avg_noise:.1%}) with many clusters ({avg_clusters:.0f}).",
                        recommendation="Consider increasing clustering epsilon (eps) "
                                     "to merge similar clusters.",
                        impact='medium',
                        related_metrics=['noise_ratio', 'clusters_found']
                    ))

        # Analyze throughput
        throughputs = [r['photos_per_second'] for r in runs if r['photos_per_second'] > 0]
        if throughputs and len(throughputs) >= 5:
            avg_throughput = statistics.mean(throughputs)
            stdev_throughput = statistics.stdev(throughputs)

            if stdev_throughput / avg_throughput > 0.5:  # High variability
                insights.append(PerformanceInsight(
                    insight_type='optimization',
                    severity='low',
                    title='Inconsistent Processing Speed',
                    description=f"Throughput varies significantly ({avg_throughput:.2f} ± {stdev_throughput:.2f} photos/sec).",
                    recommendation="Check for background processes or resource contention. "
                                 "Consider consistent batch sizes for stable performance.",
                    impact='low',
                    related_metrics=['photos_per_second']
                ))

        return insights

    def compare_configurations(self,
                              run_id_1: int,
                              run_id_2: int) -> Dict[str, Any]:
        """
        Compare two workflow runs to assess configuration impact.

        Args:
            run_id_1: First run ID
            run_id_2: Second run ID

        Returns:
            Comparison dictionary
        """
        runs = self.db.get_recent_runs(limit=1000)  # Get all recent runs
        run1 = next((r for r in runs if r['id'] == run_id_1), None)
        run2 = next((r for r in runs if r['id'] == run_id_2), None)

        if not run1 or not run2:
            return {'error': 'One or both runs not found'}

        comparison = {
            'run_1': {
                'id': run_id_1,
                'timestamp': run1['run_timestamp'],
                'quality': run1['overall_quality_score'],
                'throughput': run1['photos_per_second'],
                'duration': run1['duration_seconds'],
                'clusters': run1['clusters_found']
            },
            'run_2': {
                'id': run_id_2,
                'timestamp': run2['run_timestamp'],
                'quality': run2['overall_quality_score'],
                'throughput': run2['photos_per_second'],
                'duration': run2['duration_seconds'],
                'clusters': run2['clusters_found']
            },
            'differences': {
                'quality_change': run2['overall_quality_score'] - run1['overall_quality_score'],
                'throughput_change': run2['photos_per_second'] - run1['photos_per_second'],
                'duration_change': run2['duration_seconds'] - run1['duration_seconds'],
                'cluster_change': run2['clusters_found'] - run1['clusters_found']
            }
        }

        return comparison

    def get_performance_summary(self,
                               project_id: Optional[int] = None,
                               days: int = 30) -> Dict[str, Any]:
        """
        Get comprehensive performance summary.

        Args:
            project_id: Optional project filter
            days: Number of days to analyze

        Returns:
            Summary dictionary with stats, trends, and insights
        """
        # Get base statistics
        stats = self.db.get_performance_stats(project_id, days)

        # Get trend analyses
        quality_trend = self.analyze_quality_trend(project_id, days) if project_id else None
        throughput_trend = self.analyze_throughput_trend(project_id, days)

        # Detect regressions
        regressions = self.detect_regressions(project_id, days=7)

        # Generate recommendations
        recommendations = self.generate_optimization_recommendations(project_id) if project_id else []

        summary = {
            'period_days': days,
            'statistics': stats,
            'trends': {
                'quality': quality_trend.__dict__ if quality_trend else None,
                'throughput': throughput_trend.__dict__
            },
            'regressions': [r.__dict__ for r in regressions],
            'recommendations': [r.__dict__ for r in recommendations],
            'health_score': self._calculate_health_score(stats, regressions)
        }

        return summary

    # ========== Private Methods ==========

    def _calculate_trend(self, values: List[float]) -> Tuple[str, float]:
        """
        Calculate trend direction and strength.

        Args:
            values: List of values over time

        Returns:
            (trend_direction, trend_strength) tuple
        """
        if len(values) < 2:
            return 'stable', 0.0

        # Simple linear regression
        n = len(values)
        x = list(range(n))
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(values)

        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 'stable', 0.0

        slope = numerator / denominator

        # Determine direction
        if abs(slope) < 0.01:  # Threshold for "stable"
            direction = 'stable'
        elif slope > 0:
            direction = 'improving'
        else:
            direction = 'declining'

        # Calculate strength (normalized)
        value_range = max(values) - min(values)
        if value_range == 0:
            strength = 0.0
        else:
            strength = min(abs(slope) / value_range, 1.0)

        return direction, strength

    def _calculate_health_score(self,
                                stats: Dict[str, Any],
                                regressions: List[PerformanceInsight]) -> float:
        """
        Calculate overall health score (0-100).

        Args:
            stats: Performance statistics
            regressions: List of regressions

        Returns:
            Health score 0-100
        """
        score = 100.0

        # Penalize for low success rate
        success_rate = stats['success_rate']
        if success_rate < 0.95:
            score -= (0.95 - success_rate) * 100  # Max -95 points

        # Penalize for low quality
        avg_quality = stats['avg_quality_score']
        if avg_quality < 60:
            score -= (60 - avg_quality) * 0.5  # Max -30 points

        # Penalize for regressions
        for regression in regressions:
            if regression.severity == 'high':
                score -= 15
            elif regression.severity == 'medium':
                score -= 10
            else:
                score -= 5

        return max(0.0, min(100.0, score))
