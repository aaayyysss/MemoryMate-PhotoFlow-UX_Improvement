#!/usr/bin/env python3
"""
Face Quality Scorer - Calculate quality metrics for face crops.

Used for representative face selection in the People Tools bulk review workflow.
Higher quality faces make better representatives for person clusters.

Quality Metrics:
- Sharpness: Laplacian variance (focus/blur detection)
- Size: Face crop dimensions (larger = better)
- Frontality: Estimated from landmarks (if available)
- Brightness: Avoid over/under exposed faces
- Recency: Prefer recent photos (slight bonus)

Author: Claude Code
Date: December 16, 2025
"""

import cv2
import numpy as np
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta


class FaceQualityScorer:
    """
    Calculate quality scores for face crops to help select best representatives.
    """

    # Quality score weights (must sum to 1.0)
    WEIGHT_SHARPNESS = 0.35
    WEIGHT_SIZE = 0.25
    WEIGHT_FRONTALITY = 0.20
    WEIGHT_BRIGHTNESS = 0.15
    WEIGHT_RECENCY = 0.05

    # Thresholds for quality levels
    THRESHOLD_HIGH = 0.75  # ✅ Excellent quality
    THRESHOLD_MEDIUM = 0.50  # ⚠️ Good quality
    # Below 0.50 = ❓ Poor quality

    @staticmethod
    def calculate_sharpness(image_path: str) -> float:
        """
        Calculate sharpness using Laplacian variance.

        Higher values indicate sharper images (better focus).
        Normalized to 0-1 range where 1.0 is very sharp.

        Args:
            image_path: Path to face crop image

        Returns:
            Sharpness score (0.0-1.0)
        """
        try:
            # UNICODE PATH FIX (2026-03-17): cv2.imread fails on non-ASCII paths.
            from PIL import Image
            try:
                with Image.open(image_path) as pil_img:
                    # Convert to grayscale directly in PIL
                    img = np.array(pil_img.convert('L'))
            except Exception:
                img = None

            if img is None:
                return 0.0

            # Calculate Laplacian variance
            laplacian = cv2.Laplacian(img, cv2.CV_64F)
            variance = laplacian.var()

            # Normalize to 0-1 range
            # Empirically, variance > 100 is sharp, > 500 is very sharp
            # Using sigmoid-like curve: score = 1 / (1 + e^(-(variance-200)/100))
            score = 1.0 / (1.0 + np.exp(-(variance - 200) / 100))

            return float(np.clip(score, 0.0, 1.0))

        except Exception:
            return 0.0

    @staticmethod
    def calculate_size_score(width: int, height: int) -> float:
        """
        Calculate size score based on face crop dimensions.

        Larger faces generally have better detail and quality.
        Normalized to 0-1 range.

        Args:
            width: Face crop width in pixels
            height: Face crop height in pixels

        Returns:
            Size score (0.0-1.0)
        """
        if width <= 0 or height <= 0:
            return 0.0

        # Calculate area
        area = width * height

        # Normalize using typical face crop sizes
        # Small: 50x50 = 2,500
        # Medium: 100x100 = 10,000
        # Large: 200x200 = 40,000
        # Very Large: 300x300 = 90,000

        # Sigmoid normalization around 20,000 (roughly 140x140)
        score = 1.0 / (1.0 + np.exp(-(area - 20000) / 10000))

        return float(np.clip(score, 0.0, 1.0))

    @staticmethod
    def calculate_brightness_score(image_path: str) -> float:
        """
        Calculate brightness score - prefer well-lit faces.

        Avoid over-exposed (too bright) or under-exposed (too dark) faces.
        Optimal brightness is around 50% (127/255).

        Args:
            image_path: Path to face crop image

        Returns:
            Brightness score (0.0-1.0)
        """
        try:
            # UNICODE PATH FIX (2026-03-17): cv2.imread fails on non-ASCII paths.
            from PIL import Image
            try:
                with Image.open(image_path) as pil_img:
                    # Convert to grayscale directly in PIL
                    img = np.array(pil_img.convert('L'))
            except Exception:
                img = None

            if img is None:
                return 0.5  # Neutral score if can't load file

            # Calculate mean brightness
            mean_brightness = np.mean(img)

            # Optimal brightness is around 127 (middle gray)
            # Penalize deviation from optimal
            # Score = 1 - |brightness - 127| / 127
            deviation = abs(mean_brightness - 127)
            score = 1.0 - (deviation / 127.0)

            return float(np.clip(score, 0.0, 1.0))

        except Exception:
            return 0.5  # Neutral score if can't determine

    @staticmethod
    def calculate_frontality_score(bbox_data: Optional[Dict] = None) -> float:
        """
        Calculate frontality score - prefer frontal faces over profile.

        Currently simplified - would need facial landmarks for accurate calculation.
        Returns neutral score (0.5) for now.

        Args:
            bbox_data: Optional dict with bounding box and landmark data

        Returns:
            Frontality score (0.0-1.0)
        """
        # TODO: Implement using facial landmarks when available
        # For now, return neutral score
        return 0.5

    @staticmethod
    def calculate_recency_score(photo_date: Optional[str] = None) -> float:
        """
        Calculate recency score - slight preference for recent photos.

        Args:
            photo_date: Photo date in ISO format (YYYY-MM-DD)

        Returns:
            Recency score (0.0-1.0)
        """
        if not photo_date:
            return 0.5  # Neutral if no date

        try:
            photo_dt = datetime.fromisoformat(photo_date[:10])  # Take date part only
            today = datetime.now()
            days_old = (today - photo_dt).days

            # Score decreases with age
            # Within 30 days: 1.0
            # Within 1 year: 0.7
            # Older than 3 years: 0.3
            if days_old < 30:
                return 1.0
            elif days_old < 365:
                return 0.7
            elif days_old < 1095:  # 3 years
                return 0.5
            else:
                return 0.3

        except Exception:
            return 0.5  # Neutral if can't parse date

    @classmethod
    def calculate_overall_quality(
        cls,
        image_path: str,
        width: int,
        height: int,
        photo_date: Optional[str] = None,
        bbox_data: Optional[Dict] = None
    ) -> Dict[str, float]:
        """
        Calculate overall quality score combining all metrics.

        Args:
            image_path: Path to face crop image
            width: Face crop width in pixels
            height: Face crop height in pixels
            photo_date: Optional photo date (YYYY-MM-DD)
            bbox_data: Optional bounding box/landmark data

        Returns:
            Dict with individual scores and overall weighted score:
            {
                'sharpness': 0.0-1.0,
                'size': 0.0-1.0,
                'brightness': 0.0-1.0,
                'frontality': 0.0-1.0,
                'recency': 0.0-1.0,
                'overall': 0.0-1.0,
                'quality_level': 'high' | 'medium' | 'low',
                'quality_icon': '✅' | '⚠️' | '❓'
            }
        """
        # Calculate individual metrics
        sharpness = cls.calculate_sharpness(image_path)
        size_score = cls.calculate_size_score(width, height)
        brightness = cls.calculate_brightness_score(image_path)
        frontality = cls.calculate_frontality_score(bbox_data)
        recency = cls.calculate_recency_score(photo_date)

        # Calculate weighted overall score
        overall = (
            sharpness * cls.WEIGHT_SHARPNESS +
            size_score * cls.WEIGHT_SIZE +
            brightness * cls.WEIGHT_BRIGHTNESS +
            frontality * cls.WEIGHT_FRONTALITY +
            recency * cls.WEIGHT_RECENCY
        )

        # Determine quality level
        if overall >= cls.THRESHOLD_HIGH:
            quality_level = 'high'
            quality_icon = '✅'
        elif overall >= cls.THRESHOLD_MEDIUM:
            quality_level = 'medium'
            quality_icon = '⚠️'
        else:
            quality_level = 'low'
            quality_icon = '❓'

        return {
            'sharpness': round(sharpness, 3),
            'size': round(size_score, 3),
            'brightness': round(brightness, 3),
            'frontality': round(frontality, 3),
            'recency': round(recency, 3),
            'overall': round(overall, 3),
            'quality_level': quality_level,
            'quality_icon': quality_icon
        }

    @classmethod
    def get_quality_badge_text(cls, quality_score: float) -> Tuple[str, str]:
        """
        Get display text for quality badge.

        Args:
            quality_score: Overall quality score (0.0-1.0)

        Returns:
            Tuple of (icon, percentage_text)
        """
        percentage = int(quality_score * 100)

        if quality_score >= cls.THRESHOLD_HIGH:
            return ('✅', f'{percentage}%')
        elif quality_score >= cls.THRESHOLD_MEDIUM:
            return ('⚠️', f'{percentage}%')
        else:
            return ('❓', f'{percentage}%')


if __name__ == '__main__':
    # Test the quality scorer
    import sys

    if len(sys.argv) < 2:
        print("Usage: python face_quality_scorer.py <face_crop_path>")
        print("\nExample:")
        print("  python face_quality_scorer.py /path/to/face_crop.jpg")
        sys.exit(1)

    image_path = sys.argv[1]

    # Try to get image dimensions
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is not None:
            height, width = img.shape[:2]
        else:
            print(f"❌ Could not load image: {image_path}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading image: {e}")
        sys.exit(1)

    # Calculate quality
    result = FaceQualityScorer.calculate_overall_quality(
        image_path=image_path,
        width=width,
        height=height
    )

    print(f"\n🎯 Face Quality Analysis: {image_path}")
    print(f"{'='*60}")
    print(f"📐 Dimensions:    {width} × {height} pixels")
    print(f"🔍 Sharpness:     {result['sharpness']:.1%} (weight: {FaceQualityScorer.WEIGHT_SHARPNESS:.0%})")
    print(f"📏 Size:          {result['size']:.1%} (weight: {FaceQualityScorer.WEIGHT_SIZE:.0%})")
    print(f"💡 Brightness:    {result['brightness']:.1%} (weight: {FaceQualityScorer.WEIGHT_BRIGHTNESS:.0%})")
    print(f"👤 Frontality:    {result['frontality']:.1%} (weight: {FaceQualityScorer.WEIGHT_FRONTALITY:.0%})")
    print(f"📅 Recency:       {result['recency']:.1%} (weight: {FaceQualityScorer.WEIGHT_RECENCY:.0%})")
    print(f"{'='*60}")
    print(f"{result['quality_icon']} Overall Quality: {result['overall']:.1%} ({result['quality_level']})")
    print()
