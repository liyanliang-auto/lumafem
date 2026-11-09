"""Additional edge/compatibility checks of current production native code."""
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'python'),str(ROOT/'build/python'),str(ROOT/'tests')]
from test_physical_workflows import document,prepare,analyze,independent_mean,receipt
from lumafem import native
from lumafem.storage import ProjectStore
from lumafem.contracts import ContractError,KERNEL_VERSION
class ExtendedPhysicalTests(unittest.TestCase):
    def test_point_crushed_flags_are_only_diagnostic_in_physical_mode(self):
        d=json.loads((ROOT/'development_doc/runtime/CR-NUM-002/NC0.json').read_text())
        d['model']['discretization']['mesh']='M100'
        # Explicit unit fixture separates the two thresholds; benchmark defaults stay untouched.
        d['model']['compression_criterion']['threshold']=.004
        p=prepare(d);below=[];sequences=[]
        def accept(s):
            sequences.append(s['sequence'])
            if any(e['state']['crushed'] for e in s['edges']) and s['gauge']['current']['compression_maximum']<.004:below.append(s['sequence'])
            return receipt(s)
        r=native.analyze(p,on_step=accept)
        self.assertEqual(r['termination'],'physical_gauge_compression')
        self.assertTrue(below,'fixture did not separate point flag from physical event')
        self.assertGreater(sequences[-1],below[0])
    def test_explicit_legacy_exact_agreement_and_malformed_mixed_sources(self):
        d=document();d['model']['compression_criterion'].update(mode='route_c_point_v1',threshold=.0035)
        self.assertEqual(prepare(d)['compression_criterion']['mode'],'route_c_point_v1')
        for key,value in [('threshold',.0036),('gauge_length_mm',101),('mode',''),('threshold','0.0035')]:
            with self.subTest(key=key,value=value):
                bad=copy.deepcopy(d);bad['model']['compression_criterion'][key]=value;self.assertFalse(native.prepare(bad)['ok'])
        for key in ('mode','threshold','gauge_length_mm','source'):
            with self.subTest(missing=key):
                bad=copy.deepcopy(d);bad['model']['compression_criterion'].pop(key);self.assertFalse(native.prepare(bad)['ok'])
        bad=copy.deepcopy(d);bad['model']['compression_criterion']['operator_version']='mixed';self.assertFalse(native.prepare(bad)['ok'])
    def test_exact_crossing_and_opposite_physical_edge(self):
        d=document();d['model']['compression_criterion']['threshold']=.0035;d['model']['analysis']['maximum_control_displacement']=.04
        _,_,steps=analyze(d);threshold=steps[-1]['gauge']['current']['compression_maximum']
        d['model']['compression_criterion']['threshold']=threshold
        _,r,steps=analyze(d);self.assertEqual(r['termination'],'physical_gauge_compression')
        self.assertEqual(r['localization']['kind'],'exact_crossing');self.assertEqual(r['localization']['final_excess'],0)
        self.assertNotIn('lower_gauge',r['localization']);self.assertEqual(r['localization']['bracket_width'],0)
        d=document();d['model']['control_direction']=1
        for load in d['model']['loads']:load['reference']=[-v for v in load['reference']]
        _,r,steps=analyze(d);self.assertEqual(r['termination'],'physical_gauge_compression');self.assertEqual(steps[-1]['gauge']['current']['side'],'bottom')
    def test_total_gravity_criterion_is_independent_of_display_gauges_and_zero(self):
        d=document();d['model']['gravity']['enabled']=True;d['model']['compression_criterion']['threshold']=1e-4;d['model']['analysis']['maximum_control_displacement']=1
        p,r,steps=analyze(d);sg=next(s for s in steps if s['phase']=='SG')
        self.assertGreater(sg['gauge']['current']['compression_maximum'],0)
        self.assertAlmostEqual(independent_mean(p,sg['displacement'],sg['gauge']['current']),sg['gauge']['current']['signed_mean'],delta=1e-12)
        changed=copy.deepcopy(d);changed['observations'].update(reference_phase='S0',gauges=[dict(id='display-only',x1=23,x2=48,eta=-75)])
        _,rr,other=analyze(changed);self.assertEqual(rr['termination'],r['termination'])
        self.assertEqual(other[-1]['displacement'],steps[-1]['displacement'])
        self.assertEqual(other[-1]['gauge']['current']['compression_maximum'],steps[-1]['gauge']['current']['compression_maximum'])
    def test_old030_is_exact_view_only_and_new_v3_storage_work_guards(self):
        old=json.loads((ROOT/'specs/20261108-121013-uuv1/science-cr2-r2/base-prepared.json').read_text())
        before=copy.deepcopy(old);view=native.verify_prepared(old);self.assertTrue(view['ok']);self.assertTrue(view['analysis_read_only']);self.assertEqual(view['prepared'],before)
        self.assertFalse(native.verify_prepared(old,for_analysis=True)['ok']);called=[]
        r=native.analyze(old,cancelled=lambda:called.append('cancel'),on_progress=lambda _:called.append('progress'));self.assertEqual(r['termination'],'input_error');self.assertFalse(called)
        p,r,steps=analyze(document());self.assertEqual(p['kernel_version'],KERNEL_VERSION)
        for key,value in [('predictor_mode_index',3),('predictor_attempts',16),('predictor_newton_evaluations',100000),('predictor_decompositions',2)]:
            with self.subTest(key=key):
                s=copy.deepcopy(steps[-1]);s['convergence'][key]=value
                with self.assertRaises(ContractError):ProjectStore._step(s,p)
if __name__=='__main__':unittest.main(verbosity=2)
