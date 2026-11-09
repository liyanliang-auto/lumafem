"""Executable numerical acceptance measurements; no production defaults are changed."""
from __future__ import annotations
import argparse,concurrent.futures,csv,hashlib,json,math,subprocess,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.cache/reference-python'))
OUT=None
DRIVER=ROOT/'build/tests/lumafem_scientific_driver.exe'
SOURCE=None
ARGS=None

def run_case(name):
    d=json.loads(SOURCE.read_text(encoding='utf-8'))
    d['revision_id']=d['revision_id']+'-verification-'+name
    if ARGS.mapping!='input':d['model']['discretization']['length_mapping']=ARGS.mapping
    if ARGS.line_search!='input':d['model']['analysis']['line_search_enabled']=ARGS.line_search=='on'
    if ARGS.onsets!='input':d['model']['analysis']['transition_resolution_enabled']=ARGS.onsets=='on'
    if name.startswith('mesh'):d['model']['discretization']['mesh']='M'+name[4:]
    if name.startswith('layers'):d['model']['discretization']['layers']=int(name[6:])
    if name.startswith('gauss'):d['model']['discretization']['gauss_points']=int(name[5:])
    if name=='tight':
        for key in ['residual_absolute','residual_relative','correction_absolute','correction_relative','control_absolute','control_relative']:
            d['model']['analysis'][key]*=.1
    if name=='half':
        d['model']['analysis']['initial_step']*=.5;d['model']['analysis']['maximum_step']*=.5
    if name=='loadnorm10':
        for load in d['model']['loads']:load['reference']=[x*10 for x in load['reference']]
    input_path=OUT/(name+'-input.json');input_path.write_text(json.dumps(d),encoding='utf-8')
    with (OUT/(name+'-run.log')).open('w',encoding='utf-8') as log:
        try:r=subprocess.run([str(DRIVER),str(input_path),str(OUT/name)],stdout=log,stderr=subprocess.STDOUT,timeout=ARGS.timeout)
        except subprocess.TimeoutExpired:return {'name':name,'error':f'bounded_timeout{ARGS.timeout}s','termination':'TEST_TIMEOUT_NO_PHYSICAL_COMPLETION'}
    summary=json.loads((OUT/(name+'-summary.json')).read_text())
    return {'name':name,'exit':r.returncode,'termination':summary['termination'],'elapsed_s':summary['elapsed_s'],'summary':str(OUT/(name+'-summary.json'))}

def curve(name):
    with (OUT/(name+'-curve.csv')).open() as f:rows=[r for r in csv.DictReader(f) if r['phase']=='J']
    return np.array([[float(r['w']),float(r['P'])] for r in rows])

def compare(a,b,limit,end_limit,old_limit):
    x,y=curve(a),curve(b)
    lo=max(x[0,0],y[0,0]);hi=min(x[-1,0],y[-1,0]);grid=np.linspace(lo,hi,200)
    px=np.interp(grid,x[:,0],x[:,1]);py=np.interp(grid,y[:,0],y[:,1]);scale=max(abs(y[:,1]))
    nrmse=float(np.sqrt(np.mean((px-py)**2))/scale)
    peak=float(abs(max(x[:,1])-max(y[:,1]))/max(y[:,1]))
    endpoint=float(abs(x[-1,0]-y[-1,0])/y[-1,0])
    coverage_a=float((hi-lo)/(x[-1,0]-x[0,0]));coverage_b=float((hi-lo)/(y[-1,0]-y[0,0]));coverage=min(coverage_a,coverage_b)
    endings={name:json.loads((OUT/(name+'-summary.json')).read_text())['termination'] for name in [a,b]}
    physical=all(reason in ('reference_compression','physical_gauge_compression') for reason in endings.values())
    return {'a':a,'b':b,'nrmse':nrmse,'peak_difference':peak,'end_displacement_difference':endpoint,'physical_endpoint_difference':endpoint if physical else None,'endpoint_interpretation':'physical criterion' if physical else 'diagnostic stopping positions only','common_interval_coverage':coverage,'coverage_of_a':coverage_a,'coverage_of_b':coverage_b,'endings':endings,'new_limits':{'curve_peak':limit,'endpoint':end_limit},'old_limit':old_limit,'old_status':'PASS' if physical and max(nrmse,peak,endpoint)<=old_limit and coverage>=.95 else 'FAIL','status':'PASS' if physical and max(nrmse,peak)<=limit and endpoint<=end_limit and coverage>=.95 else 'FAIL','coarse_exception':'REQUIRES_INDEPENDENT_REPRODUCTION_AND_FINE_PAIR_PASS' if not physical and (a in ['mesh100','gauss2'] or b in ['mesh100','gauss2']) else None}

def material_reference():
    import openseespy.opensees as ops
    path=OUT/'material-native.csv'
    subprocess.run([str(DRIVER),'--materials',str(path)],check=True)
    with path.open() as f:rows=list(csv.DictReader(f))
    results=[];comparisons=[]
    for kind in ['concrete','steel']:
        ops.wipe()
        if kind=='steel':ops.uniaxialMaterial('Steel01',1,400,200000,.01)
        else:
            E=30000.;Ets=9/(.2/10-9/E);du=20/36;eu=.002+du/10-24/E
            ops.uniaxialMaterial('Concrete02',1,-30,-.002,-6,-eu,.1,3,Ets)
        ops.testUniaxialMaterial(1)
        errs=[];tangent_errors=[]
        for row in [r for r in rows if r['kind']==kind]:
            ops.setStrain(float(row['strain']))
            s=ops.getStress();t=ops.getTangent();delta=abs(s-float(row['stress']))
            errs.append(delta);tangent_errors.append(abs(t-float(row['tangent']))/ (30000 if kind=='concrete' else 200000))
            comparisons.append({'kind':kind,'strain':float(row['strain']),'native_stress':float(row['stress']),'reference_stress':s,'native_tangent':float(row['tangent']),'reference_tangent':t})
        tol=1e-6+1e-6*(30 if kind=='concrete' else 400)
        results.append({'kind':kind,'stress_max_absolute_error_MPa':max(errs),'stress_tolerance_MPa':tol,'normalized_tangent_max_difference':max(tangent_errors),'status':'PASS' if max(errs)<=tol else 'FAIL'})
    (OUT/'material-reference-raw.json').write_text(json.dumps(comparisons,indent=2))
    return {'reference':'OpenSeesPy','engine_version':ops.version(),'results':results,'source_urls':['https://openseespydoc.readthedocs.io/en/latest/src/Concrete02.html','https://openseespydoc.readthedocs.io/en/latest/src/steel01.html','https://opensees.github.io/OpenSeesDocumentation/user/pythonInstall.html']}

def main(argv=None):
    global OUT,SOURCE,ARGS
    parser=argparse.ArgumentParser(description='Explicit-input numerical matrix; never writes legacy evidence.')
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mapping',choices=['input','gauss_weight_v1','element_length_v2'],default='input')
    parser.add_argument('--line-search',choices=['input','on','off'],default='input');parser.add_argument('--onsets',choices=['input','on','off'],default='input')
    parser.add_argument('--cases',nargs='+',default=['base','mesh100','mesh25','layers20','layers80','gauss2','gauss5','tight','half','loadnorm10'])
    parser.add_argument('--workers',type=int,default=3);parser.add_argument('--timeout',type=int,default=600)
    ARGS=parser.parse_args(argv);SOURCE=ARGS.input.resolve();OUT=ARGS.output.resolve()
    legacy=(ROOT/'specs/20261107-202653-v5ge').resolve()
    if OUT==legacy or legacy in OUT.parents:parser.error('Refuse legacy pipeline output')
    if OUT.exists() and any(OUT.iterdir()):parser.error('Output must be a fresh empty directory')
    OUT.mkdir(parents=True,exist_ok=True)
    try:reference=material_reference()
    except Exception as e:reference={'error':repr(e),'status':'FAIL'}
    (OUT/'material-reference.json').write_text(json.dumps(reference,indent=2))
    names=ARGS.cases
    cases=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=ARGS.workers) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run_case,name) for name in names]):
            r=future.result()
            cases.append(r);(OUT/'case-progress.json').write_text(json.dumps(cases,indent=2));print(r['name'],r.get('termination',r.get('error')),flush=True)
    metrics=[]
    for a,b,limit,end_limit,old_limit in [('base','mesh25',.05,.10,.02),('mesh100','base',.05,.10,.02),('layers20','base',.05,.10,.02),('base','layers80',.05,.10,.02),('gauss2','base',.05,.10,.02),('base','gauss5',.05,.10,.02),('tight','base',.01,.01,.005),('half','base',.01,.01,.005),('loadnorm10','base',.01,.01,.005)]:
        if a not in names or b not in names:continue
        try:metrics.append(compare(a,b,limit,end_limit,old_limit))
        except Exception as e:metrics.append({'a':a,'b':b,'error':repr(e),'status':'FAIL'})
    evidence={'source':str(SOURCE),'configuration':vars(ARGS)|{'input':str(SOURCE),'output':str(OUT)},'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'driver_sha256':hashlib.sha256(DRIVER.read_bytes()).hexdigest(),'test_observer_port':'Only selected numerical channels; not project persistence evidence','cases':cases,'comparisons':metrics,'material_reference':reference}
    (OUT/'scientific-evidence.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps(metrics,indent=2),flush=True)
    return 0 if all(r.get('status')=='PASS' for r in metrics) and all(r.get('status')=='PASS' for r in reference.get('results',[])) and 'error' not in reference else 1
if __name__=='__main__':sys.exit(main())
