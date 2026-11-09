#include "lumafem/physical_gauge.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace lumafem {
namespace {
struct EdgePolynomial {
    double left, right, length, axial0, axial1, theta0, theta1, theta2, axialRight, thetaRight;
    double displacement(double x, double eta) const {
        if (x == left) return axial0-eta*theta0;
        if (x == right) return axialRight-eta*thetaRight;
        const double t = (x-left)/length;
        return axial0+t*axial1-eta*(theta0+t*(theta1+t*theta2));
    }
    double strain(double x, double eta) const {
        return (axial1-eta*(theta1+2.0*((x-left)/length)*theta2))/length;
    }
    double slope(double eta) const { return -2.0*eta*theta2/(length*length); }
};
}

GaugeSearchResult evaluatePhysicalGauge(const PreparedModel& model,
    const std::vector<double>& u, const std::function<bool()>& cancelled) {
    GaugeSearchResult result;
    const auto fail = [&](const char* message) {
        result.diagnostics.push_back({"PHYSICAL_GAUGE_INVALID","error","analysis","","compression_criterion",message});
        return result;
    };
    const double L = model.definition.geometry.length;
    const double h = model.definition.geometry.height;
    const double g = model.compressionCriterion.gaugeLength;
    const double threshold = model.compressionCriterion.threshold;
    if (model.compressionCriterion.mode != physicalGaugeMeanMode || !std::isfinite(L) || L <= 0.0 ||
        !std::isfinite(h) || h <= 0.0 || !std::isfinite(g) || g <= 0.0 || g > L ||
        !std::isfinite(threshold) || threshold <= 0.0 || model.nodes.size() < 2 ||
        model.elements.size()+1 != model.nodes.size() || u.size() != model.nodes.size()*3 ||
        model.compressionSearchDomain != std::array<double,2>{{0.0,L-g}})
        return fail("Invalid physical window, domain, geometry or displacement layout.");
    for (double v : u) if (!std::isfinite(v)) return fail("Nonfinite total displacement.");
    std::vector<EdgePolynomial> polynomials;
    std::vector<double> boundaries;
    for (std::size_t i=0; i<model.elements.size(); ++i) {
        if (cancelled && cancelled()) { result.cancelled = true; return result; }
        const auto& e = model.elements[i];
        if (e.nodes[0] != i || e.nodes[1] != i+1) return fail("Noncontiguous straight-beam element connectivity.");
        const double left = model.nodes[i].x, right = model.nodes[i+1].x;
        if (!std::isfinite(left) || !std::isfinite(right) || !(right > left) ||
            e.length != right-left || !std::isfinite(e.cosine) || !std::isfinite(e.sine) ||
            e.cosine != std::cos(model.definition.geometry.angle) ||
            e.sine != std::sin(model.definition.geometry.angle) ||
            (i == 0 && left != 0.0) || (i+1 == model.elements.size() && right != L))
            return fail("Invalid physical beam coordinates or local transformation.");
        for (std::size_t j=0; j<6; ++j)
            if (e.dofs[j] >= u.size() || e.dofs[j] != model.nodes[e.nodes[j/3]].dofs[j%3])
                return fail("Invalid element displacement mapping.");
        const auto& d = e.dofs;
        const double a = e.cosine*u[d[0]]+e.sine*u[d[1]];
        const double b = e.cosine*u[d[3]]+e.sine*u[d[4]];
        const double v1 = -e.sine*u[d[0]]+e.cosine*u[d[1]];
        const double v2 = -e.sine*u[d[3]]+e.cosine*u[d[4]];
        const double dv = (v2-v1)/e.length;
        EdgePolynomial p{left,right,e.length,a,b-a,u[d[2]],
            6.0*dv-4.0*u[d[2]]-2.0*u[d[5]],
            -6.0*dv+3.0*u[d[2]]+3.0*u[d[5]],b,u[d[5]]};
        const double values[] = {p.axial0,p.axial1,p.theta0,p.theta1,p.theta2};
        for (double v : values) if (!std::isfinite(v)) return fail("Nonfinite compatible EB coefficients.");
        polynomials.push_back(p);
        boundaries.push_back(left);
    }
    boundaries.push_back(L);
    std::vector<double> shiftedBoundaries;
    for (double x : boundaries) shiftedBoundaries.push_back(x-g);
    const auto owner = [&](double x) -> const EdgePolynomial& {
        auto it = std::upper_bound(boundaries.begin(),boundaries.end(),x);
        const auto index = static_cast<std::size_t>(it-boundaries.begin()-1);
        return polynomials[std::min(index,polynomials.size()-1)];
    };
    std::vector<double> knots{0.0,L-g};
    for (double x : boundaries) {
        if (x > 0.0 && x < L-g) knots.push_back(x);
        const double shifted = x-g;
        if (shifted > 0.0 && shifted < L-g) knots.push_back(shifted);
    }
    std::sort(knots.begin(),knots.end());
    knots.erase(std::unique(knots.begin(),knots.end()),knots.end());
    bool found = false;
    const auto evaluate = [&](double a, double eta, const char* side) {
        const double b = std::min(L,a+g);
        const double mean = (owner(b).displacement(b,eta)-owner(a).displacement(a,eta))/g;
        const double compression = std::max(0.0,-mean);
        if (!std::isfinite(mean) || !std::isfinite(compression)) return false;
        const auto& old = result.window;
        if (!found || compression > old.compressionMaximum ||
            (compression == old.compressionMaximum &&
             (a < old.x1 || (a == old.x1 && eta > old.eta)))) {
            auto& w = result.window;
            w.gaugeLength = g; w.threshold = threshold; w.searchDomain = {{0.0,L-g}};
            w.side = side; w.eta = eta; w.x1 = a; w.x2 = b;
            w.signedMean = mean; w.compressionMaximum = compression;
            found = true;
        }
        return true;
    };
    for (int edge=0; edge<2; ++edge) {
        const double eta = edge == 0 ? h/2.0 : -h/2.0;
        const char* side = edge == 0 ? "top" : "bottom";
        for (double a : knots) {
            if (cancelled && cancelled()) { result.cancelled = true; return result; }
            if (!evaluate(a,eta,side)) return fail("Nonfinite physical window mean.");
        }
        for (std::size_t i=1; i<knots.size(); ++i) {
            if (cancelled && cancelled()) { result.cancelled = true; return result; }
            const double lo = knots[i-1], hi = knots[i], mid = lo+0.5*(hi-lo);
            // Right-sided ownership at lo identifies the open interval even
            // when no representable midpoint exists between adjacent knots.
            const auto& left = owner(lo);
            const auto shifted = std::upper_bound(shiftedBoundaries.begin(),shiftedBoundaries.end(),lo);
            const auto rightIndex = static_cast<std::size_t>(shifted-shiftedBoundaries.begin()-1);
            const auto& right = polynomials[std::min(rightIndex,polynomials.size()-1)];
            const double slope = right.slope(eta)-left.slope(eta);
            const double derivative = right.strain(mid+g,eta)-left.strain(mid,eta);
            if (!std::isfinite(slope) || !std::isfinite(derivative)) return fail("Nonfinite window derivative.");
            if (slope == 0.0) continue;
            const double root = mid-derivative/slope;
            if (!std::isfinite(root)) return fail("Nonfinite affine derivative root.");
            const double tau = 64.0*std::numeric_limits<double>::epsilon()*
                std::max({1.0,std::abs(lo),std::abs(hi),std::abs(root),L,g});
            if (root >= lo-tau && root <= hi+tau && !evaluate(std::clamp(root,lo,hi),eta,side))
                return fail("Nonfinite stationary window mean.");
        }
    }
    result.ok = found;
    return result;
}
} // namespace lumafem
