"""Shared desktop identity and approved artwork, independent of project data."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

__all__ = ["APP_USER_MODEL_ID", "configure_application_branding"]

# Stable process identity for Windows taskbar grouping; no registry changes.
APP_USER_MODEL_ID = "LumaFEM.Desktop"


def configure_application_branding(application: QApplication) -> QIcon:
    """Apply the same process identity and decoded icon on every call.

    Call before creating native windows, including direct MainWindow use.
    Resource errors fail visibly through the desktop entry's error handler.
    """
    if not isinstance(application, QApplication) or application is not QApplication.instance():
        raise RuntimeError("Create the active QApplication before configuring LumaFEM branding.")
    if sys.platform == "win32":
        try:
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            set_identity = shell32.SetCurrentProcessExplicitAppUserModelID
            set_identity.argtypes = [wintypes.LPCWSTR]
            set_identity.restype = ctypes.c_long
            result = int(set_identity(APP_USER_MODEL_ID))
        except (AttributeError, OSError) as error:
            raise RuntimeError(
                "Cannot configure LumaFEM Windows desktop identity. "
                "Check Windows shell availability and relaunch LumaFEM. " + str(error)
            ) from error
        if result < 0:
            raise RuntimeError(
                f"Cannot configure LumaFEM Windows desktop identity (HRESULT 0x{result & 0xffffffff:08X}). "
                "Check Windows shell availability and relaunch LumaFEM."
            )
    try:
        icon_path = Path(__file__).resolve().parent / "assets" / "lumafem.png"
        if not icon_path.is_file():
            raise OSError("File is missing: " + str(icon_path))
        # QIcon(path) loads lazily; decode now so corrupt artwork cannot pass.
        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            raise ValueError("PNG could not be decoded: " + str(icon_path))
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(
            "Cannot load the LumaFEM application icon. Restore gui/assets/lumafem.png "
            "from the complete LumaFEM source or package, then relaunch. " + str(error)
        ) from error
    icon = QIcon(pixmap)
    application.setWindowIcon(icon)
    return icon
