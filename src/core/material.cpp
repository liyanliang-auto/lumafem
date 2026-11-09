#include "lumafem/material.hpp"
#include <algorithm>
#include <cmath>
#include <initializer_list>
#include <utility>

namespace lumafem {
namespace {
// Independent equations for the Yassin/Concrete02 family. Compression reversal
// bounds and the shifted tensile envelope require value-owned full histories.
struct Point { double stress; double tangent; };
struct ConcreteMap {
    double E, tensionSlope, tensionEnd, deltaEnd, compressionSlope, compressionEnd, deltaCritical;
};
Diagnostic problem(const MaterialParameters& p, const std::string& field, const std::string& message) {
    return {"MATERIAL_DOMAIN", "error", "material", p.id, field, message};
}
void require(bool condition, const MaterialParameters& p, const std::string& field, const std::string& message) {
    if (!condition) throw DomainError(problem(p, field, message));
}
bool positive(double value) { return std::isfinite(value) && value > 0.0; }
void checkDerived(double supplied, double computed, const MaterialParameters& p, const char* field) {
    require(std::isnan(supplied) || (std::isfinite(supplied) &&
        std::abs(supplied - computed) <= 1e-10 * std::max(std::abs(computed), 1e-14)),
        p, field, "Generated parameter conflicts with physical inputs and local length.");
}
ConcreteMap concreteMap(const MaterialParameters& p) {
    require(positive(p.fc) && positive(p.e0), p, "fc/e0", "Compression strength and peak strain must be finite and positive.");
    require(std::isfinite(p.fr) && p.fr >= 0.0 && p.fr < p.fc, p, "fr", "Residual strength must satisfy 0 <= fr < fc.");
    require(positive(p.ft) && positive(p.Gft) && positive(p.Gcp), p, "ft/Gft/Gcp", "Strength and energies must be finite and positive.");
    require(positive(p.lchT) && positive(p.lchC) && positive(p.referenceLength),
        p, "lch_t/lch_c/reference_length", "Local lengths and physical gauge must be finite and positive.");
    require(std::isfinite(p.unloadRatio) && p.unloadRatio > 0.0 && p.unloadRatio < 1.0,
        p, "unload_ratio", "Compression unloading ratio must satisfy 0 < ratio < 1.");
    ConcreteMap m{};
    m.E = 2.0 * p.fc / p.e0;
    const double tensionDenominator = 2.0 * p.Gft / p.lchT - p.ft * p.ft / m.E;
    require(positive(m.E) && positive(tensionDenominator), p, "lch_t", "Tensile energy mapping is not single-valued with a finite negative slope.");
    m.tensionSlope = p.ft * p.ft / tensionDenominator;
    m.tensionEnd = p.ft / m.E + p.ft / m.tensionSlope;
    m.deltaEnd = 2.0 * p.Gcp / (p.fc + p.fr);
    const double k = (p.fc - p.fr) / m.deltaEnd;
    const double localDenominator = 1.0 / p.lchC - k / m.E;
    const double referenceDenominator = 1.0 / p.referenceLength - k / m.E;
    require(positive(m.deltaEnd) && positive(localDenominator), p, "lch_c", "Compression energy mapping is not single-valued with a finite negative slope.");
    require(positive(referenceDenominator), p, "reference_length", "Route-C reference strain must increase on the softening branch.");
    require(std::isfinite(p.crushReferenceStrain) && p.crushReferenceStrain > p.e0,
        p, "crush_reference_strain", "Reference criterion must be later than peak compression.");
    m.compressionSlope = k / localDenominator;
    m.compressionEnd = p.e0 + m.deltaEnd / p.lchC - (p.fc - p.fr) / m.E;
    m.deltaCritical = (p.crushReferenceStrain - p.e0) / referenceDenominator;
    require(positive(m.compressionSlope) && std::isfinite(m.compressionEnd) && m.compressionEnd > p.e0,
        p, "eu", "Generated residual strain must be finite and later than peak compression.");
    require(positive(m.deltaCritical) && m.deltaCritical <= m.deltaEnd,
        p, "crush_reference_strain", "Reference criterion lies outside the peak-to-residual displacement interval.");
    checkDerived(p.E0, m.E, p, "E0"); checkDerived(p.E, m.E, p, "E");
    checkDerived(p.Ets, m.tensionSlope, p, "Ets"); checkDerived(p.etu, m.tensionEnd, p, "etu");
    checkDerived(p.deltaU, m.deltaEnd, p, "delta_u"); checkDerived(p.eu, m.compressionEnd, p, "eu");
    checkDerived(p.compressionSlope, m.compressionSlope, p, "compression_slope");
    checkDerived(p.deltaCritical, m.deltaCritical, p, "delta_critical");
    return m;
}
Point compressionEnvelope(const MaterialParameters& p, const ConcreteMap& m, double strain) {
    const double e = -strain;
    if (e <= p.e0) {
        const double r = e / p.e0;
        return {-p.fc * r * (2.0 - r), m.E * (1.0 - r)};
    }
    if (e < m.compressionEnd) return {-p.fc + m.compressionSlope * (e - p.e0), -m.compressionSlope};
    return {-p.fr, 0.0};
}
Point tensionEnvelope(const MaterialParameters& p, const ConcreteMap& m, double excursion) {
    const double cracking = p.ft / m.E;
    if (excursion <= cracking) return {m.E * excursion, m.E};
    if (excursion < m.tensionEnd) return {p.ft - m.tensionSlope * (excursion - cracking), -m.tensionSlope};
    return {0.0, 0.0};
}
void validateHistory(const MaterialParameters& p, const MaterialState& s) {
    for (double value : {s.strain, s.stress, s.tangent, s.minStrain, s.maxStrain,
            s.compressionStress, s.tensionStress, s.tensionEnvelopeStrain,
            s.compressionUnloadStrain, s.compressionUnloadSlope, s.tensionUnloadStrain,
            s.tensionUnloadSlope, s.reversalStrain, s.reversalStress, s.plasticStrain,
            s.backStress, s.accumulatedPlasticStrain, s.maxDeltaC,
            s.maxReferenceCompressionStrain, s.tensionEnergy, s.compressionEnergy})
        require(std::isfinite(value), p, "committed_state", "Material history contains a nonfinite required value.");
    require((std::isfinite(s.deltaC) || std::isnan(s.deltaC)) &&
        (std::isfinite(s.referenceCompressionStrain) || std::isnan(s.referenceCompressionStrain)),
        p, "committed_state", "Optional instantaneous compression coordinates cannot contain infinity.");
    require(s.minStrain <= 0.0 && s.maxStrain >= 0.0 && s.tensionEnvelopeStrain >= 0.0 &&
        s.accumulatedPlasticStrain >= 0.0 && s.maxDeltaC >= 0.0 &&
        s.maxReferenceCompressionStrain >= 0.0 && s.tensionEnergy >= 0.0 && s.compressionEnergy >= 0.0,
        p, "committed_state", "Material extrema or accumulated coordinates are invalid.");
}
void updateDirection(MaterialState& t, const MaterialState& c, double strain) {
    const double increment = strain - c.strain;
    const int direction = increment > 0.0 ? 1 : (increment < 0.0 ? -1 : c.direction);
    if (increment != 0.0 && c.direction != 0 && direction != c.direction) {
        t.reversalStrain = c.strain; t.reversalStress = c.stress;
    }
    t.direction = direction; t.strain = strain;
    t.minStrain = std::min(c.minStrain, strain); t.maxStrain = std::max(c.maxStrain, strain);
}
void evaluateConcrete(const MaterialParameters& p, const MaterialState& c, double strain, MaterialState& t) {
    const ConcreteMap m = concreteMap(p);
    // A repeated accepted state retains its selected one-sided tangent.
    if (strain == c.strain && c.branch != "initial") return;
    const bool newExtreme = strain < c.minStrain;
    const double minimum = std::min(c.minStrain, strain);
    const Point extreme = compressionEnvelope(p, m, minimum);
    double unloadSlope = m.E;
    double intercept = 0.0;
    if (minimum < 0.0) {
        // Fixed reversal pivot on the initial elastic line, determined by the
        // prescribed unloading ratio at the residual compression endpoint.
        const double pivot = (p.unloadRatio * m.E * m.compressionEnd - p.fr) / (m.E * (1.0 - p.unloadRatio));
        const double denominator = minimum - pivot;
        require(std::isfinite(denominator) && denominator != 0.0, p, "unload_ratio", "Compression history reaches a singular unloading pivot.");
        unloadSlope = (extreme.stress - m.E * pivot) / denominator;
        require(positive(unloadSlope), p, "unload_ratio", "Compression history requires a finite positive unloading stiffness.");
        intercept = minimum - extreme.stress / unloadSlope;
    }
    t.compressionStress = extreme.stress; t.compressionUnloadSlope = unloadSlope;
    t.compressionUnloadStrain = intercept; t.tensionUnloadStrain = intercept;
    const Point oldTension = tensionEnvelope(p, m, c.tensionEnvelopeStrain);
    t.tensionUnloadSlope = c.tensionEnvelopeStrain > 0.0 ? oldTension.stress / c.tensionEnvelopeStrain : m.E;
    Point response{};
    if (newExtreme) {
        response = extreme;
        t.branch = -strain <= p.e0 ? "compression_ascending" : (-strain < m.compressionEnd ? "compression_softening" : "compression_residual");
    } else if (strain <= intercept) {
        const double lower = unloadSlope * (strain - intercept);
        const double upper = 0.5 * lower;
        const double predictor = c.stress + m.E * (strain - c.strain);
        if (predictor <= lower) { response = {lower, unloadSlope}; t.branch = "compression_unload_bound"; }
        else if (predictor >= upper) { response = {upper, 0.5 * unloadSlope}; t.branch = "compression_reload_bound"; }
        else { response = {predictor, m.E}; t.branch = "compression_reversal"; }
    } else {
        const double excursion = strain - intercept;
        if (excursion <= c.tensionEnvelopeStrain) {
            response = {t.tensionUnloadSlope * excursion, t.tensionUnloadSlope}; t.branch = "tension_unload_reload";
        } else {
            response = tensionEnvelope(p, m, excursion); t.tensionEnvelopeStrain = excursion;
            t.tensionUnloadSlope = response.stress / excursion;
            t.branch = excursion <= p.ft / m.E ? "tension_elastic" : (excursion < m.tensionEnd ? "tension_softening" : "tension_zero");
        }
    }
    t.stress = response.stress; t.tangent = response.tangent;
    t.tensionStress = tensionEnvelope(p, m, t.tensionEnvelopeStrain).stress;
    t.cracked = c.cracked || t.tensionEnvelopeStrain >= p.ft / m.E;
    // These are truncated monotonic-envelope coordinate integrals, not the
    // full mechanical dissipation of arbitrary cyclic histories.
    const double opening = p.lchT * (t.tensionEnvelopeStrain - t.tensionStress / m.E);
    const double openingEnd = 2.0 * p.Gft / p.ft;
    const double w = std::min(std::max(opening, 0.0), openingEnd);
    t.tensionEnergy = std::max(c.tensionEnergy, p.ft * w - p.ft * w * w / (2.0 * openingEnd));
    const double compression = -minimum;
    double delta = 0.0;
    double reference = compression;
    if (compression > p.e0) {
        delta = p.lchC * ((compression - p.e0) - (-extreme.stress - p.fc) / m.E);
        reference = p.e0 + delta / p.referenceLength + (-extreme.stress - p.fc) / m.E;
    }
    t.maxDeltaC = std::max(c.maxDeltaC, delta);
    t.maxReferenceCompressionStrain = std::max(c.maxReferenceCompressionStrain, reference);
    const double integratedDelta = std::min(delta, m.deltaEnd);
    t.compressionEnergy = std::max(c.compressionEnergy, p.fc * integratedDelta -
        (p.fc - p.fr) * integratedDelta * integratedDelta / (2.0 * m.deltaEnd));
    t.compressionLoading = newExtreme || (strain == minimum && strain < 0.0 && c.compressionLoading);
    if (strain >= 0.0) { t.deltaC = 0.0; t.referenceCompressionStrain = 0.0; }
    else if (t.compressionLoading) { t.deltaC = delta; t.referenceCompressionStrain = reference; }
    else {
        // A monotonic envelope map applied to an unloading stress would create
        // a false physical coordinate. Its instantaneous value is absent.
        t.deltaC = missing; t.referenceCompressionStrain = missing;
    }
    t.crushed = c.crushed || (t.compressionLoading && t.referenceCompressionStrain >= p.crushReferenceStrain);
}
void evaluateSteel(const MaterialParameters& p, const MaterialState& c, double strain, MaterialState& t) {
    require(positive(p.E) && positive(p.fy), p, "E/fy", "Steel modulus and yield strength must be finite and positive.");
    require(std::isfinite(p.b) && p.b >= 0.0 && p.b < 1.0, p, "b", "Steel tangent ratio must satisfy 0 <= b < 1.");
    const double H = p.b * p.E / (1.0 - p.b);
    require(std::isfinite(H), p, "H", "Generated plastic hardening modulus is nonfinite.");
    checkDerived(p.H, H, p, "H");
    if (strain == c.strain && c.branch != "initial") return;
    const double predictor = p.E * (strain - c.plasticStrain);
    const double relative = predictor - c.backStress;
    const double excess = std::abs(relative) - p.fy;
    t.stress = predictor; t.tangent = p.E; t.branch = "steel_elastic";
    if (excess >= 0.0) {
        const double sign = relative >= 0.0 ? 1.0 : -1.0;
        const double increment = excess / (p.E + H);
        t.plasticStrain = c.plasticStrain + sign * increment;
        t.backStress = c.backStress + sign * H * increment;
        t.accumulatedPlasticStrain = c.accumulatedPlasticStrain + increment;
        t.stress = predictor - sign * p.E * increment;
        t.tangent = p.b * p.E; t.yielded = true; t.branch = "steel_plastic";
    }
    t.compressionLoading = false; t.deltaC = missing; t.referenceCompressionStrain = missing;
}
} // namespace

MaterialResponse evaluateMaterial(const MaterialParameters& p, const MaterialState& committed, double strain) {
    MaterialResponse result;
    result.state = committed;
    try {
        require(std::isfinite(strain), p, "strain", "Trial strain must be finite.");
        validateHistory(p, committed);
        MaterialState trial = committed;
        updateDirection(trial, committed, strain);
        switch (p.kind) {
        case MaterialKind::Concrete02: evaluateConcrete(p, committed, strain, trial); break;
        case MaterialKind::SteelBilinear: evaluateSteel(p, committed, strain, trial); break;
        case MaterialKind::Elastic:
            require(positive(p.E), p, "E", "Elastic modulus must be finite and positive.");
            trial.stress = p.E * strain; trial.tangent = p.E; trial.branch = "elastic";
            trial.deltaC = missing; trial.referenceCompressionStrain = missing; trial.compressionLoading = false;
            break;
        default: throw DomainError(problem(p, "kind", "Unknown constitutive family."));
        }
        require(std::isfinite(trial.stress) && std::isfinite(trial.tangent), p, "response", "Stress or consistent tangent is nonfinite.");
        validateHistory(p, trial);
        result.ok = true; result.stress = trial.stress; result.tangent = trial.tangent; result.state = std::move(trial);
    } catch (const DomainError& error) { result.diagnostics.push_back(error.diagnostic); }
    catch (const std::exception& error) { result.diagnostics.push_back(problem(p, "response", error.what())); }
    return result;
}
} // namespace lumafem
