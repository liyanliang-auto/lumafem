#include "lumafem/analysis.hpp"
#include "lumafem/physical_gauge.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>
#include <Eigen/SVD>

namespace lumafem {
namespace {
using Sparse = Eigen::SparseMatrix<double>;
using Vector = Eigen::VectorXd;

Diagnostic problem(const std::string& code, const std::string& field,
                   const std::string& message, const Id& object = "") {
    return {code,"error","analysis",object,field,message};
}

bool sameIdentity(const Identity& a, const Identity& b) {
    return a.projectId == b.projectId && a.revisionId == b.revisionId &&
           a.snapshotId == b.snapshotId && a.runId == b.runId && a.stepId == b.stepId;
}

struct Candidate {
    bool ok = false;
    bool cancelled = false;
    bool linearFailure = false;
    double lambda = 0.0;
    double gravity = 0.0;
    SystemResponse response;
    ConvergenceInfo evidence;
    std::vector<Diagnostic> diagnostics;
};

struct EdgeMaximum {
    double ratio = 0.0;
    double excess = 0.0;
    double allowedExcess = 0.0;
};

struct GaugeSearchCancelled {};

struct OnsetMaximum {
    double ratio = 0.0;
    double threshold = missing;
    Id pointId;
    std::string kind;
};

class Analysis {
    const PreparedModel& model;
    const AnalysisOptions& options;
    const AnalysisHooks& hooks;
    RunSummary summary;
    SystemState anchor;
    std::vector<double> previousAcceptedDisplacement;
    double previousAcceptedLambda = 0.0;
    bool anchorWasJack = false;
    SystemResponse anchorResponse;
    ConvergenceInfo anchorEvidence;
    Phase anchorPhase = Phase::S0;
    std::vector<int> reduced;
    std::vector<double> referenceDisplacement;
    Id referenceStep;
    double zeroControl = 0.0;
    bool referenceReady = false;
    bool crackRecorded = false;
    bool yieldRecorded = false;
    bool tensileOnsetAccepted = false;
    bool compressionPeakAccepted = false;
    bool lastAcceptedTransition = false;
    bool lastAcceptedCompressionPeak = false;
    std::size_t acceptedCount = 0;
    std::size_t messageSequence = 0;
    double maximumP = -std::numeric_limits<double>::infinity();
    EventRecord pathMaximum;
    std::optional<GaugeWindowEvidence> acceptedGaugeHistory;

    bool physicalMode() const { return model.compressionCriterion.mode == physicalGaugeMeanMode; }

    GaugeWindowEvidence gaugeWindow(const SystemResponse& response, Phase phase) {
        auto search = evaluatePhysicalGauge(model,response.state.displacement,[&]() { return cancelled(); });
        if (search.cancelled) throw GaugeSearchCancelled{};
        if (!search.ok) throw DomainError(search.diagnostics.empty() ?
            problem("PHYSICAL_GAUGE_FAILED","compression_criterion","The exact physical-window search failed.") : search.diagnostics.front());
        auto window = std::move(search.window);
        window.identity = model.identity;
        window.identity.stepId.clear();
        window.phase = phase;
        window.controlDisplacement = control(response.state.displacement);
        window.loadFactor = response.state.loadFactor;
        window.gravityFactor = response.state.gravityFactor;
        return window;
    }

    bool cancelled() {
        if (!hooks.cancelled) return false;
        bool requested = false;
        try { requested = hooks.cancelled(); }
        catch (const std::exception& e) {
            throw DomainError(problem("CANCEL_CALLBACK_ERROR","cancelled",e.what()));
        } catch (...) {
            throw DomainError(problem("CANCEL_CALLBACK_ERROR","cancelled","Cancellation callback threw an unknown exception."));
        }
        summary.cancellationRequested = summary.cancellationRequested || requested;
        return requested;
    }

    double control(const std::vector<double>& u) const {
        return model.definition.controlDirection * (u.at(model.controlDof)-zeroControl);
    }

    std::array<double,2> jackLoads(double lambda) const {
        std::array<double,2> forces{0.0,0.0};
        const auto component = static_cast<std::size_t>(model.definition.controlComponent);
        for (std::size_t i=0; i<model.definition.loads.size() && i<2; ++i)
            forces[i] = lambda * model.definition.controlDirection * model.definition.loads[i].reference[component];
        return forces;
    }

    void progress(const std::string& stage, int iteration, int halvings,
                  double displacement, double lambda, const std::string& message) {
        if (!hooks.progress) return;
        ProgressInfo info;
        info.identity = model.identity;
        info.sequence = ++messageSequence;
        info.stage = stage;
        info.message = message;
        info.acceptedSteps = acceptedCount;
        info.iteration = iteration;
        info.halvings = halvings;
        info.controlDisplacement = displacement;
        const auto forces = jackLoads(lambda);
        info.P = forces[0]+forces[1];
        try { hooks.progress(info); }
        catch (const std::exception& e) {
            throw DomainError(problem("PROGRESS_CALLBACK_ERROR","progress",e.what()));
        } catch (...) {
            throw DomainError(problem("PROGRESS_CALLBACK_ERROR","progress","Progress callback threw an unknown exception."));
        }
    }

    Vector external(double gravity, double lambda) const {
        Vector force(static_cast<Eigen::Index>(model.referenceLoad.size()));
        for (Eigen::Index i=0; i<force.size(); ++i)
            force[i] = gravity*model.gravityLoad[static_cast<std::size_t>(i)] +
                       lambda*model.referenceLoad[static_cast<std::size_t>(i)];
        return force;
    }

    ConvergenceInfo scales(double increment) const {
        ConvergenceInfo info;
        info.forceScale = options.referenceForce;
        info.momentScale = options.referenceForce*options.referenceLength;
        info.translationScale = std::max(options.referenceDisplacement,std::abs(increment));
        info.rotationScale = info.translationScale/options.referenceLength;
        const auto force = external(anchor.gravityFactor,anchor.loadFactor);
        for (const auto dof : model.freeDofs) {
            const double internal = anchorResponse.internalForce.size() == force.size() ?
                anchorResponse.internalForce[static_cast<Eigen::Index>(dof)] : 0.0;
            if (dof%3 == 2) {
                info.momentScale = std::max({info.momentScale,std::abs(force[static_cast<Eigen::Index>(dof)]),std::abs(internal)});
                info.rotationScale = std::max(info.rotationScale,std::abs(anchor.displacement[dof]));
            } else {
                info.forceScale = std::max({info.forceScale,std::abs(force[static_cast<Eigen::Index>(dof)]),std::abs(internal)});
                info.translationScale = std::max(info.translationScale,std::abs(anchor.displacement[dof]));
            }
        }
        info.targetIncrement = increment;
        info.backwardError = 0.0;
        return info;
    }

    LinearSolution linear(const Sparse& tangent, const Vector& residual,
                          const ConvergenceInfo& info, bool displacementControl,
                          double constraintResidual) const {
        const int n = static_cast<int>(model.freeDofs.size());
        const int size = n + (displacementControl ? 1 : 0);
        std::vector<Eigen::Triplet<double>> entries;
        entries.reserve(static_cast<std::size_t>(tangent.nonZeros())+2*static_cast<std::size_t>(n));
        for (int outer=0; outer<tangent.outerSize(); ++outer)
            for (Sparse::InnerIterator term(tangent,outer); term; ++term) {
                const int row = reduced[static_cast<std::size_t>(term.row())];
                const int col = reduced[static_cast<std::size_t>(term.col())];
                if (row >= 0 && col >= 0) entries.emplace_back(row,col,term.value());
            }
        Vector rhs(size);
        std::vector<double> rows(static_cast<std::size_t>(size));
        std::vector<double> columns(static_cast<std::size_t>(size));
        double patternNorm = 0.0;
        for (int i=0; i<n; ++i) {
            const auto dof = model.freeDofs[static_cast<std::size_t>(i)];
            rhs[i] = residual[static_cast<Eigen::Index>(dof)];
            rows[static_cast<std::size_t>(i)] = dof%3 == 2 ? info.momentScale : info.forceScale;
            columns[static_cast<std::size_t>(i)] = dof%3 == 2 ? info.rotationScale : info.translationScale;
            if (displacementControl && model.referenceLoad[dof] != 0.0)
                entries.emplace_back(i,n,-model.referenceLoad[dof]);
            patternNorm = std::max(patternNorm,std::abs(model.referenceLoad[dof])/rows[static_cast<std::size_t>(i)]);
        }
        if (displacementControl) {
            entries.emplace_back(n,reduced[model.controlDof],1.0);
            rhs[n] = constraintResidual;
            rows[static_cast<std::size_t>(n)] = info.translationScale;
            columns[static_cast<std::size_t>(n)] = patternNorm > 0.0 ? 1.0/patternNorm : 1.0;
        }
        Sparse matrix(size,size);
        matrix.setFromTriplets(entries.begin(),entries.end());
        return solveScaledSystem(matrix,rhs,rows,columns,options.backwardTolerance);
    }

    void convergence(const Vector& residual, const std::vector<double>& u, double lambda,
                     double target, double increment, bool displacementControl,
                     ConvergenceInfo& info) const {
        info.residualForce = info.residualMoment = 0.0;
        for (const auto dof : model.freeDofs) {
            auto& norm = dof%3 == 2 ? info.residualMoment : info.residualForce;
            norm = std::max(norm,std::abs(residual[static_cast<Eigen::Index>(dof)]));
        }
        info.controlError = displacementControl ? std::abs(u[model.controlDof]-target) : 0.0;
        info.residualPassed = info.residualForce <= options.residualAbsolute+options.residualRelative*info.forceScale &&
            info.residualMoment <= options.residualAbsolute*options.referenceLength+options.residualRelative*info.momentScale;
        info.correctionPassed = info.correctionTranslation <= options.correctionAbsolute+options.correctionRelative*info.translationScale &&
            info.correctionRotation <= options.correctionAbsolute/options.referenceLength+options.correctionRelative*info.rotationScale;
        info.controlPassed = info.controlError <= options.controlAbsolute+options.controlRelative*std::abs(increment);
        info.finitePassed = residual.allFinite() && std::isfinite(lambda) &&
            std::isfinite(info.residualForce) && std::isfinite(info.residualMoment) &&
            std::isfinite(info.correctionTranslation) && std::isfinite(info.correctionRotation) && std::isfinite(info.controlError);
    }

    bool converged(const ConvergenceInfo& info) const {
        return info.finitePassed && info.residualPassed && info.correctionPassed && info.controlPassed;
    }

    double merit(const Vector& residual, double constraintResidual,
                 const ConvergenceInfo& info, bool displacementControl) const {
        double norm = 0.0;
        for (const auto dof : model.freeDofs)
            norm = std::hypot(norm,residual[static_cast<Eigen::Index>(dof)]/
                (dof%3 == 2 ? info.momentScale : info.forceScale));
        if (displacementControl) norm = std::hypot(norm,constraintResidual/info.translationScale);
        return norm;
    }

    void correctionNorm(const Vector& correction, double factor, ConvergenceInfo& info) const {
        info.correctionTranslation = info.correctionRotation = 0.0;
        for (std::size_t i=0; i<model.freeDofs.size(); ++i) {
            auto& norm = model.freeDofs[i]%3 == 2 ? info.correctionRotation : info.correctionTranslation;
            norm = std::max(norm,std::abs(factor*correction[static_cast<Eigen::Index>(i)]));
        }
    }

    Candidate solveNewton(double gravity, bool displacementControl, double parameter,
                    int halvings, const std::string& stage, const Candidate* seed = nullptr,
                    double fixedLambda = missing, bool allowSearch = true, int iterationLimit = -1,
                    bool reportProgress = true) {
        Candidate result;
        result.gravity = gravity;
        result.lambda = seed ? seed->lambda : (displacementControl ? anchor.loadFactor :
            (std::isfinite(fixedLambda) ? fixedLambda : 0.0));
        auto u = seed ? seed->response.state.displacement : anchor.displacement;
        const double target = displacementControl ? zeroControl+model.definition.controlDirection*parameter : u[model.controlDof];
        const double increment = displacementControl ? target-anchor.displacement[model.controlDof] : 0.0;
        auto info = scales(increment);
        info.halvings = halvings;
        info.correctionTranslation = info.correctionRotation = 0.0;
        const int limit = iterationLimit < 0 ? options.maximumIterations : iterationLimit;
        for (int iteration=0; iteration<=limit; ++iteration) {
            if (cancelled()) { result.cancelled = true; result.evidence = info; return result; }
            if (reportProgress) progress(stage,iteration,halvings,control(u),result.lambda,"Full Newton candidate evaluation");
            ++info.totalNewtonEvaluations;
            auto response = evaluateSystem(model,anchor,u);
            if (!response.ok) { result.diagnostics = std::move(response.diagnostics); result.evidence = info; return result; }
            const Vector force = external(gravity,result.lambda);
            const Vector residual = force-response.internalForce;
            info.iterations = iteration;
            convergence(residual,u,result.lambda,target,increment,displacementControl,info);
            if (!info.finitePassed) {
                result.diagnostics.push_back(problem("NEWTON_NONFINITE","candidate","A required candidate residual or correction is nonfinite."));
                result.evidence = info;
                return result;
            }
            // A predictor is not a known solution: even a small seeded residual
            // must pass an actually computed current Newton correction.
            if (converged(info) && (!seed || iteration>0)) {
                result.ok = true;
                result.evidence = info;
                response.state.loadFactor = result.lambda;
                response.state.gravityFactor = gravity;
                result.response = std::move(response);
                return result;
            }
            if (iteration == limit) break;
            const auto correction = linear(response.tangent,residual,info,displacementControl,target-u[model.controlDof]);
            if (!correction.ok) {
                result.linearFailure = true;
                result.diagnostics = correction.diagnostics;
                result.evidence = info;
                return result;
            }
            result.diagnostics.insert(result.diagnostics.end(),correction.diagnostics.begin(),correction.diagnostics.end());
            info.backwardError = std::max(info.backwardError,correction.backwardError);
            if (!options.lineSearchEnabled || !allowSearch) {
                correctionNorm(correction.solution,1.0,info);
                for (std::size_t i=0; i<model.freeDofs.size(); ++i)
                    u[model.freeDofs[i]] += correction.solution[static_cast<Eigen::Index>(i)];
                if (displacementControl) result.lambda += correction.solution[static_cast<Eigen::Index>(model.freeDofs.size())];
                continue;
            }
            const double originalMerit = merit(residual,target-u[model.controlDof],info,displacementControl);
            double factor = 1.0;
            bool applied = false;
            std::vector<Diagnostic> rejectedDiagnostics;
            for (int trial=0; trial<options.maximumLineSearchTrials && factor>=options.minimumLineSearchFactor; ++trial) {
                if (cancelled()) { result.cancelled = true; result.evidence = info; return result; }
                auto trialU = u;
                for (std::size_t i=0; i<model.freeDofs.size(); ++i)
                    trialU[model.freeDofs[i]] += factor*correction.solution[static_cast<Eigen::Index>(i)];
                const double trialLambda = result.lambda+(displacementControl ?
                    factor*correction.solution[static_cast<Eigen::Index>(model.freeDofs.size())] : 0.0);
                ++info.lineSearchEvaluations;
                progress(stage,iteration,halvings,control(trialU),trialLambda,"Fixed-anchor Newton backtracking evaluation");
                // Constitutive histories always restart at the accepted anchor,
                // including rejected search points and the next Newton iterate.
                auto trialResponse = evaluateSystem(model,anchor,trialU);
                auto trialInfo = info;
                correctionNorm(correction.solution,factor,trialInfo);
                bool acceptable = false;
                if (trialResponse.ok) {
                    const Vector trialResidual = external(gravity,trialLambda)-trialResponse.internalForce;
                    convergence(trialResidual,trialU,trialLambda,target,increment,displacementControl,trialInfo);
                    const double trialMerit = merit(trialResidual,target-trialU[model.controlDof],info,displacementControl);
                    acceptable = trialInfo.finitePassed &&
                        ((std::isfinite(trialMerit) && trialMerit <=
                            (1.0-options.lineSearchSufficientDecrease*factor)*originalMerit) || converged(trialInfo));
                } else rejectedDiagnostics = std::move(trialResponse.diagnostics);
                if (acceptable) {
                    u = std::move(trialU);
                    result.lambda = trialLambda;
                    info.correctionTranslation = trialInfo.correctionTranslation;
                    info.correctionRotation = trialInfo.correctionRotation;
                    info.lastCorrectionFactor = factor;
                    info.minimumCorrectionFactor = std::min(info.minimumCorrectionFactor,factor);
                    applied = true;
                    break;
                }
                ++info.lineSearchBacktracks;
                factor *= options.lineSearchReduction;
            }
            if (!applied) {
                result.evidence = info;
                result.diagnostics.insert(result.diagnostics.end(),rejectedDiagnostics.begin(),rejectedDiagnostics.end());
                result.diagnostics.push_back(problem("NEWTON_LINE_SEARCH_LIMIT","line_search",
                    "No bounded fixed-anchor correction reduced the residual/control merit or met all convergence criteria."));
                return result;
            }
        }
        result.evidence = info;
        result.diagnostics.push_back(problem("NEWTON_ITERATION_LIMIT","maximum_iterations","Full Newton failed the joint residual, correction and control criteria."));
        return result;
    }

    Candidate solve(double gravity, bool displacementControl, double parameter,
                    int halvings, const std::string& stage) {
        const bool eligible = model.revisedPredictorsEnabled && displacementControl &&
            options.transitionResolutionEnabled &&
            model.definition.discretization.lengthMapping == "element_length_v2" &&
            anchor.loadFactor > 0.0 && parameter > control(anchor.displacement);
        const bool revised = eligible && compressionPeakAccepted;
        int totalNewton=0, predictorNewton=0, attempts=0, evaluations=0, backtracks=0;
        int decompositions=0, predictorLinears=0;
        double minimumFactor=1.0;
        auto account = [&](const Candidate& candidate, bool predictor) {
            totalNewton+=candidate.evidence.totalNewtonEvaluations;
            if (predictor) predictorNewton+=candidate.evidence.totalNewtonEvaluations;
            evaluations+=candidate.evidence.lineSearchEvaluations;
            backtracks+=candidate.evidence.lineSearchBacktracks;
            minimumFactor=std::min(minimumFactor,candidate.evidence.minimumCorrectionFactor);
        };
        auto finish = [&](Candidate candidate, double factor=missing, const std::string& kind="",
                          double amplitude=missing, double advance=missing, int modeIndex=0) {
            candidate.evidence.totalNewtonEvaluations=totalNewton;
            candidate.evidence.predictorAttempts=attempts;
            candidate.evidence.predictorNewtonEvaluations=predictorNewton;
            candidate.evidence.predictorSeedFactor=factor;
            candidate.evidence.predictorKind=kind;
            candidate.evidence.predictorAmplitude=amplitude;
            candidate.evidence.predictorControlAdvance=advance;
            candidate.evidence.predictorDecompositions=decompositions;
            candidate.evidence.predictorLinearSolves=predictorLinears;
            candidate.evidence.predictorModeIndex=modeIndex;
            candidate.evidence.lineSearchEvaluations=evaluations;
            candidate.evidence.lineSearchBacktracks=backtracks;
            candidate.evidence.minimumCorrectionFactor=minimumFactor;
            return candidate;
        };
        const double desired=parameter-control(anchor.displacement);
        const bool previousCompatible=anchorWasJack && gravity==anchor.gravityFactor &&
            previousAcceptedDisplacement.size()==anchor.displacement.size();
        const double priorAdvance=previousCompatible ? control(anchor.displacement)-control(previousAcceptedDisplacement) : missing;
        const bool hasDirectionalHistory=previousCompatible && std::isfinite(priorAdvance) &&
            priorAdvance>=options.transitionDisplacementTolerance;
        if (revised && hasDirectionalHistory) {
            const double factor=desired/priorAdvance;
            if (priorAdvance>=options.transitionDisplacementTolerance && std::isfinite(factor) && factor>0 && factor<=8) {
                Candidate seed;seed.lambda=anchor.loadFactor+factor*(anchor.loadFactor-previousAcceptedLambda);
                seed.response.state.displacement=anchor.displacement;
                for (auto dof:model.freeDofs) seed.response.state.displacement[dof]+=factor*(anchor.displacement[dof]-previousAcceptedDisplacement[dof]);
                ++attempts;
                auto candidate=solveNewton(gravity,true,parameter,halvings,stage,&seed);
                account(candidate,true);
                if (candidate.ok) return finish(std::move(candidate),factor,"accepted_secant",missing,priorAdvance);
                if (candidate.cancelled) return finish(std::move(candidate));
            }
        }
        auto ordinary=solveNewton(gravity,displacementControl,parameter,halvings,stage);
        account(ordinary,false);
        if (ordinary.ok || ordinary.cancelled || !eligible) return finish(std::move(ordinary));
        Candidate onsetSeed;
        const bool beforePeak = !compressionPeakAccepted;
        if (beforePeak) {
            // Preserve ordinary cutbacks before a disposable onset rescue.
            // A distant prepeak anchor can otherwise select a disconnected root.
            if (desired*0.5>=options.minimumStep) return finish(std::move(ordinary));
            if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
            const auto seedScales=scales(model.definition.controlDirection*desired);
            const Vector seedResidual=external(gravity,anchor.loadFactor)-anchorResponse.internalForce;
            ++predictorLinears;
            const auto direction=linear(anchorResponse.tangent,seedResidual,seedScales,true,
                                        model.definition.controlDirection*desired);
            if (!direction.ok) return finish(std::move(ordinary));
            onsetSeed.lambda=anchor.loadFactor+direction.solution[model.freeDofs.size()];
            onsetSeed.response.state.displacement=anchor.displacement;
            for (std::size_t i=0;i<model.freeDofs.size();++i)
                onsetSeed.response.state.displacement[model.freeDofs[i]]+=direction.solution[static_cast<Eigen::Index>(i)];
            // A disposable evaluated crossing is only an initial-guess gate.
            // It never promotes the compression onset latch or material history.
            ++totalNewton; ++predictorNewton;
            const auto trial=evaluateSystem(model,anchor,onsetSeed.response.state.displacement);
            if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
            if (!trial.ok || onsetMaximum(anchorResponse,"compression_peak").ratio>=1.0 ||
                onsetMaximum(trial,"compression_peak").ratio<1.0) return finish(std::move(ordinary));
        }
        double decrement=1e-4*std::max(std::abs(anchor.loadFactor),1.0);
        for (int attempt=0; (beforePeak || !hasDirectionalHistory || lastAcceptedCompressionPeak) && attempt<6; ++attempt,decrement*=2.0) {
            if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
            if (decrement>0.02*anchor.loadFactor) break;
            const double load=anchor.loadFactor-decrement;
            if (beforePeak) onsetSeed.lambda=load;
            ++attempts;
            // Every disposable probe and correction uses the SAME committed anchor.
            // Only the original displacement solution may reach accept/persist.
            auto probe=solveNewton(gravity,false,0.0,halvings,"unloading_predictor",
                                   beforePeak ? &onsetSeed : nullptr,load,false,std::min(20,options.maximumIterations),false);
            account(probe,true);
            if (probe.cancelled) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
            if (!probe.ok) continue;
            const double advance=control(probe.response.state.displacement)-control(anchor.displacement);
            if (!std::isfinite(advance) || advance<=0.0) continue;
            const double factor=desired/advance;
            if (!std::isfinite(factor) || factor<=0.0 || factor>8.0) continue;
            for (auto dof:model.freeDofs)
                probe.response.state.displacement[dof]=anchor.displacement[dof]+
                    factor*(probe.response.state.displacement[dof]-anchor.displacement[dof]);
            probe.lambda=anchor.loadFactor+factor*(load-anchor.loadFactor);
            auto corrected=solveNewton(gravity,true,parameter,halvings,stage,&probe);
            account(corrected,true);
            if (corrected.cancelled) return finish(std::move(corrected));
            if (!corrected.ok) continue;
            corrected.diagnostics.push_back({beforePeak ? "ONSET_SEEDED_PROBE_USED" : "UNLOADING_PREDICTOR_USED","information","analysis","","initial_guess",
                "A bounded disposable unloading predictor produced the original displacement solution; all joint convergence tests satisfied. An onset seed is trial-only; normal accepted onset localization remains required."});
            return finish(std::move(corrected),factor,"force_unloading",missing,advance);
        }
        if (beforePeak) return finish(std::move(ordinary));
        // A critical mode supplies a disposable basin-selection seed. The
        // controlled displacement is eliminated ONLY from this diagnostic
        // Jacobian; its physical force equation remains a row and lambda is an
        // unknown. Formal acceptance always uses the original augmented solve.
        const int n=static_cast<int>(model.freeDofs.size());
        if (n>static_cast<int>(criticalModeEquationLimit)) {
            ordinary.diagnostics.push_back(problem("CRITICAL_MODE_RESOURCE_BOUND","initial_guess",
                "The bounded dense initial-guess decomposition is unavailable above 384 free equations."));
            return finish(std::move(ordinary));
        }
        if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
        const auto physicalScales=scales(model.definition.controlDirection*desired);
        const Vector residual=external(gravity,anchor.loadFactor)-anchorResponse.internalForce;
        ++predictorLinears;
        const auto baseDirection=linear(anchorResponse.tangent,residual,physicalScales,true,
                                       model.definition.controlDirection*desired);
        if (!baseDirection.ok) return finish(std::move(ordinary));
        std::vector<std::size_t> unknown;
        unknown.reserve(static_cast<std::size_t>(n-1));
        for (auto dof:model.freeDofs) if (dof!=model.controlDof) unknown.push_back(dof);
        double pattern=0.0;
        for (auto dof:model.freeDofs) pattern=std::max(pattern,std::abs(model.referenceLoad[dof])/
            (dof%3==2?physicalScales.momentScale:physicalScales.forceScale));
        const double lambdaScale=1.0/pattern;
        Eigen::MatrixXd jacobian(n,n);
        Eigen::VectorXd columnScale(n),columnNorm(n);
        for (int i=0;i<n;++i) {
            const auto row=model.freeDofs[static_cast<std::size_t>(i)];
            const double rowScale=row%3==2?physicalScales.momentScale:physicalScales.forceScale;
            for (int j=0;j<n-1;++j) {
                const auto col=unknown[static_cast<std::size_t>(j)];
                columnScale[j]=col%3==2?physicalScales.rotationScale:physicalScales.translationScale;
                jacobian(i,j)=anchorResponse.tangent.coeff(static_cast<int>(row),static_cast<int>(col))*columnScale[j]/rowScale;
            }
            columnScale[n-1]=lambdaScale;
            jacobian(i,n-1)=-model.referenceLoad[row]*lambdaScale/rowScale;
        }
        if (!jacobian.allFinite()) return finish(std::move(ordinary));
        for (int j=0;j<n;++j) {
            columnNorm[j]=std::max(jacobian.col(j).norm(),1e-12);
            jacobian.col(j)/=columnNorm[j];
        }
        if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
        ++decompositions;
        const Eigen::JacobiSVD<Eigen::MatrixXd> decomposition(jacobian,Eigen::ComputeThinV);
        for (int modeIndex=0;modeIndex<std::min(criticalModeCount,n);++modeIndex) {
            const auto weak=decomposition.matrixV().col(n-1-modeIndex);
            std::vector<double> mode(anchor.displacement.size(),0.0);
            double normalization=0.0;
            for (int j=0;j<n-1;++j) {
                const auto dof=unknown[static_cast<std::size_t>(j)];
                mode[dof]=columnScale[j]*weak[j]/columnNorm[j];
                if (dof%3!=2) normalization=std::max(normalization,std::abs(mode[dof]));
            }
            if (normalization<1e-14) for (auto dof:model.freeDofs)
                if (dof%3==2) normalization=std::max(normalization,options.referenceLength*std::abs(mode[dof]));
            if (!std::isfinite(normalization) || normalization<=0.0) continue;
            for (auto& value:mode) value/=normalization;
            double modalLambda=columnScale[n-1]*weak[n-1]/columnNorm[n-1]/normalization;
            double sign=0.0;
            for (auto dof:model.freeDofs) if (dof%3!=2 && std::abs(mode[dof])>=0.5) { sign=mode[dof]; break; }
            if (sign==0.0) for (auto dof:model.freeDofs) if (mode[dof]!=0.0) { sign=mode[dof]; break; }
            if (sign<0.0) { for (auto& value:mode) value=-value; modalLambda=-modalLambda; }
            for (double amplitudeFactor:criticalModeStepFactors) {
                if (cancelled()) { ordinary.cancelled=true; return finish(std::move(ordinary)); }
                const double amplitude=amplitudeFactor*options.maximumStep;
                Candidate seed;
                seed.response.state.displacement=anchor.displacement;
                for (int j=0;j<n;++j) {
                    const auto dof=model.freeDofs[static_cast<std::size_t>(j)];
                    seed.response.state.displacement[dof]+=baseDirection.solution[j]+amplitude*mode[dof];
                }
                seed.lambda=anchor.loadFactor+baseDirection.solution[n]+amplitude*modalLambda;
                ++attempts;
                auto candidate=solveNewton(gravity,true,parameter,halvings,stage,&seed);
                account(candidate,true);
                if (candidate.cancelled) return finish(std::move(candidate));
                if (!candidate.ok) continue;
                candidate.diagnostics.push_back({"CRITICAL_MODE_PREDICTOR_USED","information","analysis","","initial_guess",
                    "A bounded weak-mode initial guess selected a converged equilibrium branch; the final original displacement/force equations and all joint criteria passed. The seed is not a physical imperfection or stability prediction."});
                return finish(std::move(candidate),std::abs(amplitudeFactor),"critical_mode",amplitude,desired,modeIndex+1);
            }
        }
        ordinary.diagnostics.push_back(problem("CRITICAL_MODE_PREDICTOR_EXHAUSTED","initial_guess",
            "No bounded disposable predictor produced a converged original displacement candidate."));
        return finish(std::move(ordinary));
    }

    OnsetMaximum onsetMaximum(const SystemResponse& response, const std::string& kind) const {
        OnsetMaximum maximum;
        maximum.kind = kind;
        if (response.sections.size() != model.sections.size())
            throw DomainError(problem("TRANSITION_SECTION_LAYOUT","sections","Transition states do not match the prepared Gauss sections."));
        const auto visit = [&](const FiberResult& point, const MaterialParameters& material) {
            if (material.kind != MaterialKind::Concrete02) return;
            const double threshold = kind == "tensile_onset" ? material.ft/material.E0 : material.e0;
            // Tensile excursion includes the constitutive compression-unloading
            // intercept. minStrain is a historical compression amplitude, not a
            // map of an inapplicable current unloading stress.
            const double value = kind == "tensile_onset" ? point.state.tensionEnvelopeStrain :
                std::max(0.0,-point.state.minStrain);
            if (!std::isfinite(threshold) || threshold <= 0.0 || !std::isfinite(value) || value < 0.0)
                throw DomainError(problem("TRANSITION_HISTORY_INVALID",kind,"A formal constitutive onset measure is invalid.",point.id));
            const double ratio = value/threshold;
            if (!std::isfinite(ratio))
                throw DomainError(problem("TRANSITION_HISTORY_INVALID",kind,"A constitutive onset ratio is nonfinite.",point.id));
            if (ratio > maximum.ratio) {
                maximum.ratio = ratio;
                maximum.threshold = threshold;
                maximum.pointId = point.id;
            }
        };
        for (std::size_t i=0; i<response.sections.size(); ++i) {
            const auto& section = response.sections[i];
            const auto& prepared = model.sections[i];
            if (section.id != prepared.id || section.fibers.size() != prepared.fibers.size() ||
                section.edges.size() != prepared.edges.size())
                throw DomainError(problem("TRANSITION_POINT_LAYOUT","sections","Transition material points do not match their prepared owners.",prepared.id));
            for (std::size_t j=0; j<section.fibers.size(); ++j)
                if (prepared.fibers[j].area > 0.0) visit(section.fibers[j],prepared.fibers[j].material);
            for (std::size_t j=0; j<section.edges.size(); ++j) visit(section.edges[j],prepared.edges[j].material);
        }
        return maximum;
    }

    OnsetMaximum pendingOnset(const SystemResponse& response) const {
        OnsetMaximum maximum;
        if (!tensileOnsetAccepted) maximum = onsetMaximum(response,"tensile_onset");
        if (!compressionPeakAccepted) {
            const auto compression = onsetMaximum(response,"compression_peak");
            if (compression.ratio > maximum.ratio) maximum = compression;
        }
        return maximum;
    }

    bool localizeTransition(Candidate& upper, bool displacementControl, double upperParameter,
                            TransitionInfo& evidence) {
        Candidate lower;
        lower.ok = true;
        lower.lambda = anchor.loadFactor;
        lower.gravity = anchor.gravityFactor;
        lower.response = anchorResponse;
        lower.evidence = anchorEvidence;
        const double start = displacementControl ? control(anchor.displacement) : anchor.gravityFactor;
        double low = start, high = upperParameter, trialUpper = high;
        evidence.active = true;
        // The original converged crossing is the first attempted candidate;
        // all subsequent successes and failures consume the same bounded budget.
        evidence.candidateSolves = 1;
        auto update = [&]() {
            const auto a = pendingOnset(lower.response);
            const auto b = pendingOnset(upper.response);
            evidence.kind = b.kind;
            evidence.pointId = b.pointId;
            evidence.threshold = b.threshold;
            evidence.lowerControl = control(lower.response.state.displacement);
            evidence.upperControl = control(upper.response.state.displacement);
            evidence.lowerRatio = a.ratio;
            evidence.upperRatio = b.ratio;
            evidence.lowerConverged = lower.ok;
            evidence.upperConverged = upper.ok;
            evidence.lowerEvidence = lower.evidence;
            evidence.upperEvidence = upper.evidence;
            return b;
        };
        auto maximum = update();
        if (evidence.lowerRatio >= 1.0 || maximum.ratio < 1.0 || !(high > low)) return false;
        if (maximum.ratio == 1.0) {
            // An exactly attained, converged onset is itself a zero-width
            // closed bracket. No uncomputed state or extra solve is invented.
            evidence.lowerControl = evidence.upperControl;
            evidence.lowerRatio = evidence.upperRatio;
            evidence.lowerEvidence = evidence.upperEvidence;
            return true;
        }
        while (evidence.candidateSolves < options.maximumTransitionSolves) {
            maximum = update();
            if (std::abs(evidence.upperControl-evidence.lowerControl) <= options.transitionDisplacementTolerance &&
                maximum.ratio >= 1.0 && maximum.ratio-1.0 <= options.transitionRatioTolerance) return true;
            double middle = low+0.5*(trialUpper-low);
            if (displacementControl && middle-start < options.transitionMinimumStep)
                middle = start+options.transitionMinimumStep;
            // Gravity is parameterized by its multiplier, but only actual mm
            // displacement and dimensionless strain ratios enter acceptance.
            if (!(middle > low && middle < high)) break;
            ++evidence.candidateSolves;
            auto candidate = solve(displacementControl ? anchor.gravityFactor : middle,
                                   displacementControl,middle,0,"transition");
            if (candidate.cancelled) return false;
            if (!candidate.ok) {
                trialUpper = middle;
                summary.diagnostics.insert(summary.diagnostics.end(),candidate.diagnostics.begin(),candidate.diagnostics.end());
                continue;
            }
            if (pendingOnset(candidate.response).ratio >= 1.0) {
                upper = std::move(candidate);
                high = middle;
            } else {
                lower = std::move(candidate);
                low = middle;
            }
            trialUpper = high;
        }
        maximum = update();
        return std::abs(evidence.upperControl-evidence.lowerControl) <= options.transitionDisplacementTolerance &&
            maximum.ratio >= 1.0 && maximum.ratio-1.0 <= options.transitionRatioTolerance;
    }

    EdgeMaximum edgeMaximum(const SystemResponse& response) {
        EdgeMaximum result;
        if (physicalMode()) {
            const auto window = gaugeWindow(response,response.state.gravityFactor < 1.0 ? Phase::G : Phase::J);
            result.ratio = window.compressionMaximum/window.threshold;
            result.excess = window.compressionMaximum-window.threshold;
            result.allowedExcess = std::max(options.eventStrainAbsolute,options.eventStrainRelative*window.threshold);
            if (!std::isfinite(result.ratio))
                throw DomainError(problem("PHYSICAL_GAUGE_RATIO_INVALID","compression_criterion","Nonfinite physical criterion ratio."));
            return result;
        }
        for (const auto& section : response.sections) for (const auto& edge : section.edges) {
            if (!std::isfinite(edge.referenceThreshold) || edge.referenceThreshold <= 0.0) continue;
            const double reference = edge.state.maxReferenceCompressionStrain;
            if (!std::isfinite(reference) || reference < 0.0)
                throw DomainError(problem("EVENT_HISTORY_INVALID","max_reference_compression_strain","A formal edge history has an invalid maximum reference strain.",edge.id));
            const double ratio = reference/edge.referenceThreshold;
            if (ratio > result.ratio) {
                result.ratio = ratio;
                result.excess = reference-edge.referenceThreshold;
                result.allowedExcess = std::max(options.eventStrainAbsolute,options.eventStrainRelative*edge.referenceThreshold);
            }
        }
        return result;
    }

    bool localize(Candidate& upper, bool displacementControl, double upperParameter,
                  LocalizationInfo& evidence) {
        evidence.active = true;
        Candidate lower;
        lower.ok = true;
        lower.lambda = anchor.loadFactor;
        lower.gravity = anchor.gravityFactor;
        lower.response = anchorResponse;
        lower.evidence = anchorEvidence;
        double low = displacementControl ? control(anchor.displacement) : anchor.gravityFactor;
        double high = upperParameter;
        bool lowerIsAnchor = true;
        if (physicalMode()) {
            evidence.kind = "bracket";
            evidence.controlUnit = "mm";
            evidence.candidateSolves = 1; // The original converged crossing consumed one solve.
        }
        auto update = [&]() {
            const auto a = edgeMaximum(lower.response);
            const auto b = edgeMaximum(upper.response);
            evidence.lowerControl = control(lower.response.state.displacement);
            evidence.upperControl = control(upper.response.state.displacement);
            evidence.lowerRatio = a.ratio;
            evidence.upperRatio = b.ratio;
            evidence.finalExcess = b.excess;
            evidence.lowerConverged = lower.ok;
            evidence.upperConverged = upper.ok;
            evidence.lowerEvidence = lower.evidence;
            evidence.upperEvidence = upper.evidence;
            if (physicalMode()) {
                evidence.lowerGravityFactor = lower.gravity;
                evidence.upperGravityFactor = upper.gravity;
                evidence.bracketWidth = std::abs(evidence.upperControl-evidence.lowerControl);
                evidence.lowerGauge = gaugeWindow(lower.response,lowerIsAnchor ? anchorPhase :
                    (displacementControl ? Phase::J : Phase::G));
                if (lowerIsAnchor) evidence.lowerGauge->identity.stepId = summary.memoryLastStepId;
                evidence.upperGauge = gaugeWindow(upper.response,displacementControl ? Phase::J : Phase::G);
            }
            return b;
        };
        auto maximum = update();
        if (physicalMode() && (evidence.lowerRatio >= 1.0 || maximum.ratio < 1.0 || !(high > low))) return false;
        if (maximum.excess == 0.0) {
            if (physicalMode()) {
                evidence.kind = "exact_crossing";
                evidence.lowerControl = evidence.lowerRatio = evidence.lowerGravityFactor = missing;
                evidence.lowerConverged = false;
                evidence.lowerEvidence = {};
                evidence.lowerGauge.reset();
                evidence.bracketWidth = 0.0;
                return true;
            }
            evidence.lowerControl = evidence.upperControl;
            evidence.lowerRatio = evidence.upperRatio;
            evidence.lowerEvidence = evidence.upperEvidence;
            return true;
        }
        double trialUpper = high;
        while (evidence.candidateSolves < options.maximumLocalizationSolves) {
            maximum = update();
            const double width = std::abs(evidence.upperControl-evidence.lowerControl);
            if (width <= options.eventDisplacementTolerance && maximum.excess >= 0.0 && maximum.excess <= maximum.allowedExcess)
                return true;
            if (width < options.minimumStep || !(high > low)) break;
            const double middle = low+0.5*(trialUpper-low);
            if (!(middle > low && middle < high)) break;
            if (displacementControl && middle-control(anchor.displacement) < options.minimumStep) break;
            ++evidence.candidateSolves;
            auto candidate = solve(displacementControl ? anchor.gravityFactor : middle,
                                   displacementControl,middle,0,"localization");
            if (candidate.cancelled) return false;
            if (!candidate.ok) {
                // A failed candidate is not an endpoint. Keep the last two
                // converged endpoints and retry closer to the safe endpoint.
                trialUpper = middle;
                summary.diagnostics.insert(summary.diagnostics.end(),candidate.diagnostics.begin(),candidate.diagnostics.end());
                continue;
            }
            if (edgeMaximum(candidate.response).ratio >= 1.0) {
                upper = std::move(candidate);
                high = middle;
            } else {
                lower = std::move(candidate);
                lowerIsAnchor = false;
                low = middle;
            }
            trialUpper = high;
            if (physicalMode() && edgeMaximum(upper.response).excess == 0.0) {
                update();
                evidence.kind = "exact_crossing";
                evidence.lowerControl = evidence.lowerRatio = evidence.lowerGravityFactor = missing;
                evidence.lowerConverged = false;
                evidence.lowerEvidence = {};
                evidence.lowerGauge.reset();
                evidence.bracketWidth = 0.0;
                return true;
            }
        }
        maximum = update();
        if (physicalMode()) return evidence.bracketWidth <= options.eventDisplacementTolerance &&
            evidence.lowerRatio < 1.0 && maximum.excess >= 0.0 && maximum.excess <= maximum.allowedExcess;
        return false;
    }

    EventRecord event(const FiberResult& point, const SectionResult& section,
                      EventKind kind, const AcceptedStep& step) const {
        EventRecord record;
        record.kind = kind;
        record.stepId = step.identity.stepId;
        record.elementId = section.elementId;
        record.sectionId = section.id;
        record.pointId = point.id;
        record.side = point.side;
        record.id = step.identity.stepId+":"+point.id+":"+std::to_string(static_cast<int>(kind));
        record.x = point.x;
        record.eta = point.eta;
        record.strain = point.strain;
        record.stress = point.stress;
        record.maxCompressionStrain = std::max(0.0,-point.state.minStrain);
        record.lch = kind == EventKind::FirstCrack ? point.lchT : point.lchC;
        record.referenceLength = point.referenceLength;
        record.referenceModulus = point.referenceModulus;
        record.deltaC = point.deltaC;
        record.referenceStrain = point.referenceCompressionStrain;
        record.P = step.P;
        record.controlDisplacement = step.controlDisplacement;
        record.localization = step.localization;
        for (const auto& prepared : model.sections) if (prepared.id == section.id) {
            if (point.role == "edge") {
                for (const auto& edge : prepared.edges) if (edge.id == point.id) {
                    record.source = edge.material.source;
                    record.threshold = kind == EventKind::FirstCrack ? edge.material.ft/edge.material.E0 : edge.material.crushReferenceStrain;
                    break;
                }
            } else {
                for (const auto& fiber : prepared.fibers) if (fiber.id == point.id) {
                    record.source = fiber.material.source;
                    record.threshold = fiber.material.fy;
                    break;
                }
            }
            break;
        }
        record.criterion = kind == EventKind::FirstCrack ? "physical_edge_tension_ft_over_E0" :
                           kind == EventKind::FirstYield ? "real_steel_first_return_to_yield_surface" : "route_C_fixed_physical_gauge_v1";
        return record;
    }

    double axialAt(const std::vector<double>& u, double x, double eta) const {
        for (const auto& element : model.elements) {
            const double left = model.nodes[element.nodes[0]].x;
            if (x < left-1e-9 || x > left+element.length+1e-9) continue;
            const double t = std::clamp((x-left)/element.length,0.0,1.0);
            const auto& d = element.dofs;
            const double a = element.cosine*u[d[0]]+element.sine*u[d[1]];
            const double b = element.cosine*u[d[3]]+element.sine*u[d[4]];
            const double v1 = -element.sine*u[d[0]]+element.cosine*u[d[1]];
            const double v2 = -element.sine*u[d[3]]+element.cosine*u[d[4]];
            const double theta = (-6*t+6*t*t)/element.length*v1 + (1-4*t+3*t*t)*u[d[2]] +
                                 (6*t-6*t*t)/element.length*v2 + (-2*t+3*t*t)*u[d[5]];
            return (1-t)*a+t*b-eta*theta;
        }
        throw DomainError(problem("OBSERVATION_POSITION","gauges","A gauge endpoint lies outside the prepared beam."));
    }

    double relativeDeflection(const std::vector<double>& u) const {
        const auto& supports = model.definition.supports;
        double leftX = model.definition.controlX, rightX = leftX;
        for (const auto& support : supports) if (support.constrained[1]) {
            leftX = std::min(leftX,support.x);
            rightX = std::max(rightX,support.x);
        }
        const auto ordinate = [&](double x) {
            for (const auto& node : model.nodes) if (std::abs(node.x-x) <= 1e-8) return u[node.dofs[1]];
            throw DomainError(problem("OBSERVATION_NODE","supports","A support/control observation node is missing."));
        };
        double value = u[model.nodes[model.controlNode].dofs[1]];
        if (model.definition.observations.settlementCorrection && rightX > leftX) {
            const double t = (model.definition.controlX-leftX)/(rightX-leftX);
            value -= (1-t)*ordinate(leftX)+t*ordinate(rightX);
        }
        return model.definition.observations.downwardPositive ? -value : value;
    }

    AcceptedStep output(const Candidate& candidate, Phase phase, const LocalizationInfo& localization,
                        const TransitionInfo& transition) const {
        AcceptedStep step;
        step.identity = model.identity;
        step.identity.stepId = model.identity.runId+":step:"+std::to_string(acceptedCount);
        step.sequence = acceptedCount;
        step.phase = phase;
        step.loadFactor = candidate.lambda;
        step.gravityFactor = candidate.gravity;
        const auto forces = jackLoads(candidate.lambda);
        step.P1 = forces[0]; step.P2 = forces[1]; step.P = forces[0]+forces[1];
        step.displacement = candidate.response.state.displacement;
        step.controlDisplacement = phase == Phase::SG || phase == Phase::S0 ? 0.0 : control(step.displacement);
        const auto force = external(candidate.gravity,candidate.lambda);
        step.externalForce.assign(force.data(),force.data()+force.size());
        const Vector reaction = candidate.response.internalForce-force;
        step.reactions.assign(reaction.data(),reaction.data()+reaction.size());
        step.sections = candidate.response.sections;
        for (const auto& section : step.sections) {
            step.fibers.insert(step.fibers.end(),section.fibers.begin(),section.fibers.end());
            step.edges.insert(step.edges.end(),section.edges.begin(),section.edges.end());
        }
        step.elementForces = candidate.response.elementForces;
        for (std::size_t i=0; i<step.elementForces.size(); ++i) {
            auto& end = step.elementForces[i];
            const double length = model.elements.at(i).length;
            end.distributedAxial *= candidate.gravity;
            end.distributedTransverse *= candidate.gravity;
            const double axial = end.distributedAxial*length/2;
            const double shear = end.distributedTransverse*length/2;
            const double moment = end.distributedTransverse*length*length/12;
            const std::array<double,6> consistent{axial,shear,moment,axial,shear,-moment};
            for (std::size_t j=0; j<6; ++j) end.endForces[j] -= consistent[j];
            end.source = "element_end_equilibrium_after_consistent_gravity";
        }
        step.convergence = candidate.evidence;
        step.localization = localization;
        step.transition = transition;
        if (phase == Phase::SG && step.transition.active) {
            // SG defines the new observation zero. Preserve a displacement
            // bracket in that same reference, never a gravity-factor interval.
            const double shift = control(step.displacement);
            step.transition.lowerControl -= shift;
            step.transition.upperControl -= shift;
        }
        const bool useCandidateReference = phase == Phase::SG || (phase == Phase::S0 && !model.definition.gravity.enabled);
        const auto& reference = useCandidateReference ? step.displacement : referenceDisplacement;
        const Id referenceId = useCandidateReference ? step.identity.stepId : referenceStep;
        ObservationValue w;
        w.id = "midspan_deflection"; w.quantity = "displacement"; w.unit = "mm";
        w.source = "global_Y_relative_support_chord";
        w.total = relativeDeflection(step.displacement);
        if (!reference.empty()) w.increment = w.total-relativeDeflection(reference);
        w.referenceStepId = referenceId;
        step.observations.push_back(w);
        for (const auto& gauge : model.definition.observations.gauges) {
            ObservationValue value;
            value.id = gauge.id; value.quantity = "average_axial_strain"; value.unit = "1";
            value.source = "physical_gauge_u0_minus_eta_theta_not_route_C";
            value.total = (axialAt(step.displacement,gauge.x2,gauge.eta)-axialAt(step.displacement,gauge.x1,gauge.eta))/(gauge.x2-gauge.x1);
            if (!reference.empty()) value.increment = value.total-
                (axialAt(reference,gauge.x2,gauge.eta)-axialAt(reference,gauge.x1,gauge.eta))/(gauge.x2-gauge.x1);
            value.referenceStepId = referenceId;
            step.observations.push_back(std::move(value));
        }
        if (!crackRecorded) for (const auto& section : step.sections) for (const auto& point : section.edges)
            if (point.state.cracked) step.events.push_back(event(point,section,EventKind::FirstCrack,step));
        if (!yieldRecorded) for (const auto& section : step.sections) for (const auto& point : section.fibers)
            if (point.role == "steel" && point.state.yielded) step.events.push_back(event(point,section,EventKind::FirstYield,step));
        if (!physicalMode()) for (const auto& section : step.sections) for (const auto& point : section.edges)
            if (std::isfinite(point.referenceThreshold) && point.referenceThreshold > 0.0 &&
                point.state.maxReferenceCompressionStrain >= point.referenceThreshold)
                step.events.push_back(event(point,section,EventKind::ReferenceCompression,step));
        return step;
    }

    bool accept(Candidate candidate, Phase phase, const LocalizationInfo& localization = {},
                const TransitionInfo& transition = {}) {
        // Everything which can fail before acceptance, including allocation of
        // the complete output, occurs before promoting any history.
        auto step = output(candidate,phase,localization,transition);
        if (physicalMode()) {
            auto current = gaugeWindow(candidate.response,phase);
            current.identity = step.identity;
            current.controlDisplacement = step.controlDisplacement;
            auto historical = acceptedGaugeHistory;
            if (!historical || current.compressionMaximum > historical->compressionMaximum) historical = current;
            historical->status = "accepted_historical";
            step.gauge = GaugeStateEvidence{current,*historical};
            if (current.compressionMaximum >= current.threshold) {
                EventRecord record;
                record.id = step.identity.stepId+":physical_gauge_compression";
                record.stepId = step.identity.stepId;
                record.kind = EventKind::PhysicalGaugeCompression;
                record.side = current.side;
                record.criterion = physicalGaugeMeanMode;
                record.source = model.compressionCriterion.source;
                record.threshold = current.threshold;
                record.P = step.P;
                record.controlDisplacement = step.controlDisplacement;
                record.localization = step.localization;
                record.gauge = step.gauge;
                step.events.push_back(std::move(record));
            }
        }
        const bool acceptTension = options.transitionResolutionEnabled && !tensileOnsetAccepted &&
            onsetMaximum(candidate.response,"tensile_onset").ratio >= 1.0;
        const bool acceptCompression = options.transitionResolutionEnabled && !compressionPeakAccepted &&
            onsetMaximum(candidate.response,"compression_peak").ratio >= 1.0;
        if (phase == Phase::S0 && (acceptTension || acceptCompression))
            candidate.diagnostics.push_back({"INITIAL_ONSET_PRESENT","information","analysis","","transition",
                "The accepted prescribed initial state already contains an onset; no unresolved earlier loading bracket is invented."});
        if (transition.active && acceptTension && acceptCompression)
            candidate.diagnostics.push_back({"COINCIDENT_STRUCTURAL_ONSETS","information","analysis",transition.pointId,"transition",
                "Both structural onsets lie in the accepted common pending-onset bracket; stored transition identifies the limiting normalized ratio."});
        auto promotedState = candidate.response.state;
        auto promotedGaugeHistory = step.gauge ? std::optional<GaugeWindowEvidence>(step.gauge->historical) : acceptedGaugeHistory;
        auto promotedPrevious = phase==Phase::J && anchorWasJack ? anchor.displacement : std::vector<double>{};
        auto promotedReference = (phase == Phase::SG || (phase == Phase::S0 && !model.definition.gravity.enabled)) ?
            step.displacement : referenceDisplacement;
        summary.physicalEvidence.reserve(summary.physicalEvidence.size()+step.events.size()+1);
        summary.diagnostics.reserve(summary.diagnostics.size()+candidate.diagnostics.size());
        if (cancelled()) {
            summary.termination = Termination::Cancelled;
            summary.message = "Cancelled before candidate acceptance; the candidate was discarded.";
            return false;
        }
        if (phase == Phase::J && anchorWasJack) {
            previousAcceptedDisplacement = std::move(promotedPrevious);
            previousAcceptedLambda = anchor.loadFactor;
        } else previousAcceptedDisplacement.clear();
        anchorWasJack = phase==Phase::J;
        anchor = std::move(promotedState);
        anchorResponse = std::move(candidate.response);
        anchorEvidence = candidate.evidence;
        anchorPhase = phase;
        acceptedGaugeHistory = std::move(promotedGaugeHistory);
        tensileOnsetAccepted = tensileOnsetAccepted || acceptTension;
        compressionPeakAccepted = compressionPeakAccepted || acceptCompression;
        lastAcceptedTransition = transition.active;
        lastAcceptedCompressionPeak = transition.active && transition.kind=="compression_peak";
        summary.memoryLastStep = static_cast<std::int64_t>(step.sequence);
        summary.memoryLastStepId = step.identity.stepId;
        ++acceptedCount;
        if (phase == Phase::S0) zeroControl = anchor.displacement[model.controlDof];
        for (const auto& record : step.events) {
            crackRecorded = crackRecorded || record.kind == EventKind::FirstCrack;
            yieldRecorded = yieldRecorded || record.kind == EventKind::FirstYield;
            summary.physicalCriterionReached = summary.physicalCriterionReached || record.kind == EventKind::ReferenceCompression ||
                record.kind == EventKind::PhysicalGaugeCompression;
            summary.physicalEvidence.push_back(record);
        }
        if (step.P > maximumP) {
            maximumP = step.P;
            pathMaximum = {};
            pathMaximum.id = model.identity.runId+":path_maximum";
            pathMaximum.kind = EventKind::PathMaximum;
            pathMaximum.stepId = step.identity.stepId;
            pathMaximum.P = step.P;
            pathMaximum.controlDisplacement = step.controlDisplacement;
            pathMaximum.criterion = "maximum_of_accepted_path_no_extrapolation";
            pathMaximum.source.category = "derived";
        }
        if (phase == Phase::SG || (phase == Phase::S0 && !model.definition.gravity.enabled)) {
            zeroControl = anchor.displacement[model.controlDof];
            referenceDisplacement = std::move(promotedReference);
            referenceStep = step.identity.stepId;
            referenceReady = true;
        }
        summary.localization = localization;
        summary.diagnostics.insert(summary.diagnostics.end(),candidate.diagnostics.begin(),candidate.diagnostics.end());
        if (hooks.persist) {
            PersistedStepReceipt receipt;
            try { receipt = hooks.persist(step); }
            catch (const std::exception& e) { receipt.diagnostics.push_back(problem("PERSIST_CALLBACK_ERROR","persist",e.what())); }
            catch (...) { receipt.diagnostics.push_back(problem("PERSIST_CALLBACK_ERROR","persist","Persistence callback threw an unknown exception.")); }
            if (!receipt.ok || receipt.persistedSequence != static_cast<std::int64_t>(step.sequence) || !sameIdentity(receipt.identity,step.identity)) {
                summary.termination = Termination::StorageError;
                summary.message = "Numerical step accepted in memory, but its complete persistence was not confirmed.";
                summary.diagnostics.insert(summary.diagnostics.end(),receipt.diagnostics.begin(),receipt.diagnostics.end());
                summary.diagnostics.push_back(problem("PERSISTENCE_NOT_CONFIRMED","receipt","A successful exact-identity complete-step receipt is required before advancing."));
                // A cancellation arriving during a failed transaction is
                // secondary evidence and must not replace the storage reason.
                try { cancelled(); } catch (const DomainError& e) { summary.diagnostics.push_back(e.diagnostic); }
                return false;
            }
            summary.persistedLastStep = receipt.persistedSequence;
            summary.persistedLastStepId = receipt.identity.stepId;
        }
        const bool request = cancelled();
        if (summary.physicalCriterionReached) {
            summary.termination = physicalMode() ? Termination::PhysicalGaugeCompression : Termination::ReferenceCompression;
            summary.message = physicalMode() ? "Accepted state reached the localized total compatible physical edge-window criterion." :
                "Accepted state reached the localized fixed-gauge reference compression criterion.";
            return false;
        }
        if (request) {
            summary.termination = Termination::Cancelled;
            summary.message = "Cancelled after completing the accepted step and persistence critical interval.";
            return false;
        }
        return true;
    }

    bool failed(const Candidate& candidate, const std::string& stage) {
        if (candidate.cancelled) {
            summary.termination = Termination::Cancelled;
            summary.message = "Cancelled at a Newton safe point; unaccepted trial history was discarded.";
        } else {
            summary.termination = candidate.linearFailure ? Termination::LinearFailure : Termination::NonConvergence;
            summary.message = stage+" failed after the permitted step reductions; no physical terminal event was accepted.";
            summary.diagnostics.insert(summary.diagnostics.end(),candidate.diagnostics.begin(),candidate.diagnostics.end());
        }
        return false;
    }

    bool process(Candidate candidate, Phase phase, bool displacementControl, double parameter) {
        TransitionInfo transition;
        if (options.transitionResolutionEnabled && pendingOnset(candidate.response).ratio >= 1.0) {
            if (!localizeTransition(candidate,displacementControl,parameter,transition)) {
                summary.termination = summary.cancellationRequested ? Termination::Cancelled : Termination::LocalizationFailure;
                summary.message = "No accepted structural onset: converged physical-control bracket and constitutive excess were not jointly established.";
                summary.diagnostics.push_back(problem("TRANSITION_LOCALIZATION_FAILED","transition",
                    "Unaccepted onset candidates were discarded; no onset latch or material history was promoted.",transition.pointId));
                return false;
            }
            parameter = displacementControl ? control(candidate.response.state.displacement) : candidate.gravity;
        }
        LocalizationInfo localization;
        if (edgeMaximum(candidate.response).ratio >= 1.0) {
            if (!localize(candidate,displacementControl,parameter,localization)) {
                summary.localization = localization;
                summary.termination = summary.cancellationRequested ? Termination::Cancelled : Termination::LocalizationFailure;
                summary.message = "No acceptable terminal state: converged bracket width and reference-strain excess were not jointly established.";
                summary.diagnostics.push_back(problem("EVENT_LOCALIZATION_FAILED","localization","Unaccepted crossing candidates were discarded; the last accepted anchor is unchanged."));
                return false;
            }
            // If crushing precedes or coincides with a pending onset, the
            // terminally localized candidate is authoritative. A preliminary
            // later onset bracket must not be attached to a different state.
            transition = {};
        }
        // A localized gravity terminal state need not have reached full
        // gravity, even when the original overshooting target was SG.
        if (!displacementControl && phase == Phase::SG &&
            (candidate.gravity != 1.0 || (physicalMode() && localization.active))) phase = Phase::G;
        return accept(std::move(candidate),phase,localization,transition);
    }

    bool validate() {
        const std::size_t count = model.nodes.size()*3;
        const double positive[] = {options.initialStep,options.minimumStep,options.maximumStep,options.maximumControlDisplacement,
            options.referenceLength,options.referenceDisplacement,options.referenceForce,options.residualAbsolute,options.residualRelative,
            options.correctionAbsolute,options.correctionRelative,options.controlAbsolute,options.controlRelative,options.backwardTolerance,
            options.eventDisplacementTolerance,options.eventStrainAbsolute,options.eventStrainRelative,options.growthFactor};
        for (const double value : positive) if (!std::isfinite(value) || value <= 0.0) return false;
        const double revisedPositive[] = {options.lineSearchReduction,options.lineSearchSufficientDecrease,
            options.minimumLineSearchFactor,options.transitionDisplacementTolerance,
            options.transitionRatioTolerance,options.transitionMinimumStep};
        for (const double value : revisedPositive) if (!std::isfinite(value) || value <= 0.0) return false;
        if (options.maximumLineSearchTrials < 1 || options.maximumLineSearchTrials > 64 ||
            options.lineSearchReduction >= 1.0 || options.lineSearchSufficientDecrease >= 1.0 ||
            options.minimumLineSearchFactor > 1.0 || options.maximumTransitionSolves < 1 ||
            options.maximumTransitionSolves > 40 || options.transitionRatioTolerance >= 1.0 ||
            options.transitionMinimumStep > options.transitionDisplacementTolerance ||
            (options.transitionResolutionEnabled && options.transitionMinimumStep > options.minimumStep)) return false;
        if ((options.lineSearchEnabled || options.transitionResolutionEnabled) &&
            (options.maximumIterations > 60 || options.maximumHalvings > 18)) return false;
        if (count == 0 || model.elements.empty() || model.sections.empty() || model.freeDofs.empty() ||
            model.referenceLoad.size() != count || model.gravityLoad.size() != count ||
            model.controlDof >= count || model.controlNode >= model.nodes.size() ||
            model.prescribedValues.size() != model.constrainedDofs.size() ||
            options.maximumIterations <= 0 || options.maximumHalvings < 0 || options.gravitySteps <= 0 ||
            options.maximumAcceptedSteps == 0 || options.maximumLocalizationSolves <= 0 ||
            options.growthConsecutive <= 0 || options.growthIterationLimit < 0 || options.growthFactor < 1.0 ||
            options.minimumStep > options.initialStep || options.initialStep > options.maximumStep ||
            model.definition.loads.size() > 2 ||
            !std::isfinite(model.definition.controlDirection) || std::abs(model.definition.controlDirection) != 1.0) return false;
        reduced.assign(count,-1);
        std::vector<bool> assigned(count,false);
        for (std::size_t i=0; i<model.freeDofs.size(); ++i) {
            const auto dof = model.freeDofs[i];
            if (dof >= count || assigned[dof]) return false;
            assigned[dof] = true;
            reduced[dof] = static_cast<int>(i);
        }
        if (reduced[model.controlDof] < 0) return false;
        anchor.displacement.assign(count,0.0);
        for (std::size_t i=0; i<model.constrainedDofs.size(); ++i) {
            const auto dof = model.constrainedDofs[i];
            if (dof >= count || assigned[dof] || !std::isfinite(model.prescribedValues[i])) return false;
            assigned[dof] = true;
            anchor.displacement[dof] = model.prescribedValues[i];
        }
        for (std::size_t i=0; i<count; ++i)
            if (!assigned[i] || !std::isfinite(model.referenceLoad[i]) || !std::isfinite(model.gravityLoad[i])) return false;
        if (model.identity.projectId.empty() || model.identity.revisionId.empty() || model.identity.runId.empty() || model.identity.snapshotId.empty()) return false;
        return true;
    }

    bool compliance() {
        anchorResponse = evaluateSystem(model,anchor,anchor.displacement);
        if (!anchorResponse.ok) {
            summary.diagnostics = anchorResponse.diagnostics;
            return false;
        }
        const auto info = scales(0.0);
        Vector pattern(static_cast<Eigen::Index>(model.referenceLoad.size()));
        for (Eigen::Index i=0; i<pattern.size(); ++i) pattern[i] = model.referenceLoad[static_cast<std::size_t>(i)];
        const auto response = linear(anchorResponse.tangent,pattern,info,false,0.0);
        if (!response.ok) {
            summary.diagnostics.insert(summary.diagnostics.end(),response.diagnostics.begin(),response.diagnostics.end());
            return false;
        }
        double magnitude = 0.0;
        for (std::size_t i=0; i<model.freeDofs.size(); ++i) {
            const double scale = model.freeDofs[i]%3 == 2 ? options.referenceLength : 1.0;
            magnitude = std::max(magnitude,scale*std::abs(response.solution[static_cast<Eigen::Index>(i)]));
        }
        const double value = response.solution[reduced[model.controlDof]];
        if (!std::isfinite(value) || magnitude == 0.0 || std::abs(value) <= 64*std::numeric_limits<double>::epsilon()*magnitude) {
            summary.diagnostics.push_back(problem("CONTROL_ZERO_COMPLIANCE","control_dof","The proportional load pattern has no resolvable initial elastic response at the free control DOF."));
            return false;
        }
        return true;
    }

    void execute() {
        if (model.analysisReadOnly) {
            summary.termination = Termination::InputError;
            summary.message = "Verified historical prepared producer is analysis-read-only; prepare a fresh explicit revision to analyze.";
            summary.diagnostics.push_back(problem("ANALYSIS_READ_ONLY","prepared",summary.message));
            return;
        }
        if (!validate()) {
            summary.termination = Termination::InputError;
            summary.message = "Invalid prepared topology, numerical configuration, identities or finite loads.";
            summary.diagnostics.push_back(problem("ANALYSIS_INPUT_INVALID","prepared/options",summary.message));
            return;
        }
        if (model.compressionCriterion.mode != routeCPointMode && !physicalMode()) {
            summary.termination = Termination::InputError;
            summary.message = "Unsupported compression criterion mode.";
            summary.diagnostics.push_back(problem("COMPRESSION_CRITERION_INVALID","compression_criterion",summary.message));
            return;
        }
        if (physicalMode()) {
            const auto initialGauge = evaluatePhysicalGauge(model,anchor.displacement);
            if (!initialGauge.ok || initialGauge.window.compressionMaximum >= model.compressionCriterion.threshold) {
                summary.termination = Termination::InputError;
                summary.message = "Invalid initial physical-window criterion state or initial compression already at threshold.";
                summary.diagnostics = initialGauge.diagnostics;
                summary.diagnostics.push_back(problem("PHYSICAL_GAUGE_INITIAL_INVALID","compression_criterion",summary.message));
                return;
            }
        }
        if (cancelled()) { summary.termination = Termination::Cancelled; summary.message = "Cancelled before initialization."; return; }
        if (!compliance()) { summary.termination = Termination::InputError; summary.message = "Initial stiffness/control-compliance validation failed before accepting any step."; return; }
        auto initial = solve(0.0,false,0.0,0,"initialization");
        if (!initial.ok) { failed(initial,"Initialization"); return; }
        if (physicalMode() && edgeMaximum(initial.response).ratio >= 1.0) {
            summary.termination = Termination::InputError;
            summary.message = "Equilibrated initial state is already at the physical-window threshold; no loading bracket exists.";
            summary.diagnostics.push_back(problem("PHYSICAL_GAUGE_INITIAL_INVALID","compression_criterion",summary.message));
            return;
        }
        if (!accept(std::move(initial),Phase::S0)) return;
        if (model.definition.gravity.enabled) {
            for (int nominal=1; nominal<=options.gravitySteps; ++nominal) {
                const double goal = static_cast<double>(nominal)/options.gravitySteps;
                while (anchor.gravityFactor < goal) {
                    if (acceptedCount >= options.maximumAcceptedSteps) { safety("Accepted-step safety limit reached during gravity."); return; }
                    double target = goal;
                    Candidate candidate;
                    int halvings = 0;
                    for (;;) {
                        candidate = solve(target,false,0.0,halvings,"gravity");
                        if (candidate.ok || candidate.cancelled || halvings == options.maximumHalvings) break;
                        target = anchor.gravityFactor+0.5*(target-anchor.gravityFactor);
                        ++halvings;
                        progress("gravity",0,halvings,control(anchor.displacement),0.0,"Rejected candidate discarded; reducing gravity increment");
                    }
                    if (!candidate.ok) { failed(candidate,"Gravity establishment"); return; }
                    const Phase phase = target == 1.0 ? Phase::SG : Phase::G;
                    if (!process(std::move(candidate),phase,false,target)) return;
                }
            }
        }
        if (!referenceReady) throw DomainError(problem("REFERENCE_STATE_MISSING","SG","The accepted gravity reference is missing."));
        double increment = options.initialStep;
        int easy = 0;
        while (acceptedCount < options.maximumAcceptedSteps) {
            const double current = control(anchor.displacement);
            if (current >= options.maximumControlDisplacement) { safety("Downward control-displacement safety limit reached without the reference compression criterion."); return; }
            double step = std::min(increment,options.maximumControlDisplacement-current);
            Candidate candidate;
            int halvings = 0;
            for (;;) {
                candidate = solve(anchor.gravityFactor,true,current+step,halvings,"jack");
                if (candidate.ok || candidate.cancelled || halvings == options.maximumHalvings || step*0.5 < options.minimumStep) break;
                step *= 0.5;
                ++halvings;
                progress("jack",0,halvings,current,anchor.loadFactor,"Rejected candidate discarded; halving control increment");
            }
            if (!candidate.ok) { failed(candidate,"Displacement-controlled loading"); return; }
            const int iterations = candidate.evidence.iterations;
            if (!process(std::move(candidate),Phase::J,true,current+step)) return;
            if (lastAcceptedTransition) {
                increment = std::min(step,options.initialStep);
                easy = 0;
                continue;
            }
            increment = step;
            if (halvings == 0 && iterations <= options.growthIterationLimit) ++easy; else easy = 0;
            if (easy >= options.growthConsecutive) { increment = std::min(options.maximumStep,increment*options.growthFactor); easy = 0; }
        }
        safety("Whole-run accepted-step safety limit reached without the reference compression criterion.");
    }

    void safety(const std::string& message) {
        summary.termination = Termination::SafetyLimit;
        summary.message = message;
        summary.diagnostics.push_back(problem("ANALYSIS_SAFETY_LIMIT","limits",message));
    }

public:
    Analysis(const PreparedModel& prepared, const AnalysisOptions& settings, const AnalysisHooks& ports)
        : model(prepared), options(settings), hooks(ports) { summary.identity = model.identity; }

    RunSummary run() {
        try { execute(); }
        catch (const GaugeSearchCancelled&) {
            summary.termination = Termination::Cancelled;
            summary.message = "Cancelled during exact physical-window search; no candidate or trial history was promoted.";
        }
        catch (const DomainError& e) {
            summary.termination = Termination::InternalError;
            summary.message = e.what();
            summary.diagnostics.push_back(e.diagnostic);
        } catch (const std::exception& e) {
            summary.termination = Termination::InternalError;
            summary.message = e.what();
            summary.diagnostics.push_back(problem("ANALYSIS_EXCEPTION","run",e.what()));
        } catch (...) {
            summary.termination = Termination::InternalError;
            summary.message = "Unknown exception; no further candidate accepted.";
            summary.diagnostics.push_back(problem("ANALYSIS_EXCEPTION","run",summary.message));
        }
        if (std::isfinite(maximumP)) summary.physicalEvidence.push_back(pathMaximum);
        return summary;
    }
};
} // namespace

RunSummary runAnalysis(const PreparedModel& model, const AnalysisOptions& options, const AnalysisHooks& hooks) {
    return Analysis(model,options,hooks).run();
}
} // namespace lumafem
