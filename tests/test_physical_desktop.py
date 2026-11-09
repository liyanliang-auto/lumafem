"""Actual Qt/VTK physical-mode background/save/open/export workflow."""
import csv,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python'),str(ROOT/'tests')]
from test_physical_workflows import document
from PySide6.QtWidgets import QApplication,QMessageBox
from lumafem.application import ProjectController
from lumafem.gui.main_window import MainWindow

class PhysicalDesktopTests(unittest.TestCase):
    def test_physical_edit_background_window_csv_png_save_reopen(self):
        app=QApplication.instance() or QApplication([])
        artifacts=Path(os.environ.get('LUMAFEM_TEST_ARTIFACT_DIR',str(ROOT/'.cache/physical-desktop')))/('physical-'+uuid4().hex[:8]);artifacts.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory() as folder,patch.object(QMessageBox,'warning') as warning:
            c=ProjectController(work_dir=folder);errors=[];c.errorRaised.connect(errors.append)
            w=MainWindow(template=document(),controller=c);w.show();app.processEvents()
            try:
                self.assertTrue(c.new_project(document()),errors)
                old_revision=c.document['revision_id']
                draft=c.document;draft['model']['compression_criterion']['gauge_length_mm']=120
                self.assertTrue(c.apply_document(draft),errors);self.assertNotEqual(c.document['revision_id'],old_revision)
                self.assertTrue(c.check_model()['ok'],errors);self.assertTrue(c.start_run(),errors)
                deadline=time.monotonic()+100;beats=0
                while c.state['running'] and time.monotonic()<deadline:app.processEvents();time.sleep(.01);beats+=1
                self.assertFalse(c.state['running'],'physical background timeout');self.assertGreater(beats,0)
                self.assertEqual(c.current_run['summary']['termination'],'physical_gauge_compression',errors)
                step=c.get_step();gauge=step['gauge']['current']
                self.assertTrue(c.select(dict(sequence=step['sequence'],step_id=step['identity']['step_id'],run_id=step['identity']['run_id'])))
                app.processEvents();self.assertGreater(w.results.gauge_table.rowCount(),0)
                self.assertIn('physical_gauge_mean_v1',w.results.criterion_curve.caption)
                self.assertEqual(w.viewport._step['gauge']['current'],gauge)
                w.viewport.export_png(str(artifacts/'physical-window.png'));self.assertTrue((artifacts/'physical-window.png').is_file())
                self.assertTrue(w.grab().save(str(artifacts/'physical-workbench.png')))
                for kind in ('curves','events','convergence'):
                    path=artifacts/(kind+'.csv');self.assertTrue(c.export_data(str(path),kind),errors)
                    meta=json.loads(path.with_suffix('.metadata.json').read_text())['metadata']
                    self.assertEqual(meta['termination'],'physical_gauge_compression')
                    self.assertEqual(meta['compression_criterion']['gauge_length_mm'],120)
                    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
                    self.assertTrue(rows)
                    if kind=='events':
                        event=next(r for r in rows if r['kind']=='physical_gauge_compression')
                        self.assertEqual(json.loads(event['gauge'])['current'],gauge)
                saved=Path(folder)/'physical-saved.lumafem';self.assertTrue(c.save_as(str(saved)),errors)
                run_id=c.current_run['prepared']['identity']['run_id'];self.assertTrue(c.open_project(str(saved)),errors)
                self.assertEqual(c.current_run['prepared']['identity']['run_id'],run_id)
                self.assertEqual(c.get_step()['gauge']['current'],gauge)
                self.assertEqual(warning.call_count,0,errors)
            finally:
                c.close();w.hide();w.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main(verbosity=2)
