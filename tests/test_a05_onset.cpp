#include "json_codec.hpp"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <cmath>
#include "analysis_access.inc"
using namespace lumafem;
MaterialState restore(const codec::Json& j){MaterialState q;q.strain=j.at("strain").get<decltype(q.strain)>();
q.stress=j.at("stress").get<decltype(q.stress)>();
q.tangent=j.at("tangent").get<decltype(q.tangent)>();
q.minStrain=j.at("min_strain").get<decltype(q.minStrain)>();
q.maxStrain=j.at("max_strain").get<decltype(q.maxStrain)>();
q.compressionStress=j.at("compression_stress").get<decltype(q.compressionStress)>();
q.tensionStress=j.at("tension_stress").get<decltype(q.tensionStress)>();
q.tensionEnvelopeStrain=j.at("tension_envelope_strain").get<decltype(q.tensionEnvelopeStrain)>();
q.compressionUnloadStrain=j.at("compression_unload_strain").get<decltype(q.compressionUnloadStrain)>();
q.compressionUnloadSlope=j.at("compression_unload_slope").get<decltype(q.compressionUnloadSlope)>();
q.tensionUnloadStrain=j.at("tension_unload_strain").get<decltype(q.tensionUnloadStrain)>();
q.tensionUnloadSlope=j.at("tension_unload_slope").get<decltype(q.tensionUnloadSlope)>();
q.reversalStrain=j.at("reversal_strain").get<decltype(q.reversalStrain)>();
q.reversalStress=j.at("reversal_stress").get<decltype(q.reversalStress)>();
q.plasticStrain=j.at("plastic_strain").get<decltype(q.plasticStrain)>();
q.backStress=j.at("back_stress").get<decltype(q.backStress)>();
q.accumulatedPlasticStrain=j.at("accumulated_plastic_strain").get<decltype(q.accumulatedPlasticStrain)>();
q.deltaC=j.at("delta_c").get<decltype(q.deltaC)>();
q.maxDeltaC=j.at("max_delta_c").get<decltype(q.maxDeltaC)>();
q.referenceCompressionStrain=j.at("reference_compression_strain").get<decltype(q.referenceCompressionStrain)>();
q.maxReferenceCompressionStrain=j.at("max_reference_compression_strain").get<decltype(q.maxReferenceCompressionStrain)>();
q.tensionEnergy=j.at("tension_energy").get<decltype(q.tensionEnergy)>();
q.compressionEnergy=j.at("compression_energy").get<decltype(q.compressionEnergy)>();
q.compressionLoading=j.at("compression_loading").get<decltype(q.compressionLoading)>();
q.cracked=j.at("cracked").get<decltype(q.cracked)>();
q.yielded=j.at("yielded").get<decltype(q.yielded)>();
q.crushed=j.at("crushed").get<decltype(q.crushed)>();
q.direction=j.at("direction").get<decltype(q.direction)>();
q.branch=j.at("branch").get<decltype(q.branch)>(); return q;}
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

void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
int main(int argc,char**argv){try{
 bool tension=false; double mesh=std::stod(argv[1]); std::string prefix=""; std::string mapping="element_length_v2";
 bool search=true,onset=true,physical=true,tight=std::stoi(argv[3])!=0; double stepScale=std::stod(argv[2]); int gauss=3;
 double totalLength=400,weakCentre=194.8,weakWidth=100;
  ModelDefinition d;d.name="fixed physical weak-zone bar";d.identity={"localization-test","revision","snapshot","run",""};d.geometry={totalLength,10,10,0};
 MaterialParameters p;p.id="concrete";p.fc=30;p.e0=.002;p.fr=6;p.ft=3;p.Gft=.1;p.Gcp=10;p.unloadRatio=.1;p.referenceLength=100;p.crushReferenceStrain=.0035;
 d.materials={p};d.concreteMaterialId=p.id;d.reinforcement.mode="direct";d.discretization={"custom",mesh,gauss,2,mapping};
 d.supports={{"left",0,{true,true,true},{0,0,0}},{"right",totalLength,{false,true,true},{0,0,0}}};d.loads={{"axial",totalLength,{tension?100.:-100.,0,0}}};d.controlX=totalLength;d.controlComponent=0;d.controlDirection=tension?1:-1;d.observations.referencePhase="S0";
 d.analysis.initialStep=tension?.0005:.005;d.analysis.maximumStep=tension?.005:.02;d.analysis.maximumControlDisplacement=tension?.5:5.;d.analysis.maximumAcceptedSteps=4000;
 d.analysis.initialStep*=stepScale;d.analysis.maximumStep*=stepScale;d.analysis.lineSearchEnabled=search;d.analysis.transitionResolutionEnabled=onset;
 if(search||onset){d.analysis.maximumIterations=60;d.analysis.maximumHalvings=18;}
 if(physical&&!tension){CompressionCriterionDefinition cc;cc.mode=physicalGaugeMeanMode;cc.gaugeLength=100;cc.threshold=.0035;d.compressionCriterion=cc;}
 if(tight){d.analysis.residualAbsolute*=.1;d.analysis.residualRelative*=.1;d.analysis.correctionAbsolute*=.1;d.analysis.correctionRelative*=.1;d.analysis.controlAbsolute*=.1;d.analysis.controlRelative*=.1;}
 auto prep=prepareModel(d);if(!prep.ok)throw std::runtime_error("bar preparation failed");auto&m=prep.model;

 for(auto&s:m.sections){double xx=(s.x-weakCentre)/(weakWidth/2);double factor=std::abs(xx)<1?1-.1*std::pow(1-xx*xx,2):1.;
  auto update=[&](MaterialParameters&q){q.fc*=factor;q.fr*=factor;q.ft*=factor;q.E0=2*q.fc/q.e0;q.E=missing;q.Ets=q.ft*q.ft/(2*q.Gft/q.lchT-q.ft*q.ft/q.E0);q.etu=q.ft/q.E0+q.ft/q.Ets;q.deltaU=2*q.Gcp/(q.fc+q.fr);double k=(q.fc-q.fr)/q.deltaU;q.eu=q.e0+q.deltaU/q.lchC-(q.fc-q.fr)/q.E0;q.compressionSlope=k/(1/q.lchC-k/q.E0);q.deltaCritical=(q.crushReferenceStrain-q.e0)/(1/q.referenceLength-k/q.E0);};
  for(auto&f:s.fibers)update(f.material);for(auto&e:s.edges)update(e.material);
 }


 int calls=0,cancelAfter=1000000,persistCalls=0;AnalysisHooks hooks;hooks.cancelled=[&](){return ++calls>=cancelAfter;};hooks.persist=[&](const AcceptedStep&){++persistCalls;return PersistedStepReceipt{};};
 Analysis solver(m,m.definition.analysis,hooks);require(solver.validate(),"fixture validation");codec::Json saved;std::ifstream file(argv[4]);file>>saved;
 solver.anchor.displacement=saved.at("displacement").get<std::vector<double>>();solver.anchor.loadFactor=saved.at("load_factor");solver.anchor.gravityFactor=saved.at("gravity_factor");
 for(const auto&section:saved.at("sections")){SectionState state;for(const auto&f:section.at("fibers"))state.fibers.push_back(restore(f.at("state")));for(const auto&f:section.at("edges"))state.edges.push_back(restore(f.at("state")));solver.anchor.sections.push_back(state);}
 solver.anchorResponse=evaluateSystem(m,solver.anchor,solver.anchor.displacement);require(solver.anchorResponse.ok,"original anchor assembly");
 const auto hash=fullHash(solver.anchor);const double u=solver.control(solver.anchor.displacement),target=u+.00001953125;
 require(solver.onsetMaximum(solver.anchorResponse,"compression_peak").ratio<1,"fixture is not pre-CPE");
 auto ordinary=solver.solveNewton(solver.anchor.gravityFactor,true,target,0,"unit_ordinary");require(!ordinary.ok,"historical target unexpectedly converged ordinarily; update causal fixture review");
 const int ordinaryCalls=calls;calls=0;
 auto repaired=solver.solve(solver.anchor.gravityFactor,true,target,0,"unit_A05");
 require(repaired.ok&&!repaired.cancelled,"bounded current production onset repair failed");
 require(repaired.evidence.residualPassed&&repaired.evidence.correctionPassed&&repaired.evidence.controlPassed,"seed bypassed original joint criteria");
 require(repaired.evidence.iterations>=1&&repaired.evidence.predictorKind=="force_unloading","missing actual corrected force-probe evidence");
 require(repaired.evidence.predictorLinearSolves==1&&repaired.evidence.predictorDecompositions==0&&repaired.evidence.predictorAttempts<=6,"prepeak work bounds changed");
 require(repaired.evidence.totalNewtonEvaluations>=repaired.evidence.predictorNewtonEvaluations&&repaired.evidence.totalNewtonEvaluations<=554,"actual bounded evaluation accounting");
 require(solver.onsetMaximum(repaired.response,"compression_peak").ratio>=1,"disposable result did not cross peak");
 require(fullHash(solver.anchor)==hash&&!solver.compressionPeakAccepted&&persistCalls==0,"trial promoted history/onset/storage");
 auto distant=solver.solve(solver.anchor.gravityFactor,true,u+.0001,0,"unit_boundary");
 require(distant.evidence.predictorAttempts==0&&distant.evidence.predictorLinearSolves==0,"prepeak rescue ran before ordinary cutback boundary");
 calls=0;cancelAfter=ordinaryCalls+2;auto cancelled=solver.solve(solver.anchor.gravityFactor,true,target,0,"unit_cancel_base");
 require(cancelled.cancelled&&!cancelled.ok,"post-base cancellation lost");
 require(cancelled.evidence.predictorNewtonEvaluations==1&&cancelled.evidence.totalNewtonEvaluations==ordinary.evidence.totalNewtonEvaluations+1,"base nonlinear assembly not accounted exactly");
 require(std::isnan(cancelled.evidence.predictorSeedFactor)&&cancelled.evidence.predictorKind.empty(),"cancelled trial has successful seed metadata");
 require(fullHash(solver.anchor)==hash&&!solver.compressionPeakAccepted&&persistCalls==0,"cancelled trial promoted history/onset/storage");
 std::cout<<"PASS pre-CPE original-anchor recovery/bounds/cutback guard/cancel/fullhistory "<<mesh<<" "<<stepScale<<" "<<tight<<'\n';return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
