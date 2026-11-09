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


void require(bool b,const char*m){if(!b)throw std::runtime_error(m);}
int main(int argc,char**argv){try{std::ifstream file(argv[1]);codec::Json cases;file>>cases;codec::Json reconstructed=codec::Json::array();
 for(const auto&fixture:cases){auto decoded=codec::decodePrepared(fixture.at("prepared"));auto&m=decoded.model;AnalysisHooks hooks;Analysis solver(m,m.definition.analysis,hooks);require(solver.validate(),"fixture invalid");const auto&s=fixture.at("anchor");
 solver.anchor.displacement=s.at("displacement").get<std::vector<double>>();solver.anchor.loadFactor=s.at("load_factor");solver.anchor.gravityFactor=s.at("gravity_factor");
 for(const auto&section:s.at("sections")){SectionState state;for(const auto&f:section.at("fibers"))state.fibers.push_back(restore(f.at("state")));for(const auto&f:section.at("edges"))state.edges.push_back(restore(f.at("state")));solver.anchor.sections.push_back(state);}
 solver.anchorResponse=evaluateSystem(m,solver.anchor,solver.anchor.displacement);require(solver.anchorResponse.ok,"anchor invalid");const auto hash=fullHash(solver.anchor);const auto&loc=fixture.at("summary").at("localization");bool jack=fixture.at("terminal").at("phase")=="J";
 for(const char*end:{"lower","upper"}){std::string key=end;double gravity=jack?solver.anchor.gravityFactor:loc.at(key+"_gravity_factor").get<double>();double parameter=jack?loc.at(key+"_control").get<double>():0;
 auto c=solver.solve(gravity,jack,parameter,0,"unit_independent_bracket_reconstruction");require(c.ok,"recorded bracket end could not be re-solved from same full anchor");require(c.evidence.residualPassed&&c.evidence.correctionPassed&&c.evidence.controlPassed,"reconstructed end violated original joint criteria");auto independentlyRecomputed=evaluatePhysicalGauge(m,c.response.state.displacement);require(independentlyRecomputed.ok,"global search invalid");reconstructed.push_back({{"prepared",fixture.at("prepared")},{"phase",jack?"J":"G"},{"end",key},{"displacement",c.response.state.displacement},{"recorded_window",loc.at(key+"_gauge")}});double recorded=loc.at(key+"_gauge").at("compression_maximum");require(std::abs(independentlyRecomputed.window.compressionMaximum-recorded)<=1e-12,"bracket recorded scalar mismatched same-anchor converged displacement");
 require((key=="lower"?independentlyRecomputed.window.compressionMaximum<m.compressionCriterion.threshold:independentlyRecomputed.window.compressionMaximum>=m.compressionCriterion.threshold),"threshold bracket sign changed");require(fullHash(solver.anchor)==hash&&!solver.compressionPeakAccepted,"re-solving bracket promoted state");}
 }
 if(argc>2){if(std::filesystem::exists(argv[2]))throw std::runtime_error("Refuse existing bracket reconstruction output");std::ofstream out(argv[2]);out<<reconstructed.dump();}
 std::cout<<"PASS both converged J/G bracket ends reconstructed from actual complete immutable accepted anchors\n";return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
