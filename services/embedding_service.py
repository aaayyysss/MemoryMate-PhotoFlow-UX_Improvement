"""
EmbeddingService - Visual Semantic Embedding Extraction

Version: 1.0.0
Date: 2026-01-01

This service provides visual embedding extraction using CLIP/SigLIP models
for semantic search and image understanding.

Supported Models:
- CLIP (OpenAI): ViT-B/32, ViT-B/16, ViT-L/14
- SigLIP (Google): Base, Large

Features:
- Image → embedding (512-D or 768-D vectors)
- Text → embedding (for semantic search)
- Model caching (lazy loading)
- CPU/GPU support
- Batch processing

Usage:
    from services.embedding_service import get_embedding_service

    service = get_embedding_service()

    # Extract from image
    embedding = service.extract_image_embedding('/path/to/photo.jpg')

    # Extract from text
    query_embedding = service.extract_text_embedding('sunset beach')

    # Search similar images
    results = service.search_similar(query_embedding, top_k=10)
"""

import os
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Union
from dataclasses import dataclass, field
from PIL import Image
import logging
import time

from repository.base_repository import DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class EmbeddingModel:
    """Metadata for an embedding model."""
    model_id: int
    name: str
    variant: str
    version: str
    dimension: int
    runtime: str  # 'cpu', 'gpu_local', 'gpu_remote'


@dataclass
class SearchMetrics:
    """Metrics for a semantic search operation (Phase 1 improvement)."""
    query_text: str
    start_time: float
    end_time: float
    duration_ms: float
    embedding_count: int
    result_count: int
    top_score: float
    avg_score: float
    min_similarity_threshold: float
    cache_hit: bool = False
    batch_count: int = 0
    skipped_embeddings: int = 0
    model_id: int = 0

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/storage."""
        return {
            'query': self.query_text,
            'duration_ms': self.duration_ms,
            'embeddings_searched': self.embedding_count,
            'results_found': self.result_count,
            'top_score': self.top_score,
            'avg_score': self.avg_score,
            'threshold': self.min_similarity_threshold,
            'cache_hit': self.cache_hit,
            'batches': self.batch_count,
            'skipped': self.skipped_embeddings,
        }


class EmbeddingService:
    """
    Service for extracting visual semantic embeddings.

    Architecture:
    - Lazy model loading (only load when first used)
    - Model caching (singleton pattern per model type)
    - Graceful fallback to CPU if GPU unavailable
    - Integration with ml_model registry

    Thread Safety:
    - Model loading is NOT thread-safe (use from main thread)
    - Inference is thread-safe once model loaded
    """

    def __init__(self,
                 db_connection: Optional[DatabaseConnection] = None,
                 device: str = 'auto'):
        """
        Initialize embedding service.

        Args:
            db_connection: Optional database connection
            device: Compute device ('auto', 'cpu', 'cuda', 'mps')
                   'auto' tries GPU first, falls back to CPU
        """
        self.db = db_connection or DatabaseConnection()
        self._device = None
        self._requested_device = device

        # Model cache
        self._clip_model = None
        self._clip_processor = None
        self._clip_model_id = None
        self._clip_variant = None  # Store which variant is loaded

        # Performance metrics (Phase 1 improvement)
        self._search_metrics: List[SearchMetrics] = []

        # Try to import dependencies
        self._torch_available = False
        self._transformers_available = False

        try:
            import torch
            self._torch = torch
            self._torch_available = True
            logger.info("[EmbeddingService] PyTorch available")
        except ImportError:
            logger.warning("[EmbeddingService] PyTorch not available - embeddings disabled")

        try:
            from transformers import CLIPProcessor, CLIPModel
            self._CLIPProcessor = CLIPProcessor
            self._CLIPModel = CLIPModel
            self._transformers_available = True
            logger.info("[EmbeddingService] Transformers available")
        except ImportError:
            logger.warning("[EmbeddingService] Transformers not available - embeddings disabled")

    @property
    def available(self) -> bool:
        """Check if embedding extraction is available."""
        return self._torch_available and self._transformers_available

    @property
    def device(self) -> str:
        """Get actual device being used."""
        if self._device is None:
            self._device = self._detect_device()
        return self._device

    def _detect_device(self) -> str:
        """Detect best available compute device."""
        if not self._torch_available:
            return 'cpu'

        if self._requested_device == 'cpu':
            return 'cpu'

        if self._requested_device == 'cuda' or self._requested_device == 'auto':
            if self._torch.cuda.is_available():
                logger.info("[EmbeddingService] Using CUDA GPU")
                return 'cuda'

        if self._requested_device == 'mps' or self._requested_device == 'auto':
            if hasattr(self._torch.backends, 'mps') and self._torch.backends.mps.is_available():
                logger.info("[EmbeddingService] Using Apple Metal GPU")
                return 'mps'

        logger.info("[EmbeddingService] Using CPU")
        return 'cpu'

    def load_clip_model(self, variant: Optional[str] = None) -> int:
        """
        Load CLIP model from local cache.

        Args:
            variant: Model variant (default: auto-select best available)
                    Options:
                    - 'openai/clip-vit-base-patch32' (512-D, fast)
                    - 'openai/clip-vit-base-patch16' (512-D, better quality)
                    - 'openai/clip-vit-large-patch14' (768-D, best quality)
                    - None (auto-select: large-patch14 > base-patch16 > base-patch32)

        Returns:
            int: Model ID from ml_model table

        Raises:
            RuntimeError: If dependencies not available or model files not found
        """
        if not self.available:
            raise RuntimeError(
                "Embedding extraction not available. "
                "Install: pip install torch transformers pillow"
            )

        # Check if already loaded
        if self._clip_model is not None:
            logger.info(f"[EmbeddingService] CLIP model already loaded (ID: {self._clip_model_id})")
            return self._clip_model_id

        # Auto-select best available model if variant not specified
        from utils.clip_check import check_clip_availability, get_clip_download_status, MODEL_CONFIGS, get_recommended_variant

        if variant is None:
            variant = get_recommended_variant()
            logger.info(f"[EmbeddingService] Auto-selected CLIP variant: {variant}")

        # Check if model files exist locally and get the actual path
        available, message = check_clip_availability(variant)

        if not available:
            logger.error(f"[EmbeddingService] CLIP model not available: {message}")
            config = MODEL_CONFIGS.get(variant, {})
            raise RuntimeError(
                f"CLIP model files not found for {variant}.\n\n"
                f"Please run: python download_clip_model_offline.py --variant {variant}\n\n"
                f"This will download the model files (~{config.get('size_mb', '???')}MB) "
                f"to ./models/{config.get('dir_name', '???')}/"
            )

        # Get the actual model directory path
        status = get_clip_download_status(variant)
        model_path = status.get('model_path')

        if not model_path:
            raise RuntimeError("CLIP model path not found")

        logger.info(f"[EmbeddingService] Loading CLIP model from local path: {model_path}")
        logger.info(message)

        try:
            # Set transformers to use local files only
            os.environ['TRANSFORMERS_OFFLINE'] = '1'

            # Load model and processor directly from the snapshot directory
            self._clip_processor = self._CLIPProcessor.from_pretrained(
                model_path,
                local_files_only=True
            )
            self._clip_model = self._CLIPModel.from_pretrained(
                model_path,
                local_files_only=True
            )
            self._clip_model.to(self.device)
            self._clip_model.eval()  # Set to evaluation mode

            # Get model dimension
            dimension = self._clip_model.config.projection_dim

            logger.info(
                f"[EmbeddingService] ✓ CLIP loaded from local cache: {variant} "
                f"({dimension}-D, device={self.device})"
            )

            # Register in ml_model table
            self._clip_model_id = self._register_model(
                name='clip',
                variant=variant,
                version='1.0',
                task='visual_embedding',
                runtime=self.device,
                dimension=dimension
            )

            # Store variant name for later reference
            self._clip_variant = variant

            logger.info(f"[EmbeddingService] Registered model {self._clip_model_id}: clip/{variant}")

            return self._clip_model_id

        except Exception as e:
            logger.error(f"[EmbeddingService] Failed to load CLIP: {e}")
            raise

    def _register_model(self,
                       name: str,
                       variant: str,
                       version: str,
                       task: str,
                       runtime: str,
                       dimension: int) -> int:
        """
        Register model in ml_model table.

        Returns:
            int: Model ID
        """
        with self.db.get_connection() as conn:
            # Check if model already registered
            cursor = conn.execute("""
                SELECT model_id FROM ml_model
                WHERE name = ? AND variant = ? AND version = ?
            """, (name, variant, version))

            row = cursor.fetchone()
            if row:
                return row["model_id"]

            # Register new model
            cursor = conn.execute("""
                INSERT INTO ml_model (
                    name, variant, version, task, runtime,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, (
                name, variant, version, task, runtime
            ))

            model_id = cursor.lastrowid
            conn.commit()

            logger.info(f"[EmbeddingService] Registered model {model_id}: {name}/{variant}")
            return model_id

    def extract_image_embedding(self,
                               image_path: Union[str, Path],
                               model_id: Optional[int] = None) -> np.ndarray:
        """
        Extract embedding from image.

        Args:
            image_path: Path to image file
            model_id: Optional model ID (auto-loads CLIP if None)

        Returns:
            np.ndarray: Embedding vector (normalized, shape: [dimension])

        Raises:
            FileNotFoundError: If image doesn't exist
            RuntimeError: If extraction fails
        """
        # Ensure model loaded
        if self._clip_model is None:
            self.load_clip_model()

        # Load image
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        try:
            image = Image.open(image_path).convert('RGB')

            # Process image
            inputs = self._clip_processor(
                images=image,
                return_tensors="pt",
                padding=True
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Extract embedding
            with self._torch.no_grad():
                image_features = self._clip_model.get_image_features(**inputs)

                # Normalize to unit length for cosine similarity
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)

            # Convert to numpy
            embedding = image_features.cpu().numpy()[0]

            logger.debug(f"[EmbeddingService] Extracted embedding: {image_path.name} ({embedding.shape})")
            return embedding

        except Exception as e:
            logger.error(f"[EmbeddingService] Failed to extract from {image_path}: {e}")
            raise RuntimeError(f"Embedding extraction failed: {e}")

    def extract_text_embedding(self,
                              text: str,
                              model_id: Optional[int] = None) -> np.ndarray:
        """
        Extract embedding from text query.

        Args:
            text: Search query text
            model_id: Optional model ID (auto-loads CLIP if None)

        Returns:
            np.ndarray: Embedding vector (normalized, shape: [dimension])
        """
        # Ensure model loaded
        if self._clip_model is None:
            self.load_clip_model()

        try:
            # Process text
            inputs = self._clip_processor(
                text=[text],
                return_tensors="pt",
                padding=True
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Extract embedding
            with self._torch.no_grad():
                text_features = self._clip_model.get_text_features(**inputs)

                # Normalize to unit length
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)

            # Convert to numpy
            embedding = text_features.cpu().numpy()[0]

            logger.debug(f"[EmbeddingService] Extracted text embedding: '{text}' ({embedding.shape})")
            return embedding

        except Exception as e:
            logger.error(f"[EmbeddingService] Failed to extract from text '{text}': {e}")
            raise RuntimeError(f"Text embedding extraction failed: {e}")

    def store_embedding(self,
                       photo_id: int,
                       embedding: np.ndarray,
                       model_id: Optional[int] = None) -> None:
        """
        Store embedding in photo_embedding table.

        Args:
            photo_id: Photo ID from photo_metadata table
            embedding: Embedding vector
            model_id: Model ID (uses current CLIP model if None)
        """
        if model_id is None:
            model_id = self._clip_model_id
            if model_id is None:
                raise ValueError("No model loaded - call load_clip_model() first")

        # Convert to blob
        embedding_blob = embedding.astype(np.float32).tobytes()

        logger.debug(
            f"[EmbeddingService] Storing embedding - "
            f"photo_id={photo_id} (type={type(photo_id)}), "
            f"model_id={model_id}, "
            f"dim={len(embedding)}, "
            f"blob_size={len(embedding_blob)} bytes"
        )

        with self.db.get_connection() as conn:
            # Get photo hash for freshness tracking
            cursor = conn.execute(
                "SELECT path FROM photo_metadata WHERE id = ?",
                (photo_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Photo {photo_id} not found")

            # TODO: Compute actual file hash (for now use path as placeholder)
            photo_path = row['path'] if isinstance(row, dict) else row[0]
            source_photo_hash = str(hash(photo_path))

            # Upsert embedding
            conn.execute("""
                INSERT OR REPLACE INTO photo_embedding (
                    photo_id, model_id, embedding_type, dim,
                    embedding, source_photo_hash, artifact_version,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                photo_id,
                model_id,
                'visual_semantic',
                len(embedding),  # dimension
                embedding_blob,
                source_photo_hash,
                '1.0'
            ))

            conn.commit()

            # Verify what was stored
            verify_cursor = conn.execute(
                "SELECT photo_id, dim, length(embedding) FROM photo_embedding WHERE photo_id = ? AND model_id = ?",
                (photo_id, model_id)
            )
            verify_row = verify_cursor.fetchone()
            if verify_row:
                stored_id, stored_dim, stored_size = verify_row["photo_id"], verify_row["dim"], verify_row["length(embedding)"]
                logger.debug(
                    f"[EmbeddingService] ✓ Verified storage - "
                    f"stored_photo_id={stored_id}, stored_dim={stored_dim}, stored_blob_size={stored_size} bytes"
                )

                if stored_size != len(embedding_blob):
                    logger.error(
                        f"[EmbeddingService] ❌ STORAGE CORRUPTION DETECTED! "
                        f"Expected {len(embedding_blob)} bytes but stored {stored_size} bytes!"
                    )
            else:
                logger.error(f"[EmbeddingService] ❌ Embedding not found after insert!")

            logger.debug(f"[EmbeddingService] Stored embedding for photo {photo_id}")

    def search_similar(self,
                      query_embedding: np.ndarray,
                      top_k: int = 10,
                      model_id: Optional[int] = None,
                      photo_ids: Optional[List[int]] = None,
                      min_similarity: float = 0.20,
                      batch_size: int = 1000,
                      progress_callback: Optional[callable] = None,
                      query_text: str = "") -> List[Tuple[int, float]]:
        """
        Search for similar images using cosine similarity with batch processing.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return
            model_id: Model ID to filter by (uses current model if None)
            photo_ids: Optional list of photo IDs to search within
            min_similarity: Minimum similarity threshold (default: 0.20)
                          Only results above this threshold will be returned.
                          Typical values:
                          - 0.15-0.20: Very permissive (may include unrelated images)
                          - 0.25-0.30: Moderate (good balance)
                          - 0.35-0.40: Strict (only close matches)
            batch_size: Number of embeddings to process per batch (default: 1000)
                       Larger batches = faster but more memory
                       Recommended: 500-1000 for desktop, 100-500 for mobile
            progress_callback: Optional callback(current, total, message) for progress updates
            query_text: Optional query text for metrics logging (Phase 1 improvement)

        Returns:
            List of (photo_id, similarity_score) tuples, sorted by score descending
        """
        # Start timing (Phase 1 improvement)
        start_time = time.time()

        if model_id is None:
            model_id = self._clip_model_id
            if model_id is None:
                raise ValueError("No model loaded")

        with self.db.get_connection() as conn:
            # Get total count first for batch processing
            count_query = """
                SELECT COUNT(*) as count
                FROM photo_embedding
                WHERE model_id = ? AND embedding_type = 'visual_semantic'
            """
            count_params = [model_id]

            if photo_ids:
                placeholders = ','.join('?' * len(photo_ids))
                count_query += f" AND photo_id IN ({placeholders})"
                count_params.extend(photo_ids)

            total_count = conn.execute(count_query, count_params).fetchone()["count"]

            if total_count == 0:
                logger.warning("[EmbeddingService] No embeddings found for search")
                return []

            logger.info(
                f"[EmbeddingService] Searching {total_count} embeddings with batch_size={batch_size}"
            )

            # Build query for batch processing
            query = """
                SELECT photo_id, embedding
                FROM photo_embedding
                WHERE model_id = ? AND embedding_type = 'visual_semantic'
            """
            params = [model_id]

            if photo_ids:
                placeholders = ','.join('?' * len(photo_ids))
                query += f" AND photo_id IN ({placeholders})"
                params.extend(photo_ids)

            query += " LIMIT ? OFFSET ?"

            # Process in batches
            all_results = []
            query_norm = query_embedding / np.linalg.norm(query_embedding)
            offset = 0
            batch_num = 1
            total_batches = (total_count + batch_size - 1) // batch_size
            skipped_dim_mismatch = 0
            skipped_errors = 0
            last_progress_time = time.time()

            while offset < total_count:
                # Fetch batch
                cursor = conn.execute(query, params + [batch_size, offset])
                rows = cursor.fetchall()

                if not rows:
                    break

                # Process batch
                batch_results, batch_skipped_dim, batch_skipped_err = self._process_batch(
                    rows, query_norm, min_similarity
                )
                all_results.extend(batch_results)
                skipped_dim_mismatch += batch_skipped_dim
                skipped_errors += batch_skipped_err

                # Progress callback with throttling (every 1000 items or 0.5 seconds)
                now = time.time()
                if progress_callback and (offset % 1000 == 0 or (now - last_progress_time) > 0.5):
                    progress_callback(
                        offset + len(rows),
                        total_count,
                        f"Searching... {offset + len(rows)}/{total_count} embeddings"
                    )
                    last_progress_time = now

                # Log progress
                logger.debug(
                    f"[EmbeddingService] Batch {batch_num}/{total_batches}: "
                    f"processed {len(rows)} embeddings, found {len(batch_results)} above threshold"
                )

                offset += batch_size
                batch_num += 1

            # Sort all results by similarity descending
            all_results.sort(key=lambda x: x[1], reverse=True)

            # Return top K
            top_results = all_results[:top_k]

            # Calculate statistics
            processed_count = total_count - skipped_dim_mismatch - skipped_errors

            # Compute metrics (Phase 1 improvement)
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            metrics = SearchMetrics(
                query_text=query_text,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                embedding_count=total_count,
                result_count=len(all_results),
                top_score=top_results[0][1] if top_results else 0.0,
                avg_score=sum(s for _, s in all_results) / len(all_results) if all_results else 0.0,
                min_similarity_threshold=min_similarity,
                cache_hit=False,  # Set by caller if cached
                batch_count=batch_num - 1,
                skipped_embeddings=skipped_dim_mismatch + skipped_errors,
                model_id=model_id
            )

            # Log metrics
            self._log_search_metrics(metrics)

            # Store metrics (keep last 100 searches)
            self._search_metrics.append(metrics)
            if len(self._search_metrics) > 100:
                self._search_metrics.pop(0)

            # Build detailed log message
            if top_results:
                filtered_count = len(all_results) - len(top_results)
                log_msg = (
                    f"[EmbeddingService] Search complete: "
                    f"{total_count} total embeddings, {processed_count} processed "
                    f"({skipped_dim_mismatch} dimension mismatches, {skipped_errors} errors), "
                    f"{len(all_results)} above threshold (≥{min_similarity:.2f}), "
                    f"returning top {len(top_results)} results, "
                    f"top score={top_results[0][1]:.3f}"
                )
                logger.info(log_msg)

                # Warn about dimension mismatches if significant
                if skipped_dim_mismatch > 0:
                    logger.warning(
                        f"[EmbeddingService] ⚠️ {skipped_dim_mismatch} embeddings skipped due to dimension mismatch! "
                        f"These were likely extracted with a different CLIP model. "
                        f"Consider re-extracting embeddings with Tools → Extract Embeddings to fix this."
                    )
            else:
                # No results - provide detailed diagnosis
                if skipped_dim_mismatch > 0:
                    logger.warning(
                        f"[EmbeddingService] Search found NO results! "
                        f"{total_count} total embeddings: {skipped_dim_mismatch} dimension mismatches, "
                        f"{skipped_errors} errors, {processed_count} processed. "
                        f"❌ All dimension-matched embeddings scored below {min_similarity:.2f}. "
                        f"💡 FIX: Re-extract embeddings with current model (Tools → Extract Embeddings)"
                    )
                elif len(all_results) == 0 and processed_count > 0:
                    logger.warning(
                        f"[EmbeddingService] Search complete but NO results above similarity threshold! "
                        f"{processed_count} embeddings checked, but all scores below {min_similarity:.2f}. "
                        f"Try lowering min_similarity or using different search terms."
                    )
                else:
                    logger.warning(
                        f"[EmbeddingService] Search complete but NO valid embeddings found! "
                        f"Retrieved {total_count} rows from database: {skipped_dim_mismatch} dimension mismatches, "
                        f"{skipped_errors} errors. This suggests embeddings were stored incorrectly."
                    )

            return top_results

    def _process_batch(self,
                      rows: List,
                      query_norm: np.ndarray,
                      min_similarity: float) -> Tuple[List[Tuple[int, float]], int, int]:
        """
        Process a batch of embeddings and compute similarities.

        Args:
            rows: List of (photo_id, embedding_blob) tuples
            query_norm: Normalized query embedding
            min_similarity: Minimum similarity threshold

        Returns:
            Tuple of (batch_results, skipped_dim_mismatch, skipped_errors)
            where batch_results is list of (photo_id, similarity) tuples above threshold
        """
        batch_results = []
        skipped_dim_mismatch = 0
        skipped_errors = 0

        for row in rows:
            photo_id = row["photo_id"]
            embedding_blob = row["embedding"]

            try:
                # Deserialize embedding - handle both bytes and string formats
                if isinstance(embedding_blob, str):
                    # SQLite returned as string - try multiple conversion methods
                    try:
                        embedding_blob = bytes.fromhex(embedding_blob)
                    except (ValueError, TypeError):
                        # Raw binary string - encode to bytes
                        embedding_blob = embedding_blob.encode('latin1')

                # Validate buffer size
                expected_size = len(query_norm) * 4  # query dimensions * 4 bytes per float32
                if len(embedding_blob) != expected_size:
                    # Dimension mismatch
                    actual_dim = len(embedding_blob) // 4
                    if skipped_dim_mismatch == 0:  # Log first occurrence
                        logger.warning(
                            f"[EmbeddingService] Dimension mismatch detected! "
                            f"Photo {photo_id}: embedding is {actual_dim}-D ({len(embedding_blob)} bytes), "
                            f"but query is {len(query_norm)}-D ({expected_size} bytes). "
                            f"This embedding was likely extracted with a different CLIP model. Skipping."
                        )
                    skipped_dim_mismatch += 1
                    continue

                # Compute similarity
                embedding = np.frombuffer(embedding_blob, dtype=np.float32)
                embedding_norm = embedding / np.linalg.norm(embedding)
                similarity = float(np.dot(query_norm, embedding_norm))

                # Filter by threshold
                if similarity >= min_similarity:
                    batch_results.append((photo_id, similarity))

            except Exception as e:
                if skipped_errors == 0:  # Log first error
                    logger.warning(
                        f"[EmbeddingService] Failed to process photo {photo_id}: {e}. "
                        f"Blob type: {type(embedding_blob)}, "
                        f"size: {len(embedding_blob) if hasattr(embedding_blob, '__len__') else 'N/A'}"
                    )
                skipped_errors += 1
                continue

        return batch_results, skipped_dim_mismatch, skipped_errors

    def _log_search_metrics(self, metrics: SearchMetrics):
        """
        Log search performance metrics (Phase 1 improvement).

        Args:
            metrics: SearchMetrics object with performance data
        """
        # Always log duration
        logger.info(
            f"[EmbeddingService] Search completed in {metrics.duration_ms:.0f}ms: "
            f"'{metrics.query_text}' - {metrics.result_count} results, "
            f"top score={metrics.top_score:.3f}"
        )

        # Warn about slow searches (>1 second)
        if metrics.duration_ms > 1000:
            logger.warning(
                f"[EmbeddingService] SLOW SEARCH detected: {metrics.duration_ms:.0f}ms for "
                f"{metrics.embedding_count} embeddings. Consider batch_size tuning."
            )

        # Warn about low match quality
        if metrics.result_count > 0 and metrics.top_score < 0.25:
            logger.warning(
                f"[EmbeddingService] LOW QUALITY results: top score={metrics.top_score:.3f}. "
                f"Consider refining query or checking embeddings."
            )

        # Log detailed metrics at debug level
        logger.debug(f"[EmbeddingService] Search metrics: {metrics.to_dict()}")

    def get_search_statistics(self) -> Dict:
        """
        Get aggregate search statistics (Phase 1 improvement).

        Returns:
            Dictionary with aggregate stats over last 100 searches
        """
        if not self._search_metrics:
            return {}

        return {
            'total_searches': len(self._search_metrics),
            'avg_duration_ms': sum(m.duration_ms for m in self._search_metrics) / len(self._search_metrics),
            'max_duration_ms': max(m.duration_ms for m in self._search_metrics),
            'slow_searches': sum(1 for m in self._search_metrics if m.duration_ms > 1000),
            'avg_results': sum(m.result_count for m in self._search_metrics) / len(self._search_metrics),
            'cache_hit_rate': sum(1 for m in self._search_metrics if m.cache_hit) / len(self._search_metrics),
        }

    def get_all_embeddings_for_project(self, project_id: int, model_id: Optional[int] = None) -> Dict[int, np.ndarray]:
        """
        Get all embeddings for photos in a project (batch load).

        Efficient batch load for similarity detection across entire project.
        Reads from photo_embedding table.

        Args:
            project_id: Project ID
            model_id: Optional model ID (uses current CLIP model if None)

        Returns:
            Dictionary mapping photo_id -> embedding (np.ndarray, float32)
        """
        if model_id is None:
            model_id = self._clip_model_id
            if model_id is None:
                logger.warning("[EmbeddingService] No model loaded - cannot get embeddings")
                return {}

        embeddings = {}

        with self.db.get_connection() as conn:
            query = """
                SELECT pe.photo_id, pe.embedding, pe.dim
                FROM photo_embedding pe
                JOIN photo_metadata p ON pe.photo_id = p.id
                WHERE p.project_id = ? AND pe.model_id = ? AND pe.embedding_type = 'visual_semantic'
            """

            cursor = conn.execute(query, (project_id, model_id))

            for row in cursor.fetchall():
                photo_id = row['photo_id']
                embedding_blob = row['embedding']
                stored_dim = row['dim']

                if embedding_blob is None:
                    continue

                try:
                    # Decode embedding
                    embedding = np.frombuffer(embedding_blob, dtype=np.float32)

                    if len(embedding) == stored_dim:
                        embeddings[photo_id] = embedding
                    else:
                        logger.warning(
                            f"[EmbeddingService] Embedding dimension mismatch for photo {photo_id}: "
                            f"expected {stored_dim}, got {len(embedding)}"
                        )
                except Exception as e:
                    logger.warning(f"[EmbeddingService] Failed to decode embedding for photo {photo_id}: {e}")

        logger.info(f"[EmbeddingService] Loaded {len(embeddings)} embeddings for project {project_id}")
        return embeddings

    def get_embedding(self, photo_id: int, model_id: Optional[int] = None) -> Optional[np.ndarray]:
        """
        Get embedding for a single photo.

        Args:
            photo_id: Photo ID
            model_id: Optional model ID (uses current CLIP model if None)

        Returns:
            Embedding array or None if not found
        """
        if model_id is None:
            model_id = self._clip_model_id
            if model_id is None:
                return None

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT embedding, dim FROM photo_embedding
                WHERE photo_id = ? AND model_id = ? AND embedding_type = 'visual_semantic'
                """,
                (photo_id, model_id)
            )
            row = cursor.fetchone()

            if row and row['embedding']:
                try:
                    embedding = np.frombuffer(row['embedding'], dtype=np.float32)
                    if len(embedding) == row['dim']:
                        return embedding
                except Exception as e:
                    logger.warning(f"[EmbeddingService] Failed to decode embedding for photo {photo_id}: {e}")

        return None

    def get_embedding_count(self, model_id: Optional[int] = None) -> int:
        """Get count of stored embeddings."""
        with self.db.get_connection() as conn:
            if model_id:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM photo_embedding WHERE model_id = ?",
                    (model_id,)
                )
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM photo_embedding")

            return cursor.fetchone()["COUNT(*)"]

    def clear_embeddings(self, model_id: Optional[int] = None) -> int:
        """
        Clear embeddings (useful for model upgrades).

        Args:
            model_id: Optional model ID to filter by (clears all if None)

        Returns:
            int: Number of embeddings deleted
        """
        with self.db.get_connection() as conn:
            if model_id:
                cursor = conn.execute(
                    "DELETE FROM photo_embedding WHERE model_id = ?",
                    (model_id,)
                )
            else:
                cursor = conn.execute("DELETE FROM photo_embedding")

            deleted = cursor.rowcount
            conn.commit()

            logger.info(f"[EmbeddingService] Cleared {deleted} embeddings")
            return deleted


# Singleton instance
_embedding_service = None


def get_embedding_service(device: str = 'auto') -> EmbeddingService:
    """
    Get singleton embedding service instance.

    Args:
        device: Compute device ('auto', 'cpu', 'cuda', 'mps')

    Returns:
        EmbeddingService instance
    """
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(device=device)
    return _embedding_service
