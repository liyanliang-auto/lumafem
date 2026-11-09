"""GUI-only colors and widget styling; never imported by the numerical core."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import weakref

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# Decorative separators and interactive boundaries deliberately have different
# roles. Boundaries/focus are stronger than the quiet docking separators.
_THEMES = {
    "dark": dict(app="#202226", toolbar="#1b1d21", panel="#2a2d32",
                 header="#32363c", input="#202328", viewport="#252a30",
                 plot="#22252a", text="#e5eaf0", secondary="#aeb8c3",
                 divider="#444b54", border="#7b8796", accent="#6fc4d0",
                 selected="#254851", selected_text="#e4fcff", disabled="#aab4c0",
                 alternate="#30353c", warning="#f2d49a", warning_bg="#453823",
                 error="#ffb2ab", line="#70c2ef", grid="#48525d",
                 marker="#ffb980", overlays=("#ffbe79", "#c7a8ef", "#8ad0a4", "#f49eb7")),
    "light": dict(app="#dce4e9", toolbar="#e4ebf0", panel="#f3f6f8",
                  header="#dce5ec", input="#ffffff", viewport="#e6edf2",
                  plot="#edf2f6", text="#203648", secondary="#4f6577",
                  divider="#aabac8", border="#667785", accent="#126d80",
                  selected="#c3dfe7", selected_text="#123c4b", disabled="#5d6e7d",
                  alternate="#e6edf3", warning="#694b15", warning_bg="#fff1cd",
                  error="#ab3029", line="#176da6", grid="#c5d1dc",
                  marker="#ad352c", overlays=("#9b5418", "#77509a", "#287449", "#a1405c")),
}


def colors(name: str) -> dict:
    """Return an independent color dictionary, rejecting unknown themes first."""
    if name not in _THEMES:
        raise ValueError("Unsupported interface theme: " + str(name))
    return dict(_THEMES[name])


def rgb(name: str, key: str) -> tuple[float, float, float]:
    color = QColor(colors(name)[key])
    return color.redF(), color.greenF(), color.blueF()


def palette(name: str) -> QPalette:
    c = colors(name)
    result = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "panel"),
                      (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "input"),
                      (QPalette.ColorRole.AlternateBase, "alternate"),
                      (QPalette.ColorRole.Text, "text"),
                      (QPalette.ColorRole.Button, "header"),
                      (QPalette.ColorRole.ButtonText, "text"),
                      (QPalette.ColorRole.Highlight, "selected"),
                      (QPalette.ColorRole.HighlightedText, "selected_text"),
                      (QPalette.ColorRole.ToolTipBase, "header"),
                      (QPalette.ColorRole.ToolTipText, "text"),
                      (QPalette.ColorRole.Link, "accent")):
        result.setColor(role, QColor(c[key]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    return result


def stylesheet(name: str) -> str:
    c = colors(name)
    return """
QMainWindow { background: %(app)s; }
QWidget { color: %(text)s; background-color: %(panel)s; }
QToolBar, QMenuBar { background: %(toolbar)s; border: 0; spacing: 4px; }
QToolBar { padding: 3px; border-bottom: 1px solid %(divider)s; }
QToolBar::separator { width: 1px; background: %(divider)s; margin: 3px 6px; }
QMenuBar::item { padding: 4px 9px; background: transparent; }
QMenuBar::item:selected, QMenu::item:selected { background: %(selected)s; color: %(selected_text)s; }
QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 10px; }
QDockWidget { border: 1px solid %(divider)s; }
QDockWidget::title { background: %(header)s; border-bottom: 1px solid %(divider)s; padding: 5px 7px; font-weight: 600; }
QMainWindow::separator { background: %(app)s; width: 5px; height: 5px; }
QMainWindow::separator:hover { background: %(accent)s; }
QTabWidget::pane { border: 1px solid %(divider)s; background: %(panel)s; }
QTabBar::tab { background: %(header)s; border: 1px solid %(divider)s; padding: 4px 9px; margin-right: 2px; }
QTabBar::tab:selected { background: %(selected)s; color: %(selected_text)s; border-bottom: 2px solid %(accent)s; }
QTabBar::tab:hover { border-color: %(border)s; }
QLabel { background: transparent; }
QLabel#contextPrompt { color: %(secondary)s; }
QLabel#viewportContext { background: %(header)s; padding: 4px; }
QLabel#viewportSelectionStatus { color: %(secondary)s; padding: 2px; }
QPushButton, QToolButton { background: %(header)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 3px; padding: 3px 7px; min-height: 19px; }
QPushButton:hover, QToolButton:hover { border-color: %(accent)s; background: %(selected)s; }
QPushButton:pressed, QToolButton:checked { background: %(selected)s; color: %(selected_text)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; padding: 2px 6px; }
QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; border-color: %(divider)s; }
QPushButton#monitorSubmit { border-color: %(accent)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: %(input)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 3px; padding: 3px 5px; min-height: 19px; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 2px solid %(accent)s; padding: 2px 4px; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: %(disabled)s; background: %(header)s; }
QComboBox { padding-right: 21px; }
QComboBox::drop-down { border-left: 1px solid %(border)s; width: 18px; }
QComboBox::down-arrow { width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid %(text)s; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 0; height: 0; border-left: 3px solid transparent; border-right: 3px solid transparent; border-bottom: 4px solid %(text)s; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 0; height: 0; border-left: 3px solid transparent; border-right: 3px solid transparent; border-top: 4px solid %(text)s; }
QComboBox QAbstractItemView { background: %(input)s; color: %(text)s; selection-background-color: %(selected)s; selection-color: %(selected_text)s; border: 1px solid %(border)s; }
QTreeView, QTableView, QListView, QPlainTextEdit, QTextEdit { background: %(input)s; alternate-background-color: %(alternate)s; color: %(text)s; border: 1px solid %(divider)s; selection-background-color: %(selected)s; selection-color: %(selected_text)s; }
QTreeView::item { padding: 3px 2px; }
QTreeView::item:selected, QTableView::item:selected { background: %(selected)s; color: %(selected_text)s; }
QHeaderView::section { background: %(header)s; color: %(text)s; border: 0; border-right: 1px solid %(divider)s; border-bottom: 1px solid %(border)s; padding: 4px; }
QGroupBox { border: 1px solid %(divider)s; margin-top: 9px; padding: 9px 5px 5px; }
QGroupBox::title { subcontrol-origin: margin; left: 7px; padding: 0 4px; color: %(secondary)s; }
QCheckBox { spacing: 5px; background: transparent; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid %(border)s; border-radius: 2px; background: %(input)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
QCheckBox:disabled { color: %(disabled)s; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: %(app)s; width: 11px; margin: 0; }
QScrollBar:horizontal { background: %(app)s; height: 11px; margin: 0; }
QScrollBar::handle { background: %(border)s; border-radius: 4px; min-width: 18px; min-height: 18px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QStatusBar { background: %(toolbar)s; color: %(secondary)s; border-top: 1px solid %(divider)s; }
QStatusBar::item { border: 0; }
QToolTip { background: %(header)s; color: %(text)s; border: 1px solid %(border)s; padding: 5px; }
QMessageBox, QDialog { background: %(panel)s; }
""" % c


def native_titlebar(widget: QWidget, name: str) -> dict:
    """Color this app's own native non-client frame, without OS preference edits.

    Attribute 20 is the documented immersive-dark-mode switch. Windows 11
    attributes 34/35/36 give explicit border/caption/text colors even when the
    user's Windows theme is light. HRESULT and readback are exposed for QA.
    """
    c = colors(name)
    report = {"theme": name, "class": type(widget).__name__, "supported": False}
    if sys.platform != "win32":
        report["reason"] = "Windows DWM is unavailable on this platform"
        return report
    version = sys.getwindowsversion()
    report["windows"] = [version.major, version.minor, version.build]
    hwnd = int(widget.winId())
    report["hwnd"] = hwnd
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    set_attribute = dwm.DwmSetWindowAttribute
    set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    set_attribute.restype = ctypes.c_long
    get_attribute = dwm.DwmGetWindowAttribute
    get_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    get_attribute.restype = ctypes.c_long
    attributes = [(20, wintypes.BOOL(name == "dark"))]
    if version.build >= 22000:
        for attribute, token in ((34, "divider"), (35, "toolbar"), (36, "text")):
            value = QColor(c[token])
            colorref = value.red() | (value.green() << 8) | (value.blue() << 16)
            attributes.append((attribute, wintypes.DWORD(colorref)))
    results = {}
    for attribute, value in attributes:
        result = int(set_attribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)))
        actual = type(value)()
        read_result = int(get_attribute(hwnd, attribute, ctypes.byref(actual), ctypes.sizeof(actual)))
        results[str(attribute)] = {"set_hresult": result, "get_hresult": read_result,
                                   "requested": value.value,
                                   "actual": actual.value if read_result == 0 else None}
    report["attributes"] = results
    report["set_supported"] = all(item["set_hresult"] == 0 for item in results.values())
    report["explicit_caption_colors"] = all(str(key) in results and results[str(key)]["set_hresult"] == 0
                                               for key in (34, 35, 36))
    report["color_readback_supported"] = all(str(key) in results and results[str(key)]["get_hresult"] == 0
                                               for key in (34, 35, 36))
    report["supported"] = report["set_supported"] and results["20"]["get_hresult"] == 0 and results["20"]["actual"] == results["20"]["requested"]
    report["verification_note"] = "Caption/text color setters plus actual native-frame capture are required; color readback may be unsupported"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    # Repaint only the native frame; preserve position, size, z-order and focus.
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
    return report


class NativeWindowTheme(QObject):
    """Apply per-window DWM styling to current and future owned dialogs/docks."""

    def __init__(self, root: QWidget) -> None:
        super().__init__(root)
        self._root = root
        self._theme = "dark"
        self._applying = False
        self._pending: set[int] = set()
        self.records: dict[int, dict] = {}
        QApplication.instance().installEventFilter(self)

    def _belongs(self, widget: QWidget) -> bool:
        current = widget
        while current is not None:
            if current is self._root:
                return True
            current = current.parentWidget()
        return False

    def apply(self, widget: QWidget) -> None:
        if (self._applying or not widget.isWindow() or not self._belongs(widget)
                or widget.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip)):
            return
        self._applying = True
        try:
            record = native_titlebar(widget, self._theme)
            self.records[record.get("hwnd", id(widget))] = record
        finally:
            self._applying = False

    def set_theme(self, name: str) -> None:
        colors(name)
        self._theme = name
        for widget in QApplication.topLevelWidgets():
            if self._belongs(widget) and (widget is self._root or widget.isVisible()):
                self.apply(widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (not self._applying and isinstance(watched, QWidget) and watched.isWindow()
                and event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange)
                and self._belongs(watched) and id(watched) not in self._pending):
            key, reference = id(watched), weakref.ref(watched)
            self._pending.add(key)
            def later() -> None:
                self._pending.discard(key)
                target = reference()
                if target is not None:
                    try:
                        self.apply(target)
                    except RuntimeError:
                        pass  # A transient popup may have closed before this tick.
            QTimer.singleShot(0, later)
        return False
