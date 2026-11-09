"""Actual display-state and engineering interaction checks for GUI-R1 themes."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import time
import unittest

import test_gui_workflow as base
from PySide6.QtCore import Qt, QTimer, QPoint, QEvent, QPointF
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenu, QMessageBox
from lumafem.gui.main_window import CsvImportDialog
from lumafem.gui.themes import colors

OUT = base.OUT / 'themes'
OUT.mkdir(parents=True, exist_ok=True)


class GuiThemeTests(unittest.TestCase):
    setUpClass = classmethod(base.GuiWorkflowTests.setUpClass.__func__)
    setUp = base.GuiWorkflowTests.setUp
    tearDown = base.GuiWorkflowTests.tearDown
    _close_unexpected_dialog = base.GuiWorkflowTests._close_unexpected_dialog
    pump = base.GuiWorkflowTests.pump
    choose = base.GuiWorkflowTests.choose
    tree = base.GuiWorkflowTests.tree
    field = base.GuiWorkflowTests.field
    replace_text = base.GuiWorkflowTests.replace_text

    def camera(self):
        c = self.window.viewport.renderer.GetActiveCamera()
        return (c.GetPosition(), c.GetFocalPoint(), c.GetViewUp(),
                c.GetParallelScale(), c.GetViewAngle(), c.GetParallelProjection())

    def click_action(self, key):
        w = self.window
        for button in w.findChildren(base.QPushButton):
            if button.defaultAction() == w.actions[key] if hasattr(button, 'defaultAction') else False:
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                return
        # Keyboard actions route through the actual QAction shortcut.
        shortcut = w.actions[key].shortcut().toString()
        self.assertTrue(shortcut)
        QTest.keySequence(w, w.actions[key].shortcut())
        self.pump()

    def expand_bottom(self, height=340):
        """Expose the real plot through Qt's public docking resize operation.

        Native separator dragging is separately observed by the parent. A QTest
        mouse sequence does not reproduce the platform dock-resizer grab here.
        """
        w = self.window
        w.resizeDocks([w.results_dock], [height], Qt.Orientation.Vertical)
        self.pump(200)
        self.assertGreaterEqual(w.results_dock.height(), height - 70)

    def test_theme_language_size_matrix_preserves_invalid_draft_camera_and_identity(self):
        w = self.window
        self.assertEqual((w.language, w.theme), ('zh_CN', 'dark'))
        QTest.keyClick(w, Qt.Key.Key_F6)
        self.pump(200)
        self.assertTrue(w.viewport._prepared, self.errors)
        source = copy.deepcopy(self.controller.document)
        prepared = copy.deepcopy(w.viewport._prepared)
        self.tree(('model', 'geometry'))
        self.replace_text(self.field(('model', 'geometry', 'length')), 'not-a-finite-number')
        selected = copy.deepcopy(self.controller.selection)
        w.viewport.renderer.GetActiveCamera().Azimuth(9)
        original_camera = self.camera()
        records = []
        for language in ('zh_CN', 'en'):
            self.choose(w.language_combo, language)
            for theme in ('dark', 'light'):
                self.choose(w.theme_combo, theme)
                self.assertEqual(self.camera(), original_camera)
                self.assertEqual(self.controller.selection, selected)
                self.assertEqual(self.controller.document, source)
                self.assertEqual(w.viewport._prepared, prepared)
                self.assertEqual(self.field(('model', 'geometry', 'length')).text(), 'not-a-finite-number')
                self.assertEqual((w.editor.language, w.results.language, w.viewport.language), (language,) * 3)
                for width, height in ((1500, 980), (1280, 800)):
                    w.resize(width, height)
                    w.restore_layout()
                    self.pump(150)
                    dimensions = (w.viewport.vtk_widget.width(), w.viewport.vtk_widget.height())
                    self.assertEqual((w.width(), w.height()), (width, height))
                    target = (650, 350) if width == 1500 else (500, 250)
                    self.assertGreaterEqual(dimensions[0], target[0])
                    self.assertGreaterEqual(dimensions[1], target[1])
                    self.assertEqual(self.camera(), original_camera)
                    c = colors(theme)
                    actual_background = w.viewport.renderer.GetBackground()
                    expected = QColor(c['viewport'])
                    self.assertTrue(all(abs(a-b) < 1e-6 for a, b in zip(actual_background, (expected.redF(), expected.greenF(), expected.blueF()))))
                    stem = f'{language}-{theme}-{width}x{height}'
                    w.grab().save(str(OUT / (stem + '-client-layout.png')))
                    w.viewport.export_png(str(OUT / (stem + '-vtk.png')))
                    records.append(dict(language=language, theme=theme, window=[width, height], vtk=list(dimensions), camera=self.camera()))
        with self.assertRaises(ValueError):
            w.set_theme('not-an-interface-theme')
        self.assertEqual(w.theme, 'light')
        self.assertEqual(self.camera(), original_camera)
        self.assertEqual(self.controller.document, source)
        (OUT / 'matrix-and-preservation.json').write_text(json.dumps(records, indent=2))
        self.assertEqual(self.dialogs, [])

    def test_real_edit_confirm_context_menu_undo_redo_and_focus_safe_views(self):
        w = self.window
        item = self.tree(('model', 'geometry'))
        rect = w.editor.tree.visualItemRect(item)
        w.editor_dock.hide()
        QTest.mouseDClick(w.editor.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.pump()
        self.assertTrue(w.editor_dock.isVisible())
        path = ('model', 'geometry', 'length')
        original = copy.deepcopy(self.controller.document)
        self.replace_text(self.field(path), '2300')
        self.modal_timer.stop()
        answered = []
        response = {'button': QMessageBox.StandardButton.Cancel}
        def answer():
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                answered.append(modal.text())
                button = modal.button(response['button'])
                if button:
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        timer = QTimer(); timer.timeout.connect(answer); timer.start(25)
        try:
            QTest.keyClick(w, Qt.Key.Key_F6)
            self.pump()
            self.assertTrue(answered)
            self.assertEqual(self.controller.document, original)
            self.assertEqual(self.field(path).text(), '2300')
            response['button'] = QMessageBox.StandardButton.Apply
            QTest.keyClick(w, Qt.Key.Key_F6)
            self.pump(100)
            self.assertEqual(self.controller.document['model']['geometry']['length'], 2300)
            changed_revision = self.controller.document['revision_id']
            self.assertNotEqual(changed_revision, original['revision_id'])
            QTest.keySequence(w, w.actions['undo'].shortcut())
            self.pump()
            self.assertEqual(self.controller.document['model']['geometry']['length'], 2200)
            QTest.keySequence(w, w.actions['redo'].shortcut())
            self.pump()
            self.assertEqual(self.controller.document['revision_id'], changed_revision)
            self.tree(('model', 'geometry'))
            self.replace_text(self.field(path), '2400')
            response['button'] = QMessageBox.StandardButton.Discard
            QTest.keyClick(w, Qt.Key.Key_F6)
            self.pump()
            self.assertEqual(self.controller.document['model']['geometry']['length'], 2300)
            self.assertFalse(w.editor.has_pending_changes)
        finally:
            timer.stop(); self.modal_timer.start(50)
        # The context menu is operated with real tree keyboard events.
        captured = []
        def dismiss_menu():
            menu = QApplication.activePopupWidget()
            if isinstance(menu, QMenu):
                captured.extend(action.text() for action in menu.actions())
                QTest.keyClick(menu, Qt.Key.Key_Escape)
        menu_timer = QTimer(); menu_timer.timeout.connect(dismiss_menu); menu_timer.start(25)
        try:
            w.editor.tree.setFocus()
            QTest.keyClick(w.editor.tree, Qt.Key.Key_Menu)
            self.pump()
            self.assertTrue(captured)
            self.assertTrue(any('属性' in x for x in captured))
        finally:
            menu_timer.stop()
        self.tree(('model', 'geometry'))
        field = self.field(path)
        self.replace_text(field, 'F')
        camera = self.camera()
        QTest.keyClicks(field, 'f')
        self.pump()
        self.assertEqual(field.text(), 'Ff')
        self.assertEqual(self.camera(), camera)
        QTest.mouseClick(w.editor.revert_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.dialogs, [])

    def test_job_summary_and_populated_curve_are_reachable_by_real_dock_workflow(self):
        w = self.window
        self.choose(w.module_combo, 'job')
        self.expand_bottom(340)
        w.monitor_scroll.verticalScrollBar().setValue(0)
        self.pump()
        for key in ('status', 'phase', 'accepted', 'persisted', 'termination'):
            self.assertFalse(w.job_monitor.values[key].visibleRegion().isEmpty(), key)
        self.run_short_fixture()
        self.choose(w.module_combo, 'results')
        self.expand_bottom(340)
        w.result_scroll.ensureWidgetVisible(w.results.curve)
        self.pump(150)
        self.assertFalse(w.results.curve.visibleRegion().isEmpty())
        self.assertTrue(w.results.curve._hit_points)
        visible = w.results.curve.visibleRegion()
        choices = [(point, record) for point, record in w.results.curve._hit_points if visible.contains(point.toPoint())]
        self.assertTrue(choices, 'At least one actual curve point must be on the exposed work surface')
        point, record = choices[-1]
        QTest.mouseClick(w.results.curve, Qt.MouseButton.LeftButton, pos=point.toPoint())
        self.pump()
        self.assertEqual(self.controller.selection['step_id'], record['identity']['step_id'])
        self.assertEqual(w.viewport._step['identity']['step_id'], record['identity']['step_id'])
        selected = copy.deepcopy(self.controller.selection)
        for language in ('zh_CN', 'en'):
            self.choose(w.language_combo, language)
            for theme in ('dark', 'light'):
                self.choose(w.theme_combo, theme)
                self.pump()
                self.assertEqual(self.controller.selection, selected)
                self.assertEqual(w.viewport._step['identity']['step_id'], selected['step_id'])
                w.results.export_png(str(OUT / f'populated-curve-{language}-{theme}.png'))
                w.viewport.export_png(str(OUT / f'populated-model-{language}-{theme}.png'))
        self.assertEqual(self.dialogs, [])

    def run_short_fixture(self):
        """Real native worker with a clearly test-only short physical input."""
        w = self.window
        d = self.controller.document
        d['model']['compression_criterion']['threshold'] = 1e-5
        d['model']['gravity']['enabled'] = False
        d['model']['discretization'].update(mesh='M100', layers=4, gauss_points=3)
        d['model']['analysis']['maximum_control_displacement'] = .1
        self.assertTrue(self.controller.apply_document(d), self.errors)
        QTest.keyClick(w, Qt.Key.Key_F6)
        QTest.keyClick(w, Qt.Key.Key_F5)
        deadline = time.monotonic() + 90
        while self.controller.state['running'] and time.monotonic() < deadline:
            self.pump(20)
        self.assertFalse(self.controller.state['running'])
        self.assertEqual(self.controller.current_run['summary']['termination'], 'physical_gauge_compression')

    def test_completed_frozen_context_never_claims_active_analysis(self):
        w = self.window
        self.run_short_fixture()
        self.choose(w.module_combo, 'results')
        observed_status = dict(running=self.controller.state['running'],
                               display=w.display_mode.currentData(),
                               editor_status=w.editor.status.text(),
                               snapshot=w.snapshot_label.text())
        (OUT / 'completed-frozen-status.json').write_text(json.dumps(observed_status, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertNotIn('运行中', w.editor.status.text(),
                         'A finished frozen-result inspection must not claim that analysis is running')
        self.assertNotIn('Analysis running', w.editor.status.text())
        self.choose(w.language_combo, 'en')
        self.assertNotIn('Analysis running', w.editor.status.text(),
                         'English frozen-result inspection must not claim an active analysis')
        self.choose(w.language_combo, 'zh_CN')
        self.assertEqual(self.dialogs, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
