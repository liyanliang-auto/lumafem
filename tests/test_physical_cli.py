"""Run the actual CLI view/analysis boundary, not a mocked transport."""
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"python"),str(ROOT/"build/python")]
from lumafem.contracts import KERNEL_VERSION
CLI=ROOT/'build/bin/lumafem_cli.exe'
OLD=ROOT/'specs/20261108-080032-bx6e/fix2-a04-m25-r1/mesh25-prepared.json'
class PhysicalCliTests(unittest.TestCase):
    def test_old_a04_view_exact_and_analysis_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            view=Path(folder)/'view';run=Path(folder)/'run'
            result=subprocess.run([str(CLI),'--input',str(OLD),'--output',str(view),'--mode','view'],capture_output=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual(json.loads((view/'prepared.json').read_text()),json.loads(OLD.read_text()))
            self.assertTrue(json.loads((view/'verification.json').read_text())['analysis_read_only'])
            self.assertFalse((view/'summary.json').exists())
            result=subprocess.run([str(CLI),'--input',str(OLD),'--output',str(run),'--mode','run'],capture_output=True)
            self.assertEqual(result.returncode,1);self.assertFalse(run.exists())
            self.assertIn(b'read-only',result.stderr)
    def test_current_physical_prepare_and_cli_frozen_settings_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'prepared'
            result=subprocess.run([str(CLI),'--input',str(ROOT/'development_doc/runtime/CR-NUM-002/NC0.json'),'--output',str(target),'--mode','prepare'],capture_output=True)
            self.assertEqual(result.returncode,0)
            p=json.loads((target/'prepared.json').read_text());self.assertEqual(p['kernel_version'],KERNEL_VERSION)
            self.assertEqual(p['compression_criterion']['mode'],'physical_gauge_mean_v1')
            settings=Path(folder)/'settings.json';settings.write_text('{"maximum_step":0.05}')
            bad=Path(folder)/'invalid'
            result=subprocess.run([str(CLI),'--input',str(target/'prepared.json'),'--output',str(bad),'--mode','view','--settings',str(settings)],capture_output=True)
            self.assertEqual(result.returncode,1);self.assertFalse(bad.exists())
if __name__=='__main__':unittest.main(verbosity=2)
