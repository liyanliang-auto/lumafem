"""Acceptance of already executed BAR400 physical families, with no reruns.

Usage: python tests/test_physical_bar_results.py --evidence FRESH_MATRIX_DIRECTORY
Raw runs must use the fixed A01 geometry/profile and separate sensitivity inputs.
"""
import argparse,csv,json,sys,unittest
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,required=True)
args,remaining=parser.parse_known_args();sys.argv=[sys.argv[0],*remaining]
def read(name):return json.loads((args.evidence/(name+'-summary.json')).read_text())
class PhysicalBarResults(unittest.TestCase):
    def test_compression_mesh_and_temporal_paths_reach_same_physical_end(self):
        for name in ['compression-50-base','compression-25-base','compression-12.5-base','compression-12.5-half','compression-12.5-tight']:
            with self.subTest(case=name):
                r=read(name)
                self.assertEqual((r['total_length'],r['weak_centre'],r['weak_width']),(400,194.8,100))
                self.assertTrue(r['physical_mode'])
                self.assertEqual(r['termination'],'physical_gauge_compression','required complete path failed; safety/nonconvergence is not physical completion')
                self.assertTrue(r['physical_criterion_reached'])
    def test_tension_all_meshes_and_sensitivities_reach_actual_traction_free_state(self):
        for name in ['tension-50-base','tension-25-base','tension-12.5-base','tension-12.5-half','tension-12.5-tight']:
            with self.subTest(case=name):
                r=read(name);end=r['first_traction_free_completion'];self.assertIsNotNone(end)
                self.assertLessEqual(end['F'],r['peak_force']*1e-6)
                self.assertLessEqual(end['recoverable_energy'],10e-6)
                # Independent single-band A*G=10*10*.1; no observed count fitting.
                self.assertLessEqual(abs(end['independent_coordinate_energy']-10)/10,.05)
    def test_compression_coordinate_integrals_do_not_replace_missing_paths(self):
        r=read('compression-12.5-base')
        self.assertEqual(r['termination'],'physical_gauge_compression')
        self.assertAlmostEqual(r['coordinate_energy'],r['independent_coordinate_energy'],delta=1e-8)
        self.assertGreater(r['material_work'],0)
        self.assertGreater(r['recoverable_energy'],0)
        self.assertGreater(r['residual_baseline'],0)
        self.assertNotEqual(r['material_work'],r['coordinate_energy'])
if __name__=='__main__':unittest.main(verbosity=2)
