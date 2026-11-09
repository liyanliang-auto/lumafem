#include "json_codec.hpp"
#include <fstream>
#include <functional>
#include <iostream>
#include <sstream>
using namespace lumafem;
using Json=codec::Json;
Json load(const char* path){std::ifstream f(path);std::stringstream s;s<<f.rdbuf();return codec::parse(s.str());}
void require(bool b,const char* message){if(!b)throw std::runtime_error(message);}
void rehash(Json&j){j.erase("prepared_hash");j["prepared_hash"]=codec::contentHash(j);}
void invalid(const std::function<void()>&f,const std::string& expectedField=""){try{f();}catch(const DomainError&e){if(!expectedField.empty())require(e.diagnostic.field==expectedField,"unexpected diagnostic field");return;}throw std::runtime_error("invalid contract was accepted");}
int main(int argc,char**argv){try{if(argc!=5)throw std::runtime_error("test_a04_codec old01 old020 oldA03 currentDocument");
 auto old=load(argv[1]);auto compatible=codec::decodePrepared(old);require(!compatible.model.revisedPredictorsEnabled,"old01 enabled new predictors");std::cout<<"PASS old01 compatible with predictors disabled\n";
 for(int i=2;i<=3;++i){auto frozen=load(argv[i]);try{codec::decodePrepared(frozen);throw std::runtime_error("prior producer was rerun");}catch(const DomainError&e){require(std::string(e.what()).find("read-only")!=std::string::npos,"exact valid prior producer did not reach read-only decision");}
  auto bad=frozen;bad["nodes"][0]["x"]=bad["nodes"][0]["x"].get<double>()+.01;rehash(bad);invalid([&]{codec::decodePrepared(bad);},"prepared_hash");
  if(i==3){bad=frozen;bad["predictor_policy"]["maximum_force_probes"]=7;rehash(bad);invalid([&]{codec::decodePrepared(bad);},"prepared_hash");}
  std::cout<<"PASS prior producer "<<i<<" original hash/canonical arrays protected then read-only\n";
 }
 auto current=codec::prepareDocument(load(argv[4]));require(current["ok"].get<bool>(),"current input failed preparation");auto p=current["prepared"];require(p["kernel_version"]==codec::kernelVersion&&p["predictor_policy"]["maximum_modal_modes"]==2&&p["predictor_policy"]["maximum_predictor_attempts"]==15,"wrong A04 identity/bounds");require(codec::decodePrepared(p).model.revisedPredictorsEnabled,"current policy disabled");auto bad=p;bad["kernel_version"]="9.9-future";rehash(bad);invalid([&]{codec::decodePrepared(bad);});std::cout<<"PASS current identity and unknown producer rejection\n";
 ConvergenceInfo c;c.totalNewtonEvaluations=39;c.predictorAttempts=2;c.predictorNewtonEvaluations=21;c.predictorKind="critical_mode";c.predictorSeedFactor=.1;c.predictorControlAdvance=.1;c.predictorAmplitude=.01;c.predictorDecompositions=1;c.predictorLinearSolves=1;c.predictorModeIndex=2;
 auto encoded=codec::convergenceToJson(c);require(encoded["predictor_mode_index"]==2&&encoded["total_newton_evaluations"]==39&&encoded["predictor_newton_evaluations"]==21,"actual work evidence changed");
 for(int mode:{0,3}){auto v=c;v.predictorModeIndex=mode;invalid([&]{codec::convergenceToJson(v);});}auto v=c;v.predictorAttempts=16;invalid([&]{codec::convergenceToJson(v);});v=c;v.predictorNewtonEvaluations=40;invalid([&]{codec::convergenceToJson(v);});v=c;v.predictorDecompositions=2;invalid([&]{codec::convergenceToJson(v);});std::cout<<"PASS mode/work/decomposition bounds\n";
 c=ConvergenceInfo{};encoded=codec::convergenceToJson(c);require(encoded["predictor_mode_index"]==0&&encoded["total_newton_evaluations"]==0&&encoded["predictor_seed_factor"].is_null(),"new S0 evidence wrong");std::cout<<"PASS new zero-work state has no successful predictor\n";return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
