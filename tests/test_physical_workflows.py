"""CR2 real-native cross-module contract checks, using dedicated temporary stores."""
from __future__ import annotations
import copy, hashlib, importlib.util, json, math, sqlite3, sys, tempfile, types, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python')]
from lumafem import native
from lumafem.contracts import ContractError, KERNEL_VERSION
from lumafem.storage import ProjectStore

def document():
    d=json.loads((ROOT/'development_doc/runtime/CR-NUM-002/NC0.json').read_text())
    d['revision_id']+='-physical-unit'
    d['model']['compression_criterion']['threshold']=1e-5
    d['model']['gravity']['enabled']=False
    d['model']['discretization'].update(mesh='M100',layers=4,gauss_points=3)
    d['model']['analysis']['maximum_control_displacement']=.1
    return d
def prepare(d):
    r=native.prepare(d)
    if not r['ok']:raise AssertionError(r['diagnostics'])
    return r['prepared']
def receipt(s):return dict(ok=True,identity=s['identity'],persisted_sequence=s['sequence'],transaction_id='test-memory-port',diagnostics=[])
def analyze(d,**kw):
    p=prepare(d);steps=[]
    def accept(s):steps.append(s);return receipt(s)
    result=native.analyze(p,on_step=kw.pop('on_step',accept),**kw)
    return p,result,steps

def independent_mean(p,u,w):
    """Integrate independently evaluated EB edge strain with exact 2-point Gauss.

    This uses strain shape-function second derivatives, not product endpoint D
    or its affine-root optimizer. A quadratic EB displacement has affine strain.
    """
    a,b=w['x1'],w['x2'];eta=w['eta'];total=0.
    for e in p['elements']:
        x0=p['nodes'][e['nodes'][0]]['x'];x1=p['nodes'][e['nodes'][1]]['x']
        lo=max(a,x0);hi=min(b,x1)
        if hi<=lo:continue
        dof=e['dofs'];c=e['cosine'];s=e['sine'];L=x1-x0
        aa=c*u[dof[0]]+s*u[dof[1]];ab=c*u[dof[3]]+s*u[dof[4]]
        va=-s*u[dof[0]]+c*u[dof[1]];vb=-s*u[dof[3]]+c*u[dof[4]]
        values=[]
        for z in (-1/math.sqrt(3),1/math.sqrt(3)):
            x=(lo+hi)/2+z*(hi-lo)/2;t=(x-x0)/L
            curvature=((-6+12*t)*va+L*(-4+6*t)*u[dof[2]]+(6-12*t)*vb+L*(-2+6*t)*u[dof[5]])/L**2
            values.append((ab-aa)/L-eta*curvature)
        total+=(hi-lo)*sum(values)/2
    return total/(b-a)

class PhysicalWorkflowTests(unittest.TestCase):
    def test_actual_sealed_capability_two_reader_rejects_new_three_before_content(self):
        sealed=ROOT/'specs/20261108-080032-bx6e/fix2-a04-sealed'
        manifest=json.loads((sealed/'manifest.json').read_text())['sha256']
        package=types.ModuleType('lumafem_sealed_cap2');package.__path__=[str(sealed/'python/lumafem')]
        sys.modules[package.__name__]=package
        prior=sys.dont_write_bytecode;sys.dont_write_bytecode=True
        try:
            for name in ('contracts','storage'):
                file=sealed/f'python/lumafem/{name}.py'
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),manifest[f'python/lumafem/{name}.py'])
                spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,file)
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            old=sys.modules[package.__name__+'.storage'];self.assertEqual(old._READER_CAPABILITY,2)
            error=sys.modules[package.__name__+'.contracts'].ContractError
            with tempfile.TemporaryDirectory() as folder:
                file=Path(folder)/'new-physical.lumafem'
                with ProjectStore(file) as store:store.save_document(document())
                before=hashlib.sha256(file.read_bytes()).hexdigest()
                with self.assertRaises(error):old.ProjectStore(file,readonly=True)
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),before)
        finally:sys.dont_write_bytecode=prior

    def test_modes_domains_hash_and_missing_source(self):
        d=document();p=prepare(d)
        self.assertEqual(p['kernel_version'],KERNEL_VERSION)
        self.assertEqual(p['compression_criterion']['search_domain_mm'],[0,2100])
        self.assertEqual(native.verify_prepared(p,for_analysis=True)['prepared'],p)
        for field,value in [('mode','unknown'),('gauge_length_mm',0),('gauge_length_mm',2201),('threshold',0),('threshold',-1)]:
            with self.subTest(field=field,value=value):
                bad=copy.deepcopy(d);bad['model']['compression_criterion'][field]=value
                self.assertFalse(native.prepare(bad)['ok'])
        for value in (float('nan'),float('inf')):
            bad=copy.deepcopy(d);bad['model']['compression_criterion']['threshold']=value
            with self.assertRaises(ContractError):native.prepare(bad)
        legacy=copy.deepcopy(d);legacy['model'].pop('compression_criterion');q=prepare(legacy)
        self.assertEqual(q['compression_criterion']['mode'],'route_c_point_v1')
        self.assertNotIn('compression_criterion',q['document']['model'])
        tamper=copy.deepcopy(p);tamper['compression_criterion']['threshold']*=2
        self.assertFalse(native.verify_prepared(tamper)['ok'])
        full=copy.deepcopy(d);full['model']['compression_criterion']['gauge_length_mm']=2200
        self.assertEqual(prepare(full)['compression_criterion']['search_domain_mm'],[0,0])

    def test_terminal_bracket_and_independent_accepted_windows(self):
        p,r,steps=analyze(document())
        self.assertEqual(r['termination'],'physical_gauge_compression');self.assertTrue(r['physical_criterion_reached'])
        loc=r['localization'];self.assertEqual(loc['kind'],'bracket')
        self.assertLess(loc['lower_ratio'],1);self.assertGreaterEqual(loc['upper_ratio'],1)
        self.assertLessEqual(loc['bracket_width'],p['document']['model']['analysis']['event_displacement_tolerance'])
        self.assertLessEqual(loc['candidate_solves'],30)
        for s in steps:
            w=s['gauge']['current'];self.assertEqual(w['identity'],s['identity'])
            self.assertAlmostEqual(independent_mean(p,s['displacement'],w),w['signed_mean'],delta=1e-12)
            self.assertGreaterEqual(s['gauge']['historical']['compression_maximum'],w['compression_maximum'])
        for event in r['physical_evidence']:
            if event['kind']=='physical_gauge_compression':
                self.assertEqual(event['criterion'],'physical_gauge_mean_v1')
                for key in ('point_id','section_id','element_id'):self.assertFalse(event[key])
        # The actual low/upper bracket maxima may switch location; no fixed-window assumption.
        self.assertNotEqual(loc['lower_gauge']['x1'],loc['upper_gauge']['x1'])

    def test_real_sqlite_roundtrip_and_failed_save_receipt(self):
        d=document();p=prepare(d)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new.lumafem'
            with ProjectStore(path) as store:
                store.save_document(d);store.begin_run(p)
                r=native.analyze(p,on_step=store.append_step);self.assertEqual(r['termination'],'physical_gauge_compression')
                store.finish_run(r);saved=store.load_run()
                self.assertEqual(saved['summary'],r);self.assertEqual(saved['prepared'],p)
                self.assertEqual(saved['steps'][-1]['gauge']['current'],next(e for e in r['physical_evidence'] if e['kind']=='physical_gauge_compression')['gauge']['current'])
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            with ProjectStore(path,readonly=True) as store:self.assertEqual(store.load_run()['summary'],r)
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        accepted=[]
        def fail(s):
            if s['localization']['active']:
                q=receipt(s);q.update(ok=False,persisted_sequence=accepted[-1]['sequence']);return q
            accepted.append(s);return receipt(s)
        r=native.analyze(p,on_step=fail)
        self.assertEqual(r['termination'],'storage_error');self.assertTrue(r['physical_criterion_reached'])
        self.assertEqual(r['memory_last_step'],r['persisted_last_step']+1)

    def test_safety_cancel_localization_failure_and_gravity_crossing(self):
        d=document();d['model']['compression_criterion']['threshold']=.0035
        _,r,_=analyze(d);self.assertEqual(r['termination'],'safety_limit');self.assertFalse(r['physical_criterion_reached'])
        d=document();cancel=[False]
        def progress(v):
            if v['stage']=='localization':cancel[0]=True
        _,r,steps=analyze(d,on_progress=progress,cancelled=lambda:cancel[0])
        self.assertTrue(cancel[0]);self.assertEqual(r['termination'],'cancelled')
        self.assertFalse(any(s['localization']['active'] for s in steps))
        d=document();d['model']['analysis']['maximum_localization_solves']=1
        _,r,_=analyze(d);self.assertEqual(r['termination'],'localization_failure')
        d=document();d['model']['gravity']['enabled']=True;d['model']['compression_criterion']['threshold']=1e-7
        _,r,steps=analyze(d);self.assertEqual(r['termination'],'physical_gauge_compression')
        self.assertFalse(any(s['phase']=='J' for s in steps));self.assertEqual(steps[-1]['phase'],'G')
        self.assertEqual(r['localization']['control_unit'],'mm')
        self.assertLess(r['localization']['lower_gravity_factor'],r['localization']['upper_gravity_factor'])

    def test_actual_a04_view_immutability_and_analysis_rejection_before_hooks(self):
        path=ROOT/'specs/20261108-080032-bx6e/fix2-a04-m25-r1/mesh25-prepared.json'
        p=json.loads(path.read_text());before=copy.deepcopy(p)
        view=native.verify_prepared(p);self.assertTrue(view['ok']);self.assertTrue(view['analysis_read_only'])
        self.assertEqual(view['prepared'],before);self.assertNotIn('compression_criterion',p)
        self.assertFalse(native.verify_prepared(p,for_analysis=True)['ok'])
        called=[];r=native.analyze(p,on_step=lambda s:called.append(s),on_progress=lambda s:called.append(s),cancelled=lambda:called.append('cancel'))
        self.assertEqual(r['termination'],'input_error');self.assertEqual(called,[]);self.assertEqual(p,before)

if __name__=='__main__':unittest.main(verbosity=2)
