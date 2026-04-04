#!/usr/bin/env python3
"""
Mobile Device Detection Diagnostic Tool

This script helps diagnose why mobile devices are not being detected.
Run this script when your device is connected to see detailed debug information.

Usage:
    python debug_device_detection.py
"""

import os
import platform
from pathlib import Path
import sys


def print_header(title):
    """Print a formatted section header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def check_platform():
    """Display platform information"""
    print_header("PLATFORM INFORMATION")
    print(f"Operating System: {platform.system()}")
    print(f"OS Version: {platform.version()}")
    print(f"Python Version: {sys.version}")
    print(f"User: {os.getenv('USER', 'unknown')}")


def scan_windows_drives():
    """Scan Windows drives for DCIM folders"""
    print_header("WINDOWS DRIVE SCAN")

    for drive_letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive_path = f"{drive_letter}:\\"
        exists = os.path.exists(drive_path)

        if exists:
            dcim_path = Path(drive_path) / "DCIM"
            has_dcim = dcim_path.exists() and dcim_path.is_dir()

            print(f"\n{drive_letter}:\\ - EXISTS")
            print(f"  DCIM folder: {'✓ FOUND' if has_dcim else '✗ Not found'}")

            if has_dcim:
                try:
                    subfolders = list(dcim_path.iterdir())
                    print(f"  DCIM subfolders ({len(subfolders)}):")
                    for subfolder in subfolders[:10]:  # Show first 10
                        if subfolder.is_dir():
                            print(f"    - {subfolder.name}/")
                except Exception as e:
                    print(f"  ERROR reading DCIM: {e}")


def scan_macos_volumes():
    """Scan macOS volumes for DCIM folders"""
    print_header("MACOS VOLUMES SCAN")

    volumes_path = Path("/Volumes")

    if not volumes_path.exists():
        print("❌ /Volumes directory not found")
        return

    print(f"✓ /Volumes directory exists")

    try:
        volumes = list(volumes_path.iterdir())
        print(f"\nFound {len(volumes)} volume(s):\n")

        for volume in volumes:
            if not volume.is_dir():
                continue

            dcim_path = volume / "DCIM"
            has_dcim = dcim_path.exists() and dcim_path.is_dir()

            # Skip system volumes
            is_system = volume.name in ("Macintosh HD", "System")

            print(f"  {volume.name}")
            print(f"    Path: {volume}")
            print(f"    System volume: {'Yes (SKIP)' if is_system else 'No'}")
            print(f"    DCIM folder: {'✓ FOUND' if has_dcim else '✗ Not found'}")

            if has_dcim:
                try:
                    subfolders = list(dcim_path.iterdir())
                    print(f"    DCIM subfolders ({len(subfolders)}):")
                    for subfolder in subfolders[:10]:
                        if subfolder.is_dir():
                            print(f"      - {subfolder.name}/")
                except Exception as e:
                    print(f"    ERROR reading DCIM: {e}")
            print()

    except Exception as e:
        print(f"❌ ERROR scanning volumes: {e}")


def scan_linux_mounts():
    """Scan Linux mount points for DCIM folders"""
    print_header("LINUX MOUNT POINTS SCAN")

    user = os.getenv("USER")
    mount_bases = [
        "/media",
        "/mnt",
        "/run/media",
    ]

    if user:
        mount_bases.extend([
            f"/media/{user}",
            f"/run/media/{user}",
        ])

    print(f"Current user: {user}")
    print(f"\nScanning {len(mount_bases)} mount location(s):\n")

    found_any = False

    for base in mount_bases:
        base_path = Path(base)
        exists = base_path.exists()

        print(f"📁 {base}")
        print(f"   Exists: {'✓ Yes' if exists else '✗ No'}")

        if not exists:
            print()
            continue

        try:
            mount_points = list(base_path.iterdir())
            print(f"   Mount points: {len(mount_points)}")

            for mount_point in mount_points:
                if not mount_point.is_dir():
                    continue

                found_any = True
                dcim_path = mount_point / "DCIM"
                has_dcim = dcim_path.exists() and dcim_path.is_dir()

                print(f"\n   ├─ {mount_point.name}")
                print(f"   │  Path: {mount_point}")
                print(f"   │  DCIM: {'✓ FOUND' if has_dcim else '✗ Not found'}")

                if has_dcim:
                    try:
                        subfolders = list(dcim_path.iterdir())
                        print(f"   │  DCIM subfolders ({len(subfolders)}):")
                        for subfolder in subfolders[:10]:
                            if subfolder.is_dir():
                                print(f"   │    - {subfolder.name}/")
                    except Exception as e:
                        print(f"   │  ERROR reading DCIM: {e}")

        except PermissionError:
            print(f"   ❌ Permission denied")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")

        print()

    if not found_any:
        print("⚠️  No mount points found in any location!")


def check_mtp_tools():
    """Check if MTP tools are installed (Linux only)"""
    print_header("MTP TOOLS CHECK (Linux)")

    commands = [
        ("mtp-detect", "MTP device detection tool"),
        ("jmtpfs", "FUSE-based MTP filesystem"),
        ("gio", "GNOME I/O library (includes MTP support)"),
    ]

    for cmd, description in commands:
        try:
            result = os.system(f"which {cmd} > /dev/null 2>&1")
            installed = (result == 0)
            print(f"  {cmd:15} {'✓ Installed' if installed else '✗ Not found'} - {description}")
        except Exception as e:
            print(f"  {cmd:15} ❌ Error checking: {e}")

    print("\nTo install MTP tools:")
    print("  Ubuntu/Debian: sudo apt install mtp-tools libmtp-common libmtp-runtime")
    print("  Fedora:        sudo dnf install mtp-tools")
    print("  Arch:          sudo pacman -S libmtp")


def check_ios_tools():
    """Check if iOS tools are installed (macOS/Linux)"""
    print_header("iOS DEVICE TOOLS CHECK")

    system = platform.system()

    if system == "Darwin":
        print("macOS: iOS devices should work natively")
        print("  - Make sure to click 'Trust This Computer' on your iPhone")
        print("  - Device should appear in /Volumes/")

    elif system == "Linux":
        commands = [
            ("idevicepair", "iOS device pairing tool"),
            ("ifuse", "FUSE filesystem for iOS devices"),
        ]

        for cmd, description in commands:
            try:
                result = os.system(f"which {cmd} > /dev/null 2>&1")
                installed = (result == 0)
                print(f"  {cmd:15} {'✓ Installed' if installed else '✗ Not found'} - {description}")
            except Exception as e:
                print(f"  {cmd:15} ❌ Error checking: {e}")

        print("\nTo install iOS tools:")
        print("  Ubuntu/Debian: sudo apt install libimobiledevice-utils ifuse")
        print("  Fedora:        sudo dnf install libimobiledevice-utils ifuse")
        print("  Arch:          sudo pacman -S libimobiledevice ifuse")


def test_device_scanner():
    """Test the actual DeviceScanner from the app"""
    print_header("TESTING APP'S DeviceScanner")

    try:
        # Add parent directory to path to import services
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from services.device_sources import DeviceScanner, scan_mobile_devices

        print("✓ Successfully imported DeviceScanner\n")

        scanner = DeviceScanner()
        print(f"Platform detected: {scanner.system}")

        print("\nScanning for devices...")
        devices = scanner.scan_devices()

        print(f"\n{'='*70}")
        print(f"  RESULT: Found {len(devices)} device(s)")
        print(f"{'='*70}\n")

        if devices:
            for i, device in enumerate(devices, 1):
                print(f"Device {i}:")
                print(f"  Label: {device.label}")
                print(f"  Root Path: {device.root_path}")
                print(f"  Type: {device.device_type}")
                print(f"  Folders: {len(device.folders)}")

                for folder in device.folders:
                    print(f"    • {folder.name} ({folder.photo_count} photos)")
                    print(f"      Path: {folder.path}")
                print()
        else:
            print("❌ No devices detected!")
            print("\nPossible reasons:")
            print("  1. Device not connected via USB")
            print("  2. USB connection mode not set to 'File Transfer' (Android)")
            print("  3. 'Trust This Computer' not tapped (iOS)")
            print("  4. Device not mounted by operating system")
            print("  5. DCIM folder not found on device")
            print("  6. Missing system tools (MTP/iOS tools)")

    except ImportError as e:
        print(f"❌ Failed to import DeviceScanner: {e}")
        print("Make sure you're running this script from the app directory")
    except Exception as e:
        print(f"❌ Error running DeviceScanner: {e}")
        import traceback
        traceback.print_exc()


def print_troubleshooting_guide():
    """Print troubleshooting steps"""
    print_header("TROUBLESHOOTING GUIDE")

    system = platform.system()

    print("\n📱 ANDROID DEVICES:\n")
    print("  1. Connect device via USB cable")
    print("  2. On phone: Swipe down notification panel")
    print("  3. Tap 'USB' notification")
    print("  4. Select 'File Transfer' or 'MTP' mode")
    print("  5. On Linux: Install mtp-tools if not already installed")
    print("  6. Device should appear in file manager first")
    print()

    print("📱 iOS DEVICES (iPhone/iPad):\n")
    print("  1. Connect device via USB cable")
    print("  2. On phone: Tap 'Trust This Computer' when prompted")
    print("  3. Enter device passcode if asked")

    if system == "Darwin":
        print("  4. Device should appear in /Volumes/")
    elif system == "Linux":
        print("  4. Install libimobiledevice-utils and ifuse")
        print("  5. Pair device: idevicepair pair")
        print("  6. Mount device manually or use GNOME/KDE auto-mount")
    elif system == "Windows":
        print("  4. Install iTunes or Apple Mobile Device Support")
        print("  5. Device should appear as a drive letter")
    print()

    print("💾 SD CARDS:\n")
    print("  1. Insert SD card into card reader")
    print("  2. Connect card reader to computer")
    print("  3. SD card should have a DCIM folder")
    print("  4. Device should auto-mount")
    print()

    print("🔍 VERIFICATION:\n")
    print("  1. Open your file manager (Finder/Explorer/Nautilus)")
    print("  2. Locate the device/card")
    print("  3. Verify you can see a 'DCIM' folder")
    print("  4. If you can see DCIM in file manager, app should detect it")
    print("  5. If app doesn't detect it, check the scan results above")


def main():
    """Main diagnostic routine"""
    print("\n" + "="*70)
    print("  📱 MOBILE DEVICE DETECTION DIAGNOSTIC TOOL")
    print("="*70)
    print("\nThis tool will help diagnose why your mobile device is not detected.")
    print("Please connect your device before running this script.")

    input("\nPress ENTER to start diagnostic scan... ")

    # Platform info
    check_platform()

    # Platform-specific scans
    system = platform.system()

    if system == "Windows":
        scan_windows_drives()
    elif system == "Darwin":
        scan_macos_volumes()
        check_ios_tools()
    elif system == "Linux":
        scan_linux_mounts()
        check_mtp_tools()
        check_ios_tools()
    else:
        print(f"\n⚠️  Unsupported platform: {system}")

    # Test actual scanner
    test_device_scanner()

    # Troubleshooting guide
    print_troubleshooting_guide()

    print("\n" + "="*70)
    print("  DIAGNOSTIC COMPLETE")
    print("="*70)
    print("\nIf devices are still not detected:")
    print("  1. Share this diagnostic output with the developer")
    print("  2. Check MOBILE_DEVICE_GUIDE.md for manual setup")
    print("  3. Verify device appears in your system's file manager")
    print("\n")


if __name__ == "__main__":
    main()
