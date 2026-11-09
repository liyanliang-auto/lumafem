#include "lumafem/model.hpp"
#include <algorithm>
#include <cmath>
#include <map>
#include <numeric>
#include <set>
#include <sstream>

namespace lumafem {
namespace {
constexpr double pi = 3.1415926535897932384626433832795;
bool finite(double v) { return std::isfinite(v); }
void fail(const std::string& code, const Id& object, const std::string& field,
          const std::string& message) {
    throw DomainError({code,"error","preparation",object,field,message});
}
void require(bool condition, const Id& object, const std::string& field,
             const std::string& message) {
    if (!condition) fail("INVALID_INPUT",object,field,message);
}
void positive(double value, const Id& object, const std::string& field) {
    require(finite(value) && value > 0.0,object,field,"A finite positive value is required.");
}
void prepareCompressionCriterion(PreparedModel& model, const MaterialParameters& concrete) {
    const auto& source=model.definition.compressionCriterion;
    CompressionCriterionDefinition criterion;
    if(source) {
        criterion=*source;
        require(criterion.mode==routeCPointMode || criterion.mode==physicalGaugeMeanMode,
                "compression_criterion","mode","Unknown compression criterion mode.");
        positive(criterion.gaugeLength,"compression_criterion","gauge_length_mm");
        positive(criterion.threshold,"compression_criterion","threshold");
        if(criterion.mode==routeCPointMode) {
            require(criterion.gaugeLength==concrete.referenceLength,
                    "compression_criterion","gauge_length_mm",
                    "Legacy criterion length must exactly equal the section material reference length.");
            require(criterion.threshold==concrete.crushReferenceStrain,
                    "compression_criterion","threshold",
                    "Legacy criterion threshold must exactly equal the section material reference threshold.");
        } else {
            require(criterion.gaugeLength<=model.definition.geometry.length,
                    "compression_criterion","gauge_length_mm",
                    "Physical gauge length must not exceed the entire physical beam length.");
            model.compressionSearchDomain={0.0,model.definition.geometry.length-criterion.gaugeLength};
        }
    } else {
        // Source absence is intentionally retained in definition. Resolve only
        // the prepared legacy descriptor, without upgrading old source inputs.
        // Elastic benchmarks have no Route-C material inputs: keep them absent.
        criterion.gaugeLength=concrete.referenceLength;
        criterion.threshold=concrete.crushReferenceStrain;
        criterion.source=concrete.source;
    }
    model.compressionCriterion=std::move(criterion);
}
std::string indexed(const std::string& prefix, std::size_t index) {
    return prefix + std::to_string(index + 1);
}
MaterialParameters mapped(MaterialParameters p, double lt, double lc) {
    p.lchT = lt; p.lchC = lc;
    if (p.kind == MaterialKind::Elastic) {
        positive(p.E,p.id,"E"); p.E0 = p.E;
    } else if (p.kind == MaterialKind::SteelBilinear) {
        positive(p.E,p.id,"E"); positive(p.fy,p.id,"fy");
        require(finite(p.b) && p.b >= 0.0 && p.b < 1.0,p.id,"b","Hardening ratio must be in [0,1).");
        p.H = p.b * p.E / (1.0 - p.b); p.E0 = p.E;
    } else {
        positive(p.fc,p.id,"fc"); positive(p.e0,p.id,"e0");
        positive(p.ft,p.id,"ft"); positive(p.Gft,p.id,"Gft");
        positive(p.Gcp,p.id,"Gcp"); positive(p.referenceLength,p.id,"reference_length");
        positive(lt,p.id,"lch_t"); positive(lc,p.id,"lch_c");
        require(finite(p.fr) && p.fr >= 0.0 && p.fr < p.fc,p.id,"fr","Residual compression must be in [0,fc).");
        require(finite(p.unloadRatio) && p.unloadRatio > 0.0 && p.unloadRatio < 1.0,p.id,"unload_ratio","Unloading ratio must be in (0,1).");
        p.E0 = 2.0*p.fc/p.e0;
        require(!finite(p.E) || std::abs(p.E-p.E0) <= 1e-12*p.E0,p.id,"E","Concrete initial modulus is derived from fc and e0.");
        const double tensionDenominator = 2.0*p.Gft/lt-p.ft*p.ft/p.E0;
        require(finite(tensionDenominator) && tensionDenominator > 0.0,p.id,"lch_t","Tensile length violates the stress-opening mapping domain.");
        p.Ets = p.ft*p.ft/tensionDenominator;
        p.etu = 2.0*p.Gft/(p.ft*lt);
        p.deltaU = 2.0*p.Gcp/(p.fc+p.fr);
        const double k = (p.fc-p.fr)/p.deltaU;
        const double localSlope = 1.0/lc-k/p.E0;
        const double referenceSlope = 1.0/p.referenceLength-k/p.E0;
        require(finite(localSlope) && localSlope > 0.0,p.id,"lch_c","Compression mapping is not single-valued for this length.");
        require(finite(referenceSlope) && referenceSlope > 0.0,p.id,"reference_length","Reference compression coordinate must increase on the descending branch.");
        require(finite(p.crushReferenceStrain) && p.crushReferenceStrain > p.e0,p.id,"crush_reference_strain","Reference threshold must be after the compression peak.");
        p.eu = p.e0+p.deltaU/lc-(p.fc-p.fr)/p.E0;
        p.compressionSlope = k/localSlope;
        p.deltaCritical = (p.crushReferenceStrain-p.e0)/referenceSlope;
        require(finite(p.deltaCritical) && p.deltaCritical > 0.0 && p.deltaCritical <= p.deltaU,p.id,"crush_reference_strain","Reference threshold lies beyond the defined compression branch.");
        for (double v : {p.E0,p.Ets,p.etu,p.deltaU,p.eu,p.compressionSlope,p.deltaCritical})
            require(finite(v) && v > 0.0,p.id,"mapping","Generated material parameter is invalid.");
    }
    return p;
}
std::vector<std::pair<double,double>> gauss(int count) {
    if (count == 2) { const double a=1.0/std::sqrt(3.0); return {{-a,1.0},{a,1.0}}; }
    if (count == 3) { const double a=std::sqrt(3.0/5.0); return {{-a,5.0/9.0},{0.0,8.0/9.0},{a,5.0/9.0}}; }
    const double a=std::sqrt(5.0+2.0*std::sqrt(10.0/7.0))/3.0;
    const double b=std::sqrt(5.0-2.0*std::sqrt(10.0/7.0))/3.0;
    const double wa=(322.0-13.0*std::sqrt(70.0))/900.0;
    const double wb=(322.0+13.0*std::sqrt(70.0))/900.0;
    return {{-a,wa},{-b,wb},{0.0,128.0/225.0},{b,wb},{a,wa}};
}
std::vector<double> mesh(const std::vector<double>& keys, const DiscretizationDefinition& d, double length) {
    std::vector<double> x{keys.front()};
    for (std::size_t j=1;j<keys.size();++j) {
        const double left=keys[j-1], right=keys[j], interval=right-left;
        const double target=d.mesh=="custom" ? d.targetLength : 50.0;
        const double countReal=std::ceil(interval/target);
        require(finite(countReal) && countReal >= 1.0 && countReal <= 10000.0,"mesh","target_length","Mesh exceeds the supported element count.");
        const int n=static_cast<int>(countReal);
        if (d.mesh=="M100") {
            // Pair cells on each side from the outside toward the beam midpoint.
            if (0.5*(left+right) <= length*0.5) {
                for(int i=2;i<n;i+=2) x.push_back(left+interval*double(i)/n);
            } else {
                for(int i=(n%2 ? 1:2);i<n;i+=2) x.push_back(left+interval*double(i)/n);
            }
        } else {
            const int refinement=d.mesh=="M25" ? 2:1;
            for(int i=1;i<n*refinement;++i) x.push_back(left+interval*double(i)/(n*refinement));
        }
        x.push_back(right);
        require(x.size()<=10001,"mesh","target_length","Mesh exceeds the supported element count.");
    }
    return x;
}
std::size_t nodeAt(const std::vector<PreparedNode>& nodes, double x) {
    const auto it=std::lower_bound(nodes.begin(),nodes.end(),x,[](const PreparedNode& n,double v){return n.x<v;});
    require(it!=nodes.end() && it->x==x,"mesh","key_position","A required coordinate is not an exact mesh node.");
    return static_cast<std::size_t>(it-nodes.begin());
}
void validateOptions(const AnalysisOptions& o) {
    for (const auto& value : std::vector<std::pair<const char*,double>>{
        {"initial_step",o.initialStep},{"minimum_step",o.minimumStep},{"maximum_step",o.maximumStep},
        {"maximum_control_displacement",o.maximumControlDisplacement},{"reference_displacement",o.referenceDisplacement},
        {"reference_force",o.referenceForce},{"residual_absolute",o.residualAbsolute},{"residual_relative",o.residualRelative},
        {"correction_absolute",o.correctionAbsolute},{"correction_relative",o.correctionRelative},
        {"control_absolute",o.controlAbsolute},{"control_relative",o.controlRelative},{"backward_tolerance",o.backwardTolerance},
        {"event_displacement_tolerance",o.eventDisplacementTolerance},{"event_strain_absolute",o.eventStrainAbsolute},
        {"event_strain_relative",o.eventStrainRelative}}) positive(value.second,"analysis",value.first);
    require(o.minimumStep<=o.initialStep && o.initialStep<=o.maximumStep,"analysis","initial_step","Step sizes must satisfy minimum <= initial <= maximum.");
    require(o.maximumIterations>0 && o.maximumIterations<=10000 && o.maximumHalvings>=0 && o.maximumHalvings<=100,
            "analysis","iterations","Iteration or halving limit is outside the supported domain.");
    require(o.gravitySteps>0 && o.gravitySteps<=100000 && o.maximumAcceptedSteps>0 && o.maximumAcceptedSteps<=1000000,
            "analysis","maximum_accepted_steps","Step count is outside the supported resource domain.");
    require(o.maximumLocalizationSolves>0 && o.maximumLocalizationSolves<=10000 && o.growthConsecutive>0 && o.growthIterationLimit>0,
            "analysis","localization","Localization and growth counts must be positive and bounded.");
    require(finite(o.growthFactor) && o.growthFactor>1.0 && finite(o.referenceLength) && o.referenceLength>=0.0,
            "analysis","growth_factor","Growth factor must exceed one; reference length must be nonnegative.");
    require(o.maximumLineSearchTrials>=1 && o.maximumLineSearchTrials<=64,
            "analysis","maximum_line_search_trials","Line-search trial count must be in [1,64].");
    require(finite(o.lineSearchReduction) && o.lineSearchReduction>0.0 && o.lineSearchReduction<1.0,
            "analysis","line_search_reduction","Line-search reduction must be strictly between zero and one.");
    require(finite(o.lineSearchSufficientDecrease) && o.lineSearchSufficientDecrease>0.0 && o.lineSearchSufficientDecrease<1.0,
            "analysis","line_search_sufficient_decrease","Sufficient-decrease coefficient must be strictly between zero and one.");
    require(finite(o.minimumLineSearchFactor) && o.minimumLineSearchFactor>0.0 && o.minimumLineSearchFactor<=1.0,
            "analysis","minimum_line_search_factor","Minimum line-search factor must be in (0,1].");
    require(o.maximumTransitionSolves>=1 && o.maximumTransitionSolves<=40,
            "analysis","maximum_transition_solves","Transition candidate count must be in [1,40].");
    positive(o.transitionDisplacementTolerance,"analysis","transition_displacement_tolerance");
    require(finite(o.transitionRatioTolerance) && o.transitionRatioTolerance>0.0 && o.transitionRatioTolerance<1.0,
            "analysis","transition_ratio_tolerance","Transition ratio tolerance must be strictly between zero and one.");
    require(finite(o.transitionMinimumStep) && o.transitionMinimumStep>0.0 &&
            o.transitionMinimumStep<=o.transitionDisplacementTolerance &&
            (!o.transitionResolutionEnabled || o.transitionMinimumStep<=o.minimumStep),
            "analysis","transition_minimum_step","Transition increment must be positive, no greater than its tolerance, and when enabled no greater than the normal minimum.");
    if(o.lineSearchEnabled || o.transitionResolutionEnabled)
        require(o.maximumIterations<=60 && o.maximumHalvings<=18,
                "analysis","iterations","Revised numerical algorithms support at most 60 Newton iterations and 18 halvings.");
}
void checkRigidModes(const PreparedModel& m) {
    std::vector<std::array<double,3>> rows;
    const double L=m.definition.geometry.length;
    for(std::size_t dof:m.constrainedDofs) {
        const auto& n=m.nodes[dof/3];
        if(dof%3==0) rows.push_back({1.0,0.0,-n.globalY/L});
        else if(dof%3==1) rows.push_back({0.0,1.0,n.globalX/L});
        else rows.push_back({0.0,0.0,1.0});
    }
    std::size_t rank=0;
    for(std::size_t c=0;c<3 && rank<rows.size();++c) {
        std::size_t p=rank;
        for(std::size_t i=rank;i<rows.size();++i) if(std::abs(rows[i][c])>std::abs(rows[p][c])) p=i;
        if(std::abs(rows[p][c])<1e-12) continue;
        std::swap(rows[p],rows[rank]); const double divisor=rows[rank][c];
        for(std::size_t k=c;k<3;++k) rows[rank][k]/=divisor;
        for(std::size_t i=rank+1;i<rows.size();++i) { const double a=rows[i][c]; for(std::size_t k=c;k<3;++k) rows[i][k]-=a*rows[rank][k]; }
        ++rank;
    }
    require(rank==3,"supports","constrained","Support constraints leave a planar rigid-body mode.");
}
} // namespace

PreparationResult prepareModel(const ModelDefinition& input) {
    PreparationResult result;
    try {
        PreparedModel m; m.definition=input; m.identity=input.identity;
        auto& d=m.definition;
        require(!d.identity.projectId.empty() && !d.identity.revisionId.empty(),"identity","identity","Project and model revision identities are required.");
        positive(d.geometry.length,"geometry","length"); positive(d.geometry.width,"geometry","width"); positive(d.geometry.height,"geometry","height");
        require(finite(d.geometry.angle),"geometry","angle","Beam angle must be finite radians.");
        validateOptions(d.analysis);
        const double L=d.geometry.length, w=d.geometry.width, h=d.geometry.height;
        require(finite(w*h*h*h) && finite(L*L) && finite(w*h),"geometry","dimensions","Geometry products exceed finite numeric range.");
        require(d.discretization.mesh=="custom" || d.discretization.mesh=="M100" || d.discretization.mesh=="M50" || d.discretization.mesh=="M25",
                "mesh","mesh","Unknown mesh scheme.");
        if(d.discretization.mesh=="custom") positive(d.discretization.targetLength,"mesh","target_length");
        else d.discretization.targetLength=d.discretization.mesh=="M100"?100.0:(d.discretization.mesh=="M50"?50.0:25.0);
        require(d.discretization.gaussPoints==2 || d.discretization.gaussPoints==3 || d.discretization.gaussPoints==5,"mesh","gauss_points","Supported Gauss counts are 2, 3, and 5.");
        require(d.discretization.layers>=2 && d.discretization.layers<=1000,"mesh","layers","Layer count must be in [2,1000].");
        require(d.discretization.lengthMapping=="gauss_weight_v1" || d.discretization.lengthMapping=="element_length_v2",
                "mesh","length_mapping","Unknown constitutive length mapping version.");
        const bool elementLengthMapping=d.discretization.lengthMapping=="element_length_v2";
        std::map<Id,MaterialParameters> materials;
        for(const auto& p:d.materials) {
            require(!p.id.empty() && materials.emplace(p.id,p).second,p.id,"id","Material IDs must be unique and nonempty.");
            require(p.kind==MaterialKind::Concrete02 || p.kind==MaterialKind::SteelBilinear || p.kind==MaterialKind::Elastic,p.id,"kind","Unknown material kind.");
        }
        require(materials.count(d.concreteMaterialId)!=0,d.concreteMaterialId,"concrete_material_id","Section material is missing.");
        const auto concrete=materials.at(d.concreteMaterialId);
        require(concrete.kind!=MaterialKind::SteelBilinear,concrete.id,"kind","Gross rectangle requires concrete or elastic material.");
        prepareCompressionCriterion(m,concrete);
        auto& r=d.reinforcement;
        require(r.mode=="direct" || r.mode=="cover","reinforcement","mode","Reinforcement mode must be direct or cover.");
        require(finite(r.cover) && r.cover>=0.0 && finite(r.stirrupDiameter) && r.stirrupDiameter>=0.0 && finite(r.stirrupSpacing) && r.stirrupSpacing>=0.0,
                "reinforcement","cover","Cover and stirrup dimensions must be finite nonnegative values.");
        if(r.mode=="cover") {
            require(r.coverDefinition=="stirrup_outer","reinforcement","cover_definition","Cover-driven geometry requires cover to stirrup outer surface.");
            require(r.topCount>=0 && r.topCount<=1000 && r.bottomCount>=0 && r.bottomCount<=1000 && r.topCount+r.bottomCount<=1000,"reinforcement","count","Bar counts must be nonnegative and bounded.");
            r.bars.clear();
            auto group=[&](int count,double diameter,const Id& material,const std::string& name,double sign) {
                if(count==0) return;
                positive(diameter,name,"diameter");
                const double inset=r.cover+r.stirrupDiameter+diameter/2.0;
                const double halfWidth=w/2.0-inset;
                require(halfWidth>=0.0 && h/2.0-inset>=0.0,name,"cover","Cover and bars do not fit inside the section.");
                for(int i=0;i<count;++i) r.bars.push_back({indexed("bar-"+name+"-",i),material,diameter,pi*diameter*diameter/4.0,
                    sign*(h/2.0-inset), count==1?0.0:-halfWidth+2.0*halfWidth*i/(count-1),name});
            };
            group(r.topCount,r.topDiameter,r.topMaterialId,"top",1.0);
            group(r.bottomCount,r.bottomDiameter,r.bottomMaterialId,"bottom",-1.0);
        }
        require(r.bars.size()<=1000,"reinforcement","bars","Too many longitudinal bars.");
        std::set<Id> barIds;
        double steelArea=0.0, removedFirst=0.0, removedSecond=0.0;
        for(auto& bar:r.bars) {
            require(!bar.id.empty() && barIds.insert(bar.id).second,bar.id,"id","Bar identities must be unique and nonempty.");
            positive(bar.diameter,bar.id,"diameter");
            const double area=pi*bar.diameter*bar.diameter/4.0;
            if(bar.area==0.0) bar.area=area;
            positive(bar.area,bar.id,"area");
            require(std::abs(bar.area-area)<=1e-10*area,bar.id,"area","Bar area must agree with its circular diameter.");
            require(finite(bar.eta) && finite(bar.zeta) && std::abs(bar.eta)+bar.diameter/2.0<=h/2.0 && std::abs(bar.zeta)+bar.diameter/2.0<=w/2.0,
                    bar.id,"position","Bar outer surface is outside the rectangular section.");
            require(materials.count(bar.materialId)!=0,bar.id,"material_id","Referenced bar material is missing.");
            require(materials.at(bar.materialId).kind!=MaterialKind::Concrete02,bar.id,"material_id","Longitudinal bar requires steel or explicit elastic benchmark material.");
            steelArea+=bar.area; removedFirst+=bar.area*bar.eta; removedSecond+=bar.area*bar.eta*bar.eta;
        }
        for(std::size_t i=0;i<r.bars.size();++i) for(std::size_t j=0;j<i;++j) {
            const auto& a=r.bars[i]; const auto& b=r.bars[j];
            require(std::hypot(a.eta-b.eta,a.zeta-b.zeta)>=(a.diameter+b.diameter)/2.0,a.id,"position","Longitudinal bars overlap.");
        }
        require(steelArea<w*h,"reinforcement","area","Steel replacement area consumes the gross section.");
        auto validX=[&](double x,const Id& id,const std::string& field) { require(finite(x) && x>=0.0 && x<=L,id,field,"Coordinate must lie on the finite beam axis."); };
        validX(d.controlX,"control","x");
        require(d.controlComponent>=0 && d.controlComponent<3,"control","component","Control component must be 0, 1, or 2.");
        require(finite(d.controlDirection) && std::abs(d.controlDirection)==1.0,"control","direction","Control direction must be +1 or -1.");
        std::vector<double> keys{0.0,L,d.controlX};
        std::set<Id> objectIds;
        for(const auto& s:d.supports) {
            require(!s.id.empty() && objectIds.insert(s.id).second,s.id,"id","Support/load identities must be unique and nonempty.");
            validX(s.x,s.id,"x"); keys.push_back(s.x);
            require(s.constrained[0] || s.constrained[1] || s.constrained[2],s.id,"constrained","A support must constrain at least one degree of freedom.");
            for(double v:s.values) require(finite(v),s.id,"values","Prescribed values must be finite.");
        }
        require(!d.loads.empty(),"loads","loads","A nonzero proportional load pattern is required.");
        for(const auto& p:d.loads) {
            require(!p.id.empty() && objectIds.insert(p.id).second,p.id,"id","Support/load identities must be unique and nonempty.");
            validX(p.x,p.id,"x"); keys.push_back(p.x);
            for(double v:p.reference) require(finite(v),p.id,"reference","Reference force components must be finite.");
        }
        std::sort(keys.begin(),keys.end()); keys.erase(std::unique(keys.begin(),keys.end()),keys.end());
        const auto xs=mesh(keys,d.discretization,L);
        const double c=std::cos(d.geometry.angle), s=std::sin(d.geometry.angle);
        const std::string prefix=d.identity.snapshotId.empty()?d.identity.revisionId:d.identity.snapshotId;
        for(std::size_t i=0;i<xs.size();++i) m.nodes.push_back({indexed(prefix+"/node-",i),i+1,xs[i],c*xs[i],s*xs[i],{3*i,3*i+1,3*i+2}});
        const std::size_t ndof=3*m.nodes.size();
        m.referenceLoad.assign(ndof,0.0); m.gravityLoad.assign(ndof,0.0);
        std::map<std::size_t,double> constraints;
        for(const auto& support:d.supports) {
            const auto ni=nodeAt(m.nodes,support.x);
            for(std::size_t k=0;k<3;++k) if(support.constrained[k]) {
                const std::size_t dof=m.nodes[ni].dofs[k];
                auto old=constraints.find(dof);
                require(old==constraints.end() || old->second==support.values[k],support.id,"values","Conflicting prescribed values at a shared degree of freedom.");
                constraints[dof]=support.values[k];
            }
        }
        for(const auto& kv:constraints) { m.constrainedDofs.push_back(kv.first); m.prescribedValues.push_back(kv.second); }
        for(std::size_t i=0;i<ndof;++i) if(!constraints.count(i)) m.freeDofs.push_back(i);
        checkRigidModes(m);
        m.controlNode=nodeAt(m.nodes,d.controlX); m.controlDof=m.nodes[m.controlNode].dofs[d.controlComponent];
        require(!constraints.count(m.controlDof),"control","x","Controlled degree of freedom is constrained by a real support.");
        for(const auto& p:d.loads) { const auto ni=nodeAt(m.nodes,p.x); for(std::size_t k=0;k<3;++k) m.referenceLoad[m.nodes[ni].dofs[k]]+=p.reference[k]; }
        double freeLoad=0.0;
        for(const auto i:m.freeDofs) freeLoad=std::max(freeLoad,std::abs(m.referenceLoad[i]));
        positive(freeLoad,"loads","reference");
        // Initial elastic control compliance is checked by analysis before it
        // accepts a run step; preparation remains independent of assembly.
        require(finite(d.gravity.density) && d.gravity.density>=0.0 && finite(d.gravity.acceleration) && d.gravity.acceleration>=0.0,"gravity","density","Gravity inputs must be finite and nonnegative.");
        if(d.gravity.enabled) { positive(d.gravity.density,"gravity","density"); positive(d.gravity.acceleration,"gravity","acceleration"); }
        if(d.analysis.referenceLength==0.0) {
            double xmin=L,xmax=0.0;
            for(const auto& support:d.supports) { xmin=std::min(xmin,support.x); xmax=std::max(xmax,support.x); }
            d.analysis.referenceLength=xmax>xmin?xmax-xmin:L;
        }
        require(d.observations.referencePhase=="S0" || d.observations.referencePhase=="SG" || !d.observations.referenceStepId.empty(),"observations","reference_phase","Observation reference must be S0, SG, or an explicit step.");
        if(!d.gravity.enabled && d.observations.referenceStepId.empty()) d.observations.referencePhase="S0";
        std::set<Id> gauges;
        for(const auto& gauge:d.observations.gauges) {
            require(!gauge.id.empty() && gauges.insert(gauge.id).second,gauge.id,"id","Gauge identities must be unique and nonempty.");
            validX(gauge.x1,gauge.id,"x1"); validX(gauge.x2,gauge.id,"x2");
            require(gauge.x2>gauge.x1 && finite(gauge.eta) && std::abs(gauge.eta)<=h/2.0,gauge.id,"geometry","Gauge endpoints and height are invalid.");
        }
        const auto rule=gauss(d.discretization.gaussPoints);
        // Keep the legacy first-sample validation unchanged for old descriptors.
        const double sampleLength=elementLengthMapping ? xs[1]-xs[0] : (xs[1]-xs[0])*rule.front().second/2.0;
        for(const auto& p:d.materials) (void)mapped(p,sampleLength,sampleLength);
        if(elementLengthMapping) {
            // Archived definitions must also be valid at every new effective length.
            std::set<double> lengths;
            for(std::size_t i=1;i<xs.size();++i) lengths.insert(xs[i]-xs[i-1]);
            for(double length:lengths) if(length!=sampleLength)
                for(const auto& p:d.materials) (void)mapped(p,length,length);
        }
        require((m.nodes.size()-1)*rule.size()*std::size_t(d.discretization.layers+2*r.bars.size()+2)<=1000000,"mesh","resource_limit","Prepared model exceeds one million constitutive points.");
        const double areaLayer=w*h/d.discretization.layers;
        double rectangleSecond=0.0;
        for(int i=0;i<d.discretization.layers;++i) { const double eta=-h/2.0+(i+0.5)*h/d.discretization.layers; rectangleSecond+=areaLayer*eta*eta; }
        require(rectangleSecond>removedSecond,"reinforcement","second_moment","Concrete correction makes the discrete concrete inertia nonpositive.");
        for(std::size_t i=0;i+1<m.nodes.size();++i) {
            PreparedElement element; element.id=indexed(prefix+"/element-",i); element.numericId=i+1;
            element.nodes={i,i+1}; element.length=xs[i+1]-xs[i]; element.cosine=c; element.sine=s;
            element.dofs={3*i,3*i+1,3*i+2,3*i+3,3*i+4,3*i+5};
            for(std::size_t q=0;q<rule.size();++q) {
                PreparedSection section; section.id=indexed(element.id+"/gauss-",q); section.elementId=element.id; section.elementIndex=i; section.gaussIndex=static_cast<int>(q);
                section.xi=rule[q].first; section.weight=rule[q].second; section.integrationLength=element.length*section.weight/2.0;
                section.x=xs[i]+element.length*(1.0+section.xi)/2.0;
                section.lchT=elementLengthMapping ? element.length : section.integrationLength;
                section.lchC=elementLengthMapping ? element.length : section.integrationLength;
                const auto cp=mapped(concrete,section.lchT,section.lchC);
                for(int k=0;k<d.discretization.layers;++k) section.fibers.push_back({indexed(section.id+"/concrete-",k),concrete.id,"concrete",-h/2.0+(k+0.5)*h/d.discretization.layers,0.0,areaLayer,cp});
                for(const auto& bar:r.bars) {
                    section.fibers.push_back({section.id+"/"+bar.id+"/replacement",concrete.id,"concrete_correction",bar.eta,bar.zeta,-bar.area,cp});
                    section.fibers.push_back({section.id+"/"+bar.id,bar.materialId,"steel",bar.eta,bar.zeta,bar.area,mapped(materials.at(bar.materialId),section.lchT,section.lchC)});
                }
                section.edges.push_back({section.id+"/edge-top","top",h/2.0,cp});
                section.edges.push_back({section.id+"/edge-bottom","bottom",-h/2.0,cp});
                double ac=0.0,first=0.0,second=0.0,as=0.0;
                for(const auto& f:section.fibers) { if(f.role=="steel") as+=f.area; else {ac+=f.area;first+=f.area*f.eta;second+=f.area*f.eta*f.eta;} }
                require(std::abs(ac-(w*h-steelArea))<=1e-10*w*h && std::abs(first+removedFirst)<=1e-10*w*h*h && std::abs(second-(rectangleSecond-removedSecond))<=1e-10*w*h*h*h && std::abs(as-steelArea)<=1e-10*w*h,
                        section.id,"moments","Signed fiber replacement failed discrete area/moment checks.");
                element.sectionIndices.push_back(m.sections.size()); m.sections.push_back(std::move(section));
            }
            if(d.gravity.enabled) {
                // kg/m3 * m/s2 * mm2 -> N/mm; global acceleration is downward.
                const double q=-d.gravity.density*d.gravity.acceleration*w*h*1e-9;
                const double axial=q*s, transverse=q*c, ell=element.length;
                const std::array<double,6> local{axial*ell/2.0,transverse*ell/2.0,transverse*ell*ell/12.0,
                    axial*ell/2.0,transverse*ell/2.0,-transverse*ell*ell/12.0};
                for(int end=0;end<2;++end) {
                    const int k=3*end;
                    m.gravityLoad[element.dofs[k]]+=c*local[k]-s*local[k+1];
                    m.gravityLoad[element.dofs[k+1]]+=s*local[k]+c*local[k+1];
                    m.gravityLoad[element.dofs[k+2]]+=local[k+2];
                }
            }
            m.elements.push_back(std::move(element));
        }
        for(const auto& p:d.materials) if(p.source.category=="assumption" || p.source.category=="paper" || p.source.category=="synthetic")
            m.diagnostics.push_back({"SOURCE_UNVERIFIED","information","preparation",p.id,"source","Source/default package is retained without claiming experimental calibration."});
        for(const auto& source:d.sources) if(source.category=="assumption" || source.category=="synthetic")
            m.diagnostics.push_back({"MODELING_ASSUMPTION","information","preparation","model","sources",source.reference+" "+source.quality});
        if(elementLengthMapping)
            m.diagnostics.push_back({"LENGTH_MAPPING_UNVERIFIED","warning","preparation","mesh","length_mapping","CR-NUM-001 element-length characteristic lengths are a corrected candidate requiring independent localization and mesh validation; Gauss integration lengths remain separate."});
        else
            m.diagnostics.push_back({"LENGTH_MAPPING_UNVERIFIED","warning","preparation","mesh","length_mapping","Gauss-weight characteristic lengths are a candidate requiring independent localization and mesh validation."});
        if(m.compressionCriterion.mode==physicalGaugeMeanMode)
            m.diagnostics.push_back({"COMPRESSION_CRITERION_UNVERIFIED","warning","preparation","compression_criterion","mode","Physical gauge compression uses whole-beam both-edge total compatible EB strain windows including gravity; this candidate criterion requires independent validation and does not establish experimental calibration. "+m.compressionCriterion.source.reference});
        m.diagnostics.push_back({"SECTION_MOMENTS_CHECKED","information","preparation","section","fibers","Signed replacement preserves discrete gross-minus-steel area, first moment, and second moment; layer integration error remains separate."});
        result.ok=true; result.diagnostics=m.diagnostics; result.model=std::move(m);
    } catch(const DomainError& e) { result.diagnostics.push_back(e.diagnostic); }
      catch(const std::exception& e) { result.diagnostics.push_back({"PREPARATION_FAILURE","error","preparation","model","",e.what()}); }
    return result;
}
} // namespace lumafem
