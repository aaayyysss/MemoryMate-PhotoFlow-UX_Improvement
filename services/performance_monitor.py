"""
Performance Monitoring Service
Tracks and reports operation timings for face detection and clustering workflows.

Features:
- Operation timing with decorators
- Statistical summaries (avg/min/max/median)
- Performance breakdown by operation type
- Bottleneck identification
- Export to JSON for analysis

Usage:
    monitor = PerformanceMonitor("face_clustering")

    # Method 1: Context-based timing
    metric = monitor.record_operation("dbscan_clustering")
    # ... do work ...
    metric.finish()

    # Method 2: Decorator-based timing
    @monitor.time_operation("load_embeddings")
    def load_embeddings(self):
        # ... work ...
        return result

    # Generate report
    monitor.print_summary()
"""

import time
import statistics
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
import json
from pathlib import Path


@dataclass
class OperationMetric:
    """Metrics for a single operation execution."""

    operation_name: str
    start_time: float
    end_time: Optional[float] = None
    duration: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def finish(self, success: bool = True, error: Optional[str] = None):
        """Mark operation as complete and calculate duration."""
        self.end_time = time.time()
        self.duration = self.end_time - self.start_time
        self.success = success
        self.error_message = error

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "operation_name": self.operation_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "success": self.success,
            "error_message": self.error_message,
            "metadata": self.metadata
        }


class PerformanceMonitor:
    """
    Monitor and collect performance metrics for operations.

    Tracks operation timings, calculates statistics, and generates reports
    to identify bottlenecks and optimize performance.
    """

    def __init__(self, name: str = "default"):
        """
        Initialize performance monitor.

        Args:
            name: Identifier for this monitoring session
        """
        self.name = name
        self.metrics: List[OperationMetric] = []
        self.start_time = time.time()
        self.end_time: Optional[float] = None

    def record_operation(self, operation_name: str, metadata: Optional[Dict] = None) -> OperationMetric:
        """
        Start recording an operation.

        Args:
            operation_name: Name of the operation being tracked
            metadata: Optional additional data about the operation

        Returns:
            OperationMetric object to call finish() on when done

        Example:
            metric = monitor.record_operation("face_detection", {"image_count": 100})
            # ... do work ...
            metric.finish()
        """
        metric = OperationMetric(
            operation_name=operation_name,
            start_time=time.time(),
            metadata=metadata or {}
        )
        self.metrics.append(metric)
        return metric

    def time_operation(self, operation_name: str, metadata: Optional[Dict] = None):
        """
        Decorator to automatically time a function or method.

        Args:
            operation_name: Name for this operation
            metadata: Optional additional data

        Example:
            @monitor.time_operation("load_embedding")
            def load_embedding(self, path):
                # ... work ...
                return result
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                metric = self.record_operation(operation_name, metadata)
                try:
                    result = func(*args, **kwargs)
                    metric.finish(success=True)
                    return result
                except Exception as e:
                    metric.finish(success=False, error=str(e))
                    raise
            return wrapper
        return decorator

    def finish_monitoring(self):
        """Mark the entire monitoring session as complete."""
        self.end_time = time.time()

    def get_summary(self) -> Dict:
        """
        Get performance summary statistics.

        Returns:
            Dictionary with overall stats and per-operation breakdowns
        """
        if not self.metrics:
            return {
                "name": self.name,
                "total_duration": 0,
                "total_operations": 0,
                "operations": {}
            }

        # Group metrics by operation name
        by_operation: Dict[str, List[float]] = {}
        operation_metadata: Dict[str, List[Dict]] = {}
        success_counts: Dict[str, int] = {}
        error_counts: Dict[str, int] = {}

        for metric in self.metrics:
            op_name = metric.operation_name

            # Initialize dictionaries for this operation if needed
            if op_name not in by_operation:
                by_operation[op_name] = []
                operation_metadata[op_name] = []
                success_counts[op_name] = 0
                error_counts[op_name] = 0

            # Track duration
            if metric.duration is not None:
                by_operation[op_name].append(metric.duration)

            # Track metadata
            if metric.metadata:
                operation_metadata[op_name].append(metric.metadata)

            # Track success/failure
            if metric.success:
                success_counts[op_name] += 1
            else:
                error_counts[op_name] += 1

        # Calculate total duration
        total_duration = self.end_time - self.start_time if self.end_time else time.time() - self.start_time

        # Build summary
        summary = {
            "name": self.name,
            "total_duration": total_duration,
            "total_operations": len(self.metrics),
            "successful_operations": sum(success_counts.values()),
            "failed_operations": sum(error_counts.values()),
            "operations": {}
        }

        # Calculate statistics for each operation
        for op_name, durations in by_operation.items():
            if not durations:
                continue

            total_time = sum(durations)
            percentage = (total_time / total_duration * 100) if total_duration > 0 else 0

            summary["operations"][op_name] = {
                "count": len(durations),
                "total_time": total_time,
                "percentage": percentage,
                "avg_time": statistics.mean(durations),
                "min_time": min(durations),
                "max_time": max(durations),
                "median_time": statistics.median(durations),
                "std_dev": statistics.stdev(durations) if len(durations) > 1 else 0,
                "success_count": success_counts.get(op_name, 0),
                "error_count": error_counts.get(op_name, 0),
            }

        return summary

    def print_summary(self, show_metadata: bool = False):
        """
        Print formatted performance summary to console.

        Args:
            show_metadata: If True, include operation metadata in output
        """
        summary = self.get_summary()

        print(f"\n{'='*70}")
        print(f"Performance Report: {summary['name']}")
        print(f"{'='*70}")
        print(f"Total Duration: {summary['total_duration']:.2f}s")
        print(f"Total Operations: {summary['total_operations']} "
              f"({summary.get('successful_operations', 0)} succeeded, "
              f"{summary.get('failed_operations', 0)} failed)")

        if not summary["operations"]:
            print("\nNo operation metrics recorded.")
            print(f"{'='*70}\n")
            return

        print(f"\nOperation Breakdown:")
        print(f"{'-'*70}")

        # Sort by total time (descending) to show bottlenecks first
        sorted_ops = sorted(
            summary["operations"].items(),
            key=lambda x: x[1]["total_time"],
            reverse=True
        )

        for op_name, stats in sorted_ops:
            print(f"\n{op_name}:")
            print(f"  Count: {stats['count']}")
            print(f"  Total: {stats['total_time']:.2f}s ({stats['percentage']:.1f}% of total)")
            print(f"  Avg: {stats['avg_time']:.3f}s")
            print(f"  Min/Max: {stats['min_time']:.3f}s / {stats['max_time']:.3f}s")
            print(f"  Median: {stats['median_time']:.3f}s")

            if stats['std_dev'] > 0:
                print(f"  Std Dev: {stats['std_dev']:.3f}s")

            if stats.get('error_count', 0) > 0:
                print(f"  ⚠️  Errors: {stats['error_count']}")

        print(f"\n{'='*70}")

        # Identify bottleneck
        if sorted_ops:
            bottleneck_name, bottleneck_stats = sorted_ops[0]
            if bottleneck_stats['percentage'] > 50:
                print(f"\n⚠️  BOTTLENECK DETECTED:")
                print(f"   {bottleneck_name} takes {bottleneck_stats['percentage']:.1f}% of total time")
                print(f"   Consider optimizing this operation for best performance gains")
                print(f"{'='*70}")

        print()

    def save_to_file(self, filepath: str):
        """
        Save performance metrics to JSON file for later analysis.

        Args:
            filepath: Path to save JSON file

        Example:
            monitor.save_to_file("performance_report_2024-01-15.json")
        """
        summary = self.get_summary()
        summary["metrics_detail"] = [metric.to_dict() for metric in self.metrics]
        summary["timestamp"] = datetime.now().isoformat()

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w') as f:
            json.dump(summary, f, indent=2)

        print(f"[PerformanceMonitor] Saved report to {filepath}")

    def get_bottleneck(self) -> Optional[Dict]:
        """
        Identify the operation consuming the most time.

        Returns:
            Dictionary with bottleneck operation details, or None if no metrics
        """
        summary = self.get_summary()

        if not summary["operations"]:
            return None

        # Find operation with highest total time
        bottleneck_name = max(
            summary["operations"].items(),
            key=lambda x: x[1]["total_time"]
        )

        return {
            "operation": bottleneck_name[0],
            "stats": bottleneck_name[1]
        }
