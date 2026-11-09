"""Independent OpenSees reference with explicit new inputs and fresh output paths."""
import argparse,csv,hashlib,json,math,sys,time
from pathlib import Path
import numpy as np
from physical_gauge_oracle import maximum as physical_maximum
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.cache/reference-python'))

def main(argv=None):
    parser=argparse.ArgumentParser(description='Matched OpenSees reference; explicit input and fresh output required')
    parser.add_argument('--input-dir',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',default='base');parser.add_argument('--extend-to',type=float)
    parser.add_argument('--expected-mapping',choices=['gauss_weight_v1','element_length_v2'])
    parser.add_argument('--algorithm',choices=['Newton','NewtonLineSearch'],default='Newton')
    args=parser.parse_args(argv);INPUT=args.input_dir.resolve();OUT=args.output.resolve()
    legacy=(ROOT/'specs/20261107-202653-v5ge').resolve()
    if OUT==legacy or legacy in OUT.parents:parser.error('Refuse legacy pipeline output')
    if OUT.exists() and any(OUT.iterdir()):parser.error('Output must be fresh')
    OUT.mkdir(parents=True,exist_ok=True)
    import openseespy.opensees as ops
    CASE=args.case
    STEM='beam-reference' if CASE=='base' else 'beam-reference-'+CASE
    start=time.monotonic()
    while not (INPUT/(CASE+'-summary.json')).exists():
        if time.monotonic()-start>240:raise TimeoutError('native base not complete after240s')
        time.sleep(1)
    prepared_path=INPUT/(CASE+'-prepared.json');p=json.loads(prepared_path.read_text());doc=p['document'];model=doc['model'];geometry=model['geometry']
    if args.expected_mapping and model['discretization']['length_mapping']!=args.expected_mapping:raise ValueError('Prepared mapping differs from requested reference')
    with (INPUT/(CASE+'-curve.csv')).open() as f:targets=[r for r in csv.DictReader(f) if r['phase']=='J']
    native_targets=list(targets)
    if args.extend_to is not None:
        final=float(targets[-1]['w'])
        while final<args.extend_to:
            final=min(args.extend_to,final+.05);targets.append({'w':final,'P':float('nan')})
    ops.wipe();ops.model('basic','-ndm',2,'-ndf',3)
    for i,n in enumerate(p['nodes']):ops.node(i+1,n['global_x'],n['global_y'])
    for support in model['supports']:
        idx=next(i for i,n in enumerate(p['nodes']) if n['x']==support['x'])
        ops.fix(idx+1,*[int(x) for x in support['constrained']])
    material_counter=0;steel_points=[]
    for si,s in enumerate(p['sections']):
        ops.section('Fiber',si+1)
        for fiber in s['fibers']:
            material_counter+=1;m=fiber['material'];tag=material_counter
            if m['kind']=='concrete02':ops.uniaxialMaterial('Concrete02',tag,-m['fc'],-m['e0'],-m['fr'],-m['eu'],m['unload_ratio'],m['ft'],m['Ets'])
            elif m['kind']=='steel_bilinear':ops.uniaxialMaterial('Steel01',tag,m['fy'],m['E'],m['b'])
            else:ops.uniaxialMaterial('Elastic',tag,m['E'])
            ops.fiber(fiber['eta'],fiber['zeta'],fiber['area'],tag)
            if fiber['role']=='steel':steel_points.append((s['element_index']+1,s['gauss_index']+1,fiber['eta'],fiber['zeta'],tag,m['fy']))
    ops.geomTransf('Linear',1)
    for ei,e in enumerate(p['elements']):
        indices=e['section_indices'];ss=[p['sections'][i] for i in indices]
        ops.beamIntegration('UserDefined',ei+1,len(ss),*[i+1 for i in indices],*[(s['xi']+1)/2 for s in ss],*[s['weight']/2 for s in ss])
        ops.element('dispBeamColumn',ei+1,*[n+1 for n in e['nodes']],1,ei+1)
    ops.constraints('Plain');ops.numberer('Plain');ops.system('BandGeneral');ops.test('NormUnbalance',1e-3,100);ops.algorithm(args.algorithm);ops.integrator('LoadControl',1/model['analysis']['gravity_steps']);ops.analysis('Static')
    if model['gravity']['enabled']:
        ops.timeSeries('Linear',1);ops.pattern('Plain',1,1)
        q=geometry['width']*geometry['height']*model['gravity']['density']*model['gravity']['acceleration']/1e9
        for ei in range(len(p['elements'])):ops.eleLoad('-ele',ei+1,'-type','-beamUniform',-q)
        code=ops.analyze(model['analysis']['gravity_steps'])
        if code:raise RuntimeError(f'independent gravity failure {code}')
    ops.loadConst('-time',0)
    ops.timeSeries('Linear',2);ops.pattern('Plain',2,2)
    for i,node in enumerate(p['nodes']):
        vector=p['reference_load'][3*i:3*i+3]
        if any(vector):ops.load(i+1,*vector)
    control=p['control_node']+1;sg=ops.nodeDisp(control,2)
    history=[0.]*len(p['sections']);reference_max=[0.]*len(p['sections']);rows=[];first_crack=None;first_yield=None;criterion=None
    
    def advance(target,depth=0):
        now=ops.nodeDisp(control,2);delta=target-now
        if abs(delta)<1e-10:return True
        ops.integrator('DisplacementControl',control,2,delta)
        if ops.analyze(1)==0:return True
        if depth>=12 or abs(delta)<1e-5:return False
        return advance(now+delta/2,depth+1) and advance(target,depth+1)
    
    failed=None
    physical=model.get("compression_criterion",{}).get("mode")=="physical_gauge_mean_v1"
    gauge_records=[]
    independent_solves=0;independent_bracket=None
    if physical:targets[-1]=dict(targets[-1],_independent_terminal=True)
    for row in targets:
        w=float(row['w'])
        if physical and row.get('_independent_terminal'):
            independent_solves+=1
            if len(gauge_records)<2:raise RuntimeError('Independent endpoint requires two actual safe states')
            previous=gauge_records[-1];older=gauge_records[-2]
            slope=(previous['gauge']['compression_maximum']-older['gauge']['compression_maximum'])/(previous['w']-older['w'])
            if slope<=0:raise RuntimeError('Nonpositive terminal secant; independent monotone refinement unavailable')
            gap=p['compression_criterion']['threshold']-previous['gauge']['compression_maximum']
            w=previous['w']+max(5e-5,.8*gap/slope)
            if w>model['analysis']['maximum_control_displacement']:raise RuntimeError('Independent refinement exceeded unchanged safety limit')
        target=sg-w
        if not advance(target):failed={'w':w,'reason':'independent nonlinear nonconvergence'};break
        P=ops.getLoadFactor(2)*(-sum(load['reference'][1] for load in model['loads']))
        for si,s in enumerate(p['sections']):
            deformation=ops.eleResponse(s['element_index']+1,'section',s['gauss_index']+1,'deformation')
            for eta in [-geometry['height']/2,geometry['height']/2]:
                strain=deformation[0]-eta*deformation[1]
                m=s['edges'][0]['material']
                if first_crack is None and strain>=m['ft']/m['E0']:first_crack={'w':w,'P':P,'x':s['x']}
                compression=max(0,-strain)
                if compression>history[si]:
                    history[si]=compression
                    if compression<=m['e0']:ref=compression
                    else:
                        q=m['fc']-m['compression_slope']*(compression-m['e0'])
                        delta=m['lch_c']*(compression-m['e0']-(q-m['fc'])/m['E0'])
                        ref=m['e0']+delta/m['reference_length']+(q-m['fc'])/m['E0']
                    reference_max[si]=max(reference_max[si],ref)
        if first_yield is None:
            for element,section,y,z,tag,fy in steel_points:
                response=ops.eleResponse(element,'section',section,'fiber',y,z,tag,'stressStrain')
                if response and abs(response[0])>=fy:
                    first_yield={'w':w,'P':P};break
        maxref=max(reference_max)
        if physical:
            u=[v for i in range(len(p['nodes'])) for v in ops.nodeDisp(i+1)]
            gauge=physical_maximum(p,u);gauge_records.append(dict(w=w,P=P,gauge=gauge,displacement=u))
            if criterion is None and gauge['compression_maximum']>=p['compression_criterion']['threshold']:
                lower=gauge_records[-2] if len(gauge_records)>1 else None
                independent_bracket=dict(lower=lower,upper=gauge_records[-1],candidate_solves=independent_solves,kind='bracket',control_unit='mm',bracket_width=w-lower['w'] if lower else None)
                criterion={'w':w,'P':P,'gauge':gauge,'localization_status':'independently_solved_monotone_secant_bracket','bracket':independent_bracket}
            elif row.get('_independent_terminal') and independent_solves<30:
                targets.append(dict(w=w,_independent_terminal=True))
        elif criterion is None and maxref>=doc['materials'][0]['crush_reference_strain']:criterion={'w':w,'P':P,'reference_strain':maxref}
        ops.reactions();support_nodes=[next(i+1 for i,n in enumerate(p['nodes']) if n['x']==s['x']) for s in model['supports']]
        rows.append({'w':w,'P':P,'reference_max':maxref,'left_reaction':ops.nodeReaction(support_nodes[0],2),'right_reaction':ops.nodeReaction(support_nodes[1],2)})
        if len(rows)%25==0:print('reference accepted',len(rows),w,flush=True)
        (OUT/(STEM+'-progress.json')).write_text(json.dumps({'accepted':len(rows),'last':rows[-1]}))
        if criterion and args.extend_to is not None and w>float(native_targets[-1]['w']):break
    (OUT/(STEM+'-gauge-history.json')).write_text(json.dumps(gauge_records))
    with (OUT/(STEM+'-curve.csv')).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['w','P','reference_max','left_reaction','right_reaction']);writer.writeheader();writer.writerows(rows)
    x=np.array([[float(r['w']),float(r['P'])] for r in native_targets]);y=np.array([[r['w'],r['P']] for r in rows]);grid=np.linspace(max(x[0,0],y[0,0]),min(x[-1,0],y[-1,0]),200);scale=max(abs(y[:,1]));nrmse=float(np.sqrt(np.mean((np.interp(grid,x[:,0],x[:,1])-np.interp(grid,y[:,0],y[:,1]))**2))/scale)
    peak=float(abs(max(x[:,1])-max(y[:,1]))/max(y[:,1]));coverage=float((grid[-1]-grid[0])/(x[-1,0]-x[0,0]))
    result={'algorithm':args.algorithm,'mapping':model['discretization']['length_mapping'],'input_dir':str(INPUT),'new_thresholds':{'curve':.01,'peak_event':.02},'old_curve_threshold':.005,'old_peak_event_threshold':.01,'engine_version':ops.version(),'prepared_sha256':hashlib.sha256(prepared_path.read_bytes()).hexdigest(),'elapsed_s':time.monotonic()-start,'accepted':len(rows),'failure':failed,'nrmse':nrmse,'peak_difference':peak,'coverage':coverage,'first_crack':first_crack,'first_yield':first_yield,'criterion':criterion,'same_gauss_locations_and_weights':True,'same_signed_area_correction':True,'material_count':material_counter,'status':'PASS' if not failed and nrmse<=.01 and peak<=.02 and coverage>=.95 and criterion else 'FAIL','reference_sources':['https://openseespydoc.readthedocs.io/en/latest/src/dispBeamColumn.html','https://openseespydoc.readthedocs.io/en/latest/src/fiber.html']}
    result['physical_criterion_mode']=physical
    if physical:
        threshold=p['compression_criterion']['threshold'];settings=model['analysis']
        bracket_ok=bool(independent_bracket and independent_bracket['lower']['gauge']['compression_maximum']<threshold<=independent_bracket['upper']['gauge']['compression_maximum'] and independent_bracket['bracket_width']<=settings['event_displacement_tolerance'] and independent_bracket['upper']['gauge']['compression_maximum']-threshold<=max(settings['event_strain_absolute'],settings['event_strain_relative']*threshold))
        coverage_ref=float((grid[-1]-grid[0])/(y[-1,0]-y[0,0]));endpoint_w=abs(x[-1,0]-y[-1,0])/y[-1,0];endpoint_P=abs(x[-1,1]-y[-1,1])/abs(y[-1,1])
        result.update(full_CR2_endpoint_gate='PASS' if bracket_ok else 'FAIL',coverage_of_reference=coverage_ref,endpoint_w_difference=float(endpoint_w),endpoint_P_difference=float(endpoint_P),oracle_source_sha256=hashlib.sha256(Path(__file__).with_name('physical_gauge_oracle.py').read_bytes()).hexdigest())
        result['status']='PASS' if result['status']=='PASS' and bracket_ok and coverage_ref>=.95 and max(endpoint_w,endpoint_P)<=.02 else 'FAIL'
    (OUT/(STEM+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
    return 0 if result['status']=='PASS' else 1
    

if __name__=="__main__":sys.exit(main())
