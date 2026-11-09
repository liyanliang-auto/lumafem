from __future__ import annotations
import copy,hashlib,importlib.util,json,sqlite3,sys,tempfile,types,unittest
from contextlib import closing
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python')]
from PySide6.QtWidgets import QApplication
from lumafem import native
from lumafem.contracts import ContractError
from lumafem.storage import ProjectStore
from lumafem.gui.editor import ModelEditor

def old_document():return json.loads((ROOT/'development_doc/runtime/NC0.json').read_text())
def current_small():
    d=json.loads((ROOT/'development_doc/runtime/CR-NUM-001/NC0.json').read_text())
    d['revision_id']+='-compatibility-small';d['model']['discretization'].update(mesh='M100',layers=2,gauss_points=2)
    d['model']['analysis'].update(maximum_control_displacement=.4,maximum_step=.05)
    return d
def prep(d):
    result=native.prepare(d)
    if not result['ok']:raise AssertionError(result['diagnostics'])
    return result['prepared']
def receipt(step):return {'ok':True,'identity':step['identity'],'persisted_sequence':step['sequence'],'transaction_id':'test-observer-not-project-storage','diagnostics':[]}

class RevisedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_a04_actual_modal_metrics_storage_validation(self):
        folder=ROOT/'specs/20261108-080032-bx6e/fix2-a04-m25-r1'
        p=json.loads((folder/'mesh25-prepared.json').read_text());step=json.loads((folder/'mesh25-first-modal.json').read_text())
        c=step['convergence'];self.assertEqual(c['predictor_kind'],'critical_mode');self.assertIn(c['predictor_mode_index'],(1,2))
        self.assertGreater(c['predictor_newton_evaluations'],0);self.assertGreaterEqual(c['total_newton_evaluations'],c['predictor_newton_evaluations'])
        self.assertLessEqual(c['predictor_attempts'],15);self.assertEqual(c['predictor_decompositions'],1)
        ProjectStore._step(step,p)
        for key,value in [('predictor_mode_index',0),('predictor_mode_index',3),('predictor_attempts',16),('predictor_newton_evaluations',c['total_newton_evaluations']+1)]:
            with self.subTest(field=key,value=value):
                bad=copy.deepcopy(step);bad['convergence'][key]=value
                with self.assertRaises(ContractError):ProjectStore._step(bad,p)

    def test_a03_missing_mode_remains_absent_when_viewed(self):
        folder=ROOT/'specs/20261108-080032-bx6e/fix2-modal-m25-r2'
        p=json.loads((folder/'mesh25-prepared.json').read_text());step=json.loads((folder/'mesh25-first-modal.json').read_text())
        self.assertNotIn('predictor_mode_index',step['convergence']);before=copy.deepcopy(step)
        ProjectStore._step(step,p);self.assertEqual(step,before)
        from lumafem.gui.results import ResultsPanel
        panel=ResultsPanel();panel.set_step_provider(lambda _:copy.deepcopy(step));panel.set_run({'prepared':p,'steps':[step],'summary':None})
        self.assertNotIn('predictor_mode_index',panel._resolve_step(panel._by_id[step['identity']['step_id']])['convergence']);self.assertEqual(step,before)
        panel.deleteLater();self.app.processEvents()

    def test_archived_exact_prepared_accepts_cancel_and_bad_hash_rejects(self):
        p=json.loads((ROOT/'specs/20261107-202653-v5ge/scientific-round2/base-prepared.json').read_text())
        self.assertEqual(native.analyze(p,cancelled=lambda:True)['termination'],'cancelled')
        p['nodes'][0]['x']+=.01
        self.assertEqual(native.analyze(p,cancelled=lambda:True)['termination'],'input_error')

    def test_new_mapping_and_invalid_option_domains(self):
        d=current_small();p=prep(d);self.assertEqual(p['numerics_contract'],'CR-NUM-002')
        for section in p['sections']:
            L=p['elements'][section['element_index']]['length']
            self.assertEqual(section['lch_t'],L);self.assertEqual(section['lch_c'],L)
        for key,value in [('line_search_reduction',0),('transition_ratio_tolerance',0),('maximum_transition_solves',41)]:
            bad=copy.deepcopy(d);bad['model']['analysis'][key]=value;self.assertFalse(native.prepare(bad)['ok'])
        bad=copy.deepcopy(d);bad['model']['discretization']['length_mapping']='future_unknown';self.assertFalse(native.prepare(bad)['ok'])

    def test_legacy_virtual_editor_options_do_not_mutate_wire(self):
        app=QApplication.instance() or QApplication([]);d=old_document();before=copy.deepcopy(d)
        editor=ModelEditor();editor.set_document(d)
        self.assertFalse(editor._get(('model','analysis','line_search_enabled')))
        self.assertFalse(editor._get(('model','analysis','transition_resolution_enabled')))
        self.assertEqual(editor.draft_document(),before);self.assertEqual(d,before)
        editor.deleteLater();app.processEvents()

    def test_reader_one_readonly_rollback_upgrade_and_old_reader_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'legacy.lumafem';d=old_document()
            with ProjectStore(path) as store:store.save_document(d)
            with closing(sqlite3.connect(path)) as db:
                db.execute('UPDATE metadata SET value=? WHERE key=?',('1','minimum_reader'))
                db.execute('UPDATE metadata SET value=? WHERE key=?',(json.dumps('0.1.0'),'application_version'))
                db.commit()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            with ProjectStore(path,readonly=True) as store:self.assertEqual(store.load_document(),d)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            edited=copy.deepcopy(d);edited['revision_id']+='-new-reader';edited['name']='atomic reader upgrade'
            with ProjectStore(path) as store:
                def fail(point):
                    if point=='save_document:before_commit':raise OSError('dedicated-fixture rollback')
                store.fault_injector=fail
                with self.assertRaises(ContractError):store.save_document(edited)
                self.assertEqual(store.load_document(),d)
            with closing(sqlite3.connect(path)) as db:self.assertEqual(json.loads(db.execute("SELECT value FROM metadata WHERE key='minimum_reader'").fetchone()[0]),1)
            package=types.ModuleType('lumafem_legacy_contract_test');package.__path__=[str(ROOT/'dist/LumaFEM/app/lumafem')];sys.modules[package.__name__]=package
            for name in ['contracts','storage']:
                spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            oldstore=sys.modules[package.__name__+'.storage'].ProjectStore;olderror=sys.modules[package.__name__+'.contracts'].ContractError
            with oldstore(path,readonly=True) as store:self.assertEqual(store.load_document(),d)
            with ProjectStore(path) as store:store.save_document(edited)
            with closing(sqlite3.connect(path)) as db:self.assertEqual(json.loads(db.execute("SELECT value FROM metadata WHERE key='minimum_reader'").fetchone()[0]),3)
            with self.assertRaises(olderror):oldstore(path,readonly=True)
            with closing(sqlite3.connect(path)) as db:
                db.execute('UPDATE metadata SET value=? WHERE key=?',('4','minimum_reader'));db.commit()
            with self.assertRaises(ContractError):ProjectStore(path,readonly=True)

    def test_cancel_during_transition_does_not_accept_onset(self):
        p=prep(current_small());accepted=[];cancel=[False]
        def progress(value):
            if value['stage']=='transition':cancel[0]=True
        def on_step(step):accepted.append(step);return receipt(step)
        result=native.analyze(p,on_step=on_step,on_progress=progress,cancelled=lambda:cancel[0])
        self.assertEqual(result['termination'],'cancelled');self.assertTrue(cancel[0])
        self.assertFalse(any(s['transition']['active'] for s in accepted))
        self.assertEqual(result['memory_last_step'],result['persisted_last_step'])

    def test_onset_acceptance_receipt_and_post_save_cancel_are_distinct(self):
        p=prep(current_small());accepted=[];cancel=[False]
        def on_step(step):
            accepted.append(step)
            if step['transition']['active']:cancel[0]=True
            return receipt(step)
        result=native.analyze(p,on_step=on_step,cancelled=lambda:cancel[0])
        self.assertEqual(result['termination'],'cancelled')
        onset=[s for s in accepted if s['transition']['active']];self.assertEqual(len(onset),1)
        self.assertEqual(onset[0]['transition']['kind'],'tensile_onset')
        self.assertFalse(result['physical_criterion_reached'])
        self.assertEqual(result['memory_last_step'],result['persisted_last_step'])
        p=prep(current_small());accepted=[]
        def failing(step):
            if step['transition']['active']:
                r=receipt(accepted[-1]);r['ok']=False;return r
            accepted.append(step);return receipt(step)
        result=native.analyze(p,on_step=failing)
        self.assertEqual(result['termination'],'storage_error')
        self.assertEqual(result['memory_last_step'],result['persisted_last_step']+1)

if __name__=='__main__':unittest.main(verbosity=2)
