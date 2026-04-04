# services/semantic_search_service.py
# SemanticSearchService - Text → Image Search
# Version: 1.0.1
# Date: 2026-03-09
#
# Search photos using natural language queries.
# Core Principle (non-negotiable):
# Text query → embedding → cosine similarity → matching photos

import numpy as np
import threading
import queue
import os
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from services.semantic_embedding_service import get_semantic_embedding_service
from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)

# ── Dedicated CLIP Executor Thread ─────────────────────────────────────
# All CLIP text encoding runs on ONE dedicated long-lived daemon thread.
# This eliminates the class of Windows crashes caused by CLIP/MKL native
# code being invoked from arbitrary QThreadPool workers where thread
# lifecycle, stack size, and TLS state are unpredictable.

class _ClipExecutorThread(threading.Thread):
    """Single dedicated thread for all CLIP text inference."""
    def __init__(self):
        super().__init__(name="ClipExecutor", daemon=True)
        self._queue = queue.Queue()
        self._shutdown = False

    def submit(self, embedder, text: str, timeout: float = 60.0) -> Optional[np.ndarray]:
        """Submit a text encoding request and block until result is ready."""
        result_event = threading.Event()
        result_holder = [None, None]  # [result, exception]
        self._queue.put((embedder, text, result_event, result_holder))

        if not result_event.wait(timeout=timeout):
            logger.error(
                "[ClipExecutor] Timed out waiting for encode_text(%r) after %.0fs",
                text, timeout,
            )
            return None

        if result_holder[1] is not None:
            raise result_holder[1]
        return result_holder[0]

    def shutdown(self):
        """Signal the executor to stop."""
        self._shutdown = True
        self._queue.put(None)  # sentinel to unblock

    def run(self):
        logger.info("[ClipExecutor] Dedicated CLIP inference thread started")
        while not self._shutdown:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:  # shutdown sentinel
                break

            embedder, text, result_event, result_holder = item
            try:
                result_holder[0] = embedder.encode_text(text)
            except Exception as e:
                logger.error("[ClipExecutor] encode_text(%r) failed: %s", text, e)
                result_holder[1] = e
            finally:
                result_event.set()
        logger.info("[ClipExecutor] Dedicated CLIP inference thread stopped")

# Module-level singleton
_clip_executor: Optional[_ClipExecutorThread] = None
_clip_executor_lock = threading.Lock()

def get_clip_executor() -> _ClipExecutorThread:
    """
    Get or create the singleton CLIP executor thread.

    All CLIP text encoding must run on this dedicated thread to prevent
    native access violations (0xC0000005) on Windows when multiple
    Qt worker threads attempt inference simultaneously.
    """
    global _clip_executor
    if _clip_executor is not None and _clip_executor.is_alive():
        return _clip_executor
    with _clip_executor_lock:
        if _clip_executor is None or not _clip_executor.is_alive():
            _clip_executor = _ClipExecutorThread()
            _clip_executor.start()
        return _clip_executor

@dataclass
class SearchResult:
    """Semantic search result."""
    photo_id: int
    relevance_score: float
    file_path: Optional[str] = None
    thumbnail_path: Optional[str] = None

class SemanticSearchService:
    """
    Service for text-to-image semantic search.
    """
    def __init__(self,
                 model_name: str = "clip-vit-b32",
                 db_connection: Optional[DatabaseConnection] = None,
                 project_id: Optional[int] = None):
        from utils.clip_model_registry import normalize_model_id, all_aliases_for
        self.model_name = normalize_model_id(model_name)
        self._model_aliases = all_aliases_for(self.model_name)
        self.project_id = project_id
        self.db = db_connection or DatabaseConnection()
        self.embedder = get_semantic_embedding_service(model_name=model_name)
        logger.info(f"[SemanticSearchService] Initialized with model={model_name}")

    @classmethod
    def for_project(cls, project_id: int, db_connection: Optional[DatabaseConnection] = None) -> 'SemanticSearchService':
        from repository.project_repository import ProjectRepository
        project_repo = ProjectRepository()
        canonical_model = project_repo.get_semantic_model(project_id)
        logger.info(
            f"[SemanticSearchService] Creating service for project {project_id} "
            f"with canonical model: {canonical_model}"
        )
        return cls(
            model_name=canonical_model,
            db_connection=db_connection,
            project_id=project_id
        )

    @property
    def available(self) -> bool:
        """Check if service is available."""
        return self.embedder.available

    def search(self,
               query: str,
               top_k: int = 20,
               threshold: float = 0.25,
               include_metadata: bool = False) -> List[SearchResult]:
        if not query or not query.strip():
            return []
        if not self.available:
            return []

        _thread_name = threading.current_thread().name
        logger.info(
            f"[SemanticSearchService] encode_text START: query={query!r} "
            f"thread={_thread_name} project={self.project_id}"
        )

        try:
            executor = get_clip_executor()
            query_embedding = executor.submit(self.embedder, query.strip())
        except Exception as e:
            logger.error(f"[SemanticSearchService] Failed to encode query '{query}': {e}")
            return []

        if query_embedding is None:
            return []

        # Get all photo embeddings
        photo_embeddings = self._get_all_embeddings()
        if not photo_embeddings:
            return []

        # Compute similarities
        matches = []
        for photo_id, photo_embedding in photo_embeddings:
            score = float(np.dot(query_embedding, photo_embedding))
            if score >= threshold:
                matches.append((photo_id, score))

        # Sort and limit
        matches.sort(key=lambda x: x[1], reverse=True)
        matches = matches[:top_k]

        results = [SearchResult(photo_id=pid, relevance_score=s) for pid, s in matches]
        if include_metadata:
            self._add_metadata(results)
        return results

    def _get_all_embeddings(self) -> List[tuple]:
        with self.db.get_connection() as conn:
            aliases = self._model_aliases
            placeholders = ','.join(['?'] * len(aliases))

            if self.project_id is not None:
                cursor = conn.execute(f"""
                    SELECT se.photo_id, se.embedding, se.dim
                    FROM semantic_embeddings se
                    JOIN photo_metadata pm ON se.photo_id = pm.id
                    WHERE se.model IN ({placeholders})
                    AND pm.project_id = ?
                """, (*aliases, self.project_id))
            else:
                cursor = conn.execute(f"""
                    SELECT photo_id, embedding, dim
                    FROM semantic_embeddings
                    WHERE model IN ({placeholders})
                """, tuple(aliases))

            results = []
            for row in cursor.fetchall():
                photo_id = row['photo_id']
                embedding_blob = row['embedding']
                dim = row['dim']

                if isinstance(embedding_blob, str):
                    embedding_blob = embedding_blob.encode('latin1')

                # Fix 2026-03-09: Always .copy() the array to prevent memory access violations
                if dim < 0:
                    actual_dim = abs(dim)
                    embedding = np.frombuffer(embedding_blob, dtype='float16').astype('float32')
                else:
                    actual_dim = dim
                    embedding = np.frombuffer(embedding_blob, dtype='float32').copy()

                if len(embedding) == actual_dim:
                    results.append((photo_id, embedding))
            return results

    def _add_metadata(self, results: List[SearchResult]):
        if not results: return
        photo_ids = [r.photo_id for r in results]
        placeholders = ','.join(['?'] * len(photo_ids))
        with self.db.get_connection() as conn:
            cursor = conn.execute(f"SELECT id, path FROM photo_metadata WHERE id IN ({placeholders})", photo_ids)
            metadata = {row['id']: row['path'] for row in cursor.fetchall()}
            for r in results:
                r.file_path = metadata.get(r.photo_id)

    def get_search_statistics(self) -> dict:
        with self.db.get_connection() as conn:
            aliases = self._model_aliases
            placeholders = ','.join(['?'] * len(aliases))
            if self.project_id is not None:
                total_photos = conn.execute("SELECT COUNT(*) FROM photo_metadata WHERE project_id=?", (self.project_id,)).fetchone()[0]
                embedded_photos = conn.execute(f"""
                    SELECT COUNT(*) FROM semantic_embeddings se
                    JOIN photo_metadata pm ON se.photo_id = pm.id
                    WHERE se.model IN ({placeholders}) AND pm.project_id = ?
                """, (*aliases, self.project_id)).fetchone()[0]
            else:
                total_photos = conn.execute("SELECT COUNT(*) FROM photo_metadata").fetchone()[0]
                embedded_photos = conn.execute(f"SELECT COUNT(*) FROM semantic_embeddings WHERE model IN ({placeholders})", tuple(aliases)).fetchone()[0]

            return {
                'total_photos': total_photos,
                'embedded_photos': embedded_photos,
                'coverage_percent': (embedded_photos / total_photos * 100) if total_photos > 0 else 0.0,
                'model': self.model_name,
                'project_id': self.project_id,
                'search_ready': embedded_photos > 0
            }

_project_search_services: Dict[int, SemanticSearchService] = {}

def get_semantic_search_service_for_project(project_id: int) -> SemanticSearchService:
    global _project_search_services
    if project_id not in _project_search_services:
        _project_search_services[project_id] = SemanticSearchService.for_project(project_id)
    return _project_search_services[project_id]

def invalidate_project_search_service(project_id: int):
    global _project_search_services
    if project_id in _project_search_services:
        del _project_search_services[project_id]
