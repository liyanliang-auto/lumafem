"""Prior-profile, one-band series compatibility target, independent of native states.

The unique weakest element follows from weighted peak strengths BEFORE reading
the response. The active element follows the analytic Concrete02 compression
envelope. Other elements unload from independently derived first-peak strains
using independent OpenSees Parallel/Concrete02 histories. A scalar compatible
window equation determines the truncated endpoint, not an observed band count.
"""
import argparse,hashlib,json,math,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'.cache/reference-python'))
import openseespy.opensees as ops
parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
def target(Le,resolution):
    N=round(400/Le);xi=[-math.sqrt(3/5),0,math.sqrt(3/5)];weights=np.array([5/18,4/9,5/18]);gp=[];peak=[]
    ops.wipe()
    for i in range(N):
        parameters=[];tags=[]
        for j,t in enumerate(xi):
            x=(i+.5)*Le+t*Le/2;z=(x-194.8)/50;factor=1-.1*(1-z*z)**2 if abs(z)<1 else 1
            fc,fr,ft=30*factor,6*factor,3*factor;E=2*fc/.002;du=20/(fc+fr);k=(fc-fr)/du
            tag=i*4+j+1;ops.uniaxialMaterial('Concrete02',tag,-fc,-.002,-fr,-(.002+du/Le-(fc-fr)/E),.1,ft,ft*ft/(.2/Le-ft*ft/E));tags.append(tag)
            parameters.append(dict(fc=fc,fr=fr,E=E,k=k,du=du,slope=k/(1/Le-k/E)))
        ops.uniaxialMaterial('Parallel',i*4+4,*tags,'-factors',*weights);gp.append(parameters);peak.append(float(sum(w*p['fc'] for w,p in zip(weights,parameters))))
    weakest=int(np.argmin(peak));assert peak.count(peak[weakest])==1
    first_force=peak[weakest];initial_peak_strain=[.002*(1-math.sqrt(max(0,1-first_force/q))) for q in peak]
    unload=[]
    for i in range(N):
        if i==weakest:unload.append(None);continue
        ops.testUniaxialMaterial(i*4+4)
        for strain in np.linspace(0,initial_peak_strain[i],101):ops.setStrain(-float(strain))
        strains=np.linspace(initial_peak_strain[i],0,resolution+1);stress=[]
        for strain in strains:ops.setStrain(-float(strain));stress.append(-ops.getStress())
        stress=np.array(stress);zero=next((j for j,q in enumerate(stress) if q<=0),len(stress)-1);stress=stress[:zero+1];strains=strains[:zero+1];assert np.max(np.diff(stress))<1e-8
        # Increasing-stress inverse; exact on each actual linear unloading branch.
        unload.append((stress[::-1],strains[::-1]))
    x=np.arange(N+1)*Le;knots=sorted(set([0.,300.]+[v for a in x for v in (a,a-100) if 0<v<300]))
    def state(active):
        q=np.array([max(p['fr'],p['fc']-p['slope']*(active-.002)) for p in gp[weakest]])
        force=float(np.dot(weights,q));strain=np.array([active if i==weakest else float(np.interp(force,*unload[i])) for i in range(N)])
        primitive=np.r_[0,np.cumsum(strain*Le)];means=[(np.interp(a+100,x,primitive)-np.interp(a,x,primitive))/100 for a in knots];index=int(np.argmax(means))
        return max(means),force,q,strain,knots[index]
    lo=.002;hi=.0035*100/Le+.002
    assert state(lo)[0]<.0035<=state(hi)[0]
    for _ in range(80):
        mid=(lo+hi)/2
        if state(mid)[0]<.0035:lo=mid
        else:hi=mid
    controls=[float(np.sum(state(e)[3])*Le) for e in np.linspace(.002,hi,257)];assert min(np.diff(controls))>0
    C,force,q,strains,a=state(hi);assert all(strains[i]<=initial_peak_strain[i]+1e-12 for i in range(N) if i!=weakest);energy=residual=0;coordinates=[]
    for w,p,stress in zip(weights,gp[weakest],q):
        dc=Le*(hi-.002-(stress-p['fc'])/p['E']);assert 0<=dc<=p['du']
        energy+=100*w*(p['fc']*dc-.5*p['k']*dc*dc);residual+=100*w*p['fr']*dc;coordinates.append(float(dc))
    return dict(prior_unique_weak_element=weakest,prior_element_peak_stresses=peak,first_peak_strains=initial_peak_strain,independent_terminal_active_strain=hi,independent_terminal_nodal_shortening=float(np.sum(strains)*Le),independent_terminal_mean=C,independent_window=[a,a+100],independent_terminal_force=100*force,independent_truncated_coordinate_target=energy,residual_coordinate_baseline=residual,independent_delta_c_at_GP=coordinates,other_element_terminal_strains=strains.tolist(),unloading_resolution=resolution,compatible_control_monotone_check_min_increment=float(min(np.diff(controls))),band_selection='unique prior weighted fc minimum; nonactive cells remain on independently generated unloading branch below own prior maxima; no observed support count enters target')
records=[]
for Le in (50,25,12.5):
    coarse=target(Le,2048);fine=target(Le,4096);error=abs(fine['independent_truncated_coordinate_target']-coarse['independent_truncated_coordinate_target'])/fine['independent_truncated_coordinate_target'];assert error<1e-4
    for source in sorted(args.input.glob(f'compression-{Le:g}-*-summary.json')):
        native=json.loads(source.read_text());energy_error=abs(native['coordinate_energy']-fine['independent_truncated_coordinate_target'])/fine['independent_truncated_coordinate_target']
        records.append(dict(case=source.name[:-13],mesh_length=Le,target=fine,resolution_relative_error=error,native_coordinate_energy=native['coordinate_energy'],native_independent_trapezoid_energy=native['independent_coordinate_energy'],target_relative_error=energy_error,native_end_difference=abs(native['last_u']-fine['independent_terminal_nodal_shortening'])/fine['independent_terminal_nodal_shortening'],status='PASS' if energy_error<=.05 else 'FAIL',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native_summary_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),engine_version=ops.version()))
(out/'independent-energy-targets.json').write_text(json.dumps(records,indent=2,default=lambda v:v.item()));print([(r['case'],r['status'],r['target_relative_error']) for r in records]);sys.exit(0 if len(records)==5 and all(r['status']=='PASS' for r in records) else 1)
