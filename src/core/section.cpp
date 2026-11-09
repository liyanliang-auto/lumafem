#include "lumafem/section.hpp"

#include <cmath>
#include <exception>
#include <initializer_list>
#include <string>
#include <unordered_set>
#include <utility>

namespace lumafem {
namespace {

Diagnostic sectionError(const std::string& code, const Id& object,
                        const std::string& field, const std::string& message) {
    return {code, "error", "section", object, field, message};
}

bool finiteHistory(const MaterialState& state) {
    const std::initializer_list<double> values{
        state.strain, state.stress, state.tangent, state.minStrain, state.maxStrain,
        state.compressionStress, state.tensionStress, state.tensionEnvelopeStrain,
        state.compressionUnloadStrain,
        state.compressionUnloadSlope, state.tensionUnloadStrain, state.tensionUnloadSlope,
        state.reversalStrain, state.reversalStress, state.plasticStrain, state.backStress,
        state.accumulatedPlasticStrain, state.maxDeltaC,
        state.maxReferenceCompressionStrain,
        state.tensionEnergy, state.compressionEnergy};
    for (const double value : values) {
        if (!std::isfinite(value)) return false;
    }
    // An instantaneous route-C quantity is explicitly unavailable on some
    // unloading branches. Its historical maximum still must remain finite.
    return !std::isinf(state.deltaC) && !std::isinf(state.referenceCompressionStrain) &&
           state.direction >= -1 && state.direction <= 1;
}

// This is an output projection only. No state is committed or shared with the caller.
FiberResult pointResult(const PreparedSection& section, const Id& id,
                        const Id& materialId, const std::string& role,
                        const std::string& side, double eta, double zeta, double area,
                        const MaterialParameters& parameters,
                        const MaterialResponse& response, double strain) {
    FiberResult result;
    result.id = id;
    result.sectionId = section.id;
    result.materialId = materialId;
    result.role = role;
    result.side = side;
    result.x = section.x;
    result.eta = eta;
    result.zeta = zeta;
    result.area = area;
    result.strain = strain;
    result.stress = response.stress;
    result.tangent = response.tangent;
    result.lchT = parameters.lchT;
    result.lchC = parameters.lchC;
    result.state = response.state;
    if (parameters.kind == MaterialKind::Concrete02) {
        result.referenceLength = parameters.referenceLength;
        result.referenceModulus = parameters.E0;
        result.referenceCompressionStrain = response.state.referenceCompressionStrain;
        result.referenceThreshold = parameters.crushReferenceStrain;
        result.thresholdRatio = response.state.referenceCompressionStrain /
                                parameters.crushReferenceStrain;
        result.deltaC = response.state.deltaC;
    }
    return result;
}

bool evaluatePoint(const PreparedSection& section, const Id& pointId,
                   const MaterialParameters& parameters, const MaterialState& committed,
                   double strain, MaterialResponse& material,
                   std::vector<Diagnostic>& diagnostics) {
    if (!std::isfinite(strain)) {
        diagnostics.push_back(sectionError("SECTION_STRAIN_NONFINITE", pointId, "strain",
            "The section strain reconstruction is not finite."));
        return false;
    }
    if (!finiteHistory(committed)) {
        diagnostics.push_back(sectionError("SECTION_HISTORY_NONFINITE", pointId, "committed",
            "The committed material history contains invalid numerical values."));
        return false;
    }
    try {
        material = evaluateMaterial(parameters, committed, strain);
    } catch (const DomainError& error) {
        auto diagnostic = error.diagnostic;
        diagnostic.stage = "section";
        diagnostic.objectId = pointId;
        diagnostic.message = "Section " + section.id + ": " + diagnostic.message;
        diagnostics.push_back(std::move(diagnostic));
        return false;
    } catch (const std::exception& error) {
        diagnostics.push_back(sectionError("SECTION_MATERIAL_EXCEPTION", pointId, "material",
            "Section " + section.id + ": " + error.what()));
        return false;
    }
    for (auto diagnostic : material.diagnostics) {
        diagnostic.stage = "section";
        diagnostic.objectId = pointId;
        diagnostic.message = "Section " + section.id + ": " + diagnostic.message;
        diagnostics.push_back(std::move(diagnostic));
    }
    if (!material.ok) {
        if (material.diagnostics.empty()) {
            diagnostics.push_back(sectionError("SECTION_MATERIAL_FAILURE", pointId, "material",
                "The material rejected the section trial without a response."));
        }
        return false;
    }
    if (!std::isfinite(material.stress) || !std::isfinite(material.tangent) ||
        !finiteHistory(material.state)) {
        diagnostics.push_back(sectionError("SECTION_RESPONSE_NONFINITE", pointId, "material",
            "The material trial response or its complete history is not finite."));
        return false;
    }
    if (parameters.kind == MaterialKind::Concrete02 &&
        (!std::isfinite(parameters.referenceLength) || parameters.referenceLength <= 0.0 ||
         !std::isfinite(parameters.E0) || parameters.E0 <= 0.0 ||
         !std::isfinite(parameters.crushReferenceStrain) ||
         parameters.crushReferenceStrain <= 0.0 ||
         (std::isfinite(material.state.referenceCompressionStrain) &&
          !std::isfinite(material.state.referenceCompressionStrain /
                         parameters.crushReferenceStrain)))) {
        diagnostics.push_back(sectionError("SECTION_REFERENCE_INVALID", pointId, "reference",
            "Concrete point reference length, modulus, threshold or ratio is invalid."));
        return false;
    }
    return true;
}

} // namespace

SectionResponse evaluateSection(const PreparedSection& section, const SectionState& committed,
                                double axialStrain, double curvature) {
    SectionResponse failure;
    if (!std::isfinite(axialStrain) || !std::isfinite(curvature)) {
        failure.diagnostics.push_back(sectionError("SECTION_INPUT_NONFINITE", section.id,
            "axial_strain/curvature", "Section generalized strains must be finite."));
        return failure;
    }
    if (section.id.empty() || !std::isfinite(section.x) || section.fibers.empty()) {
        failure.diagnostics.push_back(sectionError("SECTION_DEFINITION_INVALID", section.id,
            "id/x/fibers", "A section requires an identity, finite position and area fibers."));
        return failure;
    }
    // Empty arrays explicitly represent the pristine initial state. A populated array
    // must align exactly, so a remesh cannot silently inherit a prefix of old histories.
    const bool pristine = committed.fibers.empty() && committed.edges.empty();
    if (!pristine && (committed.fibers.size() != section.fibers.size() ||
                      committed.edges.size() != section.edges.size())) {
        failure.diagnostics.push_back(sectionError("SECTION_HISTORY_SHAPE", section.id,
            "committed", "Material history counts do not match the prepared section."));
        return failure;
    }

    std::unordered_set<Id> pointIds;
    for (const auto& fiber : section.fibers) {
        const bool roleValid = fiber.role == "concrete" || fiber.role == "steel" ||
                               fiber.role == "concrete_correction";
        const bool signValid = fiber.role == "concrete_correction" ? fiber.area < 0.0 :
                                                                          fiber.area > 0.0;
        if (fiber.id.empty() || !pointIds.insert(fiber.id).second || fiber.materialId.empty() ||
            fiber.materialId != fiber.material.id || !roleValid || !signValid ||
            !std::isfinite(fiber.area) || !std::isfinite(fiber.eta) ||
            !std::isfinite(fiber.zeta)) {
            failure.diagnostics.push_back(sectionError("SECTION_FIBER_INVALID", fiber.id,
                "fiber", "Fiber identity, material reference, role, signed area or coordinate is invalid."));
            return failure;
        }
    }
    for (const auto& edge : section.edges) {
        if (edge.id.empty() || !pointIds.insert(edge.id).second || edge.material.id.empty() ||
            (edge.side != "top" && edge.side != "bottom") || !std::isfinite(edge.eta)) {
            failure.diagnostics.push_back(sectionError("SECTION_EDGE_INVALID", edge.id,
                "edge", "Edge identity, side, material reference or coordinate is invalid."));
            return failure;
        }
    }

    SectionResponse trial;
    trial.result.id = section.id;
    trial.result.elementId = section.elementId;
    trial.result.gaussIndex = section.gaussIndex;
    trial.result.x = section.x;
    trial.result.axialStrain = axialStrain;
    trial.result.curvature = curvature;
    trial.state.fibers.reserve(section.fibers.size());
    trial.state.edges.reserve(section.edges.size());
    trial.result.fibers.reserve(section.fibers.size());
    trial.result.edges.reserve(section.edges.size());

    long double force = 0.0L;
    long double moment = 0.0L;
    long double k00 = 0.0L;
    long double k01 = 0.0L;
    long double k11 = 0.0L;
    const MaterialState initial;
    for (std::size_t index = 0; index < section.fibers.size(); ++index) {
        const auto& fiber = section.fibers[index];
        const auto& anchor = committed.fibers.empty() ? initial : committed.fibers[index];
        const double strain = axialStrain - fiber.eta * curvature;
        MaterialResponse material;
        if (!evaluatePoint(section, fiber.id, fiber.material, anchor, strain, material,
                           failure.diagnostics)) return failure;
        const long double area = fiber.area;
        const long double eta = fiber.eta;
        const long double forceContribution = static_cast<long double>(material.stress) * area;
        const long double stiffnessContribution = static_cast<long double>(material.tangent) * area;
        force += forceContribution;
        moment -= eta * forceContribution;
        k00 += stiffnessContribution;
        k01 -= eta * stiffnessContribution;
        k11 += eta * eta * stiffnessContribution;
        // Corrections are real independently owned material points at the actual bar
        // coordinates. Their signed areas affect N/M/K; their histories are not borrowed.
        trial.state.fibers.push_back(material.state);
        trial.result.fibers.push_back(pointResult(section, fiber.id, fiber.materialId,
            fiber.role, "", fiber.eta, fiber.zeta, fiber.area, fiber.material, material, strain));
    }
    for (std::size_t index = 0; index < section.edges.size(); ++index) {
        const auto& edge = section.edges[index];
        const auto& anchor = committed.edges.empty() ? initial : committed.edges[index];
        const double strain = axialStrain - edge.eta * curvature;
        MaterialResponse material;
        if (!evaluatePoint(section, edge.id, edge.material, anchor, strain, material,
                           failure.diagnostics)) return failure;
        trial.state.edges.push_back(material.state);
        trial.result.edges.push_back(pointResult(section, edge.id, edge.material.id,
            "edge", edge.side, edge.eta, 0.0, 0.0, edge.material, material, strain));
        // Zero-area monitors deliberately never enter any force or stiffness sum.
    }

    trial.N = static_cast<double>(force);
    trial.M = static_cast<double>(moment);
    trial.tangent = {static_cast<double>(k00), static_cast<double>(k01),
                     static_cast<double>(k01), static_cast<double>(k11)};
    if (!std::isfinite(trial.N) || !std::isfinite(trial.M) ||
        !std::isfinite(trial.tangent[0]) || !std::isfinite(trial.tangent[1]) ||
        !std::isfinite(trial.tangent[2]) || !std::isfinite(trial.tangent[3])) {
        failure.diagnostics.push_back(sectionError("SECTION_INTEGRAL_NONFINITE", section.id,
            "resultants/tangent", "Section resultants or tangent overflowed during integration."));
        return failure;
    }
    trial.result.N = trial.N;
    trial.result.M = trial.M;
    trial.result.tangent = trial.tangent;
    trial.diagnostics = std::move(failure.diagnostics);
    trial.ok = true;
    return trial;
}

} // namespace lumafem
