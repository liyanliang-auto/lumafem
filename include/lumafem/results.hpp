#pragma once
#include "lumafem/section.hpp"
#include <array>

namespace lumafem {
/// Actual analysis stage; localization candidates are never accepted steps.
enum class Phase { S0, G, SG, J };
/// Physical events independent of current unloading branch.
enum class EventKind { FirstCrack, FirstYield, ReferenceCompression, PathMaximum, PhysicalGaugeCompression };
/// End reason never conflates numerical failure with a physical criterion.
enum class Termination {
    ReferenceCompression, SafetyLimit, NonConvergence, LinearFailure,
    LocalizationFailure, Cancelled, InputError, StorageError, InternalError, PhysicalGaugeCompression
};
/// One globally maximizing physical window; never a material point or section identity.
struct GaugeWindowEvidence {
    std::string mode = physicalGaugeMeanMode; ///< physical_gauge_mean_v1.
    std::string operatorVersion = physicalGaugeOperatorVersion; ///< Exact EB operator identity.
    std::string reference = physicalGaugeReference; ///< Total undeformed-zero reference, including gravity.
    double gaugeLength = missing; ///< Full mechanical window length mm.
    double threshold = missing; ///< Selected positive compression threshold.
    std::array<double,2> searchDomain{{missing,missing}}; ///< Entire allowed start domain mm.
    std::string side; ///< top or bottom, never inferred from a fiber ID.
    double eta = missing; ///< Signed physical edge height mm.
    double x1 = missing; ///< Actual maximizing window start mm.
    double x2 = missing; ///< Actual maximizing window end mm.
    double signedMean = missing; ///< Signed TOTAL strain averaged before compression extraction.
    double compressionMaximum = missing; ///< max(0,-signedMean), globally maximized.
    std::string status = "current"; ///< current or accepted_historical.
    Identity identity; ///< Actual owning accepted state; trial evaluations have no accepted step ID.
    Phase phase = Phase::S0; ///< Real evaluation phase, annotated by analysis.
    double controlDisplacement = missing; ///< Actual physical control mm, with existing phase convention.
    double loadFactor = missing; ///< Proportional load multiplier.
    double gravityFactor = missing; ///< Established gravity fraction.
};
/// Accepted current maximum and independently owned accepted historical winning state.
struct GaugeStateEvidence {
    GaugeWindowEvidence current; ///< Maximum at this accepted state only.
    GaugeWindowEvidence historical; ///< Actual historical winner, with its original identity/phase.
};
/// Frozen per-candidate convergence evidence.
struct ConvergenceInfo {
    int iterations = 0; ///< Actual Newton evaluations.
    int halvings = 0; ///< Reductions for this accepted candidate.
    double residualForce = missing; ///< Maximum free force residual N.
    double residualMoment = missing; ///< Maximum free moment residual N mm.
    double correctionTranslation = missing; ///< Maximum translation correction mm.
    double correctionRotation = missing; ///< Maximum rotation correction rad.
    double controlError = missing; ///< Control equation absolute error mm.
    double forceScale = missing; ///< Anchor-fixed translation residual scale N.
    double momentScale = missing; ///< Anchor-fixed rotation residual scale N mm.
    double translationScale = missing; ///< Anchor-fixed correction scale mm.
    double rotationScale = missing; ///< Anchor-fixed correction scale rad.
    double targetIncrement = missing; ///< Signed candidate control increment mm.
    double backwardError = missing; ///< Scaled linear relative backward error.
    bool residualPassed = false; ///< Both force/moment limits satisfied.
    bool correctionPassed = false; ///< Both translation/rotation limits satisfied.
    bool controlPassed = false; ///< Control constraint limit satisfied.
    bool finitePassed = false; ///< Complete accepted output finite where defined.
    int lineSearchEvaluations = 0; ///< Actual nested residual/control merit evaluations.
    int lineSearchBacktracks = 0; ///< Actual rejected/damped factors during Newton corrections.
    double lastCorrectionFactor = 1.0; ///< Factor applied to the latest complete Newton correction.
    double minimumCorrectionFactor = 1.0; ///< Smallest factor actually applied in this candidate.
    int totalNewtonEvaluations = 0; ///< All evaluations within THIS candidate, including discarded predictors; excludes other cutbacks/brackets.
    int predictorAttempts = 0; ///< Actual disposable secant/force predictor attempts.
    int predictorNewtonEvaluations = 0; ///< Newton evaluations in predictor-seeded solves and force probes.
    double predictorSeedFactor = missing; ///< Last successful bounded predictor factor, unavailable otherwise.
    std::string predictorKind; ///< Successful accepted_secant, force_unloading, critical_mode; empty otherwise.
    double predictorControlAdvance = missing; ///< Physical prior/probe advance, or original target advance for a modal seed.
    double predictorAmplitude = missing; ///< Signed critical-mode initial amplitude mm; never a physical imperfection.
    int predictorDecompositions = 0; ///< Actual bounded dense weak-mode decompositions in this candidate.
    int predictorLinearSolves = 0; ///< Extra original augmented solves used only to form a modal base seed.
    int predictorModeIndex = 0; ///< Successful 1-based weak direction; zero when no modal predictor succeeded.
};
/// Nonterminal first structural softening-onset bracket, distinct from crushing.
struct TransitionInfo {
    bool active = false; ///< Whether an actual accepted onset was localized.
    std::string kind; ///< Empty, tensile_onset, or compression_peak; never a crushing event.
    Id pointId; ///< Formal Gauss material/edge point defining the onset.
    double threshold = missing; ///< Applicable tensile-excursion or compression-amplitude strain threshold.
    int candidateSolves = 0; ///< Actual attempted onset candidate solves.
    double lowerControl = missing; ///< Lower converged physical control value mm.
    double upperControl = missing; ///< Upper converged physical control value mm.
    double lowerRatio = missing; ///< Lower dimensionless constitutive onset measure.
    double upperRatio = missing; ///< Upper dimensionless constitutive onset measure.
    bool lowerConverged = false; ///< Lower endpoint has valid joint convergence evidence.
    bool upperConverged = false; ///< Upper endpoint has valid joint convergence evidence.
    ConvergenceInfo lowerEvidence; ///< Lower endpoint convergence, including actual backtracking work.
    ConvergenceInfo upperEvidence; ///< Upper endpoint convergence, including actual backtracking work.
};
/// Event localization interval with converged endpoint evidence.
struct LocalizationInfo {
    bool active = false; ///< Whether localization occurred.
    int candidateSolves = 0; ///< Actual candidate solve count.
    double lowerControl = missing; ///< Safe converged physical control value mm.
    double upperControl = missing; ///< Threshold-side converged control value mm.
    double lowerRatio = missing; ///< Safe maximum reference ratio.
    double upperRatio = missing; ///< Upper maximum reference ratio.
    double finalExcess = missing; ///< Accepted reference strain minus threshold.
    bool lowerConverged = false; ///< Lower endpoint has convergence evidence.
    bool upperConverged = false; ///< Upper endpoint has convergence evidence.
    ConvergenceInfo lowerEvidence; ///< Lower endpoint convergence.
    ConvergenceInfo upperEvidence; ///< Upper endpoint convergence.
    std::string kind; ///< Physical bracket or exact_crossing; empty preserves legacy wire shape.
    std::string controlUnit; ///< mm for active physical localization, including gravity crossings.
    double lowerGravityFactor = missing; ///< Actual lower gravity fraction, not a displacement.
    double upperGravityFactor = missing; ///< Actual upper gravity fraction.
    double bracketWidth = missing; ///< Actual absolute physical control difference mm; exact crossing is zero.
    std::optional<GaugeWindowEvidence> lowerGauge; ///< Independent converged safe maximum; absent for exact crossing.
    std::optional<GaugeWindowEvidence> upperGauge; ///< Independent converged threshold-side maximum.
};
/// One accepted physical event location; ties use multiple records.
struct EventRecord {
    Id id; ///< Stable event identity.
    EventKind kind = EventKind::FirstCrack; ///< Event classification.
    Id stepId; ///< Accepted output step.
    Id elementId; ///< True owning element.
    Id sectionId; ///< True Gauss location.
    Id pointId; ///< Actual material or edge identity.
    std::string side; ///< Physical edge side if relevant.
    std::string criterion; ///< Algorithm/version and definition.
    Provenance source; ///< Threshold/parameter provenance.
    double x = missing; ///< True axis location mm.
    double eta = missing; ///< Actual section position mm.
    double strain = missing; ///< Current local signed strain.
    double stress = missing; ///< Current local signed stress MPa.
    double maxCompressionStrain = missing; ///< Historical positive local compression amplitude.
    double lch = missing; ///< Applicable numerical characteristic length mm.
    double referenceLength = missing; ///< Fixed physical gauge mm.
    double referenceModulus = missing; ///< Reference elastic modulus MPa.
    double deltaC = missing; ///< Compression equivalent displacement mm.
    double referenceStrain = missing; ///< Route-C reference compression amplitude.
    double threshold = missing; ///< Applicable event threshold.
    double P = missing; ///< Two-jack total N at event.
    double controlDisplacement = missing; ///< Downward relative-to-SG control mm.
    LocalizationInfo localization; ///< Trigger localization evidence.
    std::optional<GaugeStateEvidence> gauge; ///< Real physical event state/history; absent for legacy point events.
};
/// Element forces distinguish equilibrium recovery from constitutive section moments.
struct ElementForceResult {
    Id id; ///< Actual element identity.
    std::array<double,6> endForces{0,0,0,0,0,0}; ///< Local resisting [N1,V1,M1,N2,V2,M2].
    double distributedAxial = 0.0; ///< Local gravity component N/mm.
    double distributedTransverse = 0.0; ///< Local gravity component N/mm.
    std::string source = "element_end_equilibrium"; ///< Recovery source label.
};
/// Named observation includes cumulative and referenced quantities without mutating physics.
struct ObservationValue {
    Id id; ///< Measurement definition identity.
    std::string quantity; ///< Displacement, strain, force, etc.
    std::string unit; ///< Internal quantity unit.
    std::string source; ///< Explicit operator and interpretation.
    double total = missing; ///< Cumulative computed value.
    double increment = missing; ///< Value minus selected accepted reference.
    Id referenceStepId; ///< Precise reference output.
};
/// Complete accepted output; arrays are copied, never aliases to trial workspace.
struct AcceptedStep {
    Identity identity; ///< Full project/revision/snapshot/run/step identity.
    std::size_t sequence = 0; ///< Monotonically increasing run sequence.
    Phase phase = Phase::S0; ///< Real physical stage.
    double loadFactor = 0.0; ///< External proportional-load multiplier.
    double P = 0.0; ///< External total two-jack load N, excludes gravity.
    double P1 = 0.0; ///< First jack force amplitude N.
    double P2 = 0.0; ///< Second jack force amplitude N.
    double gravityFactor = 0.0; ///< Established fraction of full gravity.
    double controlDisplacement = 0.0; ///< Positive downward change from SG mm.
    std::vector<double> displacement; ///< Global [ux,uy,theta] per prepared node.
    std::vector<double> reactions; ///< Full fInternal-fExternal, free entries retained as residuals.
    std::vector<double> externalForce; ///< Actual full cumulative applied force vector.
    std::vector<ElementForceResult> elementForces; ///< End equilibrium force recovery.
    std::vector<SectionResult> sections; ///< All actual Gauss constitutive outputs.
    std::vector<FiberResult> fibers; ///< Flattened section-order area results, optional convenience.
    std::vector<FiberResult> edges; ///< Flattened actual zero-area monitor outputs.
    std::vector<EventRecord> events; ///< Events newly accepted at this step only.
    std::vector<ObservationValue> observations; ///< Physical measurement operators.
    ConvergenceInfo convergence; ///< Three-part acceptance plus finite/linear evidence.
    LocalizationInfo localization; ///< Converged bracket where applicable.
    TransitionInfo transition; ///< Optional nonterminal structural onset evidence, never a physical end reason.
    std::optional<GaugeStateEvidence> gauge; ///< Required physical-mode evidence; absent old payloads remain absent.
};
/// Storage confirmation is distinct from numerical acceptance.
struct PersistedStepReceipt {
    bool ok = false; ///< Complete transaction confirmation.
    Identity identity; ///< Exact step saved.
    std::int64_t persistedSequence = -1; ///< Last complete saved sequence; -1 if none.
    std::string transactionId; ///< Storage-generated confirmation identity.
    std::vector<Diagnostic> diagnostics; ///< Storage failure details.
};
/// Actual progress, with no fabricated percent-to-failure estimate.
struct ProgressInfo {
    Identity identity; ///< Message source ownership.
    std::size_t sequence = 0; ///< Monotonic notification counter.
    std::string stage; ///< gravity, jack, localization, or finishing.
    std::string message; ///< Actual operation or diagnostic.
    std::size_t acceptedSteps = 0; ///< Completed numerical accepts.
    int iteration = 0; ///< Current Newton iteration.
    int halvings = 0; ///< Current reductions.
    double controlDisplacement = 0.0; ///< Real current/accepted control value mm.
    double P = 0.0; ///< Real load N.
};
/// Honest independent verification status; a single run does not certify the software.
struct ValidationStatus {
    std::string A = "UNVERIFIED"; ///< Materials/sections/elastic/independent numerical checks.
    std::string B = "UNVERIFIED"; ///< Convergence, localization, energy and mesh checks.
    std::string C = "PENDING_DATA"; ///< Authentic physical experiment validation.
    std::string D = "UNVERIFIED"; ///< Desktop/storage/workflow checks.
};
/// Final task conclusion, including physical evidence even when storage failed.
struct RunSummary {
    Identity identity; ///< Frozen run ownership.
    Termination termination = Termination::InternalError; ///< Primary actual end reason.
    std::string message; ///< Human-readable diagnostic.
    std::int64_t memoryLastStep = -1; ///< Last complete numerical accept.
    std::int64_t persistedLastStep = -1; ///< Last complete transaction.
    Id memoryLastStepId; ///< Accepted in-memory state identity.
    Id persistedLastStepId; ///< Last saved output identity.
    bool physicalCriterionReached = false; ///< Physical threshold independently of IO outcome.
    bool cancellationRequested = false; ///< Record cancellation race/secondary intent.
    std::vector<EventRecord> physicalEvidence; ///< Real accepted event evidence.
    std::vector<Diagnostic> diagnostics; ///< Failure/limits/quality records.
    LocalizationInfo localization; ///< Final bracket if relevant.
    ValidationStatus validation; ///< Never automatically PASS from solver completion.
};
} // namespace lumafem
