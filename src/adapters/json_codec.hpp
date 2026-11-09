#pragma once

#include "lumafem/analysis.hpp"
#include <nlohmann/json.hpp>
#include <string>

namespace lumafem::codec {
inline constexpr const char* kernelVersion = "0.3.1-cr-num-002-a05"; ///< Numerical producer identity; no validation claim.
inline constexpr const char* crNum002KernelVersion = "0.3.0-cr-num-002"; ///< Frozen original physical-criterion/A04 producer.
inline constexpr const char* numericalContractVersion = "CR-NUM-002"; ///< Current prepared interpretation.
inline constexpr int readerCapability = 3; ///< Independent of unchanged SQL/JSON schema 1.
inline constexpr const char* crNum001KernelVersion = "0.2.0-cr-num-001"; ///< Frozen original CR1 producer.
inline constexpr const char* crNum001A03KernelVersion = "0.2.1-cr-num-001-a03"; ///< Frozen single-mode producer.
inline constexpr const char* crNum001A04KernelVersion = "0.2.2-cr-num-001-a04"; ///< Frozen two-mode producer.
/// JSON exists only at the adapter boundary, never in the numerical domain.
using Json = nlohmann::json;
/// Validated prepared transport together with its exact source document.
struct PreparedInput {
    PreparedModel model; ///< Re-created and descriptor-verified native input.
    Json document; ///< Unmodified source metadata and physical inputs.
    bool analysisReadOnly = false; ///< Verified 0.2.* view-only transport, matching model.analysisReadOnly.
};
/// Parse bounded UTF-8 JSON and reject nonfinite numeric values.
Json parse(const std::string& text);
/// Canonical sorted-key JSON, shortest roundtrip float64 representation.
std::string canonical(const Json& value);
/// SHA256 of canonical UTF-8 JSON, including exact input and mapped descriptors.
std::string contentHash(const Json& value);
/// Encode a located domain diagnostic.
Json diagnosticToJson(const Diagnostic& value);
/// Encode immutable ownership identity.
Json identityToJson(const Identity& value);
/// Validate and prepare a source document; returns ok/prepared/diagnostics.
Json prepareDocument(const Json& document);
/// Verify original hash and exact producer-scoped canonical descriptor BEFORE any compatibility decision.
/// forAnalysis=true rejects verified frozen 0.2.* and 0.3.0 snapshots; false returns read-only input.
/// Old 0.1 keeps its original algorithm with revised predictors disabled; source bytes/fields stay unchanged.
PreparedInput decodePrepared(const Json& prepared, bool forAnalysis = true);
/// Encode actual physical-window evidence; unavailable numbers are null, with no point IDs.
Json gaugeWindowToJson(const GaugeWindowEvidence& value);
/// Encode current and original historical winner, without substituting one for the other.
Json gaugeStateToJson(const GaugeStateEvidence& value);
/// Encode validated search counts/factors and unchanged joint convergence evidence.
Json convergenceToJson(const ConvergenceInfo& value);
/// Encode nonterminal tensile/compressive-peak onset evidence, independently of terminal localization.
Json transitionToJson(const TransitionInfo& value);
/// Encode every accepted scalar, array, material history, event and diagnostic.
Json stepToJson(const AcceptedStep& value);
/// Encode an actual progress notification.
Json progressToJson(const ProgressInfo& value);
/// Encode the primary conclusion and secondary physical evidence.
Json summaryToJson(const RunSummary& value);
/// Parse a complete-step storage receipt, without fabricating persistence.
PersistedStepReceipt receiptFromJson(const Json& value);
/// Make an input/error conclusion for a boundary exception.
RunSummary errorSummary(const Diagnostic& diagnostic, const Identity& identity = {});
/// Convert an exception to a structured boundary diagnostic.
Diagnostic boundaryDiagnostic(const std::exception& error, const std::string& stage);
} // namespace lumafem::codec
