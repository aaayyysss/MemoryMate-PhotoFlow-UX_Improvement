"""
FFmpeg/FFprobe availability checker with user-friendly notifications.

Provides clear guidance when video processing tools are not installed.
"""

import subprocess
import os
from pathlib import Path
from typing import Tuple, Optional


def _auto_detect_ffmpeg() -> Tuple[Optional[str], Optional[str]]:
    """
    Auto-detect FFmpeg and FFprobe in common locations.

    Searches in this priority order:
    1. System PATH (already handled by _check_command, this is a fallback)
    2. C:\\ffmpeg\\bin (Windows common location)
    3. Application root directory
    4. C:\\Program Files\\ffmpeg\\bin (Windows installer location)

    Returns:
        Tuple[Optional[str], Optional[str]]: (ffprobe_path, ffmpeg_path) or (None, None)
    """
    common_locations = []

    if os.name == 'nt':  # Windows
        # Common Windows locations
        common_locations.extend([
            Path('C:/ffmpeg/bin'),
            Path('C:/ffmpeg'),
            Path('C:/Program Files/ffmpeg/bin'),
            Path('C:/Program Files/ffmpeg'),
        ])

    # Application root directory (cross-platform)
    try:
        app_root = Path(__file__).parent.parent  # Go up from utils/ to app root
        common_locations.append(app_root)
        common_locations.append(app_root / 'bin')
        common_locations.append(app_root / 'ffmpeg')
    except Exception:
        pass

    # Check each location for ffprobe and ffmpeg
    for location in common_locations:
        if not location.exists():
            continue

        # Check for ffprobe
        if os.name == 'nt':
            ffprobe_candidates = [
                location / 'ffprobe.exe',
                location / 'bin' / 'ffprobe.exe',
            ]
            ffmpeg_candidates = [
                location / 'ffmpeg.exe',
                location / 'bin' / 'ffmpeg.exe',
            ]
        else:
            ffprobe_candidates = [
                location / 'ffprobe',
                location / 'bin' / 'ffprobe',
            ]
            ffmpeg_candidates = [
                location / 'ffmpeg',
                location / 'bin' / 'ffmpeg',
            ]

        # Try to find both executables
        ffprobe_path = None
        ffmpeg_path = None

        for candidate in ffprobe_candidates:
            if candidate.exists() and _check_command(str(candidate)):
                ffprobe_path = str(candidate)
                break

        for candidate in ffmpeg_candidates:
            if candidate.exists() and _check_command(str(candidate)):
                ffmpeg_path = str(candidate)
                break

        # If we found ffprobe, return (even if ffmpeg not found)
        if ffprobe_path:
            return ffprobe_path, ffmpeg_path

    # Not found in any common location
    return None, None


def check_ffmpeg_availability() -> Tuple[bool, bool, str]:
    """
    Check if FFmpeg and FFprobe are available on the system.

    Auto-detects from multiple locations in this priority order:
    1. Custom path from user settings
    2. System PATH
    3. C:\\ffmpeg\\bin (Windows common location)
    4. Application root directory

    Returns:
        Tuple[bool, bool, str]: (ffmpeg_available, ffprobe_available, message)
    """
    # Priority 1: Try to get custom path from settings
    ffprobe_custom_path = None
    ffmpeg_custom_path = None
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        ffprobe_custom_path = settings.get_setting('ffprobe_path', '')
        # If custom ffprobe path is set, derive ffmpeg path from same directory
        if ffprobe_custom_path:
            ffprobe_dir = Path(ffprobe_custom_path).parent
            potential_ffmpeg = ffprobe_dir / 'ffmpeg.exe' if os.name == 'nt' else ffprobe_dir / 'ffmpeg'
            if potential_ffmpeg.exists():
                ffmpeg_custom_path = str(potential_ffmpeg)
    except Exception:
        pass

    # Priority 2-4: Auto-detect from common locations if no custom path
    if not ffprobe_custom_path:
        ffprobe_custom_path, ffmpeg_custom_path = _auto_detect_ffmpeg()

        # Save auto-detected path to settings for future use
        if ffprobe_custom_path:
            try:
                from settings_manager_qt import SettingsManager
                settings = SettingsManager()
                # Only save if auto-detected (not already manually configured)
                if not settings.get_setting('ffprobe_path', ''):
                    settings.set_setting('ffprobe_path', ffprobe_custom_path)
                    print(f"💾 Saved FFprobe path to settings: {ffprobe_custom_path}")
            except Exception:
                pass

    # Check ffprobe (custom path first, then system PATH)
    ffprobe_available = False
    if ffprobe_custom_path:
        ffprobe_available = _check_command(ffprobe_custom_path)
        if ffprobe_available:
            message = f"✅ FFprobe detected (custom path: {ffprobe_custom_path})"
        else:
            # Custom path configured but not working - notify user
            message = f"⚠️ FFprobe configured at '{ffprobe_custom_path}' but not working\n"
            message += "Please check Preferences (Ctrl+,) → Video Settings and update the path."
            return False, False, message
    else:
        # No custom path, check system PATH
        ffprobe_available = _check_command('ffprobe')

    # Check ffmpeg (custom path first, then system PATH)
    ffmpeg_available = False
    if ffmpeg_custom_path:
        ffmpeg_available = _check_command(ffmpeg_custom_path)
        if not ffmpeg_available:
            # Try system PATH as fallback
            ffmpeg_available = _check_command('ffmpeg')
    else:
        # No custom path, check system PATH
        ffmpeg_available = _check_command('ffmpeg')

    if ffprobe_available and ffmpeg_available:
        if ffprobe_custom_path and ffmpeg_custom_path:
            # Check if this was from user settings or auto-detected
            try:
                from settings_manager_qt import SettingsManager
                settings = SettingsManager()
                saved_path = settings.get_setting('ffprobe_path', '')
                if saved_path:
                    message = f"✅ FFmpeg and FFprobe detected (custom path: {Path(ffprobe_custom_path).parent})"
                else:
                    message = f"✅ FFmpeg and FFprobe detected (auto-detected: {Path(ffprobe_custom_path).parent})"
            except Exception:
                message = f"✅ FFmpeg and FFprobe detected (path: {Path(ffprobe_custom_path).parent})"
        elif ffprobe_custom_path:
            message = f"✅ FFmpeg and FFprobe detected (ffprobe: {ffprobe_custom_path}, ffmpeg: system)"
        else:
            message = "✅ FFmpeg and FFprobe detected (system PATH) - full video support enabled"
        return True, True, message

    elif ffprobe_available and not ffmpeg_available:
        # FFprobe works, but ffmpeg missing (affects thumbnail generation)
        if ffprobe_custom_path:
            ffprobe_dir = Path(ffprobe_custom_path).parent
            message = f"✅ FFprobe detected (custom path: {ffprobe_custom_path})\n"
            message += f"⚠️ FFmpeg not found at {ffprobe_dir} or in system PATH\n"
            message += f"💡 Tip: Install ffmpeg.exe in {ffprobe_dir} for video thumbnails"
        else:
            message = "✅ FFprobe detected\n⚠️ FFmpeg not found (needed for video thumbnails)"
        return True, True, message  # Return success since ffprobe is the critical component

    elif not ffprobe_available and not ffmpeg_available:
        message = _get_install_message(missing_both=True)
        return False, False, message

    else:  # ffmpeg available but not ffprobe
        message = _get_install_message(missing_ffprobe=True)
        return True, False, message


def _check_command(command: str) -> bool:
    """
    Check if a command is available in the system PATH.

    Args:
        command: Command name to check (e.g., 'ffmpeg', 'ffprobe')

    Returns:
        True if command is available, False otherwise
    """
    try:
        result = subprocess.run(
            [command, '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _get_install_message(missing_both: bool = False,
                        missing_ffmpeg: bool = False,
                        missing_ffprobe: bool = False) -> str:
    """
    Get user-friendly installation message based on what's missing.

    Args:
        missing_both: Both FFmpeg and FFprobe are missing
        missing_ffmpeg: Only FFmpeg is missing
        missing_ffprobe: Only FFprobe is missing

    Returns:
        Formatted message with installation instructions
    """
    if missing_both:
        tools = "FFmpeg and FFprobe"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ❌ Video thumbnails won't be generated
  ❌ Duration/resolution won't be extracted
  ❌ Video filtering will be limited
"""
    elif missing_ffmpeg:
        tools = "FFmpeg"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ✅ Metadata extraction works (via FFprobe)
  ❌ Video thumbnails won't be generated
"""
    else:  # missing_ffprobe
        tools = "FFprobe"
        impact = """
⚠️ Limited Video Support:
  ✅ Videos can be indexed and played
  ✅ Thumbnail generation works (via FFmpeg)
  ❌ Duration/resolution won't be extracted
"""

    # Platform-specific installation instructions
    if os.name == 'nt':  # Windows
        install_cmd = """
📦 Installation (Windows):
  Option 1: choco install ffmpeg
  Option 2: Download from https://www.gyan.dev/ffmpeg/builds/
           Extract to C:\\ffmpeg and add C:\\ffmpeg\\bin to PATH
"""
    elif os.name == 'posix':
        if Path('/usr/bin/apt-get').exists():  # Ubuntu/Debian
            install_cmd = """
📦 Installation (Ubuntu/Debian):
  sudo apt update && sudo apt install ffmpeg
"""
        elif Path('/usr/bin/dnf').exists():  # Fedora/RHEL
            install_cmd = """
📦 Installation (Fedora/RHEL):
  sudo dnf install ffmpeg
"""
        elif Path('/usr/bin/pacman').exists():  # Arch
            install_cmd = """
📦 Installation (Arch Linux):
  sudo pacman -S ffmpeg
"""
        elif Path('/usr/local/bin/brew').exists() or Path('/opt/homebrew/bin/brew').exists():  # macOS
            install_cmd = """
📦 Installation (macOS):
  brew install ffmpeg
"""
        else:
            install_cmd = """
📦 Installation:
  Install FFmpeg from your package manager or from https://ffmpeg.org/download.html
"""
    else:
        install_cmd = """
📦 Installation:
  Download from https://ffmpeg.org/download.html
"""

    message = f"""
{'='*70}
⚠️  {tools} Not Found
{'='*70}
{impact}
{install_cmd}

📖 For detailed instructions, see: FFMPEG_INSTALL_GUIDE.md
   (located in the application directory)

After installation:
  1. Restart this application
  2. Re-scan folders containing videos
  3. Video features will be fully enabled

{'='*70}
"""

    return message


def show_ffmpeg_status_once():
    """
    Show FFmpeg status message once per session.

    This function checks for a flag file to ensure the message
    is only shown once, avoiding repetitive notifications.

    Returns:
        Message string if this is the first check, None otherwise
    """
    flag_file = Path('.ffmpeg_check_done')

    # Check FFmpeg availability
    ffmpeg_ok, ffprobe_ok, message = check_ffmpeg_availability()

    # If both are available, create flag and return success message
    if ffmpeg_ok and ffprobe_ok:
        if not flag_file.exists():
            flag_file.touch()
        return message

    # If something is missing and we haven't shown the message yet, show it
    if not flag_file.exists():
        # Don't create flag file when tools are missing
        # This ensures the message shows every session until tools are installed
        return message

    # Tools are missing but we've already shown the message this session
    return None


if __name__ == '__main__':
    # Test the checker
    message = show_ffmpeg_status_once()
    if message:
        print(message)
