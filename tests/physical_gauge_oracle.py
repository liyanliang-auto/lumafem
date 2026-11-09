"""Independent strain-integration oracle; never imports the production helper.

Integrate the second derivatives of the four EB shape functions. On each
start-window partition the integral is quadratic, so three integrated values
determine its stationary point. This deliberately differs from the product's
endpoint-displacement/affine-strain-difference implementation.
"""
import bisect, math

def maximum(prepared, displacement):
    geometry=prepared['document']['model']['geometry']
    L=geometry['length'];h=geometry['height'];g=prepared['compression_criterion']['gauge_length_mm']
    nodes=prepared['nodes'];elements=prepared['elements'];knots=sorted(set([0.,L-g]+[x for n in nodes for x in (n['x'],n['x']-g) if 0<x<L-g]))
    best=None
    for side,eta in [('top',h/2),('bottom',-h/2)]:
        starts=[];slopes=[];values=[];prefix=[0.]
        for e in elements:
            i,j=e['nodes'];a,b=nodes[i]['x'],nodes[j]['x'];length=b-a;c,s=e['cosine'],e['sine'];d=e['dofs'];u=displacement
            axial=(c*(u[d[3]]-u[d[0]])+s*(u[d[4]]-u[d[1]]))/length
            va=-s*u[d[0]]+c*u[d[1]];vb=-s*u[d[3]]+c*u[d[4]]
            def strain(t):
                curvature=((-6+12*t)*va+length*(-4+6*t)*u[d[2]]+(6-12*t)*vb+length*(-2+6*t)*u[d[5]])/length**2
                return axial-eta*curvature
            left,right=strain(0),strain(1)
            starts.append(a);values.append(left);slopes.append((right-left)/length)
            prefix.append(prefix[-1]+length*(left+right)/2)
        def integrated(x):
            index=max(0,min(len(elements)-1,bisect.bisect_right(starts,x)-1));dx=x-starts[index]
            return prefix[index]+dx*(values[index]+dx*slopes[index]/2)
        def mean(a):return (integrated(min(L,a+g))-integrated(a))/g
        candidates=list(knots)
        for a,b in zip(knots,knots[1:]):
            y0,ym,y1=mean(a),mean((a+b)/2),mean(b)
            quadratic=2*(y1+y0-2*ym);linear=y1-y0-quadratic
            if quadratic:
                t=-linear/(2*quadratic)
                if 0<t<1:candidates.append(a+t*(b-a))
        for a in sorted(candidates):
            signed=mean(a);compression=max(0.,-signed)
            record=dict(side=side,eta=eta,x1=a,x2=min(L,a+g),signed_mean=signed,compression_maximum=compression)
            if best is None or compression>best['compression_maximum'] or (compression==best['compression_maximum'] and (a,-eta)<(best['x1'],-best['eta'])):best=record
    return best
