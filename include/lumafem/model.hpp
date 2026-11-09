#pragma once
#include "lumafem/material.hpp"
#include <array>
#include <cstddef>
#include <optional>

namespace lumafem {
inline constexpr std::size_t criticalModeEquationLimit = 384; ///< Bounded dense initial-guess work, not a model size limit.
inline constexpr int criticalModeCount = 2; ///< A04 causally required weak directions; one shared decomposition.
inline constexpr std::array<double,4> criticalModeStepFactors{{0.1,-0.1,1.0,-1.0}}; ///< Fixed signed amplitudes times maximumStep.
inline constexpr const char* routeCPointMode = "route_c_point_v1"; ///< Immutable legacy point/history criterion.
inline constexpr const char* physicalGaugeMeanMode = "physical_gauge_mean_v1"; ///< Compatible total-strain window criterion.
inline constexpr const char* physicalGaugeOperatorVersion = "eb_compatible_edge_window_v1"; ///< Exact continuous EB search.
inline constexpr const char* physicalGaugeReference = "total_from_S0"; ///< Includes gravity, independent of display zeros.
inline constexpr const char* physicalGaugeRoundoffPolicy = "bounded_affine_root_v1"; ///< Bounded roots; see physical_gauge.hpp.
/// Explicit criterion source; absence in ModelDefinition preserves old material reference inputs.
struct CompressionCriterionDefinition {
    std::string mode = routeCPointMode; ///< Supported explicit criterion identifier.
    double gaugeLength = missing; ///< Physical mm; legacy resolves material referenceLength.
    double threshold = missing; ///< Positive dimensionless amplitude; legacy resolves referenceThreshold.
    Provenance source; ///< Assumption/source of the criterion, never measured by default.
};
/// Input/output identity independent of array layout.
struct Identity {
    Id projectId; ///< Engineering project.
    Id revisionId; ///< Immutable model revision.
    Id snapshotId; ///< Prepared input identity.
    Id runId; ///< Analysis invocation.
    Id stepId; ///< Accepted step, empty for input snapshots.
};
/// Frozen numerical configuration; units N, mm, MPa, rad.
struct AnalysisOptions {
    double initialStep = 0.02; ///< Downward control increment mm.
    double minimumStep = 0.00001; ///< Smallest permitted increment mm.
    double maximumStep = 0.10; ///< Largest permitted increment mm.
    int maximumIterations = 30; ///< Full Newton iterations per candidate.
    int maximumHalvings = 12; ///< Rejected-candidate reductions per step.
    int growthConsecutive = 3; ///< Consecutive easy accepted steps before growth.
    int growthIterationLimit = 5; ///< Easy-step iteration upper bound.
    double growthFactor = 1.25; ///< Increment growth multiplier.
    int gravitySteps = 10; ///< Equal self-weight increments.
    std::size_t maximumAcceptedSteps = 20000; ///< Whole-run accepted step safety limit.
    double maximumControlDisplacement = 50.0; ///< Relative-to-SG downward limit mm.
    int maximumLocalizationSolves = 30; ///< Converged event-bracket candidate budget.
    double referenceLength = 0.0; ///< Force-moment scaling length mm; preparation fills span.
    double referenceDisplacement = 1.0; ///< Translation scale lower bound mm.
    double referenceForce = 1000.0; ///< Residual force scale lower bound N.
    double residualAbsolute = 0.001; ///< Translational residual tolerance N.
    double residualRelative = 1e-6; ///< Residual relative tolerance.
    double correctionAbsolute = 1e-8; ///< Translational Newton correction tolerance mm.
    double correctionRelative = 1e-6; ///< Correction relative tolerance.
    double controlAbsolute = 1e-8; ///< Control constraint tolerance mm.
    double controlRelative = 1e-6; ///< Relative tolerance times target increment.
    double backwardTolerance = 1e-10; ///< Scaled linear relative backward error bound.
    double eventDisplacementTolerance = 1e-4; ///< Event control bracket width mm.
    double eventStrainAbsolute = 1e-6; ///< Maximum reference threshold excess lower bound.
    double eventStrainRelative = 1e-3; ///< Maximum excess relative to threshold.
    bool lineSearchEnabled = false; ///< CR-NUM-001 bounded Newton backtracking; legacy inputs remain disabled.
    int maximumLineSearchTrials = 12; ///< Maximum merit evaluations per Newton correction.
    double lineSearchReduction = 0.5; ///< Factor multiplying a rejected correction, strictly between zero and one.
    double lineSearchSufficientDecrease = 1e-4; ///< Armijo residual/control merit decrease coefficient.
    double minimumLineSearchFactor = 1e-6; ///< Smallest permitted factor on the whole Newton correction.
    bool transitionResolutionEnabled = false; ///< Resolve the first structural tensile and compressive-peak onsets.
    int maximumTransitionSolves = 40; ///< Bounded candidate solves for a nonterminal onset bracket.
    double transitionDisplacementTolerance = 1e-6; ///< Nonterminal physical control bracket width mm.
    double transitionRatioTolerance = 1e-4; ///< Maximum dimensionless accepted onset-ratio excess.
    double transitionMinimumStep = 1e-7; ///< Event-only resolution increment mm; normal minimumStep is unchanged.
};
/// Rectangular straight beam geometry; angle supports independent elastic verification.
struct GeometryDefinition {
    double length = 0.0; ///< Axis length mm.
    double width = 0.0; ///< Section zeta extent mm.
    double height = 0.0; ///< Section eta extent mm.
    double angle = 0.0; ///< Axis angle in global XY radians.
};
/// Boundary conditions at one axis coordinate.
struct SupportDefinition {
    Id id; ///< Stable support identity.
    double x = 0.0; ///< Axis position mm.
    std::array<bool,3> constrained{false,false,false}; ///< Global ux, uy, theta constraints.
    std::array<double,3> values{0.0,0.0,0.0}; ///< Prescribed global displacement/radian values.
};
/// Proportional point load pattern component.
struct PointLoadDefinition {
    Id id; ///< Stable load identity.
    double x = 0.0; ///< Axis position mm.
    std::array<double,3> reference{0.0,0.0,0.0}; ///< Global Fx, Fy, Mz per unit load factor.
};
/// One real longitudinal steel bar.
struct RebarDefinition {
    Id id; ///< Bar identity preserved in 3D display.
    Id materialId; ///< Steel material reference.
    double diameter = 0.0; ///< Diameter mm.
    double area = 0.0; ///< Area mm2; generated from diameter when appropriate.
    double eta = 0.0; ///< Vertical center coordinate mm, positive up.
    double zeta = 0.0; ///< Transverse center coordinate mm.
    std::string group; ///< Top, bottom, or named direct-coordinate group.
};
/// Reinforcement geometry and cover convention.
struct ReinforcementDefinition {
    std::string mode = "direct"; ///< direct or cover, mutually exclusive interpretation.
    std::string coverDefinition = "stirrup_outer"; ///< Physical cover reference.
    double cover = 0.0; ///< Cover mm.
    double stirrupDiameter = 0.0; ///< Display/archive only, mm.
    double stirrupSpacing = 0.0; ///< Display/archive only, mm.
    int topCount = 0; ///< Cover-driven top bar count.
    int bottomCount = 0; ///< Cover-driven bottom bar count.
    double topDiameter = 0.0; ///< Cover-driven top diameter mm.
    double bottomDiameter = 0.0; ///< Cover-driven bottom diameter mm.
    Id topMaterialId; ///< Cover-driven top steel material.
    Id bottomMaterialId; ///< Cover-driven bottom steel material.
    std::vector<RebarDefinition> bars; ///< Explicit prepared/direct bar centers.
};
/// Equivalent whole-section density; steel is not counted twice.
struct GravityDefinition {
    bool enabled = false; ///< Whether gravity is established before jack loading.
    double density = 0.0; ///< Equivalent kg/m3.
    double acceleration = 0.0; ///< m/s2.
};
/// Deterministic key-node mesh and fiber discretization.
struct DiscretizationDefinition {
    std::string mesh = "custom"; ///< M100, M50, M25, or custom.
    double targetLength = 0.0; ///< Maximum axis length mm.
    int gaussPoints = 3; ///< Supported Gauss-Legendre point count 2, 3, 5.
    int layers = 40; ///< Concrete center-integration layer count.
    std::string lengthMapping = "element_length_v2"; ///< Explicit local-length version; omitted legacy JSON reads gauss_weight_v1.
};
/// A physical axial extensometer, not a route-C event monitor.
struct GaugeDefinition {
    Id id; ///< Stable observation identity.
    double x1 = 0.0; ///< Initial left endpoint mm.
    double x2 = 0.0; ///< Initial right endpoint mm.
    double eta = 0.0; ///< Measurement height mm.
};
/// Read-only observation setup does not alter material history.
struct ObservationDefinition {
    Id id; ///< Versioned observation identity.
    std::string referencePhase = "SG"; ///< S0, SG, or explicit referenceStepId.
    Id referenceStepId; ///< Optional precise accepted reference.
    bool downwardPositive = true; ///< Deflection sign convention.
    bool settlementCorrection = true; ///< Relative support chord operator.
    std::vector<GaugeDefinition> gauges; ///< Physical average-strain operators.
};
/// Editable domain model, without JSON or GUI objects.
struct ModelDefinition {
    Identity identity; ///< Input ownership.
    std::string name; ///< Human-readable project name.
    std::string version = "1"; ///< Model schema version.
    GeometryDefinition geometry; ///< Straight axis and rectangular section.
    ReinforcementDefinition reinforcement; ///< Longitudinal bars and stirrup archive.
    std::vector<MaterialParameters> materials; ///< Shared definitions, never point histories.
    Id concreteMaterialId; ///< Concrete definition identity.
    std::vector<SupportDefinition> supports; ///< True support constraints only.
    std::vector<PointLoadDefinition> loads; ///< Proportional force pattern.
    double controlX = 0.0; ///< Axis control position mm.
    int controlComponent = 1; ///< Global controlled component, normally uy.
    double controlDirection = -1.0; ///< Signed direction for positive control increment.
    GravityDefinition gravity; ///< Constant load stage definition.
    DiscretizationDefinition discretization; ///< Mesh and integration inputs.
    AnalysisOptions analysis; ///< Frozen solver settings.
    ObservationDefinition observations; ///< Versioned display/measurement setup.
    std::optional<CompressionCriterionDefinition> compressionCriterion; ///< nullopt means legacy, never template defaults.
    std::vector<Provenance> sources; ///< Source categories and quality notes.
};
/// Prepared node with three actual global degrees of freedom.
struct PreparedNode {
    Id id; ///< Stable identity in prepared snapshot.
    std::uint64_t numericId = 0; ///< Explicit nonnegative mesh identity.
    double x = 0.0; ///< Axis coordinate mm.
    double globalX = 0.0; ///< Global X coordinate mm.
    double globalY = 0.0; ///< Global Y coordinate mm.
    std::array<std::size_t,3> dofs{0,0,0}; ///< Array positions ux,uy,theta.
};
/// A signed area point, including steel-location concrete correction.
struct PreparedFiber {
    Id id; ///< Point identity, distinct even for shared material.
    Id materialId; ///< Source package identity.
    std::string role; ///< concrete, steel, or concrete_correction.
    double eta = 0.0; ///< Vertical coordinate mm.
    double zeta = 0.0; ///< Transverse coordinate mm.
    double area = 0.0; ///< Signed area mm2.
    MaterialParameters material; ///< Immutable locally mapped parameters.
};
/// Zero-area physical edge point with its own constitutive history.
struct EdgeMonitor {
    Id id; ///< Independent material-point identity.
    std::string side; ///< top or bottom.
    double eta = 0.0; ///< Actual physical edge mm.
    MaterialParameters material; ///< Local length mapping, fixed physical threshold.
};
/// Actual Gauss section; no nonGauss history is invented.
struct PreparedSection {
    Id id; ///< Stable section identity.
    Id elementId; ///< Parent element identity.
    std::size_t elementIndex = 0; ///< Runtime element array position.
    int gaussIndex = 0; ///< Local Gauss array position.
    double xi = 0.0; ///< Natural coordinate in [-1,1].
    double weight = 0.0; ///< Dimensionless Gauss weight.
    double integrationLength = 0.0; ///< weight times element length/2, mm.
    double x = 0.0; ///< True physical axis position mm.
    double lchT = 0.0; ///< Independently named tensile length mm.
    double lchC = 0.0; ///< Independently named compression length mm.
    std::vector<PreparedFiber> fibers; ///< Area-contributing constitutive points.
    std::vector<EdgeMonitor> edges; ///< Zero-area formal event monitor set.
};
/// Two-node small-displacement Euler-Bernoulli element.
struct PreparedElement {
    Id id; ///< Stable prepared identity.
    std::uint64_t numericId = 0; ///< Explicit mesh identity.
    std::array<std::size_t,2> nodes{0,0}; ///< Node array positions.
    double length = 0.0; ///< Axis length mm.
    double cosine = 1.0; ///< Local-to-global axis cosine.
    double sine = 0.0; ///< Local-to-global axis sine.
    std::array<std::size_t,6> dofs{0,0,0,0,0,0}; ///< Global displacement array positions.
    std::vector<std::size_t> sectionIndices; ///< Actual integration section array positions.
};
/// Complete immutable-by-use analysis input; callers pass const references.
struct PreparedModel {
    bool revisedPredictorsEnabled = true; ///< A02 policy; old frozen producer snapshots explicitly disable it.
    bool analysisReadOnly = false; ///< Strictly verified 0.2.* snapshots may be viewed, never analyzed under new mechanics.
    CompressionCriterionDefinition compressionCriterion; ///< Resolved criterion; source absence remains absent in definition.
    std::array<double,2> compressionSearchDomain{{missing,missing}}; ///< Physical start domain [0,L-Lg]; unavailable in legacy mode.
    ModelDefinition definition; ///< Exact input snapshot.
    Identity identity; ///< Input and run identity.
    std::string preparedHash; ///< Canonical descriptor/input hash set by adapter.
    std::vector<PreparedNode> nodes; ///< Explicit mesh nodes.
    std::vector<PreparedElement> elements; ///< Connectivity and transformations.
    std::vector<PreparedSection> sections; ///< All real integration sections.
    std::vector<std::size_t> constrainedDofs; ///< Removed support equations.
    std::vector<double> prescribedValues; ///< Values aligned with constrainedDofs.
    std::vector<std::size_t> freeDofs; ///< Retained equilibrium equations.
    std::vector<double> referenceLoad; ///< Full global proportional load vector.
    std::vector<double> gravityLoad; ///< Full global constant self-weight vector.
    std::size_t controlDof = 0; ///< Retained force equation and displacement constraint DOF.
    std::size_t controlNode = 0; ///< Actual control node array position.
    std::vector<Diagnostic> diagnostics; ///< Assumptions and preparation checks.
};
/// Preparation is atomic: no partial model is valid on failure.
struct PreparationResult {
    bool ok = false; ///< Complete model validity.
    PreparedModel model; ///< Use only if ok.
    std::vector<Diagnostic> diagnostics; ///< Located checks and errors.
};
/// Validate and freeze a model without changing the input definition.
PreparationResult prepareModel(const ModelDefinition&);
} // namespace lumafem
