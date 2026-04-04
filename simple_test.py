import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

try:
    from services.semantic_embedding_service import SemanticEmbeddingService
    print("✅ Successfully imported SemanticEmbeddingService")
    
    # Test creating service with non-existent model
    service = SemanticEmbeddingService(model_name="test-nonexistent-model")
    print("✅ Successfully created service instance")
    
    # Try to load model (should fail gracefully)
    try:
        service._load_model()
        print("❌ Unexpected success - model loading should have failed")
    except RuntimeError as e:
        error_str = str(e)
        if "not found offline" in error_str:
            print("✅ Correctly detected missing model without blocking")
        else:
            print(f"⚠️  Different error: {error_str}")
    except Exception as e:
        print(f"❌ Unexpected exception type: {type(e).__name__}")
        
except ImportError as e:
    print(f"❌ Import failed: {e}")
except Exception as e:
    print(f"❌ Other error: {type(e).__name__}: {e}")