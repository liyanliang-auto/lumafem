"""Approved artwork, actual Qt decode and explicit Windows process identity."""
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'build/python')]
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QIcon, QPixmap
from PySide6.QtWidgets import QApplication
from lumafem.gui import branding

ASSETS = ROOT / 'python/lumafem/gui/assets'
SIZES = [16,20,24,32,40,48,64,128,256]

class IconBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_approved_bytes_and_all_alpha_frames(self):
        provenance = json.loads((ASSETS/'provenance.json').read_text())
        for entry in provenance['files']:
            self.assertEqual(hashlib.sha256((ASSETS/entry['file']).read_bytes()).hexdigest(), entry['sha256'])
        image = Image.open(ASSETS/'lumafem.ico')
        self.assertEqual(image.ico.sizes(), {(s,s) for s in SIZES})
        for size in SIZES:
            frame = image.ico.getimage((size,size)).convert('RGBA')
            self.assertEqual(frame.getchannel('A').getextrema(), (0,255))
            self.assertLessEqual(frame.getpixel((0,0))[3], 2)

    def test_real_png_decode_and_idempotent_scaled_pixels(self):
        original = QPixmap(str(ASSETS/'lumafem.png'))
        for repeat in range(2):
            icon = branding.configure_application_branding(self.app)
            for size in SIZES:
                expected = QIcon(original).pixmap(QSize(size,size)).toImage()
                self.assertEqual(icon.pixmap(QSize(size,size)).toImage(), expected)
                self.assertEqual(self.app.windowIcon().pixmap(QSize(size,size)).toImage(), expected)
        shell = ctypes.WinDLL('shell32')
        shell.GetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        value = ctypes.c_void_p()
        self.assertEqual(shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)), 0)
        try:
            self.assertEqual(ctypes.wstring_at(value), 'LumaFEM.Desktop')
        finally:
            ctypes.OleDLL('ole32').CoTaskMemFree(value)

    def test_missing_and_corrupt_png_are_actionable(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder)/'branding.py'
            with patch.object(branding, '__file__', str(module)):
                with self.assertRaisesRegex(RuntimeError, 'Restore gui/assets/lumafem.png'):
                    branding.configure_application_branding(self.app)
                (module.parent/'assets').mkdir()
                (module.parent/'assets/lumafem.png').write_bytes(b'corrupt')
                with self.assertRaisesRegex(RuntimeError, 'could not be decoded'):
                    branding.configure_application_branding(self.app)

    def test_requires_active_application(self):
        with self.assertRaisesRegex(RuntimeError, 'active QApplication'):
            branding.configure_application_branding(None)

if __name__ == '__main__':
    unittest.main(verbosity=2)
