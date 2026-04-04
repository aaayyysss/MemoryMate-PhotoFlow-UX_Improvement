from typing import Optional, Any

from PySide6.QtCore import QObject, Signal


class SearchController(QObject):
    searchRequested = Signal(dict)
    searchCleared = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store

    def set_active_project(self, project_id: Optional[int]):
        self.store.reset_for_project(project_id)

    def set_query_text(self, text: str, **kwargs):
        self.store.update(
            query_text=text or "",
            search_mode="hybrid",
        )

    def submit_query(self, text: str):
        self.set_query_text(text)
        self.run_search()

    def set_preset(self, preset_id: str):
        state = self.store.get_state()

        chips = [chip for chip in state.active_chips if chip.get("kind") != "preset"]
        chips.insert(0, {
            "kind": "preset",
            "label": preset_id.replace("_", " ").title(),
            "value": preset_id,
        })

        self.store.update(
            preset_id=preset_id,
            active_chips=chips,
            search_mode="hybrid",
        )
        self.run_search()

    def remove_chip(self, kind: str, value: Any):
        state = self.store.get_state()

        if kind == "preset":
            state.preset_id = None
        elif kind == "person":
            state.active_people = [p for p in state.active_people if p != value]
        else:
            state.active_filters.pop(kind, None)

        state.active_chips = [
            chip for chip in state.active_chips
            if not (chip.get("kind") == kind and chip.get("value") == value)
        ]
        self.store.stateChanged.emit(state)
        self.run_search()

    def clear_search(self):
        self.store.clear_search()
        self.searchCleared.emit()

    def run_search(self):
        state = self.store.get_state()

        if not state.has_active_project:
            self.store.update(
                result_paths=[],
                result_count=0,
                search_in_progress=False,
                empty_state_reason="no_project",
            )
            return

        self.store.update(
            search_in_progress=True,
            intent_summary=self._build_intent_summary(),
        )

        payload = {
            "project_id": state.active_project_id,
            "query_text": state.query_text,
            "preset_id": state.preset_id,
            "family": state.family,
            "active_filters": dict(state.active_filters),
            "active_people": list(state.active_people),
            "sort_mode": state.sort_mode,
            "search_mode": state.search_mode,
        }
        self.searchRequested.emit(payload)

    def apply_result_summary(
        self,
        result_paths=None,
        result_count: int = 0,
        result_facets=None,
        family: Optional[str] = None,
        warnings=None,
    ):
        self.store.update(
            result_paths=result_paths or [],
            result_count=result_count,
            result_facets=result_facets or {},
            family=family,
            warnings=warnings or [],
            search_in_progress=False,
            empty_state_reason=None if result_count > 0 else "no_results",
            intent_summary=self._build_intent_summary(),
        )

    def _build_intent_summary(self) -> str:
        state = self.store.get_state()
        parts = []

        if state.preset_id:
            parts.append(state.preset_id.replace("_", " ").title())

        if state.query_text.strip():
            parts.append(state.query_text.strip())

        for person in state.active_people:
            parts.append(person)

        return " + ".join(parts) if parts else "All Photos"
