"""Independent axial truss/Parallel-Concrete02 BAR400 truncated-energy target.

No native material states are read. The fixed profile defines all GP laws and
the unique weakest element before any solved response is inspected. All nodal
strains, active ranges and truncated coordinates come from independent solves.
"""
import argparse,csv,hashlib,json,math,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.cache/reference-python'))
import openseespy.opensees as ops
parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
records=[]
for source in sorted(args.input.glob('compression*-summary.json')):
    name=source.name[:-13];native=json.loads(source.read_text());Le=native['mesh_length'];N=round(400/Le);area=100
    xi=[-math.sqrt(3/5),0,math.sqrt(3/5)];weights=[5/18,4/9,5/18]
    ops.wipe();ops.model('basic','-ndm',2,'-ndf',2)
    for i in range(N+1):ops.node(i+1,i*Le,0);ops.fix(i+1,int(i==0),1)
    parameters=[];weak=[]
    for i in range(N):
        gp=[];tags=[]
        for j,t in enumerate(xi):
            x=(i+.5)*Le+t*Le/2;z=(x-194.8)/50;factor=1-.1*(1-z*z)**2 if abs(z)<1 else 1
            fc,fr,ft=30*factor,6*factor,3*factor;E=2*fc/.002
            deltaU=20/(fc+fr);eu=.002+deltaU/Le-(fc-fr)/E;Ets=ft*ft/(.2/Le-ft*ft/E)
            tag=i*4+j+1;ops.uniaxialMaterial('Concrete02',tag,-fc,-.002,-fr,-eu,.1,ft,Ets);tags.append(tag)
            gp.append(dict(x=x,fc=fc,fr=fr,E=E,k=(fc-fr)/deltaU,deltaU=deltaU))
        parallel=i*4+4;ops.uniaxialMaterial('Parallel',parallel,*tags,'-factors',*weights)
        ops.element('truss',i+1,i+1,i+2,area,parallel);parameters.append(gp)
        weak.append(sum(w*p['fc'] for w,p in zip(weights,gp)))
    expected=int(np.argmin(weak));assert weak.count(weak[expected])==1,'a priori peak strength minimum is not unique'
    ops.timeSeries('Linear',1);ops.pattern('Plain',1,1);ops.load(N+1,-100,0)
    ops.constraints('Plain');ops.numberer('Plain');ops.system('BandGeneral');ops.test('NormUnbalance',1e-7,60);ops.algorithm('Newton');ops.analysis('Static')
    targets=[float(r['u']) for r in csv.DictReader((source.parent/(name+'-curve.csv')).open()) if float(r['u'])>0]
    maxima=np.zeros(N);previous=None;terminal=None;failure=None;history=[]
    # The same actual controls serve as independent load steps, not copied states.
    for index,u in enumerate(targets):
        delta=-u-ops.nodeDisp(N+1,1);ops.integrator('DisplacementControl',N+1,1,delta)
        code=ops.analyze(1)
        if code:failure=dict(control=u,reason='independent truss equilibrium failed',code=code);break
        nodal=np.array([ops.nodeDisp(i+1,1) for i in range(N+1)]);strain=-np.diff(nodal)/Le;maxima=np.maximum(maxima,strain)
        knots=sorted(set([0.,300.]+[v for x in np.arange(N+1)*Le for v in (x,x-100) if 0<v<300]))
        means=[-(np.interp(a+100,np.arange(N+1)*Le,nodal)-np.interp(a,np.arange(N+1)*Le,nodal))/100 for a in knots]
        best=max(range(len(knots)),key=lambda k:means[k]);C=means[best];energy=0.;residual=0.
        for i,gp in enumerate(parameters):
            for w,p in zip(weights,gp):
                if maxima[i]<=.002:continue
                slope=p['k']/(1/Le-p['k']/p['E']);q=max(p['fr'],p['fc']-slope*(maxima[i]-.002))
                dc=Le*(maxima[i]-.002-(q-p['fc'])/p['E']);dc=min(dc,p['deltaU'])
                energy+=area*w*(p['fc']*dc-.5*p['k']*dc*dc);residual+=area*w*p['fr']*dc
        current=dict(u=u,F=ops.getLoadFactor(1)*100,compression=C,x1=knots[best],x2=knots[best]+100,energy=energy,residual_baseline=residual,active_elements=[int(i) for i in np.where(maxima>.002)[0]],maxima=maxima.tolist())
        history.append(current)
        if C>=.0035:
            terminal=current;break
        previous=current
    energy_error=None if terminal is None else abs(native['coordinate_energy']-terminal['energy'])/terminal['energy']
    record=dict(case=name,independent_formulation='axial truss with Parallel independently parameterized GP Concrete02; original end-displacement controls; no native material-state copy',expected_active_element_from_prior_peak_strength=expected,prior_element_peak_strengths=weak,independent_terminal=terminal,independent_lower=previous,failure=failure,native_coordinate_energy=native['coordinate_energy'],independent_truncated_target=terminal['energy'] if terminal else None,target_relative_error=energy_error,accepted=len(history),profile_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native_summary_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),engine_version=ops.version(),status='PASS' if terminal and not failure and terminal['active_elements']==[expected] and energy_error<=.05 else 'FAIL')
    (out/(name+'-history.json')).write_text(json.dumps(history));records.append(record);print(name,record['status'],energy_error,flush=True)
(out/'independent-bar-targets.json').write_text(json.dumps(records,indent=2))
sys.exit(0 if all(r['status']=='PASS' for r in records) else 1)
