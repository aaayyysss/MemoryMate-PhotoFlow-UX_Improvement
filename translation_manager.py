"""
Translation/i18n Infrastructure for MemoryMate-PhotoFlow

Provides a centralized translation management system with:
- JSON-based translation files
- Dot notation access (e.g., "preferences.general.title")
- Format string support with parameters
- Automatic fallback to English
- Runtime language switching
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional


class TranslationManager:
    """Manages application translations with fallback support."""

    # Singleton instance
    _instance: Optional['TranslationManager'] = None

    def __init__(self, default_language: str = "en"):
        """
        Initialize translation manager.

        Args:
            default_language: ISO 639-1 language code (e.g., "en", "es", "fr")
        """
        self.default_language = "en"
        self.current_language = default_language
        self.translations: Dict[str, Dict[str, Any]] = {}
        self.locales_dir = Path(__file__).parent / "locales"

        # Create locales directory if it doesn't exist
        self.locales_dir.mkdir(exist_ok=True)

        # Load default English translations
        self._load_language("en")

        # Load requested language if different
        if default_language != "en":
            self._load_language(default_language)

    @classmethod
    def get_instance(cls, default_language: str = "en") -> 'TranslationManager':
        """Get singleton instance of TranslationManager."""
        if cls._instance is None:
            cls._instance = cls(default_language)
        return cls._instance

    def _load_language(self, language_code: str) -> bool:
        """
        Load translation file for specified language.

        Args:
            language_code: ISO 639-1 language code

        Returns:
            True if loaded successfully, False otherwise
        """
        file_path = self.locales_dir / f"{language_code}.json"

        if not file_path.exists():
            print(f"⚠️ Translation file not found: {file_path}")
            return False

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.translations[language_code] = json.load(f)
            print(f"✅ Loaded {language_code} translations from {file_path}")
            return True
        except Exception as e:
            print(f"❌ Failed to load {language_code} translations: {e}")
            return False

    def set_language(self, language_code: str) -> bool:
        """
        Switch to a different language.

        Args:
            language_code: ISO 639-1 language code

        Returns:
            True if language was loaded successfully
        """
        # Load language if not already loaded
        if language_code not in self.translations:
            if not self._load_language(language_code):
                print(f"⚠️ Failed to switch to {language_code}, staying on {self.current_language}")
                return False

        self.current_language = language_code
        print(f"🌍 Language switched to: {language_code}")
        return True

    def get(self, key: str, **kwargs) -> str:
        """
        Get translated string using dot notation.

        Args:
            key: Dot-notated key (e.g., "preferences.general.title")
            **kwargs: Format parameters for string interpolation

        Returns:
            Translated string, or key itself if not found

        Examples:
            >>> tm = TranslationManager()
            >>> tm.get("preferences.general.title")
            'General Settings'
            >>> tm.get("preferences.cache.size_mb", size=500)
            'Cache Size: 500 MB'
        """
        # Try current language first
        translation = self._get_nested(self.translations.get(self.current_language, {}), key)

        # Fallback to English if not found
        if translation is None and self.current_language != "en":
            translation = self._get_nested(self.translations.get("en", {}), key)

        # If still not found, return the key itself (helps identify missing translations)
        if translation is None:
            print(f"⚠️ Missing translation key: {key}")
            return key

        # Apply format parameters if provided
        if kwargs:
            try:
                return translation.format(**kwargs)
            except KeyError as e:
                print(f"⚠️ Missing format parameter in '{key}': {e}")
                return translation

        return translation

    def _get_nested(self, data: Dict[str, Any], key: str) -> Optional[str]:
        """
        Navigate nested dictionary using dot notation.

        Args:
            data: Dictionary to search
            key: Dot-notated key path

        Returns:
            Value if found, None otherwise
        """
        parts = key.split('.')
        current = data

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current if isinstance(current, str) else None

    def get_available_languages(self) -> list[tuple[str, str]]:
        """
        Get list of available languages.

        Returns:
            List of (language_code, display_name) tuples
        """
        languages = []

        for json_file in self.locales_dir.glob("*.json"):
            lang_code = json_file.stem

            # Try to get language name from translation file
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    lang_name = data.get("_metadata", {}).get("language_name", lang_code.upper())
                    languages.append((lang_code, lang_name))
            except Exception:
                languages.append((lang_code, lang_code.upper()))

        return sorted(languages, key=lambda x: x[0])

    def current_language_name(self) -> str:
        """Get display name of current language."""
        for code, name in self.get_available_languages():
            if code == self.current_language:
                return name
        return self.current_language.upper()


# Global convenience function for quick access
_tm_instance: Optional[TranslationManager] = None

def tr(key: str, **kwargs) -> str:
    """
    Global translation function for convenience.

    Args:
        key: Dot-notated translation key
        **kwargs: Format parameters

    Returns:
        Translated string

    Example:
        >>> from translation_manager import tr
        >>> tr("preferences.general.title")
        'General Settings'
    """
    global _tm_instance
    if _tm_instance is None:
        _tm_instance = TranslationManager.get_instance()
    return _tm_instance.get(key, **kwargs)


def set_language(language_code: str) -> bool:
    """
    Global function to switch language.

    Args:
        language_code: ISO 639-1 language code

    Returns:
        True if successful
    """
    global _tm_instance
    if _tm_instance is None:
        _tm_instance = TranslationManager.get_instance()
    return _tm_instance.set_language(language_code)


def get_translation_manager() -> TranslationManager:
    """Get the global TranslationManager instance."""
    global _tm_instance
    if _tm_instance is None:
        _tm_instance = TranslationManager.get_instance()
    return _tm_instance
