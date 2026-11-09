#pragma once

#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace lumafem {
/// Stable identity; never interpret an ID as an array offset.
using Id = std::string;
/// Explicit absence for optional numerical outputs; adapters encode as null.
inline constexpr double missing = std::numeric_limits<double>::quiet_NaN();
/// Structured diagnostic valid at every domain boundary.
struct Diagnostic {
    std::string code; ///< Stable machine-readable code.
    std::string severity = "error"; ///< error, warning, or information.
    std::string stage; ///< Preparation, material, section, system, analysis, or IO.
    Id objectId; ///< Object carrying the error.
    std::string field; ///< Editable input or output path.
    std::string message; ///< Actionable description.
};
/// Exception preserving a structured diagnostic when a value cannot be returned.
class DomainError : public std::runtime_error {
public:
    Diagnostic diagnostic; ///< Original boundary diagnostic.
    explicit DomainError(Diagnostic value)
        : std::runtime_error(value.message), diagnostic(std::move(value)) {}
};
/// Input provenance independent of material-point history.
struct Provenance {
    std::string category = "assumption"; ///< paper, assumption, calibrated, measured, synthetic, derived.
    std::string reference; ///< Source identity or explanatory note.
    std::string quality; ///< Uncertainty or quality flag.
    std::string version; ///< Source/processing version.
};
/// Constitutive family implemented by the independent kernel.
enum class MaterialKind { Concrete02, SteelBilinear, Elastic };
/// Physical inputs and generated local parameters; compression inputs are positive amplitudes.
struct MaterialParameters {
    Id id; ///< Material package identity.
    std::string version = "1"; ///< Constitutive definition version.
    MaterialKind kind = MaterialKind::Concrete02; ///< Selected model family.
    Provenance source; ///< Package-level source information.
    double fc = missing; ///< Concrete peak compression MPa.
    double e0 = missing; ///< Positive peak compression strain.
    double fr = missing; ///< Residual compression MPa.
    double ft = missing; ///< Tensile strength MPa.
    double Gft = missing; ///< Tensile stress-opening energy N/mm.
    double Gcp = missing; ///< Integral q d(deltaC) to residual point N/mm.
    double unloadRatio = missing; ///< Concrete unloading ratio, distinct from load factor.
    double referenceLength = missing; ///< Fixed physical compression gauge mm.
    double crushReferenceStrain = missing; ///< Positive route-C termination threshold.
    double E = missing; ///< Elastic or steel modulus MPa; concrete E0 is derived.
    double fy = missing; ///< Steel yield stress MPa.
    double b = missing; ///< Steel postyield tangent divided by E.
    double lchT = missing; ///< Local tensile characteristic length mm.
    double lchC = missing; ///< Local compression characteristic length mm.
    double E0 = missing; ///< Derived concrete initial modulus MPa.
    double Ets = missing; ///< Derived tensile softening slope magnitude MPa.
    double etu = missing; ///< Derived tensile zero-stress total strain.
    double deltaU = missing; ///< Compression inelastic displacement at residual point mm.
    double eu = missing; ///< Derived positive local residual-point strain.
    double compressionSlope = missing; ///< Derived compression softening slope magnitude MPa.
    double deltaCritical = missing; ///< Derived route-C event displacement mm.
    double H = missing; ///< Derived steel kinematic plastic modulus MPa.
};
/// Complete value-owned history at a single physical material point.
struct MaterialState {
    double strain = 0.0; ///< Current signed total strain, tension positive.
    double stress = 0.0; ///< Current signed stress MPa.
    double tangent = 0.0; ///< Current algorithmic tangent MPa.
    double minStrain = 0.0; ///< Most compressive accepted strain.
    double maxStrain = 0.0; ///< Largest tensile accepted strain.
    double compressionStress = 0.0; ///< Stress at compression extreme.
    double tensionStress = 0.0; ///< Stress at tension extreme.
    double tensionEnvelopeStrain = 0.0; ///< Largest tensile envelope excursion from the compression-unloading zero intercept.
    double compressionUnloadStrain = 0.0; ///< Compression unloading zero intercept.
    double compressionUnloadSlope = 0.0; ///< Compression unloading stiffness.
    double tensionUnloadStrain = 0.0; ///< Tension unloading zero intercept.
    double tensionUnloadSlope = 0.0; ///< Tension unloading stiffness.
    double reversalStrain = 0.0; ///< Last reversal total strain.
    double reversalStress = 0.0; ///< Last reversal stress.
    double plasticStrain = 0.0; ///< Steel signed plastic strain.
    double backStress = 0.0; ///< Steel kinematic backstress MPa.
    double accumulatedPlasticStrain = 0.0; ///< Steel accumulated plastic strain.
    double deltaC = 0.0; ///< Current monotonic-envelope compression displacement mm.
    double maxDeltaC = 0.0; ///< Largest historical compression displacement mm.
    double referenceCompressionStrain = 0.0; ///< Current applicable route-C reference amplitude.
    double maxReferenceCompressionStrain = 0.0; ///< Maximum valid historical reference amplitude.
    double tensionEnergy = 0.0; ///< Truncated tensile stress-opening energy N/mm.
    double compressionEnergy = 0.0; ///< Truncated integral q d(deltaC) N/mm.
    bool compressionLoading = false; ///< Current state advances compression envelope.
    bool cracked = false; ///< Historical first cracking flag.
    bool yielded = false; ///< Historical steel plasticity flag.
    bool crushed = false; ///< Historical route-C threshold flag.
    int direction = 0; ///< Last nonzero strain increment sign.
    std::string branch = "initial"; ///< Current constitutive branch, separate from historical events.
};
/// Pure trial result: caller owns state; committed input remains unchanged.
struct MaterialResponse {
    bool ok = false; ///< Whether every value is valid.
    double stress = missing; ///< Signed trial stress MPa.
    double tangent = missing; ///< Consistent trial tangent MPa.
    MaterialState state; ///< Complete candidate history.
    std::vector<Diagnostic> diagnostics; ///< Failure evidence.
};
/// Evaluate one candidate from an immutable committed anchor.
MaterialResponse evaluateMaterial(const MaterialParameters&, const MaterialState& committed, double strain);
} // namespace lumafem
