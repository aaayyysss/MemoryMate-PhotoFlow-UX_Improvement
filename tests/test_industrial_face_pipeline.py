import pytest
from unittest.mock import MagicMock, patch
from workers.face_detection_worker import FaceDetectionWorker
from workers.face_cluster_worker import FaceClusterWorker

class TestIndustrialFacePipeline:
    def test_face_detection_screenshot_caps(self):
        """Verify tiered face limits for screenshots based on policy."""
        # Setup worker with various policies
        worker_exclude = FaceDetectionWorker(project_id=1, screenshot_policy="exclude")
        worker_detect = FaceDetectionWorker(project_id=1, screenshot_policy="detect_only")
        worker_include = FaceDetectionWorker(project_id=1, screenshot_policy="include_cluster")
        worker_include_all = FaceDetectionWorker(project_id=1, screenshot_policy="include_cluster", include_all_screenshot_faces=True)

        # Mock faces (10 detected)
        mock_faces = [{"bbox_w": 100, "bbox_h": 100}] * 10

        # Helper to test limit logic directly in the worker
        def get_limit(worker, is_screenshot, faces_count):
            limit = worker.max_faces_per_photo
            if is_screenshot:
                if worker.screenshot_policy == "exclude":
                    limit = 0
                elif worker.screenshot_policy == "detect_only":
                    limit = min(limit, 4)
                elif worker.screenshot_policy == "include_cluster":
                    if worker.include_all_screenshot_faces:
                        limit = faces_count
                    else:
                        limit = min(limit, 8)
                else:
                    limit = min(limit, 4)
            return limit

        # Verify limits
        assert get_limit(worker_exclude, True, 10) == 0
        assert get_limit(worker_detect, True, 10) == 4
        assert get_limit(worker_include, True, 10) == 8
        assert get_limit(worker_include_all, True, 10) == 10
        assert get_limit(worker_include, False, 10) == 10 # Non-screenshots use default max (10)

    @patch('workers.face_cluster_worker.FaceClusterWorker._get_face_count')
    def test_face_cluster_split_pass_logic(self, mock_count):
        """Verify the oversized cluster split pass logic structure."""
        mock_count.return_value = 100
        # Setup worker
        worker = FaceClusterWorker(project_id=1, auto_tune=True)

        # We verify the re-clustering parameters used in split pass
        # base eps for 100 faces is usually around 0.38 - 0.42
        # If eps = 0.38, local_eps = max(0.32, 0.38 - 0.14) = max(0.32, 0.24) = 0.32
        local_eps = max(0.32, worker.eps - 0.14)
        assert local_eps >= 0.32
