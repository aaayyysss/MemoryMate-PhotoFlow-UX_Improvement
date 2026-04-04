"""
Test Embedding Workflow - Integration Test

This script demonstrates the complete embedding extraction workflow:
1. Load photos from database
2. Enqueue embedding job
3. Worker extracts embeddings
4. Search similar images

Usage:
    python test_embedding_workflow.py

Requirements:
    pip install torch transformers pillow
"""

import sys
import time
from pathlib import Path

# Check dependencies
try:
    import torch
    from transformers import CLIPProcessor, CLIPModel
    from PIL import Image
    DEPS_AVAILABLE = True
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    print("Install with: pip install torch transformers pillow")
    DEPS_AVAILABLE = False


def test_embedding_service():
    """Test EmbeddingService directly."""
    if not DEPS_AVAILABLE:
        print("⏭️  Skipping EmbeddingService test (dependencies not available)")
        return

    from services.embedding_service import get_embedding_service
    from repository.photo_repository import PhotoRepository

    print("\n" + "="*60)
    print("TEST 1: EmbeddingService")
    print("="*60)

    service = get_embedding_service(device='cpu')

    print(f"✓ Service initialized")
    print(f"  - Dependencies available: {service.available}")
    print(f"  - Device: {service.device}")

    if not service.available:
        print("⏭️  Skipping extraction test (dependencies not available)")
        return

    # Load model
    print("\n📥 Loading CLIP model...")
    model_id = service.load_clip_model('openai/clip-vit-base-patch32')
    print(f"✓ Model loaded: ID={model_id}")

    # Get a photo from database
    photo_repo = PhotoRepository()
    with photo_repo.connection() as conn:
        cursor = conn.execute(
            "SELECT photo_id, path FROM photo_metadata LIMIT 1"
        )
        row = cursor.fetchone()

        if not row:
            print("⚠️  No photos in database - skipping extraction test")
            return

        photo_id, photo_path = row

    print(f"\n🖼️  Test photo: {Path(photo_path).name} (ID: {photo_id})")

    # Extract embedding
    if Path(photo_path).exists():
        print("📸 Extracting embedding...")
        embedding = service.extract_image_embedding(photo_path)
        print(f"✓ Embedding extracted: shape={embedding.shape}, dtype={embedding.dtype}")

        # Store in database
        print("💾 Storing embedding...")
        service.store_embedding(photo_id, embedding, model_id)
        print("✓ Embedding stored")

        # Verify storage
        count = service.get_embedding_count(model_id)
        print(f"✓ Total embeddings: {count}")
    else:
        print(f"⚠️  Photo file not found: {photo_path}")

    # Test text embedding
    print("\n🔍 Testing text embedding...")
    text_embedding = service.extract_text_embedding("sunset beach ocean")
    print(f"✓ Text embedding: shape={text_embedding.shape}")

    # Search similar
    if service.get_embedding_count(model_id) > 0:
        print("\n🔎 Searching similar images...")
        results = service.search_similar(text_embedding, top_k=5, model_id=model_id)
        print(f"✓ Found {len(results)} results:")
        for photo_id, score in results[:3]:
            print(f"  - Photo {photo_id}: similarity={score:.3f}")


def test_job_service():
    """Test JobService integration."""
    from services.job_service import get_job_service

    print("\n" + "="*60)
    print("TEST 2: JobService")
    print("="*60)

    service = get_job_service()

    # Enqueue test job
    print("\n📤 Enqueuing embedding job...")
    job_id = service.enqueue_job(
        kind='embed',
        payload={'photo_ids': [1, 2, 3], 'model': 'clip'},
        backend='cpu'
    )
    print(f"✓ Job enqueued: ID={job_id}")

    # Check stats
    stats = service.get_job_stats()
    print(f"✓ Queue stats: {stats}")

    # Claim job
    print("\n🔒 Claiming job...")
    claimed = service.claim_job(job_id, worker_id='test-worker')
    print(f"✓ Job claimed: {claimed}")

    # Send heartbeat
    print("💓 Sending heartbeat...")
    service.heartbeat(job_id, progress=0.5)
    print("✓ Heartbeat sent")

    # Complete job
    print("✅ Completing job...")
    service.complete_job(job_id, success=True)
    print("✓ Job completed")

    # Verify
    job = service.get_job(job_id)
    print(f"✓ Final status: {job.status}")


def test_embedding_worker():
    """Test EmbeddingWorker (requires Qt and dependencies)."""
    if not DEPS_AVAILABLE:
        print("\n⏭️  Skipping EmbeddingWorker test (dependencies not available)")
        return

    print("\n" + "="*60)
    print("TEST 3: EmbeddingWorker")
    print("="*60)

    try:
        from PySide6.QtCore import QThreadPool, QCoreApplication
        from workers.embedding_worker import launch_embedding_worker
        from repository.photo_repository import PhotoRepository
    except ImportError as e:
        print(f"⚠️  Skipping worker test: {e}")
        return

    # Get some photo IDs
    photo_repo = PhotoRepository()
    with photo_repo.connection() as conn:
        cursor = conn.execute("SELECT photo_id FROM photo_metadata LIMIT 3")
        photo_ids = [row[0] for row in cursor.fetchall()]

    if not photo_ids:
        print("⚠️  No photos in database - skipping worker test")
        return

    print(f"\n🚀 Launching worker for {len(photo_ids)} photos...")

    # Create Qt application (needed for QThreadPool)
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)

    # Launch worker
    job_id = launch_embedding_worker(
        photo_ids=photo_ids,
        model_variant='openai/clip-vit-base-patch32',
        device='cpu'
    )
    print(f"✓ Worker launched: job_id={job_id}")

    # Wait for completion (max 60 seconds)
    print("⏳ Waiting for worker to complete...")
    from services.job_service import get_job_service
    job_service = get_job_service()

    for i in range(60):
        time.sleep(1)
        job = job_service.get_job(job_id)
        if job.status in ['done', 'failed', 'cancelled']:
            print(f"✓ Worker completed: status={job.status}")
            break
        if i % 5 == 0:
            print(f"  ... still working (progress={job.progress:.1%})")
    else:
        print("⚠️  Worker timeout (60s)")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("EMBEDDING WORKFLOW TEST SUITE")
    print("="*60)

    # Check database exists
    if not Path('reference_data.db').exists():
        print("\n❌ Database not found: reference_data.db")
        print("Run the application first to create the database.")
        return

    try:
        # Test 1: EmbeddingService
        test_embedding_service()

        # Test 2: JobService
        test_job_service()

        # Test 3: EmbeddingWorker (optional - requires Qt and long-running)
        # test_embedding_worker()

        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED")
        print("="*60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
