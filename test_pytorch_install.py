"""
Diagnostic script to test PyTorch installation.
Run this to check if PyTorch is properly installed.
"""

import sys
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print()

# Test 1: Check if torch is installed
print("=" * 60)
print("TEST 1: Checking if PyTorch is installed...")
print("=" * 60)
try:
    import torch
    print("✓ PyTorch is installed")
    print(f"  Version: {torch.__version__}")
    print(f"  Location: {torch.__file__}")
except ImportError as e:
    print(f"✗ PyTorch is NOT installed: {e}")
    print("\nTo install PyTorch, run:")
    print("  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
    sys.exit(1)
except Exception as e:
    print(f"✗ Error loading PyTorch: {e}")
    print(f"  Error type: {type(e).__name__}")
    print("\nThis is likely a DLL loading error on Windows.")
    print("Solutions:")
    print("  1. Install Visual C++ Redistributable 2015-2022:")
    print("     https://aka.ms/vs/17/release/vc_redist.x64.exe")
    print("  2. Reinstall PyTorch:")
    print("     pip uninstall torch torchvision")
    print("     pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
    sys.exit(1)

# Test 2: Check CUDA availability
print("\n" + "=" * 60)
print("TEST 2: Checking CUDA availability...")
print("=" * 60)
if torch.cuda.is_available():
    print(f"✓ CUDA is available")
    print(f"  CUDA version: {torch.version.cuda}")
    print(f"  Device count: {torch.cuda.device_count()}")
    print(f"  Device name: {torch.cuda.get_device_name(0)}")
else:
    print("✗ CUDA is NOT available (will use CPU)")

# Test 3: Check MPS (Mac) availability
print("\n" + "=" * 60)
print("TEST 3: Checking MPS (Apple Silicon) availability...")
print("=" * 60)
if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
    print("✓ MPS is available")
else:
    print("✗ MPS is NOT available")

# Test 4: Test basic tensor operations
print("\n" + "=" * 60)
print("TEST 4: Testing basic tensor operations...")
print("=" * 60)
try:
    x = torch.tensor([1.0, 2.0, 3.0])
    y = torch.tensor([4.0, 5.0, 6.0])
    z = x + y
    print(f"✓ Basic tensor operations work")
    print(f"  {x.tolist()} + {y.tolist()} = {z.tolist()}")
except Exception as e:
    print(f"✗ Basic tensor operations failed: {e}")
    sys.exit(1)

# Test 5: Check transformers
print("\n" + "=" * 60)
print("TEST 5: Checking transformers library...")
print("=" * 60)
try:
    import transformers
    print("✓ transformers is installed")
    print(f"  Version: {transformers.__version__}")
except ImportError:
    print("✗ transformers is NOT installed")
    print("\nTo install transformers, run:")
    print("  pip install transformers")
except Exception as e:
    print(f"✗ Error loading transformers: {e}")

# Test 6: Check PIL/Pillow
print("\n" + "=" * 60)
print("TEST 6: Checking PIL/Pillow...")
print("=" * 60)
try:
    from PIL import Image
    print("✓ Pillow is installed")
    import PIL
    print(f"  Version: {PIL.__version__}")
except ImportError:
    print("✗ Pillow is NOT installed")
    print("\nTo install Pillow, run:")
    print("  pip install pillow")
except Exception as e:
    print(f"✗ Error loading Pillow: {e}")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print("✓ All tests passed! PyTorch is ready to use.")
print("\nYou can now run the embedding extraction.")
print("Use: Tools → AI & Semantic Search → Extract Embeddings")
