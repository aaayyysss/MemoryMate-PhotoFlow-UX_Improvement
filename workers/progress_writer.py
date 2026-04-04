# common helper: progress_writer.py

import json, os, time

def write_status(status_path, phase, current, total):
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump({
            "phase": phase,
            "current": current,
            "total": total,
            "percent": round((current / total) * 100, 1) if total else 0,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f)