#include "json_codec.hpp"
#include <fstream>
#include <filesystem>
#include <iostream>
#include <iomanip>
#include <cmath>
#include "analysis_access.inc"
using namespace lumafem;
template<class T>T value(const codec::Json&j){if constexpr(std::is_floating_point<T>::value)return j.is_null()?std::numeric_limits<T>::quiet_NaN():j.get<T>();else return j.get<T>();}
MaterialState restore(const codec::Json& j){MaterialState q;q.strain=value<decltype(q.strain)>(j.at("strain"));
q.stress=value<decltype(q.stress)>(j.at("stress"));
q.tangent=value<decltype(q.tangent)>(j.at("tangent"));
q.minStrain=value<decltype(q.minStrain)>(j.at("min_strain"));
q.maxStrain=value<decltype(q.maxStrain)>(j.at("max_strain"));
q.compressionStress=value<decltype(q.compressionStress)>(j.at("compression_stress"));
q.tensionStress=value<decltype(q.tensionStress)>(j.at("tension_stress"));
q.tensionEnvelopeStrain=value<decltype(q.tensionEnvelopeStrain)>(j.at("tension_envelope_strain"));
q.compressionUnloadStrain=value<decltype(q.compressionUnloadStrain)>(j.at("compression_unload_strain"));
q.compressionUnloadSlope=value<decltype(q.compressionUnloadSlope)>(j.at("compression_unload_slope"));
q.tensionUnloadStrain=value<decltype(q.tensionUnloadStrain)>(j.at("tension_unload_strain"));
q.tensionUnloadSlope=value<decltype(q.tensionUnloadSlope)>(j.at("tension_unload_slope"));
q.reversalStrain=value<decltype(q.reversalStrain)>(j.at("reversal_strain"));
q.reversalStress=value<decltype(q.reversalStress)>(j.at("reversal_stress"));
q.plasticStrain=value<decltype(q.plasticStrain)>(j.at("plastic_strain"));
q.backStress=value<decltype(q.backStress)>(j.at("back_stress"));
q.accumulatedPlasticStrain=value<decltype(q.accumulatedPlasticStrain)>(j.at("accumulated_plastic_strain"));
q.deltaC=value<decltype(q.deltaC)>(j.at("delta_c"));
q.maxDeltaC=value<decltype(q.maxDeltaC)>(j.at("max_delta_c"));
q.referenceCompressionStrain=value<decltype(q.referenceCompressionStrain)>(j.at("reference_compression_strain"));
q.maxReferenceCompressionStrain=value<decltype(q.maxReferenceCompressionStrain)>(j.at("max_reference_compression_strain"));
q.tensionEnergy=value<decltype(q.tensionEnergy)>(j.at("tension_energy"));
q.compressionEnergy=value<decltype(q.compressionEnergy)>(j.at("compression_energy"));
q.compressionLoading=value<decltype(q.compressionLoading)>(j.at("compression_loading"));
q.cracked=value<decltype(q.cracked)>(j.at("cracked"));
q.yielded=value<decltype(q.yielded)>(j.at("yielded"));
q.crushed=value<decltype(q.crushed)>(j.at("crushed"));
q.direction=value<decltype(q.direction)>(j.at("direction"));
q.branch=value<decltype(q.branch)>(j.at("branch")); return q;}
codec::Json encode(const MaterialState&q){codec::Json j;j["strain"]=q.strain;
j["stress"]=q.stress;
j["tangent"]=q.tangent;
j["min_strain"]=q.minStrain;
j["max_strain"]=q.maxStrain;
j["compression_stress"]=q.compressionStress;
j["tension_stress"]=q.tensionStress;
j["tension_envelope_strain"]=q.tensionEnvelopeStrain;
j["compression_unload_strain"]=q.compressionUnloadStrain;
j["compression_unload_slope"]=q.compressionUnloadSlope;
j["tension_unload_strain"]=q.tensionUnloadStrain;
j["tension_unload_slope"]=q.tensionUnloadSlope;
j["reversal_strain"]=q.reversalStrain;
j["reversal_stress"]=q.reversalStress;
j["plastic_strain"]=q.plasticStrain;
j["back_stress"]=q.backStress;
j["accumulated_plastic_strain"]=q.accumulatedPlasticStrain;
j["delta_c"]=q.deltaC;
j["max_delta_c"]=q.maxDeltaC;
j["reference_compression_strain"]=q.referenceCompressionStrain;
j["max_reference_compression_strain"]=q.maxReferenceCompressionStrain;
j["tension_energy"]=q.tensionEnergy;
j["compression_energy"]=q.compressionEnergy;
j["compression_loading"]=q.compressionLoading;
j["cracked"]=q.cracked;
j["yielded"]=q.yielded;
j["crushed"]=q.crushed;
j["direction"]=q.direction;
j["branch"]=q.branch; return codec::Json::parse(j.dump());}
std::string fullHash(const SystemState&s){codec::Json j={{"u",s.displacement},{"lambda",s.loadFactor},{"gravity",s.gravityFactor},{"sections",codec::Json::array()}};for(const auto&section:s.sections){codec::Json v={{"fibers",codec::Json::array()},{"edges",codec::Json::array()}};for(const auto&q:section.fibers)v["fibers"].push_back(encode(q));for(const auto&q:section.edges)v["edges"].push_back(encode(q));j["sections"].push_back(v);}return codec::contentHash(j);}

void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
codec::Json readJson(const std::string& path){std::ifstream f(path);codec::Json j;f>>j;return j;}
double release(const MaterialParameters&p,const MaterialState&s){
 if(s.stress==0)return 0;
 if(p.kind!=MaterialKind::Concrete02){
  require(p.E>0,"nonpositive elastic modulus");
  if(p.kind==MaterialKind::SteelBilinear)require(std::abs(s.backStress)<p.fy,"reverse yield prevents specified elastic zero-stress unload");
  auto zero=evaluateMaterial(p,s,s.strain-s.stress/p.E);require(zero.ok&&std::abs(zero.stress)<1e-6,"elastic zero-stress endpoint mismatch");
  return s.stress*s.stress/(2*p.E);
 }
 const double intercept=s.compressionUnloadStrain;
 if(s.stress>0){
  require(s.strain>=intercept,"positive concrete stress below zero intercept");
  const double mid=.5*(s.strain+intercept);auto r=evaluateMaterial(p,s,mid);
  require(r.ok&&std::abs(r.stress-.5*s.stress)<1e-7,"tension unload secant mismatch");
  return .5*s.stress*(s.strain-intercept);
 }
 const double a=s.strain,b=intercept,E=s.compressionUnloadSlope>0?p.E0:0,Eu=s.compressionUnloadSlope;
 require(a<=b&&E>0&&Eu>0,"invalid compression unloading data");
 std::vector<double> knots{a,b};
 for(double factor:{1.,.5}){const double slope=factor*Eu;if(E!=slope){double x=(E*a-s.stress-slope*b)/(E-slope);if(x>a&&x<b)knots.push_back(x);}}
 std::sort(knots.begin(),knots.end());
 auto stress=[&](double e){return std::min(std::max(s.stress+E*(e-a),Eu*(e-b)),.5*Eu*(e-b));};
 double work=0;
 for(std::size_t i=1;i<knots.size();++i){double lo=knots[i-1],hi=knots[i],mid=.5*(lo+hi);auto r=evaluateMaterial(p,s,mid);require(r.ok&&std::abs(r.stress-stress(mid))<1e-7,"independent compression clamp partition mismatch");work-=.5*(stress(lo)+stress(hi))*(hi-lo);}
 require(work>=-1e-10,"negative uniaxial release");return work;
}
struct Energy {
 double tension=0,compression=0,residualCoordinate=0,releaseIndex=0,steelElastic=0;
 std::vector<std::size_t> support;
 codec::Json json(double materialWork,double externalWork)const{return {{"tension_envelope_coordinate",tension},{"compression_envelope_coordinate",compression},{"compression_residual_coordinate_baseline",residualCoordinate},{"pointwise_zero_stress_unload_release",releaseIndex},{"steel_elastic_part",steelElastic},{"accepted_grid_material_work",materialWork},{"accepted_grid_external_work",externalWork},{"nonrecovered_work_under_point_protocol",materialWork-releaseIndex},{"compression_softened_elements",support}};}
};
Energy energy(const PreparedModel&m,const SystemState&s){Energy result;std::vector<bool>active(m.elements.size(),false);
 for(std::size_t i=0;i<m.sections.size();++i){const auto&sec=m.sections[i];for(std::size_t j=0;j<sec.fibers.size();++j){const auto&f=sec.fibers[j];const auto&q=s.sections[i].fibers[j];double volume=f.area*sec.integrationLength;double r=volume*release(f.material,q);result.releaseIndex+=r;
  if(f.material.kind==MaterialKind::SteelBilinear)result.steelElastic+=r;
  if(f.material.kind==MaterialKind::Concrete02){result.tension+=volume/f.material.lchT*q.tensionEnergy;result.compression+=volume/f.material.lchC*q.compressionEnergy;result.residualCoordinate+=volume/f.material.lchC*f.material.fr*std::min(q.maxDeltaC,f.material.deltaU);if(q.minStrain < -f.material.e0)active[sec.elementIndex]=true;}
 }}
 for(std::size_t i=0;i<active.size();++i)if(active[i])result.support.push_back(i);return result;
}
double materialIncrement(const PreparedModel&m,const SystemState&old,const SystemState&next){double result=0;for(std::size_t i=0;i<m.sections.size();++i)for(std::size_t j=0;j<m.sections[i].fibers.size();++j){const auto&b=next.sections[i].fibers[j];MaterialState a;if(!old.sections.empty())a=old.sections[i].fibers[j];result+=m.sections[i].fibers[j].area*m.sections[i].integrationLength*.5*(a.stress+b.stress)*(b.strain-a.strain);}return result;}
double externalIncrement(const PreparedModel&m,const SystemState&a,const SystemState&b){double work=0;for(std::size_t i=0;i<b.displacement.size();++i){double old=a.loadFactor*m.referenceLoad[i]+a.gravityFactor*m.gravityLoad[i],next=b.loadFactor*m.referenceLoad[i]+b.gravityFactor*m.gravityLoad[i];double u=a.displacement.empty()?0:a.displacement[i];work+=.5*(old+next)*(b.displacement[i]-u);}return work;}
SystemState restoreStep(const codec::Json&j){SystemState s;s.displacement=j.at("displacement").get<std::vector<double>>();s.loadFactor=j.at("load_factor");s.gravityFactor=j.at("gravity_factor");for(const auto&section:j.at("sections")){SectionState v;for(const auto&f:section.at("fibers"))v.fibers.push_back(restore(f.at("state")));for(const auto&f:section.at("edges"))v.edges.push_back(restore(f.at("state")));s.sections.push_back(v);}return s;}
int main(int argc,char**argv){try{
 require(argc==3,"matrix_reconstruction input.json NEW-output.json");require(!std::filesystem::exists(argv[2]),"refuse existing evidence output");auto manifest=readJson(argv[1]);double common=manifest.at("common_control");codec::Json output={{"common_control",common},{"cases",codec::Json::array()}};
 for(const auto&item:manifest.at("cases")){
  auto prepared=readJson(item.at("prepared"));auto decoded=codec::decodePrepared(prepared);auto&m=decoded.model;AnalysisHooks hooks;int persisted=0;hooks.persist=[&](const AcceptedStep&){++persisted;return PersistedStepReceipt{};};Analysis solver(m,m.definition.analysis,hooks);require(solver.validate(),"invalid matrix fixture");
  std::ifstream stream(item.at("accepted").get<std::string>());std::vector<codec::Json>states;std::string line;while(std::getline(stream,line))if(!line.empty())states.push_back(codec::parse(line));auto summary=readJson(item.at("summary"));const auto&loc=summary.at("localization");
  codec::Json report={{"name",item.at("name")},{"prepared_path",item.at("prepared")},{"brackets",codec::Json::array()},{"energy_history",codec::Json::array()}};double work=0,externalWork=0;bool commonDone=false;std::size_t count=0;
  for(const auto&stored:states){const std::string phase=stored.at("phase");double w=stored.at("control_displacement");
   if(!commonDone&&phase=="J"&&w>=common){
    if(w>common){const auto hash=fullHash(solver.anchor);auto c=solver.solve(solver.anchor.gravityFactor,true,common,0,"common_coordinate_diagnostic");require(c.ok&&c.evidence.residualPassed&&c.evidence.correctionPassed&&c.evidence.controlPassed,"common-coordinate diagnostic did not converge");require(fullHash(solver.anchor)==hash&&persisted==0,"common-coordinate trial promoted state");auto trial=c.response.state;trial.loadFactor=c.lambda;trial.gravityFactor=solver.anchor.gravityFactor;auto e=energy(m,trial);report["common_energy"]=e.json(work+materialIncrement(m,solver.anchor,trial),externalWork+externalIncrement(m,solver.anchor,trial));report["common_energy"]["origin"]="converged_same_anchor_diagnostic_trial_not_committed";report["common_energy"]["control"]=solver.control(trial.displacement);report["common_energy"]["convergence"]=codec::convergenceToJson(c.evidence);commonDone=true;}
   }
   if(count+1==states.size()){
    const auto hash=fullHash(solver.anchor);const bool peak=solver.compressionPeakAccepted;
    for(const char*part:{"lower","upper"}){std::string key=part;double parameter=loc.at(key+"_control");Candidate candidate;
     if(parameter==solver.control(solver.anchor.displacement)){candidate.ok=true;candidate.lambda=solver.anchor.loadFactor;candidate.response=solver.anchorResponse;candidate.evidence.residualPassed=true;candidate.evidence.correctionPassed=true;candidate.evidence.controlPassed=true;}else candidate=solver.solve(solver.anchor.gravityFactor,true,parameter,0,"terminal_bracket_diagnostic");
     require(candidate.ok&&candidate.evidence.residualPassed&&candidate.evidence.correctionPassed&&candidate.evidence.controlPassed,"terminal bracket end did not converge");auto found=evaluatePhysicalGauge(m,candidate.response.state.displacement);require(found.ok,"reconstructed global window invalid");double expected=loc.at(key+"_gauge").at("compression_maximum");require(std::abs(found.window.compressionMaximum-expected)<=1e-12,"recorded bracket mean mismatch");require(key=="lower"?found.window.compressionMaximum<m.compressionCriterion.threshold:found.window.compressionMaximum>=m.compressionCriterion.threshold,"recorded bracket side changed");
     report["brackets"].push_back({{"end",key},{"recorded_window",loc.at(key+"_gauge")},{"displacement",candidate.response.state.displacement},{"convergence",codec::convergenceToJson(candidate.evidence)},{"actual_global_compression",found.window.compressionMaximum}});
     require(fullHash(solver.anchor)==hash&&solver.compressionPeakAccepted==peak&&persisted==0,"bracket diagnostic promoted history/latches/storage");
    }
   }
   auto old=solver.anchor;auto response=evaluateSystem(m,old,stored.at("displacement").get<std::vector<double>>());require(response.ok,"pure material/system accepted-U replay failed");response.state.loadFactor=stored.at("gauge").at("current").at("load_factor");response.state.gravityFactor=stored.at("gauge").at("current").at("gravity_factor");
   work+=materialIncrement(m,old,response.state);externalWork+=externalIncrement(m,old,response.state);auto e=energy(m,response.state);auto row=e.json(work,externalWork);row["sequence"]=stored.at("sequence");row["phase"]=phase;row["control"]=w;report["energy_history"].push_back(row);
   if(!commonDone&&phase=="J"&&w==common){report["common_energy"]=row;report["common_energy"]["origin"]="actual_accepted_state";commonDone=true;}
   if(phase=="J"&&solver.anchorWasJack){solver.previousAcceptedDisplacement=old.displacement;solver.previousAcceptedLambda=old.loadFactor;}else solver.previousAcceptedDisplacement.clear();solver.anchorWasJack=phase=="J";solver.anchor=response.state;solver.anchorResponse=std::move(response);solver.anchorPhase=phase=="J"?Phase::J:phase=="G"?Phase::G:phase=="SG"?Phase::SG:Phase::S0;
   solver.tensileOnsetAccepted=solver.tensileOnsetAccepted||solver.onsetMaximum(solver.anchorResponse,"tensile_onset").ratio>=1;solver.compressionPeakAccepted=solver.compressionPeakAccepted||solver.onsetMaximum(solver.anchorResponse,"compression_peak").ratio>=1;
   std::string id=std::to_string(stored.at("sequence").get<int>());std::string kind=item.at("transitions").value(id,std::string());solver.lastAcceptedTransition=!kind.empty();solver.lastAcceptedCompressionPeak=kind=="compression_peak";
   if(phase=="S0"||phase=="SG")solver.zeroControl=solver.anchor.displacement[m.controlDof];if(phase=="SG"||(phase=="S0"&&!m.definition.gravity.enabled)){solver.referenceReady=true;solver.referenceDisplacement=solver.anchor.displacement;}
   ++count;
  }
  auto actualLast=restoreStep(readJson(item.at("last")));report["replayed_terminal_history_hash"]=fullHash(solver.anchor);report["original_terminal_history_hash"]=fullHash(actualLast);require(report["replayed_terminal_history_hash"]==report["original_terminal_history_hash"],"accepted-U replay did not reproduce complete terminal material histories");require(commonDone&&persisted==0,"missing common coordinate or hidden persistence");report["terminal_energy"]=report["energy_history"].back();report["accepted_states"]=count;report["status"]="PASS";output["cases"].push_back(report);std::cout<<item.at("name")<<" PASS "<<count<<" actual states, both bracket ends, common/terminal energy\n"<<std::flush;
 }
 std::ofstream out(argv[2]);out<<output.dump();return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}


