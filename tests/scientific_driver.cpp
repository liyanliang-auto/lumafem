// Test-only numerical observer. Its in-memory acknowledgment port is NOT project persistence evidence.
#include "json_codec.hpp"
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <filesystem>
using namespace lumafem;
int main(int argc,char**argv){
 try{
  if(argc==2&&std::string(argv[1])=="--help"){std::cout<<"scientific_driver input.json NEW-output-prefix | --materials NEW-output.csv\n";return 0;}
  if(argc<3)throw std::runtime_error("scientific_driver input.json output-prefix | --materials output.csv");
  if(std::string(argv[1])=="--materials"){
   if(std::filesystem::exists(argv[2]))throw std::runtime_error("Refuse existing material output");
   std::ofstream out(argv[2]);out<<std::setprecision(17)<<"kind,index,strain,stress,tangent,reference,max_reference\n";
   for(int kind=0;kind<2;++kind){MaterialParameters p;p.id="test-material";p.kind=kind?MaterialKind::SteelBilinear:MaterialKind::Concrete02;
    p.fc=30;p.e0=.002;p.fr=6;p.ft=3;p.Gft=.1;p.Gcp=10;p.unloadRatio=.1;p.referenceLength=100;p.crushReferenceStrain=.0035;p.lchT=p.lchC=10;p.E=kind?200000:missing;p.fy=400;p.b=.01;
    MaterialState committed;int index=0;std::vector<double> strains;
    for(int i=0;i<=60;++i)strains.push_back(-.0001*i);
    for(int i=1;i<=64;++i)strains.push_back(-.006+.0001*i);
    for(int i=1;i<=54;++i)strains.push_back(.0004-.0001*i);
    for(double strain:strains){auto r=evaluateMaterial(p,committed,strain);if(!r.ok)throw std::runtime_error("material trial failed");committed=r.state;
     out<<(kind?"steel":"concrete")<<','<<index++<<','<<strain<<','<<r.stress<<','<<r.tangent<<','<<r.state.referenceCompressionStrain<<','<<r.state.maxReferenceCompressionStrain<<'\n';}
   }return 0;
  }
  std::ifstream input(argv[1]);std::stringstream text;text<<input.rdbuf();auto prepared=codec::prepareDocument(codec::parse(text.str()));
  if(!prepared.at("ok").get<bool>())throw std::runtime_error(prepared.dump());
  auto in=codec::decodePrepared(prepared.at("prepared"));std::string prefix=argv[2];
  for(auto suffix:{"-prepared.json","-curve.csv","-summary.json","-transitions.jsonl"})if(std::filesystem::exists(prefix+suffix))throw std::runtime_error("Refuse existing scientific output: "+prefix+suffix);
  {std::ofstream f(prefix+"-prepared.json");f<<prepared.at("prepared").dump();}
  std::ofstream transitionFile(prefix+"-transitions.jsonl");
  std::ofstream acceptedFile(prefix+"-accepted.jsonl");
  AcceptedStep lastStep;
  std::ofstream curve(prefix+"-curve.csv");curve<<std::setprecision(17)<<"sequence,phase,w,P,P1,P2,gravity,max_reference,force_residual,moment_residual,iterations,left_reaction,right_reaction,line_search_evaluations,line_search_backtracks,minimum_correction_factor,max_steel_plastic_strain,max_local_compression,transition_kind\n";
  AnalysisHooks h;h.persist=[&](const AcceptedStep&s){double ref=0;for(auto&e:s.edges)ref=std::max(ref,e.state.maxReferenceCompressionStrain);const char* phase=s.phase==Phase::S0?"S0":s.phase==Phase::G?"G":s.phase==Phase::SG?"SG":"J";
   const auto&su=in.model.definition.supports;double left=0,right=0;for(auto&n:in.model.nodes){if(n.x==su[0].x)left=s.reactions[n.dofs[1]];if(n.x==su[1].x)right=s.reactions[n.dofs[1]];}
   double plastic=0,local=0;for(auto&f:s.fibers){if(f.role=="steel")plastic=std::max(plastic,f.state.accumulatedPlasticStrain);local=std::max(local,-f.state.minStrain);}for(auto&e:s.edges)local=std::max(local,-e.state.minStrain);
   curve<<s.sequence<<','<<phase<<','<<s.controlDisplacement<<','<<s.P<<','<<s.P1<<','<<s.P2<<','<<s.gravityFactor<<','<<ref<<','<<s.convergence.residualForce<<','<<s.convergence.residualMoment<<','<<s.convergence.iterations<<','<<left<<','<<right<<','<<s.convergence.lineSearchEvaluations<<','<<s.convergence.lineSearchBacktracks<<','<<s.convergence.minimumCorrectionFactor<<','<<plastic<<','<<local<<','<<s.transition.kind<<'\n';curve.flush();
   lastStep=s;codec::Json full={{"sequence",s.sequence},{"phase",phase},{"control_displacement",s.controlDisplacement},{"P",s.P},{"displacement",s.displacement},{"convergence",codec::convergenceToJson(s.convergence)}};
   if(s.gauge)full["gauge"]=codec::gaugeStateToJson(*s.gauge);
   full["diagnostic_max_point_reference"]=ref;full["diagnostic_max_local_compression"]=local;
   acceptedFile<<full.dump()<<'\n';acceptedFile.flush();
   if(s.transition.active){transitionFile<<codec::stepToJson(s).dump()<<'\n';transitionFile.flush();}
   PersistedStepReceipt r;r.ok=true;r.identity=s.identity;r.persistedSequence=static_cast<std::int64_t>(s.sequence);r.transactionId="test-observer-not-project-storage";return r;};
  const auto start=std::chrono::steady_clock::now();auto summary=runAnalysis(in.model,in.model.definition.analysis,h);auto j=codec::summaryToJson(summary);
  j["test_observer_only"]=true;j["project_persistence_validated"]=false;j["elapsed_s"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
  {std::ofstream f(prefix+"-last.json");f<<codec::stepToJson(lastStep).dump();}
  {std::ofstream f(prefix+"-summary.json");f<<j.dump(2);}std::cout<<j.at("termination")<<'\n';return summary.termination==Termination::PhysicalGaugeCompression||summary.termination==Termination::ReferenceCompression||summary.termination==Termination::SafetyLimit?0:1;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 2;}
}
