# Quick script to identify a problematic image file by iterating and doing os.stat + Pillow open.
# Usage: python test_open_images.py "C:\path\to\folder"

import sys, os, time
from PIL import Image

def find_problem(root):
    supported = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic")
    files = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            if n.lower().endswith(supported):
                files.append(os.path.join(dirpath, n))
    print(f"[TEST] Found {len(files)} files")
    for i, p in enumerate(files, 1):
        print(f"[TEST] {i}/{len(files)} stat: {p}", flush=True)
        try:
            st = os.stat(p)
            print(f"[TEST] size={st.st_size} mtime={st.st_mtime}", flush=True)
        except Exception as e:
            print(f"[TEST] os.stat FAILED for {p}: {e}", flush=True)
            continue
        print(f"[TEST] opening {p}", flush=True)
        try:
            with Image.open(p) as img:
                print(f"[TEST] opened ok size={img.size} mode={img.mode}", flush=True)
        except Exception as e:
            print(f"[TEST] Image.open FAILED for {p}: {e}", flush=True)
            # do not exit — continue to find others
    print("[TEST] done")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_open_images.py <folder>")
        sys.exit(1)
    find_problem(sys.argv[1])