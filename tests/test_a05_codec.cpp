#include "json_codec.hpp"
#include <fstream>
#include <iostream>
using namespace lumafem;
using codec::Json;
Json read(const char*path){std::ifstream f(path);Json j;f>>j;return j;}
void rehash(Json&j){j.erase("prepared_hash");j["prepared_hash"]=codec::contentHash(j);}
bool rejects(const Json&j,bool analysis=false){try{codec::decodePrepared(j,analysis);return false;}catch(const DomainError&){return true;}}
int main(int argc,char**argv){try{
 if(argc!=3)throw std::runtime_error("old030 currentDocument required");
 auto old=read(argv[1]);auto current=codec::prepareDocument(read(argv[2])).at("prepared");auto view=codec::decodePrepared(old,false);
 auto oldMixed=old;oldMixed["predictor_policy"]=current["predictor_policy"];rehash(oldMixed);
 auto oldArray=old;oldArray["nodes"][0]["x"]=oldArray["nodes"][0]["x"].get<double>()+.01;rehash(oldArray);
 auto newPolicy=current;newPolicy["predictor_policy"]["maximum_force_probes"]=7;rehash(newPolicy);
 Json out={{"producer",codec::kernelVersion},{"old030_verified_view_only",view.analysisReadOnly},{"old030_analysis_rejected",rejects(old,true)},{"old030_self_rehashed_V3_policy_mix_rejected",rejects(oldMixed)},{"old030_self_rehashed_array_mutation_rejected",rejects(oldArray)},{"new_self_rehashed_policy_mutation_rejected",rejects(newPolicy)},{"new_analysis_enabled",!codec::decodePrepared(current).analysisReadOnly}};
 std::cout<<out.dump(2);for(auto it=out.begin();it!=out.end();++it)if(it.value().is_boolean()&&!it.value().get<bool>())return 1;return 0;
 }catch(const std::exception&e){std::cerr<<e.what();return 2;}}
