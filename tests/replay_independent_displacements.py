"""Diagnostic residual witness only; never an independent path prediction."""
from pathlib import Path
import sys,json,hashlib,time,csv,argparse
import numpy as np
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'.cache/reference-python'))
import openseespy.opensees as ops
parser=argparse.ArgumentParser();parser.add_argument('--input-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--case',default='mesh25');args=parser.parse_args();case=args.case;out=args.output.resolve();out.mkdir(exist_ok=False)
path=args.input_dir.resolve()/(case+'-prepared.json');p=json.loads(path.read_text());doc=p['document'];model=doc['model'];geometry=model['geometry']
# Exact independent element/material construction shared with the frozen
# reference observer, but ALL real beam nodal displacements are prescribed.
source=(root/'tests/run_opensees_reference.py').read_text()
setup=source[source.index("    ops.wipe();ops.model"):source.index("    ops.constraints('Plain')")]
support=setup[setup.index('    for support in model'):setup.index('    material_counter=')]
setup=setup.replace(support,'')
exec('\n'.join(line[4:] if line.startswith('    ') else line for line in setup.splitlines()))
n=len(p['nodes']);dummy=n+1
ops.node(dummy,1e6,0);ops.node(dummy+1,1e6,0);ops.fix(dummy,1,1,1);ops.fix(dummy+1,0,1,1)
ops.uniaxialMaterial('Elastic',material_counter+1,1.)
ops.element('zeroLength',len(p['elements'])+1,dummy,dummy+1,'-mat',material_counter+1,'-dir',1)
ops.constraints('Transformation');ops.numberer('Plain');ops.system('BandGeneral');ops.test('NormUnbalance',1e-12,3);ops.algorithm('Newton');ops.integrator('LoadControl',1.);ops.analysis('Static')
steps=[json.loads(line) for line in (path.parent/(case+'-accepted.jsonl')).read_text().splitlines()]
with (path.parent/(case+'-curve.csv')).open() as f:curve={int(row['sequence']):row for row in csv.DictReader(f)}
gravity=np.zeros(n*3);ref=np.zeros(n*3)
assert geometry['angle']==0
if model['gravity']['enabled']:
    q=geometry['width']*geometry['height']*model['gravity']['density']*model['gravity']['acceleration']/1e9
    for element in p['elements']:
        length=element['length'];dofs=element['dofs'];gravity[dofs]+=np.array([0,-q*length/2,-q*length**2/12,0,-q*length/2,q*length**2/12])
for load in model['loads']:
    node=next(i for i,v in enumerate(p['nodes']) if v['x']==load['x']);ref[node*3:node*3+3]+=load['reference']
free=p['free_dofs'];rows=[];previous=None;t=time.monotonic()
previous_u=np.zeros(n*3);previous_internal=np.zeros(n*3);previous_external=np.zeros(n*3);external_work=material_work=residual_work=0.
for state in steps:
    if previous is not None:ops.remove('loadPattern',previous);ops.remove('timeSeries',previous)
    tag=10000+state['sequence'];ops.timeSeries('Constant',tag);ops.pattern('Plain',tag,tag)
    for i,node in enumerate(p['nodes']):
        for component in range(3):ops.sp(i+1,component+1,state['displacement'][3*i+component])
    ops.domainChange();code=ops.analyze(1)
    if code:raise RuntimeError('prescribed replay failed '+str((state['sequence'],code)))
    ops.reactions();internal=np.array([value for i in range(n) for value in ops.nodeReaction(i+1)])
    phase=state['phase'];gravity_factor=float(curve[state['sequence']]['gravity'])
    # P is total signed two-point force; reference pattern has two vertical components.
    factor=state['P']/(-sum(load['reference'][1] for load in model['loads']))
    external_force=gravity_factor*gravity+factor*ref
    residual=internal-external_force
    u=np.array(state['displacement']);increment=u-previous_u
    external_work+=float(np.dot(.5*(previous_external+external_force),increment))
    material_work+=float(np.dot(.5*(previous_internal+internal),increment))
    residual_work+=float(np.dot(.5*((previous_internal-previous_external)+residual),increment))
    previous_u=u;previous_external=external_force;previous_internal=internal
    evidence=state['convergence'];force=max(abs(residual[i]) for i in free if i%3!=2);moment=max(abs(residual[i]) for i in free if i%3==2)
    force_bound=model['analysis']['residual_absolute']+model['analysis']['residual_relative']*evidence['force_scale']
    moment_bound=model['analysis']['residual_absolute']*model['analysis']['reference_length']+model['analysis']['residual_relative']*evidence['moment_scale']
    uerror=max(abs(ops.nodeDisp(i+1,j+1)-state['displacement'][3*i+j]) for i in range(n) for j in range(3))
    rows.append(dict(sequence=state['sequence'],phase=phase,w=state['control_displacement'],P=state['P'],force_residual=float(force),moment_residual=float(moment),force_bound=force_bound,moment_bound=moment_bound,passes=bool(force<=force_bound and moment<=moment_bound),nodal_displacement_error=uerror,external_work_nmm=external_work,independent_material_work_nmm=material_work,independent_residual_virtual_work_nmm=residual_work))
    previous=tag
    if len(rows)%50==0:print(case,len(rows),rows[-1],flush=True)
result=dict(case=case,method='independent OpenSees Concrete02/Steel01 and dispBeamColumn; all physical nodes prescribed from native accepted total-U history; new SP pattern each actual accepted state; isolated zero-load dummy scalar only; independent beam histories committed by actual analysis; NO native material history used; this is residual evidence, NOT independent equilibrium-path prediction',engine_version=ops.version(),prepared_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),accepted_U_sha256=hashlib.sha256((path.parent/(case+'-accepted.jsonl')).read_bytes()).hexdigest(),reference_source_sha256=hashlib.sha256(source.encode()).hexdigest(),replay_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),independent_gravity_vector_error=float(max(abs(gravity-np.array(p['gravity_load'])))),independent_reference_load_error=float(max(abs(ref-np.array(p['reference_load'])))),accepted=len(rows),elapsed_s=time.monotonic()-t,first_failure=next((row for row in rows if not row['passes']),None),max_force_residual=max(row['force_residual'] for row in rows),max_moment_residual=max(row['moment_residual'] for row in rows),maximum_nodal_displacement_error=max(row['nodal_displacement_error'] for row in rows),external_work_nmm=external_work,independent_material_work_nmm=material_work,residual_virtual_work_nmm=residual_work,work_identity_roundoff=abs(material_work-external_work-residual_work),work_meaning='Trapezoidal actual accepted-state external work and independent internal material deformation work. For fixed small-strain EB B, internal nodal virtual work equals GP stress-strain work exactly. Not recoverable energy or cyclic dissipation; no global unloading protocol specified.',rows=rows)
(out/'residual-witness.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))

if result['first_failure'] is not None or result['maximum_nodal_displacement_error']>1e-12:sys.exit(1)
