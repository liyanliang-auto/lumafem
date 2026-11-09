#include "json_codec.hpp"
#include <array>
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <random>
#include <set>
#include <sstream>

namespace lumafem::codec {
namespace {
constexpr int schemaVersion = 1;
constexpr std::size_t maximumTransportBytes = 256u * 1024u * 1024u;
constexpr const char* numericsContract = numericalContractVersion;
Json predictorPolicyV1() {
    return {{"id","accepted_secant_critical_mode_v1"},{"status","candidate_requires_external_validation"},
        {"activation","element_length_v2_and_transition_resolution_after_accepted_compression_peak"},
        {"history","same_accepted_anchor_no_trial_commit"},{"maximum_secant_factor",8.0},
        {"minimum_previous_advance","transition_displacement_tolerance"},
        {"force_probe_activation","missing_directional_history_or_just_accepted_compression_peak"},
        {"maximum_force_probes",6},{"maximum_force_probe_iterations",20},
        {"initial_unloading_fraction",1e-4},{"unloading_growth",2.0},{"maximum_unloading_fraction",0.02},
        {"maximum_modal_equations",384},{"modal_step_factors",Json::array({0.1,-0.1,1.0,-1.0})},
        {"modal_base","original_augmented_linear_newton_predictor"},
        {"modal_jacobian","all_free_force_rows_control_unknown_eliminated_lambda_retained"},
        {"modal_scaling","physical_rows_columns_then_positive_column_norm_equilibration"},
        {"modal_sign","first_half_max_translation_positive_otherwise_first_nonzero"},
        {"modal_normalization","maximum_translation_one_mm_rotation_equivalent_fallback"},
        {"maximum_modal_decompositions",1},{"maximum_modal_base_linear_solves",1},
        {"maximum_predictor_attempts",11},{"formal_corrector","original_augmented_full_newton_actual_correction_all_joint_criteria"},
        {"work_scope","per_candidate_includes_all_predictors_excludes_other_cutbacks_and_brackets"}};
}
Json predictorPolicyV2() {
    auto value=predictorPolicyV1();
    value["id"]="accepted_secant_critical_subspace_v2";
    // Immutable A04 wire policy; producer verification must not follow future runtime defaults.
    value["maximum_modal_equations"]=384;
    value["modal_step_factors"]=Json::array({0.1,-0.1,1.0,-1.0});
    value["maximum_modal_modes"]=2;
    value["candidates_per_modal_direction"]=4;
    value["maximum_predictor_attempts"]=15;
    return value;
}
Json predictorPolicy() {
    auto value=predictorPolicyV2();
    value["id"]="accepted_secant_onset_seeded_probe_subspace_v3";
    value["activation"]="element_length_v2_and_transition_resolution_after_accepted_compression_peak_or_failed_prepeak_evaluated_linear_crossing";
    value["prepeak_order"]="ordinary_failure_then_original_augmented_linear_seed_then_fixed_force_probes_no_modal";
    value["prepeak_probe_seed"]="original_target_linear_displacement_seed_with_exact_fixed_probe_lambda";
    value["prepeak_crossing"]="valid_trial_compression_ratio_at_least_one_accepted_anchor_below_one_no_latch";
    value["prepeak_cutback_gate"]="half_desired_increment_below_existing_normal_minimum_step";
    value["maximum_prepeak_base_evaluations"]=1;
    value["maximum_prepeak_force_probes"]=6;
    value["prepeak_base_work"]="one_original_linear_solve_and_one_counted_predictor_residual_evaluation";
    return value;
}
constexpr std::array<const char*,10> revisedOptionKeys{{
    "line_search_enabled","maximum_line_search_trials","line_search_reduction",
    "line_search_sufficient_decrease","minimum_line_search_factor",
    "transition_resolution_enabled","maximum_transition_solves",
    "transition_displacement_tolerance","transition_ratio_tolerance","transition_minimum_step"}};

[[noreturn]] void invalid(const std::string& field, const std::string& message) {
    throw DomainError({"invalid_transport", "error", "format", "", field, message});
}
void object(const Json& j, const std::string& field) {
    if (!j.is_object()) invalid(field, "Expected an object.");
}
const Json& required(const Json& j, const char* key) {
    object(j, key);
    if (!j.contains(key)) invalid(key, "Missing required field.");
    return j.at(key);
}
std::string text(const Json& j, const std::string& field, bool nonempty = false) {
    if (!j.is_string()) invalid(field, "Expected text.");
    auto value = j.get<std::string>();
    if (nonempty && value.empty()) invalid(field, "Identity must not be empty.");
    return value;
}
double number(const Json& j, const std::string& field) {
    if (!j.is_number() || !std::isfinite(j.get<double>())) invalid(field, "Expected a finite number.");
    return j.get<double>();
}
template<class T> T integer(const Json& j, const std::string& field) {
    if (!(j.is_number_integer() || j.is_number_unsigned())) invalid(field, "Expected an integer.");
    const long double value = j.get<long double>();
    if (value < static_cast<long double>(std::numeric_limits<T>::lowest()) ||
        value > static_cast<long double>(std::numeric_limits<T>::max())) invalid(field, "Integer is out of range.");
    return j.get<T>();
}
bool boolean(const Json& j, const std::string& field) {
    if (!j.is_boolean()) invalid(field, "Expected a boolean.");
    return j.get<bool>();
}
const Json& array(const Json& j, const std::string& field) {
    if (!j.is_array()) invalid(field, "Expected an array.");
    return j;
}
void finiteJson(const Json& j, std::size_t depth = 0) {
    if (depth > 128) invalid("", "JSON nesting exceeds the supported bound.");
    if (j.is_number_float() && !std::isfinite(j.get<double>())) invalid("", "Nonfinite JSON number.");
    if (j.is_structured()) for (const auto& value : j) finiteJson(value, depth + 1);
}
Json numeric(double value) {
    if (std::isinf(value)) invalid("output", "An infinite result cannot be serialized.");
    return std::isnan(value) ? Json(nullptr) : Json(value);
}
std::string uniqueId(const char* prefix) {
    std::random_device generator;
    std::ostringstream result;
    result << prefix << '-' << std::hex << std::setfill('0');
    for (int i = 0; i < 4; ++i) result << std::setw(8) << static_cast<std::uint32_t>(generator());
    return result.str();
}
Identity identityFromJson(const Json& j, bool step = false) {
    object(j, "identity");
    Identity v;
    v.projectId = text(required(j, "project_id"), "project_id", true);
    v.revisionId = text(required(j, "revision_id"), "revision_id", true);
    v.snapshotId = text(required(j, "snapshot_id"), "snapshot_id", true);
    v.runId = text(required(j, "run_id"), "run_id", true);
    if (j.contains("step_id")) v.stepId = text(j.at("step_id"), "step_id", step);
    else if (step) invalid("step_id", "Receipt must identify its accepted step.");
    return v;
}
Provenance sourceFromJson(const Json& j) {
    object(j, "source");
    Provenance v;
    if (j.contains("category")) v.category = text(j.at("category"), "source.category");
    if (j.contains("reference")) v.reference = text(j.at("reference"), "source.reference");
    if (j.contains("quality")) v.quality = text(j.at("quality"), "source.quality");
    if (j.contains("version")) v.version = text(j.at("version"), "source.version");
    const std::set<std::string> allowed{"assumption","paper","measured","calibrated","synthetic","derived"};
    if (!allowed.count(v.category)) invalid("source.category", "Unknown provenance category.");
    return v;
}
// Private to the adapter: only verified known-producer reconstruction may use
// historical provenance defaults. Public new preparation has no bypass flag.
enum class SourceContext { NewDocument, FrozenProducer };
Provenance criterionSourceFromJson(const Json& criterion, SourceContext context) {
    if(context==SourceContext::FrozenProducer) return sourceFromJson(required(criterion,"source"));
    const std::string field="compression_criterion.source";
    if(!criterion.contains("source")) invalid(field,"Missing required field.");
    const auto& source=criterion.at("source"); object(source,field);
    const std::array<const char*,4> keys{{"category","reference","quality","version"}};
    const std::set<std::string> allowedKeys(keys.begin(),keys.end());
    for(const auto* key:keys) {
        if(!source.contains(key)) invalid(field+"."+key,"Missing required provenance field.");
        text(source.at(key),field+"."+key);
    }
    for(auto it=source.begin();it!=source.end();++it)
        if(!allowedKeys.count(it.key())) invalid(field+"."+it.key(),"Unknown provenance field.");
    const std::set<std::string> categories{"assumption","paper","measured","calibrated","synthetic","derived"};
    if(!categories.count(source.at("category").get<std::string>()))
        invalid(field+".category","Unknown provenance category.");
    return sourceFromJson(source);
}
Json sourceToJson(const Provenance& v) {
    return {{"category",v.category},{"reference",v.reference},{"quality",v.quality},{"version",v.version}};
}
Diagnostic diagnosticFromJson(const Json& j) {
    Diagnostic v;
    v.code = text(required(j,"code"),"code");
    v.severity = text(required(j,"severity"),"severity");
    v.stage = text(required(j,"stage"),"stage");
    v.objectId = text(required(j,"object_id"),"object_id");
    v.field = text(required(j,"field"),"field");
    v.message = text(required(j,"message"),"message");
    return v;
}
template<class T, class F> Json list(const std::vector<T>& values, F convert) {
    Json result = Json::array();
    for (const auto& value : values) result.push_back(convert(value));
    return result;
}
Json diagnosticsToJson(const std::vector<Diagnostic>& v) {
    return list(v,diagnosticToJson);
}
template<std::size_t N> std::array<double,N> numbers(const Json& j, const std::string& field) {
    array(j,field);
    if (j.size()!=N) invalid(field,"Array has an incorrect component count.");
    std::array<double,N> v{};
    for (std::size_t i=0;i<N;++i) v[i]=number(j.at(i),field);
    return v;
}

#define READ_D(field,key) if (j.contains(key)) v.field=number(j.at(key),key)
#define READ_I(field,key) if (j.contains(key)) v.field=integer<decltype(v.field)>(j.at(key),key)
#define READ_S(field,key) if (j.contains(key)) v.field=text(j.at(key),key)
#define READ_B(field,key) if (j.contains(key)) v.field=boolean(j.at(key),key)
#define PUT_D(field,key) j[key]=numeric(v.field)
#define PUT(field,key) j[key]=v.field

MaterialParameters materialFromJson(const Json& j) {
    object(j,"material");
    MaterialParameters v;
    v.id=text(required(j,"id"),"material.id",true);
    READ_S(version,"version");
    const auto kind=text(required(j,"kind"),"material.kind");
    if (kind=="concrete02") v.kind=MaterialKind::Concrete02;
    else if (kind=="steel_bilinear") v.kind=MaterialKind::SteelBilinear;
    else if (kind=="elastic") v.kind=MaterialKind::Elastic;
    else invalid("material.kind","Unsupported constitutive family.");
    v.source=sourceFromJson(required(j,"source"));
    // Only independent inputs are read. Local mapped fields are never trusted as editable inputs.
    for (const char* field : {"E0","Ets","etu","delta_u","eu","compression_slope","delta_critical","H","lch_t","lch_c"})
        if(j.contains(field)) invalid(field,"Derived material fields are read-only and cannot be independent source inputs.");
    READ_D(fc,"fc"); READ_D(e0,"e0"); READ_D(fr,"fr"); READ_D(ft,"ft");
    READ_D(Gft,"Gft"); READ_D(Gcp,"Gcp"); READ_D(unloadRatio,"unload_ratio");
    READ_D(referenceLength,"reference_length"); READ_D(crushReferenceStrain,"crush_reference_strain");
    READ_D(E,"E"); READ_D(fy,"fy"); READ_D(b,"b");
    return v;
}
Json materialToJson(const MaterialParameters& v) {
    Json j={{"id",v.id},{"version",v.version},{"source",sourceToJson(v.source)}};
    j["kind"]=v.kind==MaterialKind::Concrete02?"concrete02":v.kind==MaterialKind::SteelBilinear?"steel_bilinear":"elastic";
    PUT_D(fc,"fc"); PUT_D(e0,"e0"); PUT_D(fr,"fr"); PUT_D(ft,"ft"); PUT_D(Gft,"Gft"); PUT_D(Gcp,"Gcp");
    PUT_D(unloadRatio,"unload_ratio"); PUT_D(referenceLength,"reference_length"); PUT_D(crushReferenceStrain,"crush_reference_strain");
    PUT_D(E,"E"); PUT_D(fy,"fy"); PUT_D(b,"b"); PUT_D(lchT,"lch_t"); PUT_D(lchC,"lch_c");
    PUT_D(E0,"E0"); PUT_D(Ets,"Ets"); PUT_D(etu,"etu"); PUT_D(deltaU,"delta_u"); PUT_D(eu,"eu");
    PUT_D(compressionSlope,"compression_slope"); PUT_D(deltaCritical,"delta_critical"); PUT_D(H,"H");
    return j;
}
void validateRevisedOptions(const AnalysisOptions& v) {
    if(v.maximumLineSearchTrials<1 || v.maximumLineSearchTrials>64)
        invalid("maximum_line_search_trials","Expected a count from 1 through 64.");
    if(!std::isfinite(v.lineSearchReduction) || v.lineSearchReduction<=0.0 || v.lineSearchReduction>=1.0)
        invalid("line_search_reduction","Expected a finite factor strictly between zero and one.");
    if(!std::isfinite(v.lineSearchSufficientDecrease) || v.lineSearchSufficientDecrease<=0.0 || v.lineSearchSufficientDecrease>=1.0)
        invalid("line_search_sufficient_decrease","Expected a finite coefficient strictly between zero and one.");
    if(!std::isfinite(v.minimumLineSearchFactor) || v.minimumLineSearchFactor<=0.0 || v.minimumLineSearchFactor>1.0)
        invalid("minimum_line_search_factor","Expected a finite factor in (0,1].");
    if(v.maximumTransitionSolves<1 || v.maximumTransitionSolves>40)
        invalid("maximum_transition_solves","Expected a count from 1 through 40.");
    if(!std::isfinite(v.transitionDisplacementTolerance) || v.transitionDisplacementTolerance<=0.0)
        invalid("transition_displacement_tolerance","Expected a finite positive physical displacement tolerance.");
    if(!std::isfinite(v.transitionRatioTolerance) || v.transitionRatioTolerance<=0.0 || v.transitionRatioTolerance>=1.0)
        invalid("transition_ratio_tolerance","Expected a finite ratio tolerance strictly between zero and one.");
    if(!std::isfinite(v.transitionMinimumStep) || v.transitionMinimumStep<=0.0 ||
       (v.transitionResolutionEnabled && (!std::isfinite(v.minimumStep) || v.transitionMinimumStep>v.minimumStep)) ||
       v.transitionMinimumStep>v.transitionDisplacementTolerance)
        invalid("transition_minimum_step","Event increment must be positive, no greater than onset tolerance, and when enabled no greater than the normal minimum.");
}
AnalysisOptions optionsFromJson(const Json& j) {
    object(j,"analysis"); AnalysisOptions v;
    std::set<std::string> fields{"initial_step","minimum_step","maximum_step","maximum_iterations","maximum_halvings","growth_consecutive","growth_iteration_limit","growth_factor","gravity_steps","maximum_accepted_steps","maximum_control_displacement","maximum_localization_solves","reference_length","reference_displacement","reference_force","residual_absolute","residual_relative","correction_absolute","correction_relative","control_absolute","control_relative","backward_tolerance","event_displacement_tolerance","event_strain_absolute","event_strain_relative"};
    fields.insert(revisedOptionKeys.begin(),revisedOptionKeys.end());
    for(auto it=j.begin();it!=j.end();++it) if(!fields.count(it.key())) invalid(it.key(),"Unknown analysis option.");
    READ_D(initialStep,"initial_step"); READ_D(minimumStep,"minimum_step"); READ_D(maximumStep,"maximum_step");
    READ_I(maximumIterations,"maximum_iterations"); READ_I(maximumHalvings,"maximum_halvings");
    READ_I(growthConsecutive,"growth_consecutive"); READ_I(growthIterationLimit,"growth_iteration_limit"); READ_D(growthFactor,"growth_factor");
    READ_I(gravitySteps,"gravity_steps"); READ_I(maximumAcceptedSteps,"maximum_accepted_steps");
    READ_D(maximumControlDisplacement,"maximum_control_displacement"); READ_I(maximumLocalizationSolves,"maximum_localization_solves");
    READ_D(referenceLength,"reference_length"); READ_D(referenceDisplacement,"reference_displacement"); READ_D(referenceForce,"reference_force");
    READ_D(residualAbsolute,"residual_absolute"); READ_D(residualRelative,"residual_relative");
    READ_D(correctionAbsolute,"correction_absolute"); READ_D(correctionRelative,"correction_relative");
    READ_D(controlAbsolute,"control_absolute"); READ_D(controlRelative,"control_relative"); READ_D(backwardTolerance,"backward_tolerance");
    READ_D(eventDisplacementTolerance,"event_displacement_tolerance"); READ_D(eventStrainAbsolute,"event_strain_absolute"); READ_D(eventStrainRelative,"event_strain_relative");
    READ_B(lineSearchEnabled,"line_search_enabled"); READ_I(maximumLineSearchTrials,"maximum_line_search_trials");
    READ_D(lineSearchReduction,"line_search_reduction"); READ_D(lineSearchSufficientDecrease,"line_search_sufficient_decrease");
    READ_D(minimumLineSearchFactor,"minimum_line_search_factor"); READ_B(transitionResolutionEnabled,"transition_resolution_enabled");
    READ_I(maximumTransitionSolves,"maximum_transition_solves"); READ_D(transitionDisplacementTolerance,"transition_displacement_tolerance");
    READ_D(transitionRatioTolerance,"transition_ratio_tolerance"); READ_D(transitionMinimumStep,"transition_minimum_step");
    validateRevisedOptions(v);
    return v;
}
Json optionsToJson(const AnalysisOptions& v) {
    validateRevisedOptions(v);
    Json j=Json::object();
    PUT_D(initialStep,"initial_step"); PUT_D(minimumStep,"minimum_step"); PUT_D(maximumStep,"maximum_step");
    PUT(maximumIterations,"maximum_iterations"); PUT(maximumHalvings,"maximum_halvings");
    PUT(growthConsecutive,"growth_consecutive"); PUT(growthIterationLimit,"growth_iteration_limit"); PUT_D(growthFactor,"growth_factor");
    PUT(gravitySteps,"gravity_steps"); PUT(maximumAcceptedSteps,"maximum_accepted_steps");
    PUT_D(maximumControlDisplacement,"maximum_control_displacement"); PUT(maximumLocalizationSolves,"maximum_localization_solves");
    PUT_D(referenceLength,"reference_length"); PUT_D(referenceDisplacement,"reference_displacement"); PUT_D(referenceForce,"reference_force");
    PUT_D(residualAbsolute,"residual_absolute"); PUT_D(residualRelative,"residual_relative");
    PUT_D(correctionAbsolute,"correction_absolute"); PUT_D(correctionRelative,"correction_relative");
    PUT_D(controlAbsolute,"control_absolute"); PUT_D(controlRelative,"control_relative"); PUT_D(backwardTolerance,"backward_tolerance");
    PUT_D(eventDisplacementTolerance,"event_displacement_tolerance"); PUT_D(eventStrainAbsolute,"event_strain_absolute"); PUT_D(eventStrainRelative,"event_strain_relative");
    PUT(lineSearchEnabled,"line_search_enabled"); PUT(maximumLineSearchTrials,"maximum_line_search_trials");
    PUT_D(lineSearchReduction,"line_search_reduction"); PUT_D(lineSearchSufficientDecrease,"line_search_sufficient_decrease");
    PUT_D(minimumLineSearchFactor,"minimum_line_search_factor"); PUT(transitionResolutionEnabled,"transition_resolution_enabled");
    PUT(maximumTransitionSolves,"maximum_transition_solves"); PUT_D(transitionDisplacementTolerance,"transition_displacement_tolerance");
    PUT_D(transitionRatioTolerance,"transition_ratio_tolerance"); PUT_D(transitionMinimumStep,"transition_minimum_step");
    return j;
}
ModelDefinition modelFromJson(const Json& document, const Identity* frozen, SourceContext context) {
    object(document,"document");
    if (integer<int>(required(document,"schema_version"),"schema_version")!=schemaVersion) invalid("schema_version","Unsupported document schema.");
    ModelDefinition result;
    result.identity.projectId=text(required(document,"project_id"),"project_id",true);
    result.identity.revisionId=text(required(document,"revision_id"),"revision_id",true);
    result.name=text(required(document,"name"),"name");
    if (frozen) {
        if (frozen->projectId!=result.identity.projectId || frozen->revisionId!=result.identity.revisionId) invalid("identity","Source document does not belong to the frozen revision.");
        result.identity=*frozen;
    } else if (document.contains("identity")) {
        auto provided=identityFromJson(document.at("identity"));
        if (provided.projectId!=result.identity.projectId || provided.revisionId!=result.identity.revisionId || !provided.stepId.empty()) invalid("identity","Input identity disagrees with source revision or identifies a step.");
        result.identity=provided;
    } else {
        result.identity.snapshotId=uniqueId("snapshot"); result.identity.runId=uniqueId("run");
    }
    const auto& model=required(document,"model"); object(model,"model");
    if(model.contains("compression_criterion")) {
        const auto& j=model.at("compression_criterion"); object(j,"compression_criterion");
        const std::set<std::string> keys{"mode","gauge_length_mm","threshold","source"};
        for(auto it=j.begin();it!=j.end();++it)
            if(!keys.count(it.key())) invalid("compression_criterion."+it.key(),"Unknown criterion source field.");
        CompressionCriterionDefinition v;
        v.mode=text(required(j,"mode"),"compression_criterion.mode",true);
        v.gaugeLength=number(required(j,"gauge_length_mm"),"compression_criterion.gauge_length_mm");
        v.threshold=number(required(j,"threshold"),"compression_criterion.threshold");
        v.source=criterionSourceFromJson(j,context);
        if((v.mode!=routeCPointMode && v.mode!=physicalGaugeMeanMode) || v.gaugeLength<=0.0 || v.threshold<=0.0)
            invalid("compression_criterion","Unsupported mode or nonpositive criterion parameter.");
        result.compressionCriterion=std::move(v);
    }
    result.version=text(required(model,"version"),"model.version",true);
    if(result.version!="1") invalid("model.version","Unsupported model definition version.");
    const auto& geometry=required(model,"geometry");
    result.geometry.length=number(required(geometry,"length"),"geometry.length");
    result.geometry.width=number(required(geometry,"width"),"geometry.width");
    result.geometry.height=number(required(geometry,"height"),"geometry.height");
    result.geometry.angle=number(required(geometry,"angle"),"geometry.angle");
    result.concreteMaterialId=text(required(model,"concrete_material_id"),"concrete_material_id",true);
    const auto& reinforcement=required(model,"reinforcement"); object(reinforcement,"reinforcement");
    {
        const auto& j=reinforcement; auto& v=result.reinforcement;
        READ_S(mode,"mode"); READ_S(coverDefinition,"cover_definition"); READ_D(cover,"cover");
        READ_D(stirrupDiameter,"stirrup_diameter"); READ_D(stirrupSpacing,"stirrup_spacing");
        READ_I(topCount,"top_count"); READ_I(bottomCount,"bottom_count");
        READ_D(topDiameter,"top_diameter"); READ_D(bottomDiameter,"bottom_diameter");
        READ_S(topMaterialId,"top_material_id"); READ_S(bottomMaterialId,"bottom_material_id");
        for (const auto& bar : array(required(j,"bars"),"reinforcement.bars")) {
            RebarDefinition item;
            item.id=text(required(bar,"id"),"bar.id",true);
            item.materialId=text(required(bar,"material_id"),"bar.material_id",true);
            item.diameter=number(required(bar,"diameter"),"bar.diameter");
            if (bar.contains("area")) item.area=number(bar.at("area"),"bar.area");
            item.eta=number(required(bar,"eta"),"bar.eta"); item.zeta=number(required(bar,"zeta"),"bar.zeta");
            item.group=text(required(bar,"group"),"bar.group"); v.bars.push_back(item);
        }
    }
    for (const auto& j : array(required(model,"supports"),"supports")) {
        SupportDefinition v; v.id=text(required(j,"id"),"support.id",true); v.x=number(required(j,"x"),"support.x");
        const auto& flags=array(required(j,"constrained"),"support.constrained");
        if (flags.size()!=3) invalid("support.constrained","Expected ux,uy,theta flags.");
        for (std::size_t i=0;i<3;++i) v.constrained[i]=boolean(flags.at(i),"support.constrained");
        v.values=numbers<3>(required(j,"values"),"support.values"); result.supports.push_back(v);
    }
    for (const auto& j : array(required(model,"loads"),"loads")) {
        PointLoadDefinition v; v.id=text(required(j,"id"),"load.id",true); v.x=number(required(j,"x"),"load.x");
        v.reference=numbers<3>(required(j,"reference"),"load.reference"); result.loads.push_back(v);
    }
    result.controlX=number(required(model,"control_x"),"control_x");
    result.controlComponent=integer<int>(required(model,"control_component"),"control_component");
    result.controlDirection=number(required(model,"control_direction"),"control_direction");
    {
        const auto& j=required(model,"gravity"); object(j,"gravity"); auto& v=result.gravity;
        v.enabled=boolean(required(j,"enabled"),"gravity.enabled");
        v.density=number(required(j,"density"),"gravity.density"); v.acceleration=number(required(j,"acceleration"),"gravity.acceleration");
    }
    {
        const auto& j=required(model,"discretization"); object(j,"discretization"); auto& v=result.discretization;
        v.mesh=text(required(j,"mesh"),"mesh"); v.targetLength=number(required(j,"target_length"),"target_length");
        v.gaussPoints=integer<int>(required(j,"gauss_points"),"gauss_points"); v.layers=integer<int>(required(j,"layers"),"layers");
        // Absence in schema-1 JSON retains its historical meaning, independently of native defaults.
        v.lengthMapping=j.contains("length_mapping")?text(j.at("length_mapping"),"length_mapping"):"gauss_weight_v1";
        if(v.lengthMapping!="gauss_weight_v1" && v.lengthMapping!="element_length_v2")
            invalid("length_mapping","Unsupported constitutive length mapping version.");
    }
    result.analysis=optionsFromJson(required(model,"analysis"));
    for (const auto& j : array(required(document,"materials"),"materials")) result.materials.push_back(materialFromJson(j));
    {
        const auto& j=required(document,"observations"); object(j,"observations"); auto& v=result.observations;
        v.id=text(required(j,"id"),"observations.id",true);
        READ_S(referencePhase,"reference_phase"); READ_S(referenceStepId,"reference_step_id");
        READ_B(downwardPositive,"downward_positive"); READ_B(settlementCorrection,"settlement_correction");
        for (const auto& gauge : array(required(j,"gauges"),"gauges")) {
            GaugeDefinition item;
            item.id=text(required(gauge,"id"),"gauge.id",true); item.x1=number(required(gauge,"x1"),"gauge.x1");
            item.x2=number(required(gauge,"x2"),"gauge.x2"); item.eta=number(required(gauge,"eta"),"gauge.eta"); v.gauges.push_back(item);
        }
    }
    for (const auto& source : array(required(document,"sources"),"sources")) result.sources.push_back(sourceFromJson(source));
    return result;
}
Json fiberDefinitionToJson(const PreparedFiber& v) {
    return {{"id",v.id},{"material_id",v.materialId},{"role",v.role},{"eta",v.eta},{"zeta",v.zeta},{"area",v.area},{"material",materialToJson(v.material)}};
}
Json edgeDefinitionToJson(const EdgeMonitor& v) {
    return {{"id",v.id},{"side",v.side},{"eta",v.eta},{"material",materialToJson(v.material)}};
}
Json preparedToJson(const PreparedModel& v, const Json& document) {
    Json nodes=Json::array(),elements=Json::array(),sections=Json::array();
    for (const auto& n:v.nodes) nodes.push_back({{"id",n.id},{"numeric_id",n.numericId},{"x",n.x},{"global_x",n.globalX},{"global_y",n.globalY},{"dofs",n.dofs}});
    for (const auto& e:v.elements) elements.push_back({{"id",e.id},{"numeric_id",e.numericId},{"nodes",e.nodes},{"length",e.length},{"cosine",e.cosine},{"sine",e.sine},{"dofs",e.dofs},{"section_indices",e.sectionIndices}});
    for (const auto& s:v.sections) sections.push_back({{"id",s.id},{"element_id",s.elementId},{"element_index",s.elementIndex},{"gauss_index",s.gaussIndex},{"xi",s.xi},{"weight",s.weight},{"integration_length",s.integrationLength},{"x",s.x},{"lch_t",s.lchT},{"lch_c",s.lchC},{"fibers",list(s.fibers,fiberDefinitionToJson)},{"edges",list(s.edges,edgeDefinitionToJson)}});
    Json j={{"schema_version",schemaVersion},{"kernel_version",kernelVersion},{"numerics_contract",numericsContract},{"predictor_policy",predictorPolicy()},{"hash_encoding","canonical-json-utf8-v1-sha256"},
        {"identity",identityToJson(v.identity)},{"document",document},{"nodes",nodes},{"elements",elements},{"sections",sections},
        {"constrained_dofs",v.constrainedDofs},{"prescribed_values",v.prescribedValues},{"free_dofs",v.freeDofs},
        {"reference_load",v.referenceLoad},{"gravity_load",v.gravityLoad},{"control_dof",v.controlDof},{"control_node",v.controlNode},
        {"analysis",optionsToJson(v.definition.analysis)},{"diagnostics",diagnosticsToJson(v.diagnostics)},
        {"units",{{"length","mm"},{"force","N"},{"stress","MPa"},{"rotation","rad"},{"curvature","1/mm"},{"moment","N.mm"}}}};
    // Cover-generated bar centers are actual prepared geometry, separately from unmodified editable inputs.
    j["reinforcement"]=Json::array();
    for (const auto& b:v.definition.reinforcement.bars) j["reinforcement"].push_back({{"id",b.id},{"material_id",b.materialId},{"diameter",b.diameter},{"area",b.area},{"eta",b.eta},{"zeta",b.zeta},{"group",b.group}});
    const auto& criterion=v.compressionCriterion;
    const bool physical=criterion.mode==physicalGaugeMeanMode;
    j["compression_criterion"]={{"mode",criterion.mode},{"gauge_length_mm",numeric(criterion.gaugeLength)},
        {"threshold",numeric(criterion.threshold)},{"source",sourceToJson(criterion.source)},
        {"operator_version",physical?physicalGaugeOperatorVersion:routeCPointMode},
        {"reference",physical?physicalGaugeReference:"point_history"},
        {"search_domain_mm",physical?Json::array({numeric(v.compressionSearchDomain[0]),numeric(v.compressionSearchDomain[1])}):Json(nullptr)},
        {"edges",physical?Json::array({"top","bottom"}):Json::array()},
        {"roundoff_policy",physical?physicalGaugeRoundoffPolicy:"not_applicable"}};
    j["prepared_hash"]=contentHash(j);
    return j;
}
Json stateToJson(const MaterialState& v) {
    Json j=Json::object();
    PUT_D(strain,"strain"); PUT_D(stress,"stress"); PUT_D(tangent,"tangent");
    PUT_D(minStrain,"min_strain"); PUT_D(maxStrain,"max_strain"); PUT_D(compressionStress,"compression_stress"); PUT_D(tensionStress,"tension_stress");
    PUT_D(tensionEnvelopeStrain,"tension_envelope_strain");
    PUT_D(compressionUnloadStrain,"compression_unload_strain"); PUT_D(compressionUnloadSlope,"compression_unload_slope");
    PUT_D(tensionUnloadStrain,"tension_unload_strain"); PUT_D(tensionUnloadSlope,"tension_unload_slope");
    PUT_D(reversalStrain,"reversal_strain"); PUT_D(reversalStress,"reversal_stress");
    PUT_D(plasticStrain,"plastic_strain"); PUT_D(backStress,"back_stress"); PUT_D(accumulatedPlasticStrain,"accumulated_plastic_strain");
    PUT_D(deltaC,"delta_c"); PUT_D(maxDeltaC,"max_delta_c");
    PUT_D(referenceCompressionStrain,"reference_compression_strain"); PUT_D(maxReferenceCompressionStrain,"max_reference_compression_strain");
    PUT_D(tensionEnergy,"tension_energy"); PUT_D(compressionEnergy,"compression_energy");
    PUT(compressionLoading,"compression_loading"); PUT(cracked,"cracked"); PUT(yielded,"yielded"); PUT(crushed,"crushed"); PUT(direction,"direction"); PUT(branch,"branch");
    return j;
}
Json fiberToJson(const FiberResult& v) {
    Json j={{"id",v.id},{"section_id",v.sectionId},{"material_id",v.materialId},{"role",v.role},{"side",v.side},{"state",stateToJson(v.state)}};
    PUT_D(x,"x"); PUT_D(eta,"eta"); PUT_D(zeta,"zeta"); PUT_D(area,"area");
    PUT_D(strain,"strain"); PUT_D(stress,"stress"); PUT_D(tangent,"tangent");
    PUT_D(lchT,"lch_t"); PUT_D(lchC,"lch_c"); PUT_D(referenceLength,"reference_length"); PUT_D(referenceModulus,"reference_modulus");
    PUT_D(referenceCompressionStrain,"reference_compression_strain"); PUT_D(referenceThreshold,"reference_threshold");
    PUT_D(thresholdRatio,"threshold_ratio"); PUT_D(deltaC,"delta_c"); return j;
}
Json convergenceToJsonImpl(const ConvergenceInfo& v) {
    if(v.totalNewtonEvaluations<0 || v.predictorAttempts<0 || v.predictorAttempts>15 ||
       v.predictorNewtonEvaluations<0 || v.predictorNewtonEvaluations>v.totalNewtonEvaluations ||
       (std::isfinite(v.predictorSeedFactor) && (v.predictorSeedFactor<=0 || v.predictorSeedFactor>8 || v.predictorAttempts==0)))
        invalid("convergence.predictor","Invalid actual bounded predictor work or seed factor.");
    if((v.predictorKind!="" && v.predictorKind!="accepted_secant" && v.predictorKind!="force_unloading" && v.predictorKind!="critical_mode") ||
       v.predictorDecompositions<0 || v.predictorDecompositions>1 || v.predictorLinearSolves<0 || v.predictorLinearSolves>1 ||
       (!v.predictorKind.empty() && (!std::isfinite(v.predictorSeedFactor) || !std::isfinite(v.predictorControlAdvance) || v.predictorControlAdvance<=0)) ||
       (v.predictorKind=="critical_mode" && (!std::isfinite(v.predictorAmplitude) || v.predictorAmplitude==0 || v.predictorDecompositions!=1)))
        invalid("convergence.predictor","Invalid actual predictor kind, displacement or decomposition evidence.");
    if((v.predictorKind=="critical_mode" && (v.predictorModeIndex<1 || v.predictorModeIndex>criticalModeCount)) ||
       (v.predictorKind!="critical_mode" && v.predictorModeIndex!=0))
        invalid("convergence.predictor_mode_index","Successful modal direction must be 1-based; otherwise unavailable as zero.");
    if(v.lineSearchEvaluations<0 || v.lineSearchBacktracks<0 || v.lineSearchBacktracks>v.lineSearchEvaluations ||
       !std::isfinite(v.lastCorrectionFactor) || v.lastCorrectionFactor<=0.0 || v.lastCorrectionFactor>1.0 ||
       !std::isfinite(v.minimumCorrectionFactor) || v.minimumCorrectionFactor<=0.0 ||
       v.minimumCorrectionFactor>v.lastCorrectionFactor)
        invalid("convergence.line_search","Invalid actual backtracking counts or correction factors.");
    Json j=Json::object(); PUT(iterations,"iterations"); PUT(halvings,"halvings");
    PUT_D(residualForce,"residual_force"); PUT_D(residualMoment,"residual_moment");
    PUT_D(correctionTranslation,"correction_translation"); PUT_D(correctionRotation,"correction_rotation"); PUT_D(controlError,"control_error");
    PUT_D(forceScale,"force_scale"); PUT_D(momentScale,"moment_scale"); PUT_D(translationScale,"translation_scale"); PUT_D(rotationScale,"rotation_scale");
    PUT_D(targetIncrement,"target_increment"); PUT_D(backwardError,"backward_error");
    PUT(residualPassed,"residual_passed"); PUT(correctionPassed,"correction_passed"); PUT(controlPassed,"control_passed"); PUT(finitePassed,"finite_passed");
    PUT(lineSearchEvaluations,"line_search_evaluations"); PUT(lineSearchBacktracks,"line_search_backtracks");
    PUT_D(lastCorrectionFactor,"last_correction_factor"); PUT_D(minimumCorrectionFactor,"minimum_correction_factor");
    PUT(totalNewtonEvaluations,"total_newton_evaluations"); PUT(predictorAttempts,"predictor_attempts");
    PUT(predictorNewtonEvaluations,"predictor_newton_evaluations"); PUT_D(predictorSeedFactor,"predictor_seed_factor");
    PUT(predictorKind,"predictor_kind"); PUT_D(predictorControlAdvance,"predictor_control_advance");
    PUT_D(predictorAmplitude,"predictor_amplitude"); PUT(predictorDecompositions,"predictor_decompositions");
    PUT(predictorLinearSolves,"predictor_linear_solves"); PUT(predictorModeIndex,"predictor_mode_index"); return j;
}
Json transitionToJsonImpl(const TransitionInfo& v) {
    if(v.kind!="" && v.kind!="tensile_onset" && v.kind!="compression_peak")
        invalid("transition.kind","Unsupported nonterminal transition kind.");
    if(v.candidateSolves<0 || v.candidateSolves>40)
        invalid("transition.candidate_solves","Invalid actual transition solve count.");
    if(v.active && (v.kind.empty() || v.pointId.empty() || !std::isfinite(v.threshold) || v.threshold<=0.0 ||
        v.candidateSolves==0 || !v.lowerConverged || !v.upperConverged ||
        !std::isfinite(v.lowerControl) || !std::isfinite(v.upperControl) ||
        !std::isfinite(v.lowerRatio) || !std::isfinite(v.upperRatio)))
        invalid("transition","Active onset requires a located threshold and converged finite endpoints.");
    Json j={{"lower_evidence",convergenceToJsonImpl(v.lowerEvidence)},{"upper_evidence",convergenceToJsonImpl(v.upperEvidence)}};
    PUT(active,"active"); PUT(kind,"kind"); PUT(pointId,"point_id"); PUT_D(threshold,"threshold");
    PUT(candidateSolves,"candidate_solves"); PUT_D(lowerControl,"lower_control"); PUT_D(upperControl,"upper_control");
    PUT_D(lowerRatio,"lower_ratio"); PUT_D(upperRatio,"upper_ratio");
    PUT(lowerConverged,"lower_converged"); PUT(upperConverged,"upper_converged"); return j;
}
Json localizationToJson(const LocalizationInfo& v) {
    Json j={{"lower_evidence",convergenceToJsonImpl(v.lowerEvidence)},{"upper_evidence",convergenceToJsonImpl(v.upperEvidence)}};
    PUT(active,"active"); PUT(candidateSolves,"candidate_solves");
    PUT_D(lowerControl,"lower_control"); PUT_D(upperControl,"upper_control"); PUT_D(lowerRatio,"lower_ratio"); PUT_D(upperRatio,"upper_ratio");
    PUT_D(finalExcess,"final_excess"); PUT(lowerConverged,"lower_converged"); PUT(upperConverged,"upper_converged");
    if(!v.kind.empty()) {
        if(v.kind!="bracket" && v.kind!="exact_crossing") invalid("localization.kind","Unknown physical localization kind.");
        PUT(kind,"kind"); PUT(controlUnit,"control_unit");
        PUT_D(lowerGravityFactor,"lower_gravity_factor"); PUT_D(upperGravityFactor,"upper_gravity_factor");
        PUT_D(bracketWidth,"bracket_width");
        if(v.lowerGauge) j["lower_gauge"]=gaugeWindowToJson(*v.lowerGauge);
        if(v.upperGauge) j["upper_gauge"]=gaugeWindowToJson(*v.upperGauge);
    }
    return j;
}
const char* phaseName(Phase v) {
    switch(v) { case Phase::S0:return "S0"; case Phase::G:return "G"; case Phase::SG:return "SG"; case Phase::J:return "J"; }
    invalid("phase","Unknown analysis phase.");
}
const char* eventName(EventKind v) {
    switch(v) { case EventKind::FirstCrack:return "first_crack"; case EventKind::FirstYield:return "first_yield"; case EventKind::ReferenceCompression:return "reference_compression"; case EventKind::PathMaximum:return "path_maximum"; case EventKind::PhysicalGaugeCompression:return "physical_gauge_compression"; }
    invalid("event","Unknown event kind.");
}
const char* terminationName(Termination v) {
    switch(v) {
    case Termination::ReferenceCompression:return "reference_compression"; case Termination::SafetyLimit:return "safety_limit";
    case Termination::PhysicalGaugeCompression:return "physical_gauge_compression";
    case Termination::NonConvergence:return "non_convergence"; case Termination::LinearFailure:return "linear_failure";
    case Termination::LocalizationFailure:return "localization_failure"; case Termination::Cancelled:return "cancelled";
    case Termination::InputError:return "input_error"; case Termination::StorageError:return "storage_error"; case Termination::InternalError:return "internal_error";
    }
    invalid("termination","Unknown termination reason.");
}
Json eventToJson(const EventRecord& v) {
    Json j={{"id",v.id},{"kind",eventName(v.kind)},{"step_id",v.stepId},{"element_id",v.elementId},{"section_id",v.sectionId},{"point_id",v.pointId},{"side",v.side},{"criterion",v.criterion},{"source",sourceToJson(v.source)},{"localization",localizationToJson(v.localization)}};
    PUT_D(x,"x"); PUT_D(eta,"eta"); PUT_D(strain,"strain"); PUT_D(stress,"stress"); PUT_D(maxCompressionStrain,"max_compression_strain");
    PUT_D(lch,"lch"); PUT_D(referenceLength,"reference_length"); PUT_D(referenceModulus,"reference_modulus"); PUT_D(deltaC,"delta_c");
    PUT_D(referenceStrain,"reference_strain"); PUT_D(threshold,"threshold"); PUT_D(P,"P"); PUT_D(controlDisplacement,"control_displacement");
    if(v.gauge) j["gauge"]=gaugeStateToJson(*v.gauge);
    return j;
}
Json sectionToJson(const SectionResult& v) {
    return {{"id",v.id},{"element_id",v.elementId},{"gauss_index",v.gaussIndex},{"x",v.x},{"axial_strain",v.axialStrain},{"curvature",v.curvature},{"N",v.N},{"M",v.M},{"tangent",v.tangent},{"fibers",list(v.fibers,fiberToJson)},{"edges",list(v.edges,fiberToJson)}};
}
Json elementForceToJson(const ElementForceResult& v) {
    return {{"id",v.id},{"end_forces",v.endForces},{"distributed_axial",v.distributedAxial},{"distributed_transverse",v.distributedTransverse},{"source",v.source}};
}
Json observationToJson(const ObservationValue& v) {
    return {{"id",v.id},{"quantity",v.quantity},{"unit",v.unit},{"source",v.source},{"total",numeric(v.total)},{"increment",numeric(v.increment)},{"reference_step_id",v.referenceStepId}};
}

// SHA-256 (FIPS 180-4), local to the format adapter; no cryptographic dependency enters the kernel.
std::uint32_t rotate(std::uint32_t x,unsigned n) { return (x>>n)|(x<<(32-n)); }
std::string sha256(const std::string& value) {
    constexpr std::array<std::uint32_t,64> constants{{
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2}};
    std::array<std::uint32_t,8> hash{{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}};
    std::vector<unsigned char> bytes(value.begin(),value.end());
    const std::uint64_t bitLength=static_cast<std::uint64_t>(bytes.size())*8;
    bytes.push_back(0x80); while(bytes.size()%64!=56) bytes.push_back(0);
    for(int i=7;i>=0;--i) bytes.push_back(static_cast<unsigned char>(bitLength>>(i*8)));
    for(std::size_t base=0;base<bytes.size();base+=64) {
        std::array<std::uint32_t,64> w{};
        for(std::size_t i=0;i<16;++i) for(std::size_t b=0;b<4;++b) w[i]=(w[i]<<8)|bytes[base+4*i+b];
        for(std::size_t i=16;i<64;++i) {
            const auto s0=rotate(w[i-15],7)^rotate(w[i-15],18)^(w[i-15]>>3);
            const auto s1=rotate(w[i-2],17)^rotate(w[i-2],19)^(w[i-2]>>10);
            w[i]=w[i-16]+s0+w[i-7]+s1;
        }
        auto a=hash[0],b=hash[1],c=hash[2],d=hash[3],e=hash[4],f=hash[5],g=hash[6],h=hash[7];
        for(std::size_t i=0;i<64;++i) {
            const auto s1=rotate(e,6)^rotate(e,11)^rotate(e,25);
            const auto t1=h+s1+((e&f)^((~e)&g))+constants[i]+w[i];
            const auto s0=rotate(a,2)^rotate(a,13)^rotate(a,22);
            const auto t2=s0+((a&b)^(a&c)^(b&c));
            h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        hash[0]+=a;hash[1]+=b;hash[2]+=c;hash[3]+=d;hash[4]+=e;hash[5]+=f;hash[6]+=g;hash[7]+=h;
    }
    std::ostringstream result; result<<std::hex<<std::setfill('0');
    for(auto part:hash) result<<std::setw(8)<<part;
    return result.str();
}
#undef READ_D
#undef READ_I
#undef READ_S
#undef READ_B
#undef PUT_D
#undef PUT
} // namespace

Json parse(const std::string& textValue) {
    if (textValue.size()>maximumTransportBytes) invalid("","JSON transport exceeds 256 MiB.");
    try {
        auto result=Json::parse(textValue); finiteJson(result); return result;
    } catch(const DomainError&) { throw; }
    catch(const Json::exception& error) { throw DomainError({"invalid_json","error","format","","",error.what()}); }
}
std::string canonical(const Json& value) { finiteJson(value); return value.dump(-1,' ',false,Json::error_handler_t::strict); }
std::string contentHash(const Json& value) { return sha256(canonical(value)); }
Json diagnosticToJson(const Diagnostic& v) {
    return {{"code",v.code},{"severity",v.severity},{"stage",v.stage},{"object_id",v.objectId},{"field",v.field},{"message",v.message}};
}
Json identityToJson(const Identity& v) {
    return {{"project_id",v.projectId},{"revision_id",v.revisionId},{"snapshot_id",v.snapshotId},{"run_id",v.runId},{"step_id",v.stepId}};
}
Json prepareDocument(const Json& document) {
    try {
        finiteJson(document);
        auto result=prepareModel(modelFromJson(document,nullptr,SourceContext::NewDocument));
        if(!result.ok) return {{"ok",false},{"prepared",nullptr},{"diagnostics",diagnosticsToJson(result.diagnostics)}};
        auto prepared=preparedToJson(result.model,document);
        return {{"ok",true},{"prepared",prepared},{"diagnostics",diagnosticsToJson(result.diagnostics)}};
    } catch(const std::exception& error) {
        return {{"ok",false},{"prepared",nullptr},{"diagnostics",Json::array({diagnosticToJson(boundaryDiagnostic(error,"preparation"))})}};
    }
}
Json convergenceToJson(const ConvergenceInfo& value) { return convergenceToJsonImpl(value); }
Json transitionToJson(const TransitionInfo& value) { return transitionToJsonImpl(value); }
Json gaugeWindowToJson(const GaugeWindowEvidence& v) {
    Json j={{"mode",v.mode},{"operator_version",v.operatorVersion},{"reference",v.reference},
        {"gauge_length_mm",numeric(v.gaugeLength)},{"threshold",numeric(v.threshold)},
        {"search_domain_mm",Json::array({numeric(v.searchDomain[0]),numeric(v.searchDomain[1])})},
        {"side",v.side},{"eta",numeric(v.eta)},{"x1",numeric(v.x1)},{"x2",numeric(v.x2)},
        {"signed_mean",numeric(v.signedMean)},{"compression_maximum",numeric(v.compressionMaximum)},
        {"status",v.status},{"identity",identityToJson(v.identity)},{"phase",phaseName(v.phase)},
        {"control_displacement",numeric(v.controlDisplacement)},{"load_factor",numeric(v.loadFactor)},
        {"gravity_factor",numeric(v.gravityFactor)}};
    finiteJson(j); return j;
}
Json gaugeStateToJson(const GaugeStateEvidence& v) {
    return {{"current",gaugeWindowToJson(v.current)},{"historical",gaugeWindowToJson(v.historical)}};
}
PreparedInput decodePrepared(const Json& prepared, bool forAnalysis) {
    finiteJson(prepared); object(prepared,"prepared");
    if(integer<int>(required(prepared,"schema_version"),"schema_version")!=schemaVersion) invalid("schema_version","Unsupported prepared schema.");
    auto identity=identityFromJson(required(prepared,"identity"));
    if(!identity.stepId.empty()) invalid("identity.step_id","A prepared input cannot identify an accepted step.");
    const auto claimed=text(required(prepared,"prepared_hash"),"prepared_hash",true);
    auto original=prepared; original.erase("prepared_hash");
    if(claimed!=contentHash(original)) invalid("prepared_hash","Stored prepared content hash is invalid.");
    const auto kernel=text(required(prepared,"kernel_version"),"kernel_version",true);
    const bool legacy=kernel=="0.1.0" && !prepared.contains("numerics_contract");
    const bool priorNumerics=kernel==crNum001KernelVersion;
    const bool priorPredictor=kernel==crNum001A03KernelVersion;
    const bool priorA04=kernel==crNum001A04KernelVersion;
    const bool priorPhysical=kernel==crNum002KernelVersion;
    const bool priorCR1=priorNumerics || priorPredictor || priorA04;
    const bool readOnly=priorCR1 || priorPhysical;
    if(!legacy && ((kernel!=kernelVersion && !readOnly) ||
        text(required(prepared,"numerics_contract"),"numerics_contract")!=(priorCR1?"CR-NUM-001":numericsContract)))
        invalid("numerics_contract","Unsupported prepared numerical contract. Prepare a new snapshot explicitly.");
    const auto& document=required(prepared,"document");
    if((legacy || priorCR1) && required(document,"model").contains("compression_criterion"))
        invalid("compression_criterion","An old producer cannot declare a new criterion source field.");
    if(legacy) {
        const auto& options=required(required(document,"model"),"analysis");
        const auto& frozenOptions=required(prepared,"analysis");
        for(const auto* key:revisedOptionKeys)
            if(options.contains(key) || frozenOptions.contains(key))
                invalid(key,"A BL1 descriptor cannot declare revised numerical settings.");
        const auto& discretization=required(required(document,"model"),"discretization");
        if(discretization.contains("length_mapping") && text(discretization.at("length_mapping"),"length_mapping")!="gauss_weight_v1")
            invalid("length_mapping","A BL1 descriptor cannot declare a revised material length.");
    }
    // The original hash and known producer/contract have been checked above.
    // Preserve that producer's source defaults, then verify every reconstructed
    // descriptor below; never feed frozen sources through new-write validation.
    auto result=prepareModel(modelFromJson(document,&identity,SourceContext::FrozenProducer));
    if(!result.ok) {
        if(!result.diagnostics.empty()) throw DomainError(result.diagnostics.front());
        invalid("document","Frozen source no longer prepares successfully.");
    }
    auto reconstructed=preparedToJson(result.model,document);
    if(legacy || priorCR1) {
        // These producers predate this additive descriptor. Nothing else is dropped.
        reconstructed.erase("compression_criterion");
        reconstructed["kernel_version"]=kernel;
        if(priorCR1) reconstructed["numerics_contract"]="CR-NUM-001";
    }
    if(priorPhysical) reconstructed["kernel_version"]=kernel;
    if(priorA04 || priorPhysical) reconstructed["predictor_policy"]=predictorPolicyV2();
    if(legacy || priorNumerics) {
        reconstructed.erase("predictor_policy");
        reconstructed["kernel_version"]=kernel;
    }
    if(priorPredictor) {
        reconstructed["kernel_version"]=kernel;
        reconstructed["predictor_policy"]=predictorPolicyV1();
    }
    if(legacy) {
        // This exact, version-scoped projection removes only additive CR contract fields.
        // Old arrays, source metadata, diagnostics and every physical input still compare byte-canonically.
        reconstructed.erase("numerics_contract"); reconstructed["kernel_version"]="0.1.0";
        for(const auto* key:revisedOptionKeys) reconstructed["analysis"].erase(key);
    }
    if(legacy || readOnly) {
        reconstructed.erase("prepared_hash"); reconstructed["prepared_hash"]=contentHash(reconstructed);
    }
    if(claimed!=reconstructed.at("prepared_hash").get<std::string>() || canonical(prepared)!=canonical(reconstructed))
        invalid("prepared_hash","Prepared contents do not match the frozen source, mapping version, or canonical hash. Prepare a new snapshot.");
    if(readOnly && forAnalysis)
        invalid("kernel_version","Frozen 0.2.0/A03/A04 and 0.3.0 numerical snapshots are read-only. View saved results unchanged, or explicitly prepare a new snapshot for analysis.");
    result.model.preparedHash=claimed;
    // Existing BL1 compatibility runs retain their original algorithm. The
    // All frozen 0.2.* producers are explicitly read-only under this contract.
    result.model.revisedPredictorsEnabled=!legacy && !readOnly;
    result.model.analysisReadOnly=readOnly;
    return {std::move(result.model),document,readOnly};
}
Json stepToJson(const AcceptedStep& v) {
    Json j={{"identity",identityToJson(v.identity)},{"sequence",v.sequence},{"phase",phaseName(v.phase)},
        {"load_factor",numeric(v.loadFactor)},{"P",numeric(v.P)},{"P1",numeric(v.P1)},{"P2",numeric(v.P2)},
        {"gravity_factor",numeric(v.gravityFactor)},{"control_displacement",numeric(v.controlDisplacement)},
        {"displacement",v.displacement},{"reactions",v.reactions},{"external_force",v.externalForce},
        {"element_forces",list(v.elementForces,elementForceToJson)},{"sections",list(v.sections,sectionToJson)},
        {"fibers",list(v.fibers,fiberToJson)},{"edges",list(v.edges,fiberToJson)},
        {"events",list(v.events,eventToJson)},{"observations",list(v.observations,observationToJson)},
        {"convergence",convergenceToJsonImpl(v.convergence)},{"localization",localizationToJson(v.localization)},{"transition",transitionToJsonImpl(v.transition)}};
    if(v.gauge) j["gauge"]=gaugeStateToJson(*v.gauge);
    j["array_metadata"]={
        {"displacement",{{"shape",{v.displacement.size()/3,3}},{"components",{"ux","uy","theta"}},{"units",{"mm","mm","rad"}},{"location","node"}}},
        {"reactions",{{"shape",{v.reactions.size()/3,3}},{"components",{"Fx","Fy","Mz"}},{"units",{"N","N","N.mm"}},{"location","node"}}},
        {"external_force",{{"shape",{v.externalForce.size()/3,3}},{"components",{"Fx","Fy","Mz"}},{"units",{"N","N","N.mm"}},{"location","node"}}}};
    finiteJson(j); return j;
}
Json progressToJson(const ProgressInfo& v) {
    return {{"identity",identityToJson(v.identity)},{"sequence",v.sequence},{"stage",v.stage},{"message",v.message},{"accepted_steps",v.acceptedSteps},{"iteration",v.iteration},{"halvings",v.halvings},{"control_displacement",numeric(v.controlDisplacement)},{"P",numeric(v.P)}};
}
Json summaryToJson(const RunSummary& v) {
    return {{"identity",identityToJson(v.identity)},{"termination",terminationName(v.termination)},{"message",v.message},
        {"memory_last_step",v.memoryLastStep},{"persisted_last_step",v.persistedLastStep},{"memory_last_step_id",v.memoryLastStepId},{"persisted_last_step_id",v.persistedLastStepId},
        {"physical_criterion_reached",v.physicalCriterionReached},{"cancellation_requested",v.cancellationRequested},
        {"physical_evidence",list(v.physicalEvidence,eventToJson)},{"diagnostics",diagnosticsToJson(v.diagnostics)},{"localization",localizationToJson(v.localization)},
        {"validation_status",{{"A",v.validation.A},{"B",v.validation.B},{"C",v.validation.C},{"D",v.validation.D}}}};
}
PersistedStepReceipt receiptFromJson(const Json& j) {
    finiteJson(j); object(j,"receipt"); PersistedStepReceipt v;
    v.ok=boolean(required(j,"ok"),"receipt.ok"); v.identity=identityFromJson(required(j,"identity"),true);
    v.persistedSequence=integer<std::int64_t>(required(j,"persisted_sequence"),"receipt.persisted_sequence");
    if(v.persistedSequence< -1) invalid("persisted_sequence","Invalid persisted sequence.");
    v.transactionId=text(required(j,"transaction_id"),"receipt.transaction_id",v.ok);
    for(const auto& item:array(required(j,"diagnostics"),"receipt.diagnostics")) v.diagnostics.push_back(diagnosticFromJson(item));
    return v;
}
RunSummary errorSummary(const Diagnostic& diagnostic, const Identity& identity) {
    RunSummary result; result.identity=identity; result.termination=Termination::InputError;
    result.message=diagnostic.message; result.diagnostics.push_back(diagnostic); return result;
}
Diagnostic boundaryDiagnostic(const std::exception& error, const std::string& stage) {
    if(const auto* domain=dynamic_cast<const DomainError*>(&error)) return domain->diagnostic;
    return {"boundary_error","error",stage,"","",error.what()};
}
} // namespace lumafem::codec
