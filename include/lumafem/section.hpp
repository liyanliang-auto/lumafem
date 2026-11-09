#pragma once
#include "lumafem/model.hpp"
#include <array>

namespace lumafem {
/// Full value-owned histories aligned with the prepared section arrays.
struct SectionState {
    std::vector<MaterialState> fibers; ///< One history per signed area point.
    std::vector<MaterialState> edges; ///< Independent zero-area edge histories.
};
/// Trial/result material point data with physical source and identity.
struct FiberResult {
    Id id; ///< Point identity.
    Id sectionId; ///< True Gauss section identity.
    Id materialId; ///< Definition reference.
    std::string role; ///< concrete, steel, concrete_correction, or edge.
    std::string side; ///< Edge side when applicable.
    double x = 0.0; ///< True axis position mm.
    double eta = 0.0; ///< Actual height mm.
    double zeta = 0.0; ///< Actual transverse location mm.
    double area = 0.0; ///< Signed area mm2, zero for edges.
    double strain = missing; ///< Cumulative local signed strain.
    double stress = missing; ///< Cumulative local signed stress MPa.
    double tangent = missing; ///< Consistent tangent MPa.
    double lchT = missing; ///< Tensile map length mm.
    double lchC = missing; ///< Compression map length mm.
    double referenceLength = missing; ///< Fixed physical compression gauge mm.
    double referenceModulus = missing; ///< Fixed compression reference E0 MPa.
    double referenceCompressionStrain = missing; ///< Applicable route-C amplitude.
    double referenceThreshold = missing; ///< Route-C threshold amplitude.
    double thresholdRatio = missing; ///< Reference amplitude divided by threshold.
    double deltaC = missing; ///< Equivalent compression displacement mm.
    MaterialState state; ///< Complete trial/history data, copied by value.
};
/// Generalized section result with N/M conjugate to epsilon0/kappa.
struct SectionResult {
    Id id; ///< True Gauss section identity.
    Id elementId; ///< Owning element.
    int gaussIndex = 0; ///< Actual local integration index.
    double x = 0.0; ///< True section axis coordinate mm.
    double axialStrain = 0.0; ///< Cumulative centerline strain.
    double curvature = 0.0; ///< Curvature 1/mm, epsilon=epsilon0-eta*kappa.
    double N = 0.0; ///< Integral sigma dA, N.
    double M = 0.0; ///< Integral -eta*sigma dA, N mm.
    std::array<double,4> tangent{0,0,0,0}; ///< Row-major generalized 2x2 tangent.
    std::vector<FiberResult> fibers; ///< All area points, including signed corrections.
    std::vector<FiberResult> edges; ///< Zero-area independent monitors.
};
/// Candidate section evaluation with no hidden commit.
struct SectionResponse {
    bool ok = false; ///< All points evaluated successfully.
    double N = missing; ///< Resultant axial force N.
    double M = missing; ///< Resultant conjugate moment N mm.
    std::array<double,4> tangent{0,0,0,0}; ///< Row-major section consistent tangent.
    SectionState state; ///< Complete candidate histories.
    SectionResult result; ///< Read-only output copy.
    std::vector<Diagnostic> diagnostics; ///< Located failures.
};
/// Pure trial, fiber strain = axialStrain - eta*curvature.
SectionResponse evaluateSection(const PreparedSection&, const SectionState& committed,
                                double axialStrain, double curvature);
} // namespace lumafem
