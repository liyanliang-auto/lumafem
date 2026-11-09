#pragma once
#include "lumafem/results.hpp"
#include <functional>

namespace lumafem {
/// Pure exact-search outcome. Failure/cancellation never promotes an accepted history.
struct GaugeSearchResult {
    bool ok = false; ///< Full valid global maximum available.
    bool cancelled = false; ///< Cooperative cancellation observed during search.
    GaugeWindowEvidence window; ///< Geometric/strain evidence; analysis supplies phase/identity/load metadata.
    std::vector<Diagnostic> diagnostics; ///< Located invalid-input or evaluation failures.
};
/// Search both full physical edges using total global displacement and exact EB interpolation.
/// Partition at x_i and x_i-Lg; evaluate endpoints and every affine derivative root.
/// bounded_affine_root_v1: interval [lo,hi] admits a computed root r only within
/// tau=64*epsilon*max(1,abs(lo),abs(hi),abs(r),L,Lg) mm; clamp to [lo,hi].
/// Exact duplicate coordinates may be removed; never tolerance-merge distinct knots.
/// Compare actual compression values strictly; exact ties choose lower x1, then top.
/// No state mutation; no display gauges, Gauss sampling, or material histories are used.
GaugeSearchResult evaluatePhysicalGauge(const PreparedModel&,
    const std::vector<double>& totalDisplacement,
    const std::function<bool()>& cancelled = {});
} // namespace lumafem
