"""Behavioral checks; scientific fixtures stay in the ignored local case directory."""
from __future__ import annotations
import copy, hashlib, json, math, os, sqlite3, sys, tempfile, time, unittest
from uuid import uuid4
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ARTIFACTS=Path(os.environ.get('LUMAFEM_TEST_ARTIFACT_DIR',str(ROOT/'.cache/test-artifacts'/uuid4().hex)))
ARTIFACTS.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python')]
from lumafem import native
from lumafem.contracts import ContractError, copy_value, same_run, validate_finite
from lumafem.storage import ProjectStore
from lumafem.observations import import_csv, preview_csv, compare_curves, export_csv, observe_step
from PySide6.QtWidgets import QApplication, QMessageBox
from lumafem.application import ProjectController
from lumafem.gui.main_window import MainWindow

def document():
    return json.loads((ROOT/'development_doc/runtime/NC0.json').read_text(encoding='utf-8'))

def short_document():
    d=document()
    d['model']['gravity']['enabled']=False
    d['model']['analysis']['maximum_control_displacement']=.04
    d['model']['discretization']['mesh']='M100'
    return d

def format_one_document():
    """Synthetic format-1 fixture, separate from regression for actual NC0 package IDs."""
    d=short_document()
    for m in d['materials']:m['version']='1'
    return d

def prepared(d=None):
    p=native.prepare(d or short_document())
    if not p['ok']: raise AssertionError(p['diagnostics'])
    return p['prepared']

class ContractTests(unittest.TestCase):
    def test_copy_and_identity_do_not_alias(self):
        a={'values':[1.]};b=copy_value(a);b['values'][0]=2;self.assertEqual(a['values'],[1.])
        with self.assertRaises(ContractError): validate_finite({'x':float('nan')})
        self.assertFalse(same_run({'run_id':'a'},{'run_id':'a'}))

    def test_default_mesh_geometry_and_nested_key_nodes(self):
        all_x=[]
        for mesh,counts in [('M100',(25,24)),('M50',(45,44)),('M25',(89,88))]:
            d=document();d['model']['discretization']['mesh']=mesh;p=prepared(d)
            self.assertEqual((len(p['nodes']),len(p['elements'])),counts)
            xs={x['x'] for x in p['nodes']};all_x.append(xs)
            self.assertTrue({0,250,800,1100,1400,1950,2200}<=xs)
            self.assertEqual(len(p['sections']),counts[1]*3)
            self.assertAlmostEqual(sum(s['integration_length'] for s in p['sections']),2200.,places=8)
        self.assertTrue(all_x[0]<=all_x[1]<=all_x[2])

    def test_invalid_model_is_structured_error(self):
        d=document();d['model']['geometry']['height']=-1
        r=native.prepare(d);self.assertFalse(r['ok']);self.assertTrue(r['diagnostics'])
        d=document();d['materials'].clear();self.assertFalse(native.prepare(d)['ok'])

    def test_prepared_mutation_is_rejected(self):
        p=prepared();p['nodes'][0]['x']+=1
        r=native.analyze(p);self.assertEqual(r['termination'],'input_error')

    def test_memory_run_accepts_no_false_saved_position(self):
        r=native.analyze(prepared())
        self.assertEqual(r['termination'],'safety_limit')
        self.assertGreater(r['memory_last_step'],0);self.assertEqual(r['persisted_last_step'],-1)
        self.assertEqual(r['validation_status']['C'],'PENDING_DATA')

    def test_cooperative_cancel_before_accept(self):
        r=native.analyze(prepared(),cancelled=lambda:True)
        self.assertEqual(r['termination'],'cancelled');self.assertEqual(r['memory_last_step'],-1)

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'project.lumafem';self.doc=format_one_document();self.p=prepared(self.doc)

    def test_default_material_package_version_is_not_schema_version(self):
        with ProjectStore(self.path) as store:
            store.save_document(document())
            self.assertEqual(store.load_document(),document())

    def run_saved(self):
        with ProjectStore(self.path) as store:
            store.save_document(self.doc);store.begin_run(self.p)
            result=native.analyze(self.p,on_step=store.append_step)
            store.finish_run(result)
        return result

    def test_model_and_full_result_exact_reopen(self):
        r=self.run_saved();self.assertEqual(r['termination'],'safety_limit')
        with ProjectStore(self.path,readonly=True) as store:
            self.assertEqual(store.load_document(),self.doc)
            run=store.load_run();self.assertEqual(run['summary'],r)
            self.assertEqual(len(run['steps']),r['persisted_last_step']+1)
            step=run['steps'][-1];self.assertTrue(step['sections']);self.assertTrue(step['fibers'])
            self.assertEqual(step,store.load_step(self.p['identity']['run_id'],step['sequence']))

    def test_fault_before_commit_retains_old_complete_step(self):
        with ProjectStore(self.path) as store:
            store.save_document(self.doc);store.begin_run(self.p)
            def inject(phase):
                if phase=='append_step:before_commit': raise OSError('injected dedicated-copy IO failure')
            store.fault_injector=inject
            r=native.analyze(self.p,on_step=store.append_step)
            self.assertEqual(r['termination'],'storage_error')
            self.assertEqual(r['memory_last_step'],0);self.assertEqual(r['persisted_last_step'],-1)
        with ProjectStore(self.path,readonly=True) as store:
            self.assertEqual(store.load_run()['steps'],[])

    def test_backup_recovery_and_missing_source_do_not_destroy_project(self):
        self.run_saved()
        with ProjectStore(self.path) as store:
            target=Path(self.tmp.name)/'backup.lumafem';store.backup(target)
            d=copy.deepcopy(self.doc);d['revision_id']='recovery-revision';d['name']='unsaved edit'
            rec=Path(self.tmp.name)/'recover.lumafem';store.write_recovery(d,rec)
        with ProjectStore(target,readonly=True) as store:self.assertEqual(store.load_document(),self.doc)
        with ProjectStore(rec,readonly=True) as store:self.assertEqual(store.load_document(),d)

    def test_unknown_schema_is_rejected(self):
        self.run_saved()
        with sqlite3.connect(self.path) as db: db.execute('PRAGMA user_version=999')
        with self.assertRaises(ContractError):ProjectStore(self.path,readonly=True)

class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'channels.csv'
        self.path.write_text('p1,p2,mid,left,right\n0,0,0,0,0\n1,2,1,0.1,0.1\n2,4,2,0.2,0.2\n',encoding='utf-8')
        self.mapping={'channels':{'P1':{'column':'p1','unit':'kN'},'P2':{'column':'p2','unit':'kN'},'midpoint':{'column':'mid','unit':'mm'},'left_support':{'column':'left','unit':'mm'},'right_support':{'column':'right','unit':'mm'}},'load_source':'sum','zero_row':0,'settlement_correction':True,'control_x':500,'support_x':[0,1000]}

    def test_csv_calibration_units_sum_zero_chord_and_readonly_source(self):
        before=self.path.read_bytes();d=import_csv(str(self.path),self.mapping)
        self.assertEqual(d['rows'][1]['P'],3000);self.assertAlmostEqual(d['rows'][1]['w'],.9)
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(preview_csv(str(self.path))['row_count'],3)

    def test_pressure_requires_explicit_calibration(self):
        m=copy.deepcopy(self.mapping);m['channels']['P1']['unit']='MPa'
        with self.assertRaises(ContractError):import_csv(str(self.path),m)
        m['channels']['P1']['calibration']={'coefficients':[0,2],'output_unit':'kN','reference':'synthetic calibration'}
        d=import_csv(str(self.path),m);self.assertEqual(d['rows'][1]['P'],4000)

    def test_synthetic_comparison_never_physical_pass_and_export_roundtrip(self):
        d=import_csv(str(self.path),self.mapping);d['synthetic']=True
        sim=[{'w':r['w'],'P':r['P']} for r in d['rows']]
        r=compare_curves(sim,d)
        self.assertNotEqual(r.get('physical_validation'),'PASS')
        out=Path(self.tmp.name)/'export.csv';export_csv(str(out),sim,{'synthetic':True})
        self.assertTrue(out.is_file());self.assertTrue(out.with_suffix('.metadata.json').is_file())

class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_actual_qt_vtk_new_check_png_selection_and_background_save(self):
        notices=[]
        with tempfile.TemporaryDirectory() as t, patch.object(QMessageBox,'warning',side_effect=lambda *args:notices.append(str(args[-1]))):
            controller=ProjectController(work_dir=t);errors=[];controller.errorRaised.connect(errors.append)
            window=MainWindow(template=format_one_document(),controller=controller)
            window.show();self.app.processEvents()
            self.assertTrue(controller.new_project(format_one_document()),errors)
            self.assertTrue(controller.check_model().get('ok'),errors)
            self.assertEqual(notices,[],notices)
            for _ in range(5):self.app.processEvents();time.sleep(.01)
            png=ARTIFACTS/'desktop-tested.png'
            self.assertTrue(window.grab().save(str(png)))
            window.viewport.export_png(str(ARTIFACTS/f'viewport-tested-{uuid4().hex[:8]}.png'))
            self.assertTrue(controller.start_run(),errors)
            deadline=time.monotonic()+60;heartbeat=0
            while controller.state['running'] and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.01);heartbeat+=1
            self.assertFalse(controller.state['running'],'background task timed out')
            self.assertGreater(heartbeat,0)
            self.assertEqual(controller.current_run['summary']['termination'],'safety_limit',errors)
            step=controller.get_step();self.assertTrue(step['sections'])
            self.assertTrue(controller.select({'sequence':step['sequence'],'step_id':step['identity']['step_id'],'run_id':step['identity']['run_id']}))
            self.app.processEvents();self.assertEqual(window.results._selection['step_id'],step['identity']['step_id'])
            self.assertTrue(controller.save_as(str(Path(t)/'saved.lumafem')))
            self.assertTrue(controller.close());window.hide();window.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main(verbosity=2)

