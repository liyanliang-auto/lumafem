#include "lumafem/physical_gauge.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace lumafem;
void require(bool b,const char* s){if(!b)throw std::runtime_error(s);}
void near(double a,double b,double tol,const char*s){require(std::isfinite(a)&&std::abs(a-b)<=tol,s);}
// Manufactured curvature is triangular, integrated analytically twice. These
// antiderivatives are independent of the product's Hermite/root enumeration.
constexpr double peak=433.7, height=.001, ml=2e-6, mr=1e-6;
double K(double x){return x<=peak?(height-ml*peak)*x+ml*x*x/2:
 K(peak)+height*(x-peak)-mr*(x-peak)*(x-peak)/2;}
double V(double x){if(x<=peak)return (height-ml*peak)*x*x/2+ml*x*x*x/6;
 double t=x-peak;return V(peak)+K(peak)*t+height*t*t/2-mr*t*t*t/6;}
PreparedModel mesh(std::vector<double> xs,double g=100,double angle=0){
 PreparedModel p;p.definition.geometry={1000,20,40,angle};p.compressionCriterion.mode=physicalGaugeMeanMode;
 p.compressionCriterion.gaugeLength=g;p.compressionCriterion.threshold=.0035;p.compressionSearchDomain={0,1000-g};
 for(size_t i=0;i<xs.size();++i){PreparedNode n;n.x=xs[i];n.dofs={3*i,3*i+1,3*i+2};p.nodes.push_back(n);}
 for(size_t i=1;i<xs.size();++i){PreparedElement e;e.nodes={i-1,i};e.length=xs[i]-xs[i-1];e.cosine=std::cos(angle);e.sine=std::sin(angle);e.dofs={3*i-3,3*i-2,3*i-1,3*i,3*i+1,3*i+2};p.elements.push_back(e);}return p;}
std::vector<double> field(const PreparedModel&p,double axial,double bend,double rigid=0){
 std::vector<double> u;double c=std::cos(p.definition.geometry.angle),s=std::sin(p.definition.geometry.angle);
 for(auto&n:p.nodes){double a=axial*n.x+7, v=bend*V(n.x)+rigid*n.x+3;
 u.insert(u.end(),{c*a-s*v,s*a+c*v,bend*K(n.x)+rigid});}return u;}
int main(){try{
 int count=0;auto p=mesh({0,peak,1000});
 // Exactly zero, including strict deterministic flat ties.
 {auto r=evaluatePhysicalGauge(p,std::vector<double>(p.nodes.size()*3,0));require(r.ok&&r.window.x1==0&&r.window.side=="top","flat exact tie");near(r.window.compressionMaximum,0,0,"zero field");++count;}
 for(double curvature:{-5e-5,5e-5}){auto q=mesh({0,17.2,433.7,798.1,1000});std::vector<double>u;for(auto&n:q.nodes)u.insert(u.end(),{0,.5*curvature*n.x*n.x,curvature*n.x});auto r=evaluatePhysicalGauge(q,u);near(r.window.compressionMaximum,.001,1e-12,"pure constant bending integral");require(r.window.side==(curvature>0?"top":"bottom"),"pure bending side");++count;}
 {auto q=mesh({0,119.2,650.3,1000});std::vector<double>u;for(auto&n:q.nodes)u.insert(u.end(),{0,1e-7*n.x*n.x*n.x/6,.5e-7*n.x*n.x});auto r=evaluatePhysicalGauge(q,u);near(r.window.x1,900,1e-8,"whole-beam endpoint maximum");near(r.window.compressionMaximum,20*1e-7*950,1e-12,"linear strain exact integral");++count;}
 {auto q=mesh({0,125,250,750,875,1000},125);std::vector<double>u(18,0);double axial[]={0,0,-.125,-.125,-.25,-.25};for(int i=0;i<6;++i)u[3*i]=axial[i];auto r=evaluatePhysicalGauge(q,u);near(r.window.compressionMaximum,.001,1e-12,"two disjoint physical bands");require(r.window.x1==125&&r.window.side=="top","equal band maxima tie");for(int n:{2,3,5}){q.sections.resize(n);q.definition.observations.gauges={{"arbitrary-display",12,15,-20}};auto changed=evaluatePhysicalGauge(q,u);near(changed.window.compressionMaximum,r.window.compressionMaximum,1e-12,"Gauss/display sensors changed criterion");++count;}++count;}
 {auto q=mesh({0,400,1000},1000);std::vector<double>u(9,0);u[3]=-.4;u[6]=.2;auto r=evaluatePhysicalGauge(q,u);near(r.window.signedMean,.0002,1e-12,"signed mixed tension/compression integral");near(r.window.compressionMaximum,0,0,"must average signed field before extracting compression");++count;}
 for(double axial:{0.,.001,-.001}){auto r=evaluatePhysicalGauge(p,field(p,axial,0));require(r.ok,"uniform field rejected");near(r.window.compressionMaximum,std::max(0.,-axial),1e-12,"uniform strain oracle");++count;}
 const double optimum=peak-mr*100/(ml+mr), mean=(K(optimum+100)-K(optimum))/100;
 for(double angle:{0.,.731})for(double sign:{-1.,1.})for(bool refined:{false,true}){
  auto q=refined?mesh({0,71.3,230.1,peak,611.2,899.9,1000},100,angle):mesh({0,peak,1000},100,angle);
  auto r=evaluatePhysicalGauge(q,field(q,0,sign,.013));require(r.ok,"bending field rejected");
  near(r.window.compressionMaximum,20*mean,1e-12,"analytically integrated triangular curvature mean");
  near(r.window.x1,optimum,1e-8,"isolated non-grid stationary maximum");require(r.window.side==(sign>0?"top":"bottom"),"wrong edge");++count;}
 for(double g:{100.,1000.}){auto q=mesh({0,peak,1000},g);auto r=evaluatePhysicalGauge(q,field(q,.03,1));require(r.ok,"signed cancellation rejected");near(r.window.compressionMaximum,0,1e-12,"signed average before compression extraction");++count;}
 auto q=mesh({0,peak,1000},1000);auto r=evaluatePhysicalGauge(q,field(q,0,1));near(r.window.x1,0,0,"full length domain");near(r.window.compressionMaximum,20*std::abs(K(1000))/1000,1e-12,"full length integrated oracle");++count;
 auto u=field(p,0,1);require(evaluatePhysicalGauge(p,u,[]{return true;}).cancelled,"cancel ignored");++count;
 for(int bad=0;bad<5;++bad){auto x=p;auto v=u;if(bad==0)x.compressionCriterion.gaugeLength=0;if(bad==1)x.compressionSearchDomain={0,1};if(bad==2)v.pop_back();if(bad==3)v[0]=std::numeric_limits<double>::infinity();if(bad==4)x.elements[0].dofs[0]=99;require(!evaluatePhysicalGauge(x,v).ok,"invalid operator input accepted");++count;}
 std::cout<<"PASS "<<count<<" independent analytic/manufactured/boundary cases\n";return 0;
 }catch(std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
