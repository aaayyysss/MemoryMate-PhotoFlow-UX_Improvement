# services/face_quality_analyzer.py
# Face Quality Analysis for better clustering and representative selection
# Phase 2A: Advanced Analytics & Quality Improvements

"""
Face Quality Analyzer

Provides comprehensive quality metrics for face images to enable:
- Better representative face selection
- Quality-based filtering
- Clustering quality assessment
- User insights into face detection quality

Metrics:
1. Blur Detection (Laplacian variance)
2. Lighting Quality (histogram analysis)
3. Face Size/Resolution scoring
4. Aspect Ratio validation
5. Multi-factor quality score
"""

import numpy as np
import cv2
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class FaceQualityMetrics:
    """
    Comprehensive quality metrics for a face crop.

    Attributes:
        blur_score: Laplacian variance (higher = sharper, >100 is good)
        lighting_score: 0-100, based on histogram distribution (50-80 is good)
        size_score: 0-100, based on face size relative to image
        aspect_ratio: Width/height ratio (0.5-1.6 is normal for faces)
        confidence: Detection confidence from face detector (0-1)
        overall_quality: Weighted combination of all metrics (0-100)
        is_good_quality: Boolean flag for quick filtering
        quality_label: Human-readable quality assessment (Excellent, Good, Fair, Poor)
    """
    blur_score: float
    lighting_score: float
    size_score: float
    aspect_ratio: float
    confidence: float
    overall_quality: float
    is_good_quality: bool
    quality_label: str

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/logging."""
        return {
            'blur_score': self.blur_score,
            'lighting_score': self.lighting_score,
            'size_score': self.size_score,
            'aspect_ratio': self.aspect_ratio,
            'confidence': self.confidence,
            'overall_quality': self.overall_quality,
            'is_good_quality': self.is_good_quality,
            'quality_label': self.quality_label
        }


class FaceQualityAnalyzer:
    """
    Analyzer for assessing face image quality.

    Uses multiple metrics to provide comprehensive quality assessment:
    - Blur detection (sharpness)
    - Lighting analysis (exposure, contrast)
    - Size assessment (resolution, face area)
    - Aspect ratio validation (face proportions)

    Quality thresholds are configurable for different use cases.
    """

    # Default quality thresholds
    DEFAULT_THRESHOLDS = {
        'blur_min': 100.0,           # Minimum Laplacian variance for sharp image
        'lighting_min': 40.0,        # Minimum lighting score
        'lighting_max': 90.0,        # Maximum lighting score (avoid overexposure)
        'size_min': 0.02,            # Minimum face area (2% of image)
        'aspect_min': 0.5,           # Minimum aspect ratio
        'aspect_max': 1.6,           # Maximum aspect ratio
        'confidence_min': 0.6,       # Minimum detection confidence
        'overall_min': 60.0          # Minimum overall quality score
    }

    # Weights for overall quality calculation
    QUALITY_WEIGHTS = {
        'blur': 0.30,        # 30% - sharpness is very important
        'lighting': 0.25,    # 25% - good lighting is important
        'size': 0.20,        # 20% - larger faces are better
        'aspect': 0.10,      # 10% - aspect ratio validation
        'confidence': 0.15   # 15% - detector confidence
    }

    def __init__(self, thresholds: Optional[Dict] = None):
        """
        Initialize quality analyzer.

        Args:
            thresholds: Optional custom thresholds (overrides defaults)
        """
        self.thresholds = self.DEFAULT_THRESHOLDS.copy()
        if thresholds:
            self.thresholds.update(thresholds)

    def analyze_face_crop(self,
                         image_path: str,
                         bbox: Tuple[int, int, int, int],
                         confidence: float = 1.0) -> FaceQualityMetrics:
        """
        Analyze quality of a face crop.

        Args:
            image_path: Path to source image
            bbox: Bounding box (x, y, w, h) of face in image
            confidence: Detection confidence from face detector

        Returns:
            FaceQualityMetrics with all quality scores
        """
        try:
            # Load image
            # UNICODE PATH FIX (2026-03-14): cv2.imread fails on Windows non-ASCII paths.
            # Use PIL then convert to BGR numpy array for OpenCV compatibility.
            # Patch C: Enhanced Unicode-safe PIL loader with ImageOps support.
            from PIL import Image, ImageOps
            try:
                with Image.open(image_path) as pil_img:
                    # Fix orientation
                    pil_img = ImageOps.exif_transpose(pil_img)
                    # Ensure image is in RGB before converting to numpy
                    if pil_img.mode != "RGB":
                        pil_img = pil_img.convert("RGB")

                    arr = np.array(pil_img, copy=True)
                    # Convert RGB (PIL) to BGR (OpenCV)
                    img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            except Exception as load_err:
                logger.warning(f"[FACE_QUALITY_LOAD_FAIL] Robust loader failed for {image_path}: {load_err}")
                img = None

            if img is None:
                logger.warning(f"Failed to load image: {image_path}")
                return self._default_metrics(confidence)

            # Extract face crop
            x, y, w, h = bbox
            if w <= 0 or h <= 0:
                logger.warning(f"Invalid bbox: {bbox}")
                return self._default_metrics(confidence)

            # Ensure bbox is within image bounds
            img_h, img_w = img.shape[:2]
            x = max(0, min(x, img_w - 1))
            y = max(0, min(y, img_h - 1))
            w = min(w, img_w - x)
            h = min(h, img_h - y)

            face_crop = img[y:y+h, x:x+w]

            if face_crop.size == 0:
                logger.warning(f"Empty face crop for {image_path}")
                return self._default_metrics(confidence)

            # Calculate individual metrics
            blur_score = self._calculate_blur_score(face_crop)
            lighting_score = self._calculate_lighting_score(face_crop)
            size_score = self._calculate_size_score(w, h, img_w, img_h)
            aspect_ratio = w / h if h > 0 else 0.0

            # Calculate overall quality (weighted combination)
            overall_quality = self._calculate_overall_quality(
                blur_score, lighting_score, size_score, aspect_ratio, confidence
            )

            # Determine if face meets quality thresholds
            is_good_quality = self._is_good_quality(
                blur_score, lighting_score, size_score, aspect_ratio, confidence, overall_quality
            )

            # Get quality label
            quality_label = self.get_quality_label(overall_quality)

            return FaceQualityMetrics(
                blur_score=blur_score,
                lighting_score=lighting_score,
                size_score=size_score,
                aspect_ratio=aspect_ratio,
                confidence=confidence,
                overall_quality=overall_quality,
                is_good_quality=is_good_quality,
                quality_label=quality_label
            )

        except Exception as e:
            logger.error(f"Error analyzing face quality for {image_path}: {e}", exc_info=True)
            return self._default_metrics(confidence)

    def _calculate_blur_score(self, face_crop: np.ndarray) -> float:
        """
        Calculate blur score using Laplacian variance.

        Higher values indicate sharper images.
        Typical values:
        - < 50: Very blurry
        - 50-100: Moderate blur
        - 100-500: Good sharpness
        - > 500: Excellent sharpness

        Args:
            face_crop: Face image crop (BGR)

        Returns:
            Laplacian variance (higher = sharper)
        """
        try:
            # Convert to grayscale
            gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

            # Calculate Laplacian
            laplacian = cv2.Laplacian(gray, cv2.CV_64F)

            # Variance of Laplacian (measures focus)
            variance = laplacian.var()

            return float(variance)

        except Exception as e:
            logger.debug(f"Error calculating blur score: {e}")
            return 0.0

    def _calculate_lighting_score(self, face_crop: np.ndarray) -> float:
        """
        Calculate lighting quality score (0-100).

        Analyzes histogram distribution to assess:
        - Brightness (mean value)
        - Contrast (standard deviation)
        - Exposure (clipping at extremes)

        Good lighting typically has:
        - Mean brightness: 80-170 (out of 255)
        - Good contrast: std dev > 30
        - Minimal clipping (<5% at 0 or 255)

        Args:
            face_crop: Face image crop (BGR)

        Returns:
            Lighting quality score 0-100
        """
        try:
            # Convert to grayscale for analysis
            gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

            # Calculate histogram statistics
            mean_brightness = gray.mean()
            std_contrast = gray.std()

            # Check for clipping (over/under exposure)
            total_pixels = gray.size
            clipped_dark = np.sum(gray < 10) / total_pixels
            clipped_bright = np.sum(gray > 245) / total_pixels

            # Score components
            # Brightness score (0-100): prefer 80-170 range
            if 80 <= mean_brightness <= 170:
                brightness_score = 100.0
            elif mean_brightness < 80:
                brightness_score = max(0, (mean_brightness / 80) * 100)
            else:  # > 170
                brightness_score = max(0, ((255 - mean_brightness) / (255 - 170)) * 100)

            # Contrast score (0-100): prefer std > 30
            contrast_score = min(100, (std_contrast / 50) * 100)

            # Exposure score (0-100): penalize clipping
            clipping_penalty = (clipped_dark + clipped_bright) * 200  # Penalty factor
            exposure_score = max(0, 100 - clipping_penalty)

            # Weighted combination
            lighting_score = (
                brightness_score * 0.4 +
                contrast_score * 0.3 +
                exposure_score * 0.3
            )

            return float(lighting_score)

        except Exception as e:
            logger.debug(f"Error calculating lighting score: {e}")
            return 50.0  # Neutral score

    def _calculate_size_score(self, face_w: int, face_h: int,
                             img_w: int, img_h: int) -> float:
        """
        Calculate size/resolution score (0-100).

        Larger faces (relative to image) are generally better quality:
        - More pixels = more detail
        - Easier to see facial features
        - Better for recognition/clustering

        Args:
            face_w: Face width in pixels
            face_h: Face height in pixels
            img_w: Image width in pixels
            img_h: Image height in pixels

        Returns:
            Size score 0-100
        """
        try:
            # Calculate face area as percentage of image
            face_area = face_w * face_h
            img_area = img_w * img_h

            if img_area == 0:
                return 0.0

            face_ratio = face_area / img_area

            # Scoring:
            # - < 1%: Very small (score 0-20)
            # - 1-2%: Small (score 20-40)
            # - 2-5%: Medium (score 40-70)
            # - 5-20%: Good (score 70-90)
            # - > 20%: Excellent (score 90-100)

            if face_ratio < 0.01:
                score = face_ratio * 2000  # 0-20
            elif face_ratio < 0.02:
                score = 20 + (face_ratio - 0.01) * 2000  # 20-40
            elif face_ratio < 0.05:
                score = 40 + (face_ratio - 0.02) * 1000  # 40-70
            elif face_ratio < 0.20:
                score = 70 + (face_ratio - 0.05) * 133.33  # 70-90
            else:
                score = 90 + min(10, (face_ratio - 0.20) * 50)  # 90-100

            return float(min(100, score))

        except Exception as e:
            logger.debug(f"Error calculating size score: {e}")
            return 50.0

    def _calculate_overall_quality(self, blur_score: float, lighting_score: float,
                                   size_score: float, aspect_ratio: float,
                                   confidence: float) -> float:
        """
        Calculate overall quality score (0-100) as weighted combination.

        Args:
            blur_score: Sharpness score (Laplacian variance)
            lighting_score: Lighting quality (0-100)
            size_score: Size/resolution score (0-100)
            aspect_ratio: Face aspect ratio
            confidence: Detection confidence (0-1)

        Returns:
            Overall quality score 0-100
        """
        # Normalize blur score to 0-100 (cap at 500 for excellent)
        blur_normalized = min(100, (blur_score / 500) * 100)

        # Normalize aspect ratio to 0-100
        # Good range: 0.5-1.6, optimal: ~0.8-1.2
        if 0.8 <= aspect_ratio <= 1.2:
            aspect_normalized = 100.0
        elif 0.5 <= aspect_ratio <= 1.6:
            # Partial score for acceptable but not ideal
            aspect_normalized = 70.0
        else:
            aspect_normalized = 0.0

        # Normalize confidence to 0-100
        confidence_normalized = confidence * 100

        # Weighted combination
        overall = (
            blur_normalized * self.QUALITY_WEIGHTS['blur'] +
            lighting_score * self.QUALITY_WEIGHTS['lighting'] +
            size_score * self.QUALITY_WEIGHTS['size'] +
            aspect_normalized * self.QUALITY_WEIGHTS['aspect'] +
            confidence_normalized * self.QUALITY_WEIGHTS['confidence']
        )

        return float(overall)

    def _is_good_quality(self, blur_score: float, lighting_score: float,
                        size_score: float, aspect_ratio: float,
                        confidence: float, overall_quality: float) -> bool:
        """
        Determine if face meets quality thresholds.

        All individual metrics AND overall score must meet thresholds.

        Args:
            blur_score: Sharpness score
            lighting_score: Lighting quality
            size_score: Size score
            aspect_ratio: Face aspect ratio
            confidence: Detection confidence
            overall_quality: Overall quality score

        Returns:
            True if face meets all quality criteria
        """
        return (
            blur_score >= self.thresholds['blur_min'] and
            self.thresholds['lighting_min'] <= lighting_score <= self.thresholds['lighting_max'] and
            size_score >= (self.thresholds['size_min'] * 5000) and  # Convert ratio to score
            self.thresholds['aspect_min'] <= aspect_ratio <= self.thresholds['aspect_max'] and
            confidence >= self.thresholds['confidence_min'] and
            overall_quality >= self.thresholds['overall_min']
        )

    def _default_metrics(self, confidence: float = 0.0) -> FaceQualityMetrics:
        """
        Return default/fallback metrics when analysis fails.

        Args:
            confidence: Detection confidence to preserve

        Returns:
            FaceQualityMetrics with default values
        """
        return FaceQualityMetrics(
            blur_score=0.0,
            lighting_score=0.0,
            size_score=0.0,
            aspect_ratio=0.0,
            confidence=confidence,
            overall_quality=0.0,
            is_good_quality=False,
            quality_label="Poor"
        )

    @staticmethod
    def get_quality_label(overall_quality: float) -> str:
        """
        Get human-readable quality label.

        Args:
            overall_quality: Overall quality score 0-100

        Returns:
            Quality label (Excellent, Good, Fair, Poor)
        """
        if overall_quality >= 80:
            return "Excellent"
        elif overall_quality >= 60:
            return "Good"
        elif overall_quality >= 40:
            return "Fair"
        else:
            return "Poor"
