# services/face_detection_benchmark.py
# Enhancement: Performance benchmarking for face detection
# Targets: Apple Photos, Google Photos, Microsoft Photos performance levels
# Based on proof of concept benchmarking approach
# ------------------------------------------------------

"""
Face Detection Performance Benchmarking

Provides benchmarking utilities to compare our face detection performance
against industry standards (Apple Photos, Google Photos, Microsoft Photos).

Target Performance (Industry Standards):
- Apple Photos: ~100-200 faces/second (M1/M2 chips with Neural Engine)
- Google Photos: ~50-100 faces/second (server-side processing)
- Microsoft Photos: ~30-50 faces/second (local CPU processing)

Our Target Performance:
- GPU (CUDA): 50-100 faces/second
- CPU (modern): 20-50 faces/second
"""

import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a face detection benchmark run."""

    # Timing
    duration_seconds: float

    # Input
    photos_processed: int

    # Output
    faces_detected: int

    # Performance metrics
    faces_per_second: float
    photos_per_second: float

    # Hardware info
    hardware_type: str  # 'GPU' or 'CPU'

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)

    def get_performance_rating(self) -> str:
        """
        Get performance rating compared to industry standards.

        Returns:
            str: 'Excellent', 'Good', 'Fair', or 'Poor'
        """
        fps = self.faces_per_second

        if self.hardware_type == 'GPU':
            if fps >= 50:
                return 'Excellent (comparable to Apple/Google Photos)'
            elif fps >= 30:
                return 'Good (comparable to Google Photos)'
            elif fps >= 20:
                return 'Fair (comparable to Microsoft Photos)'
            else:
                return 'Poor (below industry standards)'
        else:  # CPU
            if fps >= 30:
                return 'Excellent (above typical CPU performance)'
            elif fps >= 20:
                return 'Good (meets industry CPU standards)'
            elif fps >= 10:
                return 'Fair (acceptable for CPU processing)'
            else:
                return 'Poor (below acceptable CPU performance)'

    def print_summary(self):
        """Print a formatted summary of the benchmark."""
        print("\n" + "="*70)
        print("FACE DETECTION BENCHMARK RESULTS")
        print("="*70)
        print(f"Hardware: {self.hardware_type}")
        print()
        print("METRICS:")
        print(f"  Photos processed: {self.photos_processed}")
        print(f"  Faces detected: {self.faces_detected}")
        print(f"  Duration: {self.duration_seconds:.2f}s")
        print(f"  Faces/second: {self.faces_per_second:.2f}")
        print(f"  Photos/second: {self.photos_per_second:.2f}")
        print()
        print(f"RATING: {self.get_performance_rating()}")
        print("="*70)


def compare_with_standards(result: BenchmarkResult) -> Dict:
    """
    Compare benchmark result with industry standards.

    Args:
        result: BenchmarkResult to compare

    Returns:
        Dict with comparison metrics
    """
    standards = {
        'Apple Photos (M2 + Neural Engine)': 150,
        'Google Photos (Server)': 75,
        'Microsoft Photos (CPU)': 40,
        'Our Target (GPU)': 50,
        'Our Target (CPU)': 25,
    }

    our_performance = result.faces_per_second

    comparison = {
        'our_performance': our_performance,
        'standards': standards,
        'percentages': {}
    }

    for name, standard_fps in standards.items():
        percentage = (our_performance / standard_fps * 100) if standard_fps > 0 else 0
        comparison['percentages'][name] = percentage

    return comparison


def print_industry_comparison(result: BenchmarkResult):
    """Print comparison with industry standards."""
    comparison = compare_with_standards(result)

    print("\n" + "="*70)
    print("COMPARISON WITH INDUSTRY STANDARDS")
    print("="*70)
    print(f"Our Performance: {comparison['our_performance']:.2f} faces/second\n")

    for name, standard in comparison['standards'].items():
        percentage = comparison['percentages'][name]
        bar_length = int(percentage / 5)  # Scale to fit in terminal
        bar = "█" * min(bar_length, 40)

        status = "✓" if percentage >= 80 else "○"
        print(f"{status} {name:40s} {standard:3.0f} fps  {percentage:5.1f}% {bar}")

    print("="*70 + "\n")


def create_benchmark_result(start_time: float,
                           end_time: float,
                           photos_processed: int,
                           faces_detected: int,
                           hardware_type: str) -> BenchmarkResult:
    """
    Create a benchmark result from timing data.

    Args:
        start_time: Start timestamp (from time.time())
        end_time: End timestamp (from time.time())
        photos_processed: Number of photos processed
        faces_detected: Number of faces detected
        hardware_type: 'GPU' or 'CPU'

    Returns:
        BenchmarkResult with calculated metrics
    """
    duration = end_time - start_time

    faces_per_second = faces_detected / duration if duration > 0 else 0
    photos_per_second = photos_processed / duration if duration > 0 else 0

    return BenchmarkResult(
        duration_seconds=duration,
        photos_processed=photos_processed,
        faces_detected=faces_detected,
        faces_per_second=faces_per_second,
        photos_per_second=photos_per_second,
        hardware_type=hardware_type
    )
