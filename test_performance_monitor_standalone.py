#!/usr/bin/env python3
"""
Standalone test for PerformanceMonitor (no external dependencies).
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


class PerformanceMonitor:
    """Monitor and collect performance metrics for operations."""

    def __init__(self, name: str = "default"):
        self.name = name
        self.metrics: List[OperationMetric] = []
        self.start_time = time.time()
        self.end_time: Optional[float] = None

    def record_operation(self, operation_name: str, metadata: Optional[Dict] = None) -> OperationMetric:
        """Start recording an operation."""
        metric = OperationMetric(
            operation_name=operation_name,
            start_time=time.time(),
            metadata=metadata or {}
        )
        self.metrics.append(metric)
        return metric

    def finish_monitoring(self):
        """Mark the entire monitoring session as complete."""
        self.end_time = time.time()

    def get_summary(self) -> Dict:
        """Get performance summary statistics."""
        if not self.metrics:
            return {
                "name": self.name,
                "total_duration": 0,
                "total_operations": 0,
                "operations": {}
            }

        # Group metrics by operation name
        by_operation: Dict[str, List[float]] = {}
        success_counts: Dict[str, int] = {}
        error_counts: Dict[str, int] = {}

        for metric in self.metrics:
            op_name = metric.operation_name

            if op_name not in by_operation:
                by_operation[op_name] = []
                success_counts[op_name] = 0
                error_counts[op_name] = 0

            if metric.duration is not None:
                by_operation[op_name].append(metric.duration)

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


def main():
    """Test PerformanceMonitor standalone."""
    print("=" * 70)
    print("PerformanceMonitor Standalone Test")
    print("=" * 70)

    # Create monitor
    monitor = PerformanceMonitor("test_workflow")
    print("✅ PerformanceMonitor created")

    # Test operation 1: Loading data
    metric1 = monitor.record_operation("load_data", {"items": 100})
    time.sleep(0.1)  # Simulate work
    metric1.finish()
    print("✅ Operation 1 recorded: load_data (0.1s)")

    # Test operation 2: Processing
    metric2 = monitor.record_operation("process_data", {"algorithm": "dbscan"})
    time.sleep(0.2)  # Simulate work
    metric2.finish()
    print("✅ Operation 2 recorded: process_data (0.2s)")

    # Test operation 3: Saving (multiple times)
    for i in range(3):
        metric = monitor.record_operation("save_batch", {"batch": i})
        time.sleep(0.05)  # Simulate work
        metric.finish()
    print("✅ Operation 3 recorded: save_batch x3 (0.05s each)")

    # Test operation 4: Error case
    metric4 = monitor.record_operation("error_operation")
    time.sleep(0.01)
    metric4.finish(success=False, error="Test error")
    print("✅ Operation 4 recorded: error_operation (failed)")

    # Finish monitoring
    monitor.finish_monitoring()
    print("✅ Monitoring finished")

    # Get summary
    summary = monitor.get_summary()
    print("\n" + "=" * 70)
    print("Summary Statistics")
    print("=" * 70)
    print(f"Total Operations: {summary['total_operations']}")
    print(f"Total Duration: {summary['total_duration']:.3f}s")
    print(f"Successful: {summary['successful_operations']}")
    print(f"Failed: {summary['failed_operations']}")

    print("\nOperation Breakdown:")
    for op_name, stats in summary["operations"].items():
        print(f"\n  {op_name}:")
        print(f"    Count: {stats['count']}")
        print(f"    Total Time: {stats['total_time']:.3f}s ({stats['percentage']:.1f}%)")
        print(f"    Avg: {stats['avg_time']:.3f}s")
        print(f"    Min/Max: {stats['min_time']:.3f}s / {stats['max_time']:.3f}s")
        if stats['error_count'] > 0:
            print(f"    Errors: {stats['error_count']}")

    # Validate results
    print("\n" + "=" * 70)
    print("Validation")
    print("=" * 70)

    assert summary['total_operations'] == 6, f"Expected 6 operations, got {summary['total_operations']}"
    print("✅ Correct number of operations tracked")

    assert summary['successful_operations'] == 5, f"Expected 5 successful, got {summary['successful_operations']}"
    print("✅ Correct success count")

    assert summary['failed_operations'] == 1, f"Expected 1 failed, got {summary['failed_operations']}"
    print("✅ Correct error count")

    assert len(summary['operations']) == 4, f"Expected 4 operation types, got {len(summary['operations'])}"
    print("✅ Correct number of operation types")

    assert summary['operations']['save_batch']['count'] == 3, "Expected 3 save_batch operations"
    print("✅ Correct batch operation count")

    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED - PerformanceMonitor working correctly!")
    print("=" * 70)


if __name__ == "__main__":
    main()
