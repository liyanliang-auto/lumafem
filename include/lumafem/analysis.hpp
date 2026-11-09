#pragma once
#include "lumafem/system.hpp"
#include <functional>

namespace lumafem {
/// Plain C++ application ports; the kernel has no GUI/storage dependencies.
struct AnalysisHooks {
    std::function<bool()> cancelled; ///< Cooperative cancellation at safe points.
    std::function<PersistedStepReceipt(const AcceptedStep&)> persist; ///< Synchronous complete-step receipt.
    std::function<void(const ProgressInfo&)> progress; ///< Actual phase/iteration notification.
};
/// Full Newton gravity then displacement-control solve; only this operation accepts histories.
RunSummary runAnalysis(const PreparedModel&, const AnalysisOptions&, const AnalysisHooks&);
} // namespace lumafem
