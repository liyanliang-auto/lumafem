#include "json_codec.hpp"
#include <csignal>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace fs=std::filesystem;
using namespace lumafem;
using namespace lumafem::codec;
namespace {
volatile std::sig_atomic_t interrupted=0;
void requestCancellation(int) { interrupted=1; }
struct Options {
    fs::path input,output,settings;
    std::string mode="run";
    bool help=false;
};
std::string readFile(const fs::path& path) {
    std::ifstream stream(path,std::ios::binary);
    if(!stream) throw std::runtime_error("Could not open the requested input file.");
    stream.seekg(0,std::ios::end);
    auto count=stream.tellg();
    if(count<0 || count>256ll*1024*1024) throw std::runtime_error("Input is unreadable or exceeds 256 MiB.");
    std::string result(static_cast<std::size_t>(count),'\0'); stream.seekg(0);
    if(!result.empty() && !stream.read(result.data(),static_cast<std::streamsize>(result.size()))) throw std::runtime_error("Could not read the complete input file.");
    return result;
}
void writeFile(const fs::path& path,const Json& value) {
    // New output paths only: this tool never overwrites source inputs or an old run.
    if(fs::exists(path)) throw std::runtime_error("Output already exists; select a new output directory.");
    const auto temporary=fs::path(path.native()+fs::path(".writing").native());
    if(fs::exists(temporary)) throw std::runtime_error("A previous incomplete output exists; select a new output directory.");
    {
        std::ofstream stream(temporary,std::ios::binary|std::ios::trunc);
        if(!stream) throw std::runtime_error("Could not create output.");
        stream<<canonical(value)<<'\n'; stream.flush();
        if(!stream) throw std::runtime_error("Could not write complete output.");
        stream.close(); if(!stream) throw std::runtime_error("Could not close complete output.");
    }
    fs::rename(temporary,path);
}
Options parseOptions(const std::vector<fs::path>& arguments) {
    Options options;
    for(std::size_t i=1;i<arguments.size();++i) {
        const auto flag=arguments[i].string();
        if(flag=="--help" || flag=="-h") { options.help=true; continue; }
        if(i+1>=arguments.size()) throw std::runtime_error("Missing option value. Use --help.");
        if(flag=="--input") options.input=arguments[++i];
        else if(flag=="--output") options.output=arguments[++i];
        else if(flag=="--settings") options.settings=arguments[++i];
        else if(flag=="--mode") options.mode=arguments[++i].string();
        else throw std::runtime_error("Unknown option. Use --help.");
    }
    if(!options.help && (options.input.empty() || options.output.empty())) throw std::runtime_error("--input and --output are required.");
    if(options.mode!="run" && options.mode!="prepare" && options.mode!="view") throw std::runtime_error("--mode must be prepare, run or view.");
    return options;
}
int execute(const std::vector<fs::path>& arguments) {
    try {
        const auto options=parseOptions(arguments);
        if(options.help) {
            std::cout<<"LumaFEM native core tool (N, mm, MPa, rad)\n"
                "  lumafem_cli --input document.json --output NEW_DIRECTORY [--mode prepare|run|view] [--settings analysis.json]\n"
                "  Input may be an editable document, or a complete frozen prepared object.\n"
                "  Settings are explicit analysis-field overrides for editable inputs only.\n"
                "  Outputs preserve full snapshots, accepted states, events and end reason.\n"
                "  View requires a frozen snapshot, verifies it and preserves old read-only producers without analysis.\n"
                "  This tool never overwrites an existing output directory or certifies experimental validity.\n";
            return 0;
        }
        auto source=parse(readFile(options.input));
        const bool frozen=source.contains("prepared_hash");
        if(options.mode=="view" && (!frozen || !options.settings.empty()))
            throw std::runtime_error("View requires a frozen prepared snapshot without settings.");
        if(!options.settings.empty()) {
            if(frozen) throw std::runtime_error("Settings cannot modify a frozen prepared snapshot; prepare a new source revision.");
            const auto settings=parse(readFile(options.settings));
            if(!settings.is_object()) throw std::runtime_error("Settings must be an analysis-field object.");
            if(!source.contains("model") || !source["model"].is_object()) throw std::runtime_error("Editable model is missing.");
            const std::set<std::string> fields{"initial_step","minimum_step","maximum_step","maximum_iterations","maximum_halvings","growth_consecutive","growth_iteration_limit","growth_factor","gravity_steps","maximum_accepted_steps","maximum_control_displacement","maximum_localization_solves","reference_length","reference_displacement","reference_force","residual_absolute","residual_relative","correction_absolute","correction_relative","control_absolute","control_relative","backward_tolerance","event_displacement_tolerance","event_strain_absolute","event_strain_relative","line_search_enabled","maximum_line_search_trials","line_search_reduction","line_search_sufficient_decrease","minimum_line_search_factor","transition_resolution_enabled","maximum_transition_solves","transition_displacement_tolerance","transition_ratio_tolerance","transition_minimum_step"};
            for(auto it=settings.begin();it!=settings.end();++it) {
                if(!fields.count(it.key())) throw std::runtime_error("Unknown analysis setting.");
                source["model"]["analysis"][it.key()]=it.value();
            }
            source["sources"].push_back({{"category","assumption"},{"reference","Explicit CLI analysis settings override"},{"quality","New analysis snapshot; no verification claimed"},{"version","1"}});
        }
        Json preparation=frozen?Json{{"ok",true},{"prepared",source},{"diagnostics",Json::array()}}:prepareDocument(source);
        // Verify frozen snapshots before creating files or invoking any analysis hook.
        std::optional<PreparedInput> input;
        if(preparation.at("ok").get<bool>()) input=decodePrepared(preparation.at("prepared"),options.mode!="view");
        if(fs::exists(options.output)) throw std::runtime_error("Output directory already exists; select a new directory.");
        fs::create_directories(options.output);
        writeFile(options.output/"preparation.json",preparation);
        if(!preparation.at("ok").get<bool>()) {
            std::cerr<<"Model preparation rejected the input. See preparation.json.\n"; return 2;
        }
        const auto prepared=preparation.at("prepared");
        writeFile(options.output/"input.json",input->document);
        writeFile(options.output/"prepared.json",prepared);
        if(options.mode=="view") {
            writeFile(options.output/"verification.json",Json{{"ok",true},{"prepared",prepared},
                {"analysis_read_only",input->analysisReadOnly},{"diagnostics",Json::array()}});
            std::cout<<"Verified snapshot written; analysis_read_only="<<(input->analysisReadOnly?"true":"false")<<".\n";
            return 0;
        }
        if(options.mode=="prepare") { std::cout<<"Prepared snapshot written.\n"; return 0; }
        const auto stepsDir=options.output/"steps"; fs::create_directory(stepsDir);
        std::ofstream progress(options.output/"progress.jsonl",std::ios::binary|std::ios::trunc);
        if(!progress) throw std::runtime_error("Could not create progress log.");
        AnalysisHooks hooks;
        std::signal(SIGINT,requestCancellation);
        hooks.cancelled=[]() { return interrupted!=0; };
        std::int64_t lastSaved=-1;
        hooks.persist=[&](const AcceptedStep& step) {
            PersistedStepReceipt receipt; receipt.identity=step.identity; receipt.persistedSequence=lastSaved;
            try {
                std::ostringstream filename; filename<<std::setfill('0')<<std::setw(8)<<step.sequence<<".json";
                writeFile(stepsDir/filename.str(),stepToJson(step));
                receipt.ok=true; receipt.persistedSequence=static_cast<std::int64_t>(step.sequence);
                receipt.transactionId="cli-file-"+step.identity.stepId; lastSaved=receipt.persistedSequence;
            } catch(const std::exception& error) { receipt.diagnostics.push_back(boundaryDiagnostic(error,"storage")); }
            return receipt;
        };
        hooks.progress=[&](const ProgressInfo& notification) {
            progress<<canonical(progressToJson(notification))<<'\n'; progress.flush();
            if(!progress) throw DomainError({"progress_write_failed","error","storage","","progress","Could not write the progress log."});
        };
        auto summary=runAnalysis(input->model,input->model.definition.analysis,hooks);
        writeFile(options.output/"summary.json",summaryToJson(summary));
        std::cout<<summaryToJson(summary).at("termination").get<std::string>()
            <<"; memory step "<<summary.memoryLastStep<<"; saved step "<<summary.persistedLastStep<<'\n';
        return (summary.termination==Termination::ReferenceCompression || summary.termination==Termination::PhysicalGaugeCompression)?0:3;
    } catch(const std::exception& error) {
        std::cerr<<canonical(diagnosticToJson(boundaryDiagnostic(error,"cli")))<<'\n'; return 1;
    }
}
} // namespace

#ifdef _WIN32
int wmain(int argc,wchar_t* argv[]) {
    std::vector<fs::path> arguments; for(int i=0;i<argc;++i) arguments.emplace_back(argv[i]); return execute(arguments);
}
#else
int main(int argc,char* argv[]) {
    std::vector<fs::path> arguments; for(int i=0;i<argc;++i) arguments.emplace_back(argv[i]); return execute(arguments);
}
#endif
