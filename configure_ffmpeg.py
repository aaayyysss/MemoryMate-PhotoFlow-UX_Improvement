#!/usr/bin/env python3
"""
Configure FFmpeg/FFprobe Path for MemoryMate-PhotoFlow

This script helps users configure a custom path to ffprobe for video metadata extraction.
Useful for users without admin rights who can't add ffmpeg to system PATH.

Usage:
    python configure_ffmpeg.py

The script will:
1. Check if ffprobe is available in system PATH
2. Allow you to specify a custom path if needed
3. Test the custom path
4. Save the configuration
"""

import os
import sys
import subprocess
from pathlib import Path


def check_ffprobe(path='ffprobe'):
    """Check if ffprobe is available at given path."""
    try:
        result = subprocess.run(
            [path, '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Extract version info
            first_line = result.stdout.split('\n')[0] if result.stdout else ''
            return True, first_line
        return False, "ffprobe found but returned error"
    except FileNotFoundError:
        return False, "ffprobe not found"
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 70)
    print("MEMORYMATE-PHOTOFLOW - FFmpeg Configuration Helper")
    print("=" * 70)
    print()

    # Check system PATH first
    print("Step 1: Checking system PATH for ffprobe...")
    available, message = check_ffprobe()

    if available:
        print(f"✓ ffprobe found in system PATH")
        print(f"  {message}")
        print()
        response = input("Do you want to use the system ffprobe? (Y/n): ").strip().lower()
        if response != 'n':
            print()
            print("✓ Using system ffprobe (no custom path needed)")
            print("  Settings will use default PATH lookup")

            # Clear any existing custom path
            try:
                from settings_manager_qt import SettingsManager
                settings = SettingsManager()
                settings.set('ffprobe_path', '')
                print("  Cleared any existing custom path from settings")
            except Exception as e:
                print(f"  Note: Could not update settings: {e}")

            return
    else:
        print(f"✗ ffprobe not found in system PATH")
        print(f"  {message}")
        print()

    # Ask for custom path
    print("Step 2: Enter custom ffprobe path")
    print()
    print("Please enter the full path to ffprobe.exe (or ffprobe on Linux/Mac).")
    print("Examples:")
    print("  Windows: C:\\ffmpeg\\bin\\ffprobe.exe")
    print("  Linux:   /home/user/ffmpeg/bin/ffprobe")
    print("  Mac:     /Users/user/ffmpeg/bin/ffprobe")
    print()

    while True:
        custom_path = input("ffprobe path (or 'quit' to exit): ").strip()

        if custom_path.lower() == 'quit':
            print("Exiting without saving.")
            return

        if not custom_path:
            print("  Please enter a path or type 'quit'")
            continue

        # Remove quotes if user copy-pasted with them
        custom_path = custom_path.strip('"').strip("'")

        # Check if path exists
        if not Path(custom_path).exists():
            print(f"  ✗ Path does not exist: {custom_path}")
            print("    Please check the path and try again")
            continue

        # Test the path
        print(f"  Testing ffprobe at: {custom_path}")
        available, message = check_ffprobe(custom_path)

        if available:
            print(f"  ✓ ffprobe works!")
            print(f"    {message}")
            break
        else:
            print(f"  ✗ ffprobe test failed: {message}")
            print("    Please check the path and try again")
            continue

    # Save to settings
    print()
    print("Step 3: Saving configuration...")
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        settings.set('ffprobe_path', custom_path)
        print("  ✓ Configuration saved to photo_app_settings.json")
        print()
        print("=" * 70)
        print("SUCCESS!")
        print("=" * 70)
        print()
        print("MemoryMate-PhotoFlow will now use ffprobe from:")
        print(f"  {custom_path}")
        print()
        print("Video metadata extraction should work correctly.")
        print("Restart the app to apply changes.")
    except Exception as e:
        print(f"  ✗ Failed to save settings: {e}")
        print()
        print("You can manually edit photo_app_settings.json and add:")
        print(f'  "ffprobe_path": "{custom_path}"')
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Exiting without saving.")
        sys.exit(0)
