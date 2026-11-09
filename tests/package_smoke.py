"""Actual relocated packaged-runtime Qt/VTK/SQLite/CLI workflow; no repo imports."""
import argparse,csv,hashlib,json,os,shutil,subprocess,sys,time,sqlite3
from pathlib import Path
sys.dont_write_bytecode=True
parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--legacy',type=Path);parser.add_argument('--reopen',type=Path)
args=parser.parse_args();bundle=args.bundle.resolve();out=args.output.resolve();out.mkdir(exist_ok=False)
sys.path[:0]=[str(bundle/'app'),str(bundle/'native')]
import _lumafem
import lumafem
from lumafem.application import ProjectController
from lumafem.storage import ProjectStore
from lumafem.gui.main_window import MainWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
import vtkmodules.vtkRenderingOpenGL2
assert Path(lumafem.__file__).resolve().is_relative_to(bundle)
assert Path(_lumafem.__file__).resolve().is_relative_to(bundle)
assert _lumafem.kernel_version=='0.3.1-cr-num-002-a05' and _lumafem.reader_capability==3 and _lumafem.schema_version==1
def digest(file):return hashlib.sha256(Path(file).read_bytes()).hexdigest()
app=QApplication.instance() or QApplication([]);errors=[];c=ProjectController(work_dir=str(out/'work'));c.errorRaised.connect(errors.append)
d=json.loads((bundle/'cases/NC0.json').read_text(encoding='utf-8-sig'));d['name']='Packaged physical workflow QA fixture; not scientific baseline';d['model']['compression_criterion']['threshold']=1e-5;d['model']['gravity']['enabled']=False;d['model']['discretization'].update(mesh='M100',layers=4);d['model']['analysis']['maximum_control_displacement']=.1
w=MainWindow(template=d,controller=c);w.show();app.processEvents()
record=dict(bundle=str(bundle),python=sys.executable,native=_lumafem.__file__,application=lumafem.__file__,producer=_lumafem.kernel_version,reader_capability=3,schema_version=1,steps={},issues=[])
def wait():
    end=time.monotonic()+100;beats=0
    while c.state['running'] and time.monotonic()<end:app.processEvents();time.sleep(.01);beats+=1
    assert not c.state['running'],'background task timeout'
    return beats
try:
    if args.reopen:
        before=digest(args.reopen);assert c.open_project(str(args.reopen)),errors
        assert c.current_run['summary']['termination']=='physical_gauge_compression';step=c.get_step();assert step['gauge']['current']['reference']=='total_from_S0'
        w.viewport.export_png(str(out/'offline-window.png'));assert c.export_data(str(out/'offline-curves.csv'),'curves'),errors
        assert digest(args.reopen)==before,'offline viewing changed saved physical project'
        record['steps']['offline_reopen']=dict(path=str(args.reopen),sha256=before,gauge=step['gauge']['current'],analysis_started=False)
    else:
        assert c.new_project(d),errors;old=c.document['revision_id'];edited=c.document;edited['model']['compression_criterion']['gauge_length_mm']=120
        assert c.apply_document(edited),errors;assert c.document['revision_id']!=old;assert c.check_model()['ok'];assert c.start_run(),errors
        beats=wait();assert beats>0;assert c.current_run['summary']['termination']=='physical_gauge_compression',errors
        step=c.get_step();assert c.select(dict(sequence=step['sequence'],step_id=step['identity']['step_id'],run_id=step['identity']['run_id']));app.processEvents()
        assert w.results.gauge_table.rowCount()>0;assert w.viewport._step['gauge']['current']==step['gauge']['current']
        w.viewport.export_png(str(out/'physical-window.png'));assert w.grab().save(str(out/'workbench.png'))
        for kind in ('curves','events','convergence'):
            file=out/(kind+'.csv');assert c.export_data(str(file),kind),errors;meta=json.loads(file.with_suffix('.metadata.json').read_text())['metadata'];assert meta['termination']=='physical_gauge_compression';assert meta['compression_criterion']['gauge_length_mm']==120
        saved=out/'physical-saved.lumafem';assert c.save_as(str(saved)),errors
        record['steps']['physical']=dict(path=str(saved),sha256=digest(saved),summary=c.current_run['summary'],gauge=step['gauge']['current'],event_loop_heartbeats=beats)
        source=out/'cli-input.json';source.write_text(json.dumps(c.document))
        cliout=out/'cli-physical';command=[str(bundle/'bin/lumafem_cli.exe'),'--input',str(source),'--output',str(cliout),'--mode','run']
        with (out/'cli-physical.log').open('wb') as log:rc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=90).returncode
        assert rc==0;assert json.loads((cliout/'summary.json').read_text())['termination']=='physical_gauge_compression';assert json.loads((cliout/'prepared.json').read_text())['kernel_version']==_lumafem.kernel_version
        record['steps']['CLI_new_physical']=True
        cancel=copy_document=json.loads(json.dumps(d));cancel['model']['compression_criterion']['threshold']=.0035;cancel['model']['analysis']['maximum_control_displacement']=50
        assert c.new_project(cancel),errors;assert c.start_run(),errors;QTimer.singleShot(10,c.cancel_run);wait()
        assert c.current_run['summary']['termination']=='cancelled',errors
        record['steps']['background_cancel']=c.current_run['summary']
        legacy=out/'legacy-dedicated-copy.lumafem';shutil.copyfile(args.legacy,legacy);before=digest(legacy)
        with ProjectStore(legacy,readonly=True) as store:old_document=store.load_document();old_run=store.load_run()
        assert c.open_project(str(legacy)),errors;assert c.current_run['analysis_read_only'];assert 'gauge' not in c.get_step();assert digest(legacy)==before
        assert c.export_data(str(out/'legacy-curves.csv'),'curves'),errors;w.viewport.export_png(str(out/'legacy-window.png'))
        record['steps']['legacy_readonly_view']=dict(original_sha256=before,prepared=old_run['prepared']['kernel_version'],analysis_read_only=True,no_synthesized_gauge=True)
        oldprepared=out/'legacy-prepared.json';oldprepared.write_text(json.dumps(old_run['prepared']))
        with (out/'cli-legacy-view.log').open('wb') as log:rc=subprocess.run([str(bundle/'bin/lumafem_cli.exe'),'--input',str(oldprepared),'--output',str(out/'cli-legacy-view'),'--mode','view'],stdout=log,stderr=subprocess.STDOUT,timeout=30).returncode
        assert rc==0;assert json.loads((out/'cli-legacy-view/verification.json').read_text())['analysis_read_only']
        assert c.save_as(str(out/'legacy-save-as.lumafem')),errors
        with ProjectStore(legacy,readonly=True) as store:after_document=store.load_document()
        after=digest(legacy);record['steps']['legacy_save_as']=dict(before_sha256=before,after_sha256=after,original_source_document_unchanged=old_document==after_document,required_semantics='Source old file byte-identical; new copy preserves original source/prepared/results/IDs/metadata, with no implicit view-state revision/capability upgrade.')
        if after!=before:record['issues'].append('Save As rewrote analysis-read-only old source: application.py save_as writes new view revision/capability before copying; current package not qualified.')
        def tables(file):
            with sqlite3.connect(file) as db:
                names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                return {name:db.execute('SELECT * FROM '+chr(34)+name.replace(chr(34),chr(34)*2)+chr(34)).fetchall() for name in names}
        plain=out/'legacy-save-as.lumafem'
        assert tables(legacy)==tables(plain),'historical destination SQL tables/metadata changed'
        record['steps']['legacy_save_as']['all_destination_SQL_tables_equal']=True
        old_copy_hash=digest(plain);edited=c.document;edited['name']='Explicit destination-only edited legacy fixture';assert c.apply_document(edited),errors
        destination=out/'legacy-explicit-edit.lumafem';assert c.save_as(str(destination)),errors;assert digest(plain)==old_copy_hash,'explicit Save As edit changed its source copy'
        with ProjectStore(destination,readonly=True) as store:
            assert store.load_document()['name']=='Explicit destination-only edited legacy fixture';assert store.load_run()['prepared']==old_run['prepared'];assert store.load_run()['steps']==old_run['steps']
        with sqlite3.connect(destination) as db:assert json.loads(db.execute("SELECT value FROM metadata WHERE key='minimum_reader'").fetchone()[0])==3
        record['steps']['explicit_edit_destination_only']=dict(source_byte_identical=True,target_minimum_reader=3,old_prepared_results_preserved=True)
    assert not errors,errors
except Exception as error:
    record['issues'].append(repr(error))
finally:
    c.close();w.hide();w.deleteLater();app.processEvents()
record['status']='PASS' if not record['issues'] else 'FAIL';(out/'package-smoke.json').write_text(json.dumps(record,indent=2));print(record['status'],record['issues']);sys.exit(0 if record['status']=='PASS' else 1)
