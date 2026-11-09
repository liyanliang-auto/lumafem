"""Actual application Save As must preserve its immutable legacy source file."""
import hashlib,json,shutil,sqlite3,sys,tempfile,unittest,os
from uuid import uuid4
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python')]
from PySide6.QtWidgets import QApplication
from lumafem.application import ProjectController
from lumafem.storage import ProjectStore
FIXTURE=Path(os.environ.get('LUMAFEM_LEGACY_FIXTURE',str(ROOT/'specs/20261108-121013-uuv1/testing2-legacy-cap2-fixture.lumafem')))
OUT=Path(os.environ.get('LUMAFEM_TEST_EVIDENCE_DIR',str(ROOT/'.cache/test-artifacts')))/('legacy-'+uuid4().hex)
OUT.mkdir(parents=True,exist_ok=False)
class LegacySaveAsTests(unittest.TestCase):
    def test_application_save_as_does_not_rewrite_legacy_original(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'legacy-copy.lumafem';target=Path(folder)/'save-as.lumafem'
            shutil.copyfile(FIXTURE,original)
            def snapshot():
                with ProjectStore(original,readonly=True) as store:document=store.load_document();run=store.load_run()
                with sqlite3.connect(original) as db:metadata=dict(db.execute('SELECT key,value FROM metadata'))
                return dict(sha256=hashlib.sha256(original.read_bytes()).hexdigest(),document=document,prepared=run['prepared'],metadata=metadata,analysis_read_only=run['analysis_read_only'])
            before=snapshot();c=ProjectController(work_dir=folder);errors=[];c.errorRaised.connect(errors.append)
            try:
                self.assertTrue(c.open_project(str(original)),errors);self.assertTrue(c.current_run['analysis_read_only'])
                self.assertEqual(snapshot()['sha256'],before['sha256'],'view already changed legacy file')
                self.assertTrue(c.save_as(str(target)),errors);after=snapshot()
                (OUT/'legacy-save-as-evidence.json').write_text(json.dumps(dict(before=before,after=after,errors=errors,source_generated_fixture_unchanged=True),indent=2))
                self.assertEqual(after['sha256'],before['sha256'],'Save As rewrote the analysis-read-only legacy original')
                self.assertEqual(after['document'],before['document']);self.assertEqual(after['metadata'],before['metadata'])
                with ProjectStore(target,readonly=True) as store:self.assertEqual(store.load_document(),before['document']);self.assertEqual(store.load_run()['prepared'],before['prepared'])
                def tables(file):
                    with sqlite3.connect(file) as db:
                        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                        return {name:db.execute('SELECT * FROM '+chr(34)+name.replace(chr(34),chr(34)*2)+chr(34)).fetchall() for name in names}
                self.assertEqual(tables(original),tables(target),'Save As changed SQL rows/metadata in destination')
            finally:c.close();app.processEvents()
if __name__=='__main__':unittest.main(verbosity=2)
