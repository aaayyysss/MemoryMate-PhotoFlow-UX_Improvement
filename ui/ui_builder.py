"""
UIBuilder - Helper for building toolbars, menus, and controls.

Extracted from main_window_qt.py (Phase 2, Step 2.5)

Responsibilities:
- Simplify toolbar creation with fluent API
- Reduce boilerplate in MainWindow initialization
- Provide shortcuts for common UI patterns (actions, menus, checkboxes)

Version: 09.20.00.00
"""

from PySide6.QtWidgets import QToolBar, QLabel, QCheckBox, QComboBox as QSortComboBox
from PySide6.QtGui import QAction, QIcon


class UIBuilder:
    """
    Helper for building toolbars, menus, and controls with less boilerplate.
    Used by MainWindow during __init__ to reduce clutter.
    """
    def __init__(self, main):
        self.main = main
        self.tb = None

    def make_toolbar(self, name="Tools"):
        tb = QToolBar(name, self.main)
        self.main.addToolBar(tb)
        self.tb = tb
        return tb

    def action(self, text, icon=None, shortcut=None, tooltip=None, checkable=False, handler=None):
        act = QAction(text, self.main)
        if icon:
            act.setIcon(QIcon.fromTheme(icon))
        if shortcut:
            try:
                act.setShortcut(shortcut)
            except Exception:
                pass
        if tooltip:
            act.setToolTip(tooltip)
        act.setCheckable(checkable)
        if handler:
            act.triggered.connect(handler)
        if self.tb:
            self.tb.addAction(act)
        return act

    def separator(self):
        if self.tb:
            self.tb.addSeparator()

    def menu(self, title, icon=None):
        m = self.main.menuBar().addMenu(title)
        return m

    def menu_action(self, menu, text, shortcut=None, tooltip=None, checkable=False, handler=None):
        act = QAction(text, self.main)
        if shortcut:
            try:
                act.setShortcut(shortcut)
            except Exception:
                pass
        if tooltip:
            act.setToolTip(tooltip)
        act.setCheckable(checkable)
        if handler:
            act.triggered.connect(handler)
        menu.addAction(act)
        return act

    def combo_sort(self, label_text, options, on_change):
        self.tb.addWidget(QLabel(label_text))
        combo = QSortComboBox()
        combo.addItems(options)
        combo.currentIndexChanged.connect(lambda *_: on_change())
        self.tb.addWidget(combo)
        return combo

    def checkbox(self, text, checked=True):
        chk = QCheckBox(text)
        chk.setChecked(checked)
        if self.tb:
            self.tb.addWidget(chk)
        return chk
