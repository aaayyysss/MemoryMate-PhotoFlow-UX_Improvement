# services/suggestion_service.py
# Search Suggestion Service - Autocomplete and query suggestions
#
# Provides real-time suggestions as the user types in the search widget.
# Combines multiple signal sources:
# - Entity names (people, locations, tags)
# - Search history (recent and saved searches)
# - SmartFind presets
# - Structured token completions (type:, date:, is:, has:)
# - Date range suggestions
#
# Modeled after Google Photos / Apple Photos search autocomplete.

"""
SuggestionService - Real-time search autocomplete.

Usage:
    from services.suggestion_service import SuggestionService

    svc = SuggestionService(project_id=1)

    # Get suggestions as user types
    suggestions = svc.suggest("joh")
    # → [
    #     {"type": "person", "text": "John Smith", "icon": "👤", "action": "person:face_001"},
    #     {"type": "history", "text": "John's birthday photos", "icon": "🕐"},
    #     ...
    # ]

    suggestions = svc.suggest("type:")
    # → [
    #     {"type": "token", "text": "type:video", "icon": "🎬"},
    #     {"type": "token", "text": "type:photo", "icon": "📷"},
    #     {"type": "token", "text": "type:screenshot", "icon": "📱"},
    # ]
"""

from __future__ import annotations
import re
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Suggestion:
    """A single search suggestion."""
    type: str           # "person", "location", "tag", "preset", "history",
                        # "token", "date", "entity"
    text: str           # Display text
    icon: str = ""      # Emoji icon for display
    action: str = ""    # Optional action string (e.g. "person:face_001")
    score: float = 0.0  # Relevance score for ranking
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Structured token completions ──
_TOKEN_COMPLETIONS = {
    "type:": [
        Suggestion("token", "type:video", "\U0001f3ac", "type:video"),
        Suggestion("token", "type:photo", "\U0001f4f7", "type:photo"),
        Suggestion("token", "type:screenshot", "\U0001f4f1", "type:screenshot"),
    ],
    "is:": [
        Suggestion("token", "is:favorite", "\u2b50", "is:fav"),
    ],
    "has:": [
        Suggestion("token", "has:location", "\U0001f4cd", "has:location"),
        Suggestion("token", "has:faces", "\U0001f464", "has:faces"),
        Suggestion("token", "has:text", "\U0001f4dd", "has:text"),
    ],
    "date:": [
        Suggestion("token", "date:today", "\U0001f4c5", "date:today"),
        Suggestion("token", "date:yesterday", "\U0001f4c5", "date:yesterday"),
        Suggestion("token", "date:this_week", "\U0001f4c5", "date:this_week"),
        Suggestion("token", "date:last_week", "\U0001f4c5", "date:last_week"),
        Suggestion("token", "date:this_month", "\U0001f4c5", "date:this_month"),
        Suggestion("token", "date:last_month", "\U0001f4c5", "date:last_month"),
        Suggestion("token", "date:this_year", "\U0001f4c5", "date:this_year"),
        Suggestion("token", "date:last_year", "\U0001f4c5", "date:last_year"),
    ],
    "ext:": [
        Suggestion("token", "ext:jpg", "\U0001f4c4", "ext:jpg"),
        Suggestion("token", "ext:png", "\U0001f4c4", "ext:png"),
        Suggestion("token", "ext:heic", "\U0001f4c4", "ext:heic"),
        Suggestion("token", "ext:mp4", "\U0001f4c4", "ext:mp4"),
        Suggestion("token", "ext:raw", "\U0001f4c4", "ext:raw"),
    ],
    "rating:": [
        Suggestion("token", "rating:5", "\u2b50", "rating:5"),
        Suggestion("token", "rating:4", "\u2b50", "rating:4"),
        Suggestion("token", "rating:3", "\u2b50", "rating:3"),
    ],
}

# Token prefix triggers
_TOKEN_PREFIXES = list(_TOKEN_COMPLETIONS.keys())


class SuggestionService:
    """
    Real-time search suggestion service.

    Aggregates suggestions from multiple sources and ranks them
    by relevance to the current input prefix.
    """

    # Maximum suggestions per source
    _MAX_PER_SOURCE = 5
    _MAX_TOTAL = 12

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._entity_graph = None
        self._person_service = None
        self._history_service = None

    def _get_entity_graph(self):
        if self._entity_graph is None:
            try:
                from services.entity_graph import get_entity_graph
                self._entity_graph = get_entity_graph(self.project_id)
            except Exception:
                pass
        return self._entity_graph

    def _get_person_service(self):
        if self._person_service is None:
            try:
                from services.person_search_service import PersonSearchService
                self._person_service = PersonSearchService(self.project_id)
            except Exception:
                pass
        return self._person_service

    def _get_history_service(self):
        if self._history_service is None:
            try:
                from services.search_history_service import get_search_history_service
                self._history_service = get_search_history_service()
            except Exception:
                pass
        return self._history_service

    def suggest(self, prefix: str, limit: int = 0) -> List[Dict[str, Any]]:
        """
        Get ranked suggestions for a search prefix.

        Args:
            prefix: Current text in search widget
            limit: Max suggestions (0 = default _MAX_TOTAL)

        Returns:
            List of suggestion dicts with type, text, icon, action, score
        """
        if not prefix or not prefix.strip():
            return self._empty_suggestions()

        max_results = limit or self._MAX_TOTAL
        prefix = prefix.strip()
        all_suggestions: List[Suggestion] = []

        # 1. Token completions (type:, date:, etc.)
        token_suggestions = self._suggest_tokens(prefix)
        all_suggestions.extend(token_suggestions)

        # If we're mid-token (e.g. "type:v"), return only token completions
        if any(prefix.lower().startswith(tp) for tp in _TOKEN_PREFIXES):
            return self._format(all_suggestions[:max_results])

        # 2. Person name suggestions
        person_suggestions = self._suggest_persons(prefix)
        all_suggestions.extend(person_suggestions)

        # 3. Entity suggestions (locations, tags, dates)
        entity_suggestions = self._suggest_entities(prefix)
        all_suggestions.extend(entity_suggestions)

        # 4. Preset suggestions
        preset_suggestions = self._suggest_presets(prefix)
        all_suggestions.extend(preset_suggestions)

        # 5. History suggestions
        history_suggestions = self._suggest_history(prefix)
        all_suggestions.extend(history_suggestions)

        # Rank by score (descending), deduplicate by text
        all_suggestions.sort(key=lambda s: s.score, reverse=True)
        seen_texts = set()
        unique = []
        for s in all_suggestions:
            key = s.text.lower()
            if key not in seen_texts:
                seen_texts.add(key)
                unique.append(s)

        return self._format(unique[:max_results])

    def _empty_suggestions(self) -> List[Dict[str, Any]]:
        """Suggestions when the search box is empty (quick actions)."""
        suggestions = [
            Suggestion("hint", "Type to search photos...", "\U0001f50d", score=0),
        ]

        # Add recent searches
        history_svc = self._get_history_service()
        if history_svc:
            try:
                recent = history_svc.get_recent_searches(limit=5)
                for r in recent:
                    text = getattr(r, 'query_text', None) or ""
                    if text:
                        suggestions.append(Suggestion(
                            "history", text, "\U0001f552", text,
                            score=0.3,
                        ))
            except Exception:
                pass

        # Add top people
        person_svc = self._get_person_service()
        if person_svc:
            try:
                people = person_svc.get_all_person_names()
                for p in people[:3]:
                    name = p["name"]
                    count = p["photo_count"]
                    suggestions.append(Suggestion(
                        "person", name, "\U0001f464",
                        f"person:{p['branch_key']}",
                        score=0.2,
                        metadata={"photo_count": count},
                    ))
            except Exception:
                pass

        return self._format(suggestions)

    def _suggest_tokens(self, prefix: str) -> List[Suggestion]:
        """Suggest structured token completions."""
        prefix_lower = prefix.lower()
        results = []

        # Check if prefix matches a token key start
        for token_key, completions in _TOKEN_COMPLETIONS.items():
            if prefix_lower.startswith(token_key):
                # We're inside a token - filter completions by value
                value_part = prefix_lower[len(token_key):]
                for comp in completions:
                    if not value_part or comp.text.lower().startswith(prefix_lower):
                        s = Suggestion(
                            comp.type, comp.text, comp.icon, comp.action,
                            score=1.0,
                        )
                        results.append(s)
            elif token_key.startswith(prefix_lower):
                # Prefix matches start of a token key (e.g. "ty" → "type:")
                results.append(Suggestion(
                    "token", token_key, "\u2699\ufe0f", token_key,
                    score=0.8,
                ))

        return results[:self._MAX_PER_SOURCE]

    def _suggest_persons(self, prefix: str) -> List[Suggestion]:
        """Suggest person names matching the prefix."""
        person_svc = self._get_person_service()
        if not person_svc:
            return []

        try:
            people = person_svc.get_all_person_names()
            prefix_lower = prefix.lower()
            results = []
            for p in people:
                name = p["name"]
                if name.lower().startswith(prefix_lower) or prefix_lower in name.lower():
                    score = 0.9 if name.lower().startswith(prefix_lower) else 0.6
                    results.append(Suggestion(
                        "person", name, "\U0001f464",
                        f"person:{p['branch_key']}",
                        score=score,
                        metadata={"photo_count": p["photo_count"]},
                    ))
            return results[:self._MAX_PER_SOURCE]
        except Exception:
            return []

    def _suggest_entities(self, prefix: str) -> List[Suggestion]:
        """Suggest locations, tags, and dates from the entity graph."""
        graph = self._get_entity_graph()
        if not graph:
            return []

        try:
            matches = graph.search_entities(
                prefix,
                entity_types=["location", "tag", "year"],
                limit=self._MAX_PER_SOURCE,
            )
            results = []
            for node in matches:
                icon_map = {
                    "location": "\U0001f4cd",
                    "tag": "\U0001f3f7\ufe0f",
                    "year": "\U0001f4c5",
                    "date": "\U0001f4c5",
                }
                icon = icon_map.get(node.entity_type, "\U0001f50d")
                score = 0.7 if node.display_name.lower().startswith(prefix.lower()) else 0.4
                results.append(Suggestion(
                    "entity", node.display_name, icon,
                    score=score,
                    metadata={
                        "entity_type": node.entity_type,
                        "entity_id": node.entity_id,
                        "photo_count": node.photo_count,
                    },
                ))
            return results
        except Exception:
            return []

    def _suggest_presets(self, prefix: str) -> List[Suggestion]:
        """Suggest SmartFind presets matching the prefix."""
        try:
            from services.smart_find_service import BUILTIN_PRESETS
            prefix_lower = prefix.lower()
            results = []
            for preset in BUILTIN_PRESETS:
                name = preset.get("name", "")
                if name.lower().startswith(prefix_lower) or prefix_lower in name.lower():
                    icon = preset.get("icon", "\U0001f50d")
                    score = 0.75 if name.lower().startswith(prefix_lower) else 0.5
                    results.append(Suggestion(
                        "preset", name, icon,
                        action=preset.get("id", ""),
                        score=score,
                        metadata={"preset_id": preset.get("id")},
                    ))
            return results[:self._MAX_PER_SOURCE]
        except Exception:
            return []

    def _suggest_history(self, prefix: str) -> List[Suggestion]:
        """Suggest from search history."""
        history_svc = self._get_history_service()
        if not history_svc:
            return []

        try:
            recent = history_svc.get_recent_searches(limit=20)
            prefix_lower = prefix.lower()
            results = []
            for record in recent:
                text = getattr(record, 'query_text', None) or ""
                if text and prefix_lower in text.lower():
                    score = 0.65 if text.lower().startswith(prefix_lower) else 0.35
                    results.append(Suggestion(
                        "history", text, "\U0001f552", text,
                        score=score,
                    ))
            return results[:self._MAX_PER_SOURCE]
        except Exception:
            return []

    def _format(self, suggestions: List[Suggestion]) -> List[Dict[str, Any]]:
        """Convert Suggestion objects to dicts for the UI."""
        return [
            {
                "type": s.type,
                "text": s.text,
                "icon": s.icon,
                "action": s.action or s.text,
                "score": round(s.score, 3),
                "metadata": s.metadata,
            }
            for s in suggestions
        ]


# ── Module-level singleton cache ──
_service_cache: Dict[int, SuggestionService] = {}


def get_suggestion_service(project_id: int) -> SuggestionService:
    """Get or create a SuggestionService for a project."""
    if project_id not in _service_cache:
        _service_cache[project_id] = SuggestionService(project_id)
    return _service_cache[project_id]
