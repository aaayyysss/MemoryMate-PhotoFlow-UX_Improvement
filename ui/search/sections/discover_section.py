from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QPushButton
from shiboken6 import isValid


class DiscoverSection(QGroupBox):
    presetSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Discover", parent)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(6)
        self.cards = {}

        self._build_default_cards()

    def _build_default_cards(self):
        presets = [
            ("beach", "🌊 Beach"),
            ("mountains", "🏔 Mountains"),
            ("city", "🌆 City"),
            ("forest", "🌲 Forest"),
            ("documents", "📄 Documents"),
            ("screenshots", "📱 Screenshots"),
        ]

        for preset_id, label in presets:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, p=preset_id: self.presetSelected.emit(p))
            self.layout.addWidget(btn)
            self.cards[preset_id] = btn

        self.layout.addStretch(1)

    def update_counts(self, counts: dict):
        if not isValid(self):
            return
        for preset_id, btn in self.cards.items():
            count = counts.get(preset_id)
            base_text = btn.text().split(" (")[0]
            if count is None:
                btn.setText(base_text)
            else:
                btn.setText(f"{base_text} ({count})")
