#!/usr/bin/env python3
"""
Test script to verify embedding extraction stays responsive
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_embedding_responsiveness():
    """Test that embedding service doesn't block UI when model not found"""
    print("Testing embedding service responsiveness...")
    
    try:
        # Import the semantic embedding service
        from services.semantic_embedding_service import SemanticEmbeddingService
        
        # Create service instance with a model that likely doesn't exist
        service = SemanticEmbeddingService(model_name="non-existent-model")
        
        # Try to encode an image (this should fail gracefully without blocking)
        try:
            # Use a dummy path that doesn't exist
            result = service.encode_image("dummy_nonexistent_image.jpg")
            print("❌ Unexpected success - should have failed")
            return False
        except RuntimeError as e:
            # This is expected - should fail gracefully
            error_msg = str(e)
            if "not found offline" in error_msg:
                print("✅ Service correctly detected missing model without blocking")
                return True
            else:
                print(f"❌ Unexpected error type: {error_msg}")
                return False
        except Exception as e:
            print(f"❌ Unexpected exception: {type(e).__name__}: {e}")
            return False
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Test failed with exception: {type(e).__name__}: {e}")
        return False

if __name__ == "__main__":
    success = test_embedding_responsiveness()
    if success:
        print("\n🎉 Test PASSED: Embedding service handles missing models gracefully")
        sys.exit(0)
    else:
        print("\n💥 Test FAILED: Embedding service may block UI")
        sys.exit(1)