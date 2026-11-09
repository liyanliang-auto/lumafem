"""Actual Qt/VTK regression for the reviewed GUI-R1 workbench.

All projects and screenshots are dedicated copies. No numerical/IO service is
mocked. Qt events exercise the visible work surface; internal identities are
inspected only as assertions on those events' actual outcomes.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'build/python')]
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QLabel, QPushButton
from lumafem.application import ProjectController
from lumafem.gui.main_window import MainWindow, CsvImportDialog

OUT = Path(os.environ.get('LUMAFEM_GUI_TEST_DIR', str(ROOT / '.cache/gui-workflow' / uuid4().hex)))
OUT.mkdir(parents=True, exist_ok=True)

def document():
    return json.loads((ROOT / 'development_doc/runtime/CR-NUM-002/NC0.json').read_text(encoding='utf-8-sig'))

class GuiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.errors = []
        self.slot_errors = []
        self.dialogs = []
        self._old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, traceback: self.slot_errors.append(f'{kind.__name__}: {error}')
        self.controller = ProjectController(work_dir=self.folder.name)
        self.controller.errorRaised.connect(self.errors.append)
        self.window = MainWindow(template=document(), controller=self.controller)
        self.window.show()
        self.assertTrue(self.controller.new_project(document()), self.errors)
        self.modal_timer = QTimer()
        self.modal_timer.timeout.connect(self._close_unexpected_dialog)
        self.modal_timer.start(50)
        self.pump()

    def _close_unexpected_dialog(self):
        modal = QApplication.activeModalWidget()
        if isinstance(modal, QMessageBox):
            self.dialogs.append(dict(title=modal.windowTitle(), text=modal.text()))
            modal.reject()

    def pump(self, milliseconds=100):
        QTest.qWait(milliseconds)
        self.app.processEvents()

    def choose(self, combo, key):
        index = combo.findData(key)
        self.assertGreaterEqual(index, 0)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Home)
        for _ in range(index):
            QTest.keyClick(combo, Qt.Key.Key_Down)
        self.pump()
        self.assertEqual(combo.currentData(), key)

    def tree(self, path):
        item = self.window.editor._path_items[path]
        self.window.editor.tree.scrollToItem(item)
        self.pump(30)
        rect = self.window.editor.tree.visualItemRect(item)
        self.assertTrue(rect.isValid())
        QTest.mouseClick(self.window.editor.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.pump(30)
        return item

    def field(self, path):
        widget = self.window.editor.findChild(QLineEdit, 'field_' + '.'.join(map(str, path)))
        self.assertIsNotNone(widget, path)
        return widget

    def replace_text(self, field, text):
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(field, text)
        self.pump(30)

    def tearDown(self):
        if hasattr(self, 'modal_timer'):
            self.modal_timer.stop()
        if self.controller.state.get('running'):
            self.controller.cancel_run()
            end = time.monotonic()+30
            while self.controller.state.get('running') and time.monotonic()<end:
                self.pump(10)
        self.controller.close()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.folder.cleanup()
        sys.excepthook = self._old_hook
        self.assertEqual(self.slot_errors, [], 'Unhandled Qt slot exceptions')
        (OUT / (self.id().split('.')[-1] + '-observed.json')).write_text(json.dumps(dict(errors=self.errors, dialogs=self.dialogs), ensure_ascii=False, indent=2), encoding='utf-8')

    def test_default_chinese_eight_modules_and_supported_layouts(self):
        w = self.window
        self.assertEqual(w.language, 'zh_CN')
        self.assertEqual([w.module_combo.itemData(i) for i in range(w.module_combo.count())],
                         ['geometry','materials','section','step','loads','mesh','job','results'])
        self.assertEqual(w.module_combo.itemText(0), '\u51e0\u4f55')
        for width, height in ((1500,980),(1280,800)):
            w.resize(width,height)
            w.restore_layout()
            self.pump(200)
            self.assertTrue(w.grab().save(str(OUT/f'layout-zh-{width}x{height}.png')))
            actual = dict(width=w.width(),height=w.height(),viewport_width=w.viewport.vtk_widget.width(),viewport_height=w.viewport.vtk_widget.height())
            (OUT/f'layout-zh-{width}x{height}.json').write_text(json.dumps(actual))
            self.assertEqual((w.width(),w.height()), (width,height), actual)
            self.assertGreaterEqual(w.viewport.vtk_widget.width(),320)
            self.assertGreaterEqual(w.viewport.vtk_widget.height(),220)
            for dock in (w.navigation_dock,w.properties_dock,w.results_dock):
                self.assertTrue(dock.isVisible())
        self.choose(w.language_combo,'en')
        self.assertEqual(w.editor.language,'en')
        self.assertEqual(w.results.language,'en')
        self.assertEqual(w.viewport.language,'en')
        self.assertEqual(w.module_combo.itemText(0),'Geometry')
        self.assertTrue(w.grab().save(str(OUT/'layout-en-1280x800.png')))
        self.assertEqual(self.dialogs,[])

    def test_exact_pending_and_invalid_input_survives_navigation_and_language(self):
        w = self.window
        self.tree(('model','geometry'))
        path=('model','geometry','length')
        self.replace_text(self.field(path),'2300.000')
        before = self.controller.document
        self.choose(w.module_combo,'materials')
        self.choose(w.language_combo,'en')
        self.choose(w.module_combo,'geometry')
        self.tree(('model','geometry'))
        self.assertEqual(self.field(path).text(),'2300.000')
        self.assertEqual(self.controller.document,before)
        self.replace_text(self.field(path),'invalid-value')
        self.choose(w.module_combo,'materials')
        self.choose(w.language_combo,'zh_CN')
        self.choose(w.module_combo,'geometry')
        self.tree(('model','geometry'))
        self.assertEqual(self.field(path).text(),'invalid-value')
        self.assertEqual(self.controller.document,before)
        self.assertTrue(w.editor.has_pending_changes)
        QTest.mouseClick(w.editor.revert_button,Qt.MouseButton.LeftButton)
        self.pump()
        self.assertFalse(w.editor.has_pending_changes)
        self.assertEqual(self.controller.document,before)
        self.assertEqual(self.dialogs,[])

    def test_initial_geometry_context_keeps_internal_revision_out_of_common_properties(self):
        common = self.window.editor.form_scroll.widget()
        labels = [widget.text() for widget in common.findChildren(QLabel)]
        buttons = [widget.text() for widget in common.findChildren(QPushButton)]
        self.assertFalse(any('numerical_revision' in text for text in labels+buttons),
                         dict(labels=labels,buttons=buttons))
        self.assertEqual(self.dialogs,[])

    def test_actual_vtk_tree_pick_and_unicode_export_preserve_snapshot(self):
        w=self.window
        QTest.keyClick(w, Qt.Key.Key_F6)
        self.pump(250)
        self.assertTrue(w.viewport._prepared,self.errors)
        original=copy.deepcopy(w.viewport._prepared)
        self.tree(('model','geometry'))
        self.assertEqual(w.viewport._selection.get('object_id'),'geometry')
        self.assertTrue(w.viewport._selection_actors)
        self.choose(w.module_combo,'materials')
        self.tree(('materials',0))
        self.assertFalse(w.viewport._selection_actors)
        self.assertIn('\u65e0\u5bf9\u5e94\u51e0\u4f55',w.viewport.selection_label.text())
        QTest.mouseClick(w.viewport.visibility_button,Qt.MouseButton.LeftButton)
        for key in ('stirrups','rebar','original'):
            if w.viewport._visible[key].isChecked():
                QTest.mouseClick(w.viewport._visible[key],Qt.MouseButton.LeftButton)
        QTest.mouseClick(w.viewport.visibility_button,Qt.MouseButton.LeftButton)
        w.viewport.front_view()
        self.pump(150)
        # Pick a real generated body actor from its world bounds through actual
        # QVTK mouse events, rather than emitting a synthetic objectSelected.
        actor=w.viewport._geometry_actors[len(w.viewport._geometry_actors)//2]
        expected=w.viewport._actor_ids[actor.GetAddressAsString('')]
        bounds=actor.GetBounds()
        world=[(bounds[2*i]+bounds[2*i+1])/2 for i in range(3)]
        w.viewport.renderer.SetWorldPoint(*world,1)
        w.viewport.renderer.WorldToDisplay()
        display=w.viewport.renderer.GetDisplayPoint()
        rw_width,rw_height=w.viewport.render_window.GetSize()
        logical=QPoint(round(display[0]*w.viewport.vtk_widget.width()/rw_width),
                       round((rw_height-display[1])*w.viewport.vtk_widget.height()/rw_height))
        QTest.mouseMove(w.viewport.vtk_widget,logical)
        self.pump(50)
        QTest.mouseClick(w.viewport.vtk_widget,Qt.MouseButton.LeftButton,pos=logical)
        self.pump(150)
        event=w.viewport.interactor.GetEventPosition()
        diagnostic_pick=w.viewport.picker.Pick(event[0],event[1],0,w.viewport.renderer)
        picked=w.viewport.picker.GetActor()
        diagnostic_id=w.viewport._actor_ids.get(picked.GetAddressAsString(''),'') if picked else ''
        (OUT/'actual-pick-context.json').write_text(json.dumps(dict(world=world,display=display,
            logical=[logical.x(),logical.y()],render_size=[rw_width,rw_height],
            qt_size=[w.viewport.vtk_widget.width(),w.viewport.vtk_widget.height()],
            actual_event=list(event),selection=w.viewport._selection,expected=expected,
            diagnostic_manual_picker_hit=diagnostic_pick,diagnostic_manual_actor=diagnostic_id)))
        self.assertTrue(diagnostic_pick,'Independent VTK cell picker must hit the rendered body')
        self.assertTrue(diagnostic_id)
        self.assertEqual(w.viewport._selection.get('object_id'),diagnostic_id,
                         'Actual Qt mouse click must select the rendered actor under that same event point')
        image=OUT/'real-viewport-zh.png'
        w.viewport.export_png(str(image))
        self.assertGreater(image.stat().st_size,1000)
        with self.assertRaises(Exception):
            w.viewport.export_png(str(image))
        self.assertEqual(w.viewport._prepared,original)
        self.assertEqual(self.dialogs,[])

    def test_real_unicode_png_and_language_preserve_model_camera(self):
        w=self.window
        QTest.keyClick(w,Qt.Key.Key_F6)
        self.pump(200)
        original=copy.deepcopy(w.viewport._prepared)
        camera=w.viewport.renderer.GetActiveCamera()
        before=(camera.GetPosition(),camera.GetFocalPoint(),camera.GetViewUp(),camera.GetParallelScale())
        w.viewport.export_png(str(OUT/'export-model-zh.png'))
        self.choose(w.language_combo,'en')
        w.viewport.export_png(str(OUT/'export-model-en.png'))
        self.assertEqual(w.viewport._prepared,original)
        self.assertEqual((camera.GetPosition(),camera.GetFocalPoint(),camera.GetViewUp(),camera.GetParallelScale()),before)
        self.assertGreater((OUT/'export-model-zh.png').stat().st_size,1000)
        self.assertGreater((OUT/'export-model-en.png').stat().st_size,1000)
        with self.assertRaises(Exception) as caught:
            w.viewport.export_png(str(OUT/'export-model-zh.png'))
        self.assertEqual(caught.exception.diagnostic['code'],'export_exists')
        self.assertEqual(self.dialogs,[])

    def test_bilingual_csv_dialog_preserves_mapping_and_actual_standard_buttons(self):
        w=self.window
        source=ROOT/'development_doc/runtime/CR-NUM-002/synthetic_channels.csv'
        dialog=CsvImportDialog(str(source),self.controller.document,w)
        dialog.show()
        self.pump()
        original=copy.deepcopy(dialog._mapping())
        self.assertEqual(dialog.load_source.currentData(),'sum')
        self.choose(w.language_combo,'en')
        # Dialog connects to its parent's live language coordinator.
        self.pump()
        self.assertEqual(dialog._mapping(),original)
        self.assertIn('CSV',dialog.windowTitle())
        self.assertIn('sum',str(dialog.load_source.currentData()))
        self.assertTrue(dialog.grab().save(str(OUT/'csv-dialog-en.png')))
        self.choose(w.language_combo,'zh_CN')
        self.assertEqual(dialog._mapping(),original)
        self.assertTrue(dialog.grab().save(str(OUT/'csv-dialog-zh.png')))
        dialog.reject()
        dialog.deleteLater()
        self.assertEqual(self.dialogs,[])

    def test_real_background_cancel_language_and_mutation_locks(self):
        w=self.window
        QTest.keyClick(w,Qt.Key.Key_F6)
        self.pump(100)
        QTest.keyClick(w,Qt.Key.Key_F5)
        self.pump(30)
        self.assertTrue(self.controller.state['running'],self.errors)
        self.assertFalse(w.actions['run'].isEnabled())
        self.assertFalse(w.editor.apply_button.isEnabled())
        before=self.controller.document
        self.choose(w.language_combo,'en')
        self.assertTrue(self.controller.state['running'])
        self.assertEqual(self.controller.document,before)
        QTest.mouseClick(w.job_monitor.cancel_button,Qt.MouseButton.LeftButton)
        end=time.monotonic()+30
        while self.controller.state['running'] and time.monotonic()<end:
            self.pump(10)
        self.assertFalse(self.controller.state['running'],'cooperative cancel timed out')
        self.assertEqual(self.controller.current_run['summary']['termination'],'cancelled',self.errors)
        self.assertFalse(w.actions['cancel'].isEnabled())
        self.assertEqual(self.controller.document,before)
        self.assertTrue(w.grab().save(str(OUT/'cancelled-en.png')))
        self.assertEqual(self.dialogs,[])

    def test_real_completed_frame_curve_unavailable_and_save_copy(self):
        w=self.window
        d=self.controller.document
        d['model']['compression_criterion']['threshold']=1e-5
        d['model']['gravity']['enabled']=False
        d['model']['discretization'].update(mesh='M100',layers=4,gauss_points=3)
        d['model']['analysis']['maximum_control_displacement']=.1
        self.assertTrue(self.controller.apply_document(d),self.errors)
        QTest.keyClick(w,Qt.Key.Key_F6)
        QTest.keyClick(w,Qt.Key.Key_F5)
        end=time.monotonic()+90
        while self.controller.state['running'] and time.monotonic()<end:
            self.pump(20)
        self.assertFalse(self.controller.state['running'])
        self.assertEqual(self.controller.current_run['summary']['termination'],'physical_gauge_compression',self.errors)
        self.choose(w.module_combo,'results')
        self.pump(150)
        self.assertTrue(w.viewport._step)
        original=copy.deepcopy(self.controller.get_step())
        original_identity=copy.deepcopy(original['identity'])
        self.choose(w.language_combo,'en')
        self.assertEqual(self.controller.get_step()['identity'],original_identity)
        self.assertEqual(w.viewport._step['identity'],original_identity)
        points=w.results.curve._hit_points
        self.assertTrue(points)
        point,record=points[0]
        QTest.mouseClick(w.results.curve,Qt.MouseButton.LeftButton,pos=point.toPoint())
        self.pump(100)
        self.assertEqual(self.controller.selection['step_id'],record['identity']['step_id'])
        self.assertEqual(w.viewport._step['identity']['step_id'],record['identity']['step_id'])
        selected_identity=copy.deepcopy(self.controller.get_step()['identity'])
        old_step=w.viewport._step['identity']['step_id']
        w.results.set_selection(dict(run_id=original_identity['run_id'],step_id='not-a-saved-frame',sequence=original['sequence']))
        self.pump()
        self.assertIsNone(w.viewport._step)
        self.assertIn('unavailable',w.results.identity_label.text().lower())
        self.choose(w.language_combo,'zh_CN')
        self.assertIsNone(w.viewport._step)
        self.assertTrue(w.grab().save(str(OUT/'unavailable-zh.png')))
        saved=Path(self.folder.name)/'gui-copy.lumafem'
        self.assertTrue(self.controller.save_as(str(saved)),self.errors)
        self.assertTrue(self.controller.open_project(str(saved)),self.errors)
        self.assertEqual(self.controller.get_step()['identity'],selected_identity)
        for kind in ('curves','events','convergence'):
            self.assertTrue(self.controller.export_data(str(OUT/(kind+'-'+uuid4().hex[:8]+'.csv')),kind),self.errors)
        self.assertEqual(self.dialogs,[])

if __name__ == '__main__':
    unittest.main(verbosity=2)
