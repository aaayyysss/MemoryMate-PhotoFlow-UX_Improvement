# CLIP Models Directory

This directory is where the application looks for CLIP embedding models.

## Quick Start

Download models using one of these methods:

### Option 1: Automatic Download (Recommended)
```bash
# Download the large model (best quality, ~1.7GB)
python download_clip_large.py

# OR download a specific variant
python download_clip_model_offline.py --variant openai/clip-vit-large-patch14
```

### Option 2: Manual Download via Application
1. Launch MemoryMate-PhotoFlow
2. Go to **Preferences** (Ctrl+,)
3. Navigate to **"🔍 Visual Embeddings"** section
4. Click **"Download CLIP Model"**

## Expected Directory Structure

After downloading, you should have:

```
models/
  openai--clip-vit-base-patch32/    (or other variant)
    snapshots/
      <commit_hash>/
        config.json
        pytorch_model.bin
        preprocessor_config.json
        tokenizer_config.json
        vocab.json
        merges.txt
        tokenizer.json
        special_tokens_map.json
    refs/
      main
```

## Supported Model Variants

1. **openai/clip-vit-large-patch14** (Recommended)
   - Best quality semantic search
   - 768-D embeddings
   - ~1.7GB download

2. **openai/clip-vit-base-patch16**
   - Good balance of quality and speed
   - 512-D embeddings
   - ~600MB download

3. **openai/clip-vit-base-patch32** (Fastest)
   - Fast inference
   - 512-D embeddings
   - ~600MB download

## Troubleshooting

### Model Not Detected?

Run the checker script:
```bash
python check_clip_models.py
```

This will show:
- Which models are installed
- Where they're located
- Any missing files

### Manual Model Placement

If you have model files from another source:

1. Create the directory structure shown above
2. Place all model files in the `snapshots/<commit_hash>/` directory
3. Create `refs/main` file containing the commit hash
4. Run `python check_clip_models.py` to verify

## Common Issues

**Issue**: "No CLIP models found in models/ directory!"

**Solution**: The models directory exists but contains no model files. Run the download script.

**Issue**: "Embedding extraction failed"

**Solution**:
1. Verify PyTorch is installed: `pip install torch transformers`
2. Check model files are complete: `python check_clip_models.py`
3. Ensure you have enough disk space (~2GB free)

## Legacy Directory Names

The application also checks for legacy directory names for backward compatibility:
- `clip-vit-large-patch14/` → `openai--clip-vit-large-patch14/`
- `clip-vit-base-patch16/` → `openai--clip-vit-base-patch16/`
- `clip-vit-base-patch32/` → `openai--clip-vit-base-patch32/`

Both naming conventions are supported.

## After Installation

Once models are downloaded:
1. Restart the application (or just continue if already running)
2. Go to **Tools → Extract Embeddings**
3. The app will automatically detect and use the best available model
4. Wait for extraction to complete
5. Semantic search will be available in the search toolbar
