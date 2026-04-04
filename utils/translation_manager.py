"""
Translation Manager for i18n (Internationalization) Support

Provides a simple JSON-based translation system for multi-language support.
Translations are loaded from lang/*.json files based on user preference.

Usage:
    from utils.translation_manager import get_translator

    t = get_translator()
    print(t.get('menu.file'))  # Output: "File"
    print(t.get('messages.success.settings_saved'))  # Output: "Settings saved successfully"
    print(t.get('messages.error.scan_failed', error="Permission denied"))  # Format string
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class TranslationManager:
    """
    Manages application translations with JSON-based language files.

    Features:
    - Loads translations from lang/{language}.json
    - Fallback to English if translation missing
    - Support for format strings with parameters
    - Nested key access with dot notation
    - Singleton pattern for global access
    """

    def __init__(self, language: str = 'en'):
        """
        Initialize translation manager.

        Args:
            language: Language code (e.g., 'en', 'de', 'ar', 'zh')
        """
        self.language = language
        self.translations: Dict[str, Any] = {}
        self.fallback_translations: Dict[str, Any] = {}

        self._load_translations()

    def _load_translations(self):
        """Load translation files from lang/ directory."""
        try:
            # Determine lang directory path
            lang_dir = Path(__file__).parent.parent / 'lang'

            # Load requested language
            lang_file = lang_dir / f'{self.language}.json'
            if lang_file.exists():
                with lang_file.open('r', encoding='utf-8') as f:
                    self.translations = json.load(f)
                logger.info(f"✓ Loaded translations: {self.language}")
            else:
                logger.warning(f"⚠️ Translation file not found: {lang_file}")
                self.translations = {}

            # Always load English as fallback
            if self.language != 'en':
                fallback_file = lang_dir / 'en.json'
                if fallback_file.exists():
                    with fallback_file.open('r', encoding='utf-8') as f:
                        self.fallback_translations = json.load(f)
                    logger.info("✓ Loaded English fallback translations")
                else:
                    logger.warning("⚠️ English fallback translations not found")
                    self.fallback_translations = {}
            else:
                # English is loaded as main, no fallback needed
                self.fallback_translations = {}

        except Exception as e:
            logger.error(f"❌ Failed to load translations: {e}")
            self.translations = {}
            self.fallback_translations = {}

    def get(self, key: str, **kwargs) -> str:
        """
        Get translated string for given key.

        Args:
            key: Translation key in dot notation (e.g., 'menu.file')
            **kwargs: Format string parameters

        Returns:
            Translated string (or key if translation not found)

        Examples:
            >>> t.get('menu.file')
            'File'
            >>> t.get('messages.success.scan_complete', count=42)
            'Scan complete: 42 files processed'
        """
        # Split key into parts
        keys = key.split('.')

        # Try to find in main translations
        value = self._get_nested(self.translations, keys)

        # Fallback to English if not found
        if value is None and self.fallback_translations:
            value = self._get_nested(self.fallback_translations, keys)
            if value is not None:
                logger.debug(f"Using fallback translation for: {key}")

        # If still not found, return key itself as last resort
        if value is None:
            logger.warning(f"⚠️ Translation not found: {key}")
            return key

        # Format string with parameters if provided
        if kwargs:
            try:
                return value.format(**kwargs)
            except KeyError as e:
                logger.warning(f"⚠️ Missing format parameter in '{key}': {e}")
                return value

        return value

    def _get_nested(self, data: Dict[str, Any], keys: list) -> Optional[str]:
        """
        Get nested value from dictionary using list of keys.

        Args:
            data: Dictionary to search
            keys: List of keys for nested access

        Returns:
            Value if found, None otherwise
        """
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None

        # Return only if it's a string (leaf node)
        return current if isinstance(current, str) else None

    def set_language(self, language: str):
        """
        Change current language.

        Args:
            language: New language code (e.g., 'de', 'ar', 'zh')
        """
        if language != self.language:
            self.language = language
            self._load_translations()
            logger.info(f"✓ Language changed to: {language}")

    def get_available_languages(self) -> list[str]:
        """
        Get list of available language codes.

        Returns:
            List of language codes (e.g., ['en', 'de', 'ar'])
        """
        try:
            lang_dir = Path(__file__).parent.parent / 'lang'
            if not lang_dir.exists():
                return ['en']

            languages = []
            for lang_file in lang_dir.glob('*.json'):
                languages.append(lang_file.stem)

            return sorted(languages)
        except Exception as e:
            logger.error(f"❌ Failed to get available languages: {e}")
            return ['en']

    def get_language_name(self, code: str) -> str:
        """
        Get display name for language code.

        Args:
            code: Language code

        Returns:
            Display name (e.g., 'English', 'Deutsch', 'العربية')
        """
        names = {
            'en': 'English',
            'de': 'Deutsch (German)',
            'ar': 'العربية (Arabic)',
            'zh': '中文 (Chinese)',
            'es': 'Español (Spanish)',
            'fr': 'Français (French)',
            'ru': 'Русский (Russian)',
            'ja': '日本語 (Japanese)',
            'ko': '한국어 (Korean)',
            'it': 'Italiano (Italian)',
            'pt': 'Português (Portuguese)'
        }
        return names.get(code, code.upper())


# Global singleton instance
_translator: Optional[TranslationManager] = None


def get_translator(language: Optional[str] = None) -> TranslationManager:
    """
    Get global translator instance.

    Args:
        language: Language code (if None, uses existing or loads from settings)

    Returns:
        TranslationManager instance
    """
    global _translator

    # If language specified, create new translator
    if language is not None:
        _translator = TranslationManager(language=language)
        return _translator

    # If translator exists, return it
    if _translator is not None:
        return _translator

    # Create new translator with language from settings
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        user_language = settings.get_setting('language', 'en')
    except Exception:
        user_language = 'en'

    _translator = TranslationManager(language=user_language)
    return _translator


def set_language(language: str):
    """
    Set global application language.

    Args:
        language: Language code (e.g., 'de', 'ar', 'zh')
    """
    translator = get_translator()
    translator.set_language(language)

    # Save to settings
    try:
        from settings_manager_qt import SettingsManager
        settings = SettingsManager()
        settings.set_setting('language', language)
        logger.info(f"💾 Saved language preference: {language}")
    except Exception as e:
        logger.warning(f"⚠️ Could not save language preference: {e}")


# Convenience function for inline translations
t = lambda key, **kwargs: get_translator().get(key, **kwargs)


if __name__ == '__main__':
    # Test the translation manager
    print("="*70)
    print("Translation Manager Test")
    print("="*70)

    translator = TranslationManager('en')

    print(f"\nApp name: {translator.get('app.name')}")
    print(f"Menu > File: {translator.get('menu.file')}")
    print(f"Save button: {translator.get('preferences.save')}")

    print(f"\nWith parameters:")
    print(translator.get('messages.success.scan_complete', count=42))
    print(translator.get('face_detection.people_found', count=5))

    print(f"\nAvailable languages: {translator.get_available_languages()}")

    print(f"\nMissing key (should return key): {translator.get('nonexistent.key')}")

    print("\n" + "="*70)
