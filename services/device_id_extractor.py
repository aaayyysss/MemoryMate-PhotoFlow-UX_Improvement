"""
Device ID Extraction Service

Extracts unique, persistent device identifiers for mobile devices,
cameras, USB drives, and SD cards across Windows, macOS, and Linux.

This enables the app to recognize when the same device is reconnected.
"""

import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class DeviceIdentifier:
    """Represents a unique device identifier"""
    device_id: str          # Unique persistent ID
    device_name: str        # Human-readable name
    device_type: str        # "android", "ios", "camera", "usb", "sd_card"
    serial_number: Optional[str] = None  # Physical serial (if available)
    volume_guid: Optional[str] = None    # Volume GUID (Windows only)
    mount_point: str = ""   # Current mount path


class DeviceIDExtractor:
    """
    Extracts unique device IDs from various storage devices.

    Strategy:
    1. Android (MTP): Use USB serial number via mtp-detect/libmtp
    2. iOS: Use device UUID via idevice_id
    3. USB/SD Cards: Use volume UUID or generate from serial + label
    4. Fallback: Hash of mount point + volume label
    """

    def __init__(self):
        self.system = platform.system()

    def extract_device_id(self, root_path: str, device_type: str) -> DeviceIdentifier:
        """
        Extract unique device ID from mount point.

        Args:
            root_path: Device mount point (e.g., "/media/user/phone")
            device_type: Device type hint ("android", "ios", "camera", etc.)

        Returns:
            DeviceIdentifier with unique ID and metadata
        """
        print(f"[DeviceIDExtractor] Extracting ID for {device_type} device at: {root_path}")

        # Normalize path
        root_path = os.path.abspath(root_path)

        # Try type-specific extraction
        if device_type == "android":
            print(f"[DeviceIDExtractor] Using Android extraction method")
            return self._extract_android_id(root_path)
        elif device_type == "ios":
            print(f"[DeviceIDExtractor] Using iOS extraction method")
            return self._extract_ios_id(root_path)
        else:
            # Generic USB/SD card/camera
            print(f"[DeviceIDExtractor] Using generic volume extraction method")
            return self._extract_volume_id(root_path, device_type)

    def _extract_android_id(self, root_path: str) -> DeviceIdentifier:
        """
        Extract Android device ID via MTP.

        Uses:
        - Linux: mtp-detect to get USB serial
        - Windows: WMI to query device serial
        - macOS: Android File Transfer detection
        """
        print(f"[DeviceIDExtractor] _extract_android_id() called")
        print(f"[DeviceIDExtractor]   System: {self.system}")

        serial = None
        device_name = Path(root_path).name or "Android Device"
        print(f"[DeviceIDExtractor]   Device name: {device_name}")

        if self.system == "Linux":
            print(f"[DeviceIDExtractor]   Attempting Linux MTP detection...")
            serial = self._get_mtp_serial_linux(root_path)
        elif self.system == "Windows":
            print(f"[DeviceIDExtractor]   Attempting Windows MTP detection...")
            serial = self._get_mtp_serial_windows(root_path)
        elif self.system == "Darwin":
            print(f"[DeviceIDExtractor]   Attempting macOS MTP detection...")
            serial = self._get_mtp_serial_macos(root_path)

        if serial:
            device_id = f"android:{serial}"
            print(f"[DeviceIDExtractor]   ✓ Serial extracted: {serial}")
            print(f"[DeviceIDExtractor]   Device ID: {device_id}")
        else:
            # Fallback: Hash mount path + timestamp (not ideal but works)
            device_id = f"android:unknown:{self._hash_path(root_path)}"
            print(f"[DeviceIDExtractor]   ✗ No serial found, using fallback")
            print(f"[DeviceIDExtractor]   Device ID (fallback): {device_id}")

        return DeviceIdentifier(
            device_id=device_id,
            device_name=device_name,
            device_type="android",
            serial_number=serial,
            mount_point=root_path
        )

    def _extract_ios_id(self, root_path: str) -> DeviceIdentifier:
        """
        Extract iOS device UUID.

        Uses:
        - Linux/macOS: idevice_id from libimobiledevice
        - Windows: iTunes device enumeration
        """
        print(f"[DeviceIDExtractor] _extract_ios_id() called")
        print(f"[DeviceIDExtractor]   System: {self.system}")

        device_uuid = None
        device_name = Path(root_path).name or "iPhone"
        print(f"[DeviceIDExtractor]   Device name: {device_name}")

        if self.system in ["Linux", "Darwin"]:
            print(f"[DeviceIDExtractor]   Attempting Unix iOS detection (idevice_id)...")
            device_uuid = self._get_ios_uuid_unix(root_path)
        elif self.system == "Windows":
            print(f"[DeviceIDExtractor]   Attempting Windows iOS detection...")
            device_uuid = self._get_ios_uuid_windows(root_path)

        if device_uuid:
            device_id = f"ios:{device_uuid}"
            print(f"[DeviceIDExtractor]   ✓ UUID extracted: {device_uuid}")
            print(f"[DeviceIDExtractor]   Device ID: {device_id}")
        else:
            # Fallback
            device_id = f"ios:unknown:{self._hash_path(root_path)}"
            print(f"[DeviceIDExtractor]   ✗ No UUID found, using fallback")
            print(f"[DeviceIDExtractor]   Device ID (fallback): {device_id}")

        return DeviceIdentifier(
            device_id=device_id,
            device_name=device_name,
            device_type="ios",
            serial_number=device_uuid,
            mount_point=root_path
        )

    def _extract_volume_id(self, root_path: str, device_type: str) -> DeviceIdentifier:
        """
        Extract volume UUID for USB drives, SD cards, cameras.

        Uses:
        - Linux: blkid to get UUID
        - macOS: diskutil to get UUID
        - Windows: wmic to get VolumeSerialNumber
        """
        print(f"[DeviceIDExtractor] _extract_volume_id() called")
        print(f"[DeviceIDExtractor]   System: {self.system}")
        print(f"[DeviceIDExtractor]   Device type: {device_type}")

        volume_uuid = None
        volume_label = Path(root_path).name or "Storage Device"
        print(f"[DeviceIDExtractor]   Volume label: {volume_label}")

        if self.system == "Linux":
            print(f"[DeviceIDExtractor]   Attempting Linux volume UUID detection (blkid)...")
            volume_uuid = self._get_volume_uuid_linux(root_path)
        elif self.system == "Darwin":
            print(f"[DeviceIDExtractor]   Attempting macOS volume UUID detection (diskutil)...")
            volume_uuid = self._get_volume_uuid_macos(root_path)
        elif self.system == "Windows":
            print(f"[DeviceIDExtractor]   Attempting Windows volume UUID detection (wmic)...")
            volume_uuid = self._get_volume_uuid_windows(root_path)

        if volume_uuid:
            device_id = f"{device_type}:{volume_uuid}"
            print(f"[DeviceIDExtractor]   ✓ Volume UUID extracted: {volume_uuid}")
            print(f"[DeviceIDExtractor]   Device ID: {device_id}")
        else:
            # Fallback: Use volume label + hash
            device_id = f"{device_type}:{self._hash_path(root_path)}"
            print(f"[DeviceIDExtractor]   ✗ No volume UUID found, using fallback")
            print(f"[DeviceIDExtractor]   Device ID (fallback): {device_id}")

        return DeviceIdentifier(
            device_id=device_id,
            device_name=volume_label,
            device_type=device_type,
            volume_guid=volume_uuid,
            mount_point=root_path
        )

    # ======================================================================
    # Platform-specific device ID extraction methods
    # ======================================================================

    def _get_mtp_serial_linux(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on Linux via mtp-detect."""
        print(f"[DeviceIDExtractor]     _get_mtp_serial_linux() - root_path: {root_path}")
        try:
            # Run mtp-detect to list MTP devices
            print(f"[DeviceIDExtractor]     Running: mtp-detect")
            result = subprocess.run(
                ["mtp-detect"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                print(f"[DeviceIDExtractor]     ✓ mtp-detect succeeded")
                print(f"[DeviceIDExtractor]     Output length: {len(result.stdout)} chars")
                # Parse output for serial number
                for line in result.stdout.splitlines():
                    if "Serial number:" in line:
                        serial = line.split(":", 1)[1].strip()
                        print(f"[DeviceIDExtractor]     Found serial line: {line}")
                        if serial and serial != "0":
                            print(f"[DeviceIDExtractor]     ✓ Valid serial: {serial}")
                            return serial
                        else:
                            print(f"[DeviceIDExtractor]     ✗ Invalid serial (empty or '0')")
                print(f"[DeviceIDExtractor]     ✗ No 'Serial number:' line found in output")
            else:
                print(f"[DeviceIDExtractor]     ✗ mtp-detect failed with return code {result.returncode}")
                print(f"[DeviceIDExtractor]     stderr: {result.stderr}")
        except FileNotFoundError:
            print(f"[DeviceIDExtractor]     ✗ mtp-detect command not found (libmtp not installed?)")
        except subprocess.TimeoutExpired:
            print(f"[DeviceIDExtractor]     ✗ mtp-detect timed out after 5 seconds")
        except Exception as e:
            print(f"[DeviceIDExtractor]     ✗ MTP detection failed: {e}")

        print(f"[DeviceIDExtractor]     Returning None (no MTP serial found)")
        return None

    def _get_mtp_serial_windows(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on Windows via WMI."""
        # Note: Windows MTP devices don't appear as drive letters by default
        # They appear in "This PC" but require Windows Portable Devices API
        # For now, fall back to hash-based ID
        return None

    def _get_mtp_serial_macos(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on macOS."""
        # macOS requires Android File Transfer app for MTP
        # No native command-line tools available
        return None

    def _get_ios_uuid_unix(self, root_path: str) -> Optional[str]:
        """Get iOS device UUID on Linux/macOS via idevice_id."""
        print(f"[DeviceIDExtractor]     _get_ios_uuid_unix() - root_path: {root_path}")
        try:
            # List all connected iOS devices
            print(f"[DeviceIDExtractor]     Running: idevice_id -l")
            result = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                print(f"[DeviceIDExtractor]     ✓ idevice_id succeeded")
                # Get first device UUID
                lines = result.stdout.strip().splitlines()
                print(f"[DeviceIDExtractor]     Found {len(lines)} iOS device(s)")
                if lines:
                    uuid = lines[0].strip()
                    print(f"[DeviceIDExtractor]     ✓ First device UUID: {uuid}")
                    return uuid
                else:
                    print(f"[DeviceIDExtractor]     ✗ No iOS devices found")
            else:
                print(f"[DeviceIDExtractor]     ✗ idevice_id failed with return code {result.returncode}")
                print(f"[DeviceIDExtractor]     stderr: {result.stderr}")
        except FileNotFoundError:
            print(f"[DeviceIDExtractor]     ✗ idevice_id command not found (libimobiledevice not installed?)")
        except subprocess.TimeoutExpired:
            print(f"[DeviceIDExtractor]     ✗ idevice_id timed out after 5 seconds")
        except Exception as e:
            print(f"[DeviceIDExtractor]     ✗ iOS detection failed: {e}")

        print(f"[DeviceIDExtractor]     Returning None (no iOS UUID found)")
        return None

    def _get_ios_uuid_windows(self, root_path: str) -> Optional[str]:
        """Get iOS device UUID on Windows."""
        # Windows: iTunes creates device identifiers
        # Would need to query iTunes COM interface or registry
        # For now, fall back to hash-based ID
        return None

    def _get_volume_uuid_linux(self, root_path: str) -> Optional[str]:
        """Get volume UUID on Linux via blkid."""
        print(f"[DeviceIDExtractor]     _get_volume_uuid_linux() - root_path: {root_path}")
        try:
            # Find device for mount point
            print(f"[DeviceIDExtractor]     Running: findmnt -n -o SOURCE {root_path}")
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", root_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                device = result.stdout.strip()
                print(f"[DeviceIDExtractor]     ✓ Found device: {device}")

                # Get UUID for device
                print(f"[DeviceIDExtractor]     Running: blkid -s UUID -o value {device}")
                result2 = subprocess.run(
                    ["blkid", "-s", "UUID", "-o", "value", device],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result2.returncode == 0:
                    uuid_val = result2.stdout.strip()
                    if uuid_val:
                        print(f"[DeviceIDExtractor]     ✓ UUID found: {uuid_val}")
                        return uuid_val
                    else:
                        print(f"[DeviceIDExtractor]     ✗ blkid returned empty UUID")
                else:
                    print(f"[DeviceIDExtractor]     ✗ blkid failed with return code {result2.returncode}")
                    print(f"[DeviceIDExtractor]     stderr: {result2.stderr}")
            else:
                print(f"[DeviceIDExtractor]     ✗ findmnt failed with return code {result.returncode}")
                print(f"[DeviceIDExtractor]     stderr: {result.stderr}")
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceIDExtractor]     ✗ Linux volume UUID extraction failed: {e}")

        print(f"[DeviceIDExtractor]     Returning None (no UUID found)")
        return None

    def _get_volume_uuid_macos(self, root_path: str) -> Optional[str]:
        """Get volume UUID on macOS via diskutil."""
        try:
            result = subprocess.run(
                ["diskutil", "info", root_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Parse diskutil output
                for line in result.stdout.splitlines():
                    if "Volume UUID:" in line:
                        uuid_val = line.split(":", 1)[1].strip()
                        if uuid_val:
                            return uuid_val
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] macOS volume UUID extraction failed: {e}")

        return None

    def _get_volume_uuid_windows(self, root_path: str) -> Optional[str]:
        """Get volume serial number on Windows via wmic."""
        try:
            # Extract drive letter (e.g., "E:" from "E:\\")
            drive_letter = Path(root_path).anchor.rstrip("\\")

            result = subprocess.run(
                ["wmic", "volume", "where", f"DriveLetter='{drive_letter}'", "get", "DeviceID"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) > 1:
                    device_id = lines[1].strip()
                    if device_id:
                        return device_id
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] Windows volume ID extraction failed: {e}")

        return None

    def _hash_path(self, path: str) -> str:
        """Generate deterministic hash from path (last resort fallback)."""
        # Use volume label + path hash
        # This is NOT persistent across remounts but better than nothing
        path_hash = abs(hash(path)) % 100000
        return f"{path_hash:05d}"


# Convenience function
def get_device_id(root_path: str, device_type: str) -> DeviceIdentifier:
    """
    Extract device ID from mount point.

    Args:
        root_path: Device mount path
        device_type: Type hint ("android", "ios", "camera", etc.)

    Returns:
        DeviceIdentifier with unique ID

    Example:
        >>> device = get_device_id("/media/user/Galaxy_S22", "android")
        >>> print(device.device_id)
        "android:ABC123XYZ"
    """
    extractor = DeviceIDExtractor()
    return extractor.extract_device_id(root_path, device_type)


if __name__ == "__main__":
    # Test device ID extraction
    import sys

    if len(sys.argv) < 2:
        print("Usage: python device_id_extractor.py <mount_path> [device_type]")
        print("Example: python device_id_extractor.py /media/user/phone android")
        sys.exit(1)

    mount_path = sys.argv[1]
    dev_type = sys.argv[2] if len(sys.argv) > 2 else "usb"

    device = get_device_id(mount_path, dev_type)

    print(f"Device ID: {device.device_id}")
    print(f"Device Name: {device.device_name}")
    print(f"Device Type: {device.device_type}")
    print(f"Serial Number: {device.serial_number}")
    print(f"Volume GUID: {device.volume_guid}")
    print(f"Mount Point: {device.mount_point}")
