#include "json_codec.hpp"
#include <pybind11/pybind11.h>

namespace py = pybind11;
namespace {
using namespace lumafem;
using namespace lumafem::codec;

Diagnostic callbackError(py::error_already_set& error, const char* code, const char* stage) {
    // Consume the Python exception while holding the GIL. No traceback-owned object
    // or Python exception is allowed to unwind through a GIL-free solver frame.
    const std::string message=error.what();
    error.restore(); PyErr_Clear();
    return {code,"error",stage,"","",message};
}
std::string prepareJson(const std::string& document) {
    py::gil_scoped_release release;
    try { return canonical(prepareDocument(parse(document))); }
    catch(const std::exception& error) {
        return canonical(Json{{"ok",false},{"prepared",nullptr},
            {"diagnostics",Json::array({diagnosticToJson(boundaryDiagnostic(error,"preparation"))})}});
    }
}
std::string verifyPreparedJson(const std::string& preparedText, bool forAnalysis) {
    py::gil_scoped_release release;
    try {
        const auto prepared=parse(preparedText);
        const auto input=decodePrepared(prepared,forAnalysis);
        return canonical(Json{{"ok",true},{"prepared",prepared},
            {"analysis_read_only",input.analysisReadOnly},{"diagnostics",Json::array()}});
    } catch(const std::exception& error) {
        return canonical(Json{{"ok",false},{"prepared",nullptr},{"analysis_read_only",nullptr},
            {"diagnostics",Json::array({diagnosticToJson(boundaryDiagnostic(error,"native_adapter"))})}});
    }
}
std::string analyzeJson(const std::string& preparedText, py::object onStep,
                        py::object onProgress, py::object cancelled) {
    const bool hasStep=!onStep.is_none(),hasProgress=!onProgress.is_none(),hasCancel=!cancelled.is_none();
    if((hasStep&&!PyCallable_Check(onStep.ptr())) || (hasProgress&&!PyCallable_Check(onProgress.ptr())) ||
       (hasCancel&&!PyCallable_Check(cancelled.ptr()))) throw py::type_error("Callbacks must be callable or None.");
    RunSummary summary;
    std::vector<Diagnostic> callbackDiagnostics;
    {
        py::gil_scoped_release release;
        try {
            const auto input=decodePrepared(parse(preparedText));
            summary.identity=input.model.identity;
            AnalysisHooks hooks;
            if(hasStep) hooks.persist=[&](const AcceptedStep& step) {
                PersistedStepReceipt receipt; receipt.identity=step.identity;
                try {
                    const auto payload=canonical(stepToJson(step));
                    py::gil_scoped_acquire acquire;
                    try {
                        auto response=onStep(py::str(payload));
                        if(!py::isinstance<py::str>(response)) throw DomainError({"invalid_receipt","error","storage",step.identity.stepId,"receipt","Storage callback must return receipt JSON text."});
                        return receiptFromJson(parse(response.cast<std::string>()));
                    } catch(py::error_already_set& error) {
                        receipt.diagnostics.push_back(callbackError(error,"storage_callback_error","storage"));
                    }
                } catch(const std::exception& error) { receipt.diagnostics.push_back(boundaryDiagnostic(error,"storage")); }
                return receipt;
            };
            if(hasProgress) hooks.progress=[&](const ProgressInfo& progress) {
                const auto payload=canonical(progressToJson(progress));
                py::gil_scoped_acquire acquire;
                try { onProgress(py::str(payload)); }
                catch(py::error_already_set& error) {
                    auto diagnostic=callbackError(error,"progress_callback_error","application");
                    callbackDiagnostics.push_back(diagnostic); throw DomainError(diagnostic);
                }
            };
            if(hasCancel) hooks.cancelled=[&]() {
                py::gil_scoped_acquire acquire;
                try {
                    auto response=cancelled();
                    if(!py::isinstance<py::bool_>(response)) throw DomainError({"invalid_cancel_result","error","application","","cancelled","Cancellation callback must return a boolean."});
                    return response.cast<bool>();
                } catch(py::error_already_set& error) {
                    auto diagnostic=callbackError(error,"cancel_callback_error","application");
                    callbackDiagnostics.push_back(diagnostic); throw DomainError(diagnostic);
                }
            };
            summary=runAnalysis(input.model,input.model.definition.analysis,hooks);
            if(!callbackDiagnostics.empty()) {
                summary.diagnostics.insert(summary.diagnostics.end(),callbackDiagnostics.begin(),callbackDiagnostics.end());
                if(summary.termination!=Termination::StorageError) {
                    summary.termination=Termination::InternalError;
                    summary.message="An application callback failed; see the structured diagnostics.";
                }
            }
        } catch(const std::exception& error) {
            const auto diagnostic=boundaryDiagnostic(error,"native_adapter");
            summary.diagnostics.push_back(diagnostic); summary.message=diagnostic.message;
            summary.termination=summary.identity.runId.empty()?Termination::InputError:Termination::InternalError;
        }
    }
    return canonical(summaryToJson(summary));
}
} // namespace

PYBIND11_MODULE(_lumafem,module) {
    module.doc()="LumaFEM independent C++ kernel transport. All native solve work releases the GIL.";
    module.attr("schema_version")=1;
    module.attr("kernel_version")=kernelVersion;
    module.attr("reader_capability")=readerCapability;
    module.def("prepare_json",&prepareJson,py::arg("document_json"),
        "Validate and freeze a document. Returns complete prepared JSON or located diagnostics.");
    module.def("verify_prepared_json",&verifyPreparedJson,py::arg("prepared_json"),py::arg("for_analysis")=false,
        "Verify original hash and exact producer descriptor; return unchanged input and producer-derived read-only status.");
    module.def("analyze_json",&analyzeJson,py::arg("prepared_json"),py::arg("on_step")=py::none(),
        py::arg("on_progress")=py::none(),py::arg("cancelled")=py::none(),
        "Analyze a descriptor-verified snapshot with synchronous persistence and cooperative cancellation.");
}
