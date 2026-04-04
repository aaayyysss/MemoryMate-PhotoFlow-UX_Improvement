#!/usr/bin/env python3
"""
Tests for Face Quality Scorer.

Tests quality metric calculations for face crop selection.

Author: Claude Code
Date: December 16, 2025
"""

import pytest
import numpy as np
import cv2
import tempfile
import os
from pathlib import Path
from ui.face_quality_scorer import FaceQualityScorer


class TestFaceQualityScorer:
    """Test suite for FaceQualityScorer class."""

    @pytest.fixture
    def temp_image(self):
        """Create a temporary test image."""
        # Create a simple grayscale image
        img = np.random.randint(0, 256, (100, 100), dtype=np.uint8)

        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name

        cv2.imwrite(temp_path, img)

        yield temp_path

        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    @pytest.fixture
    def sharp_image(self):
        """Create a sharp test image (high frequency content)."""
        # Create checkerboard pattern (high sharpness)
        img = np.zeros((100, 100), dtype=np.uint8)
        img[::2, ::2] = 255
        img[1::2, 1::2] = 255

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name

        cv2.imwrite(temp_path, img)

        yield temp_path

        if os.path.exists(temp_path):
            os.unlink(temp_path)

    @pytest.fixture
    def blurry_image(self):
        """Create a blurry test image (low frequency content)."""
        # Create smooth gradient (low sharpness)
        img = np.linspace(0, 255, 100, dtype=np.uint8)
        img = np.tile(img, (100, 1))

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            temp_path = f.name

        cv2.imwrite(temp_path, img)

        yield temp_path

        if os.path.exists(temp_path):
            os.unlink(temp_path)

    def test_calculate_sharpness_sharp_image(self, sharp_image):
        """Test sharpness calculation on sharp image."""
        score = FaceQualityScorer.calculate_sharpness(sharp_image)

        assert 0.0 <= score <= 1.0, "Sharpness score should be in [0, 1]"
        assert score > 0.5, "Sharp image should have high sharpness score"

    def test_calculate_sharpness_blurry_image(self, blurry_image):
        """Test sharpness calculation on blurry image."""
        score = FaceQualityScorer.calculate_sharpness(blurry_image)

        assert 0.0 <= score <= 1.0, "Sharpness score should be in [0, 1]"
        assert score < 0.5, "Blurry image should have low sharpness score"

    def test_calculate_sharpness_missing_file(self):
        """Test sharpness calculation on missing file."""
        score = FaceQualityScorer.calculate_sharpness("/nonexistent/file.jpg")

        assert score == 0.0, "Missing file should return 0.0"

    def test_calculate_size_score_small_face(self):
        """Test size score for small face crop."""
        score = FaceQualityScorer.calculate_size_score(50, 50)

        assert 0.0 <= score <= 1.0, "Size score should be in [0, 1]"
        assert score < 0.5, "Small face should have low size score"

    def test_calculate_size_score_large_face(self):
        """Test size score for large face crop."""
        score = FaceQualityScorer.calculate_size_score(300, 300)

        assert 0.0 <= score <= 1.0, "Size score should be in [0, 1]"
        assert score > 0.5, "Large face should have high size score"

    def test_calculate_size_score_zero_dimensions(self):
        """Test size score with zero dimensions."""
        score = FaceQualityScorer.calculate_size_score(0, 100)

        assert score == 0.0, "Zero dimension should return 0.0"

    def test_calculate_brightness_score(self, temp_image):
        """Test brightness score calculation."""
        score = FaceQualityScorer.calculate_brightness_score(temp_image)

        assert 0.0 <= score <= 1.0, "Brightness score should be in [0, 1]"

    def test_calculate_brightness_score_missing_file(self):
        """Test brightness score on missing file."""
        score = FaceQualityScorer.calculate_brightness_score("/nonexistent/file.jpg")

        assert score == 0.5, "Missing file should return neutral score 0.5"

    def test_calculate_frontality_score(self):
        """Test frontality score (currently returns neutral)."""
        score = FaceQualityScorer.calculate_frontality_score()

        assert score == 0.5, "Frontality should return neutral score 0.5 (not implemented yet)"

    def test_calculate_recency_score_recent(self):
        """Test recency score for recent photo."""
        from datetime import datetime, timedelta

        recent_date = (datetime.now() - timedelta(days=15)).isoformat()
        score = FaceQualityScorer.calculate_recency_score(recent_date)

        assert score == 1.0, "Recent photo (< 30 days) should get score 1.0"

    def test_calculate_recency_score_old(self):
        """Test recency score for old photo."""
        old_date = "2020-01-01"
        score = FaceQualityScorer.calculate_recency_score(old_date)

        assert score == 0.3, "Old photo (> 3 years) should get score 0.3"

    def test_calculate_recency_score_no_date(self):
        """Test recency score with no date."""
        score = FaceQualityScorer.calculate_recency_score(None)

        assert score == 0.5, "No date should return neutral score 0.5"

    def test_calculate_overall_quality(self, temp_image):
        """Test overall quality calculation."""
        result = FaceQualityScorer.calculate_overall_quality(
            image_path=temp_image,
            width=150,
            height=150,
            photo_date="2024-01-01"
        )

        # Check all fields are present
        assert 'sharpness' in result
        assert 'size' in result
        assert 'brightness' in result
        assert 'frontality' in result
        assert 'recency' in result
        assert 'overall' in result
        assert 'quality_level' in result
        assert 'quality_icon' in result

        # Check scores are in valid range
        assert 0.0 <= result['overall'] <= 1.0
        assert result['quality_level'] in ['high', 'medium', 'low']
        assert result['quality_icon'] in ['✅', '⚠️', '❓']

    def test_overall_quality_weights_sum_to_one(self):
        """Test that all quality weights sum to 1.0."""
        total = (
            FaceQualityScorer.WEIGHT_SHARPNESS +
            FaceQualityScorer.WEIGHT_SIZE +
            FaceQualityScorer.WEIGHT_BRIGHTNESS +
            FaceQualityScorer.WEIGHT_FRONTALITY +
            FaceQualityScorer.WEIGHT_RECENCY
        )

        assert abs(total - 1.0) < 0.001, "Weights should sum to 1.0"

    def test_get_quality_badge_text_high(self):
        """Test quality badge for high quality score."""
        icon, text = FaceQualityScorer.get_quality_badge_text(0.85)

        assert icon == '✅', "High quality should use ✅ icon"
        assert '85' in text, "Text should contain percentage"

    def test_get_quality_badge_text_medium(self):
        """Test quality badge for medium quality score."""
        icon, text = FaceQualityScorer.get_quality_badge_text(0.60)

        assert icon == '⚠️', "Medium quality should use ⚠️ icon"
        assert '60' in text, "Text should contain percentage"

    def test_get_quality_badge_text_low(self):
        """Test quality badge for low quality score."""
        icon, text = FaceQualityScorer.get_quality_badge_text(0.30)

        assert icon == '❓', "Low quality should use ❓ icon"
        assert '30' in text, "Text should contain percentage"

    def test_quality_level_thresholds(self):
        """Test quality level threshold boundaries."""
        # High quality (>= 0.75)
        result = FaceQualityScorer.calculate_overall_quality(
            image_path="/fake/path.jpg",
            width=300,
            height=300
        )
        # Note: This will fail to load image, but we can test the logic

        # Test threshold values directly
        assert FaceQualityScorer.THRESHOLD_HIGH == 0.75
        assert FaceQualityScorer.THRESHOLD_MEDIUM == 0.50


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
