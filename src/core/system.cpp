#include "lumafem/system.hpp"

#include <Eigen/SparseLU>
#include <algorithm>
#include <cmath>
#include <exception>
#include <limits>
#include <string>
#include <unordered_set>
#include <utility>

namespace lumafem {
namespace {

using Vector6 = Eigen::Matrix<double,6,1>;
using Matrix6 = Eigen::Matrix<double,6,6>;
using StrainMatrix = Eigen::Matrix<double,2,6>;

Diagnostic systemError(const std::string& code, const Id& object,
                       const std::string& field, const std::string& message) {
    return {code, "error", "system", object, field, message};
}

bool finiteValues(const std::vector<double>& values) {
    return std::all_of(values.begin(), values.end(), [](double x) { return std::isfinite(x); });
}

// Global translations are projected onto the undeformed local axis and normal.
Matrix6 transformation(double c, double s) {
    Matrix6 t = Matrix6::Zero();
    for (int offset : {0,3}) {
        t(offset,offset) = c;
        t(offset,offset+1) = s;
        t(offset+1,offset) = -s;
        t(offset+1,offset+1) = c;
        t(offset+2,offset+2) = 1.0;
    }
    return t;
}

// theta=dv/dx, kappa=d2v/dx2 and epsilon(eta)=epsilon0-eta*kappa.
// The row for curvature is the second derivative of cubic Hermite interpolation.
StrainMatrix strainMatrix(double xi, double length) {
    const double t = 0.5 * (xi + 1.0);
    const double invL = 1.0 / length;
    const double invL2 = invL * invL;
    StrainMatrix b = StrainMatrix::Zero();
    b(0,0) = -invL;
    b(0,3) = invL;
    b(1,1) = (-6.0 + 12.0*t) * invL2;
    b(1,2) = (-4.0 + 6.0*t) * invL;
    b(1,4) = (6.0 - 12.0*t) * invL2;
    b(1,5) = (-2.0 + 6.0*t) * invL;
    return b;
}

double infinityNorm(const Eigen::VectorXd& vector) {
    return vector.size() ? vector.cwiseAbs().maxCoeff() : 0.0;
}

} // namespace

SystemResponse evaluateSystem(const PreparedModel& model, const SystemState& committed,
                              const std::vector<double>& displacement) {
    SystemResponse failure;
    const Id object = model.identity.snapshotId;
    const auto reject = [&](const std::string& code, const Id& id,
                            const std::string& field, const std::string& message) {
        failure.diagnostics.push_back(systemError(code, id, field, message));
    };
    try {
        if (model.nodes.empty() || model.elements.empty() || model.sections.empty() ||
            model.nodes.size() > static_cast<std::size_t>(std::numeric_limits<int>::max()/3)) {
            reject("SYSTEM_MODEL_SHAPE", object, "nodes/elements/sections",
                   "The prepared structure is empty or exceeds supported sparse indices.");
            return failure;
        }
        const std::size_t count = model.nodes.size()*3;
        if (displacement.size() != count || !finiteValues(displacement)) {
            reject("SYSTEM_DISPLACEMENT_SHAPE", object, "displacement",
                   "The full finite displacement vector must contain three values per node.");
            return failure;
        }
        if ((!committed.sections.empty() && committed.sections.size() != model.sections.size()) ||
            (!committed.displacement.empty() &&
             (committed.displacement.size() != count || !finiteValues(committed.displacement))) ||
            !std::isfinite(committed.loadFactor) || !std::isfinite(committed.gravityFactor)) {
            reject("SYSTEM_HISTORY_SHAPE", object, "committed",
                   "Committed history does not match the prepared structure or is nonfinite.");
            return failure;
        }
        std::vector<bool> occupied(count, false);
        std::unordered_set<Id> nodeIds;
        for (const auto& node : model.nodes) {
            if (node.id.empty() || !nodeIds.insert(node.id).second ||
                !std::isfinite(node.x) || !std::isfinite(node.globalX) || !std::isfinite(node.globalY)) {
                reject("SYSTEM_NODE_INVALID", node.id, "node", "A node identity or coordinate is invalid.");
                return failure;
            }
            for (const auto dof : node.dofs) {
                if (dof >= count || occupied[dof]) {
                    reject("SYSTEM_DOF_INVALID", node.id, "dofs",
                           "Node degrees of freedom must form a unique full-vector mapping.");
                    return failure;
                }
                occupied[dof] = true;
            }
        }
        double gravityPerLength = 0.0;
        if (model.definition.gravity.enabled) {
            const auto& gravity = model.definition.gravity;
            const auto& geometry = model.definition.geometry;
            if (!std::isfinite(gravity.density) || gravity.density <= 0.0 ||
                !std::isfinite(gravity.acceleration) || gravity.acceleration <= 0.0 ||
                !std::isfinite(geometry.width) || geometry.width <= 0.0 ||
                !std::isfinite(geometry.height) || geometry.height <= 0.0) {
                reject("SYSTEM_GRAVITY_INVALID", object, "gravity",
                       "Self-weight requires positive finite density, acceleration and section dimensions.");
                return failure;
            }
            // kg/m3 * m/s2 * mm2 -> N/mm. Equivalent density already includes steel.
            gravityPerLength = gravity.density * gravity.acceleration *
                               geometry.width * geometry.height * 1e-9;
            if (!std::isfinite(gravityPerLength)) {
                reject("SYSTEM_GRAVITY_NONFINITE", object, "gravity", "The line load overflowed.");
                return failure;
            }
        }

        SystemResponse result;
        result.internalForce = Eigen::VectorXd::Zero(static_cast<Eigen::Index>(count));
        result.state.displacement = displacement;
        result.state.sections.resize(model.sections.size());
        result.state.loadFactor = committed.loadFactor;
        result.state.gravityFactor = committed.gravityFactor;
        result.sections.resize(model.sections.size());
        result.elementForces.reserve(model.elements.size());
        std::vector<bool> visited(model.sections.size(), false);
        std::unordered_set<Id> elementIds;
        std::unordered_set<Id> sectionIds;
        std::vector<Eigen::Triplet<double>> entries;
        if (model.elements.size() > entries.max_size()/36) {
            reject("SYSTEM_RESOURCE_LIMIT", object, "elements", "The sparse assembly exceeds its capacity.");
            return failure;
        }
        entries.reserve(model.elements.size()*36);

        for (std::size_t elementIndex=0; elementIndex<model.elements.size(); ++elementIndex) {
            const auto& element = model.elements[elementIndex];
            if (element.id.empty() || !elementIds.insert(element.id).second ||
                element.nodes[0] >= model.nodes.size() || element.nodes[1] >= model.nodes.size() ||
                element.nodes[0] == element.nodes[1] || !std::isfinite(element.length) ||
                element.length <= 0.0 || !std::isfinite(element.cosine) ||
                !std::isfinite(element.sine) ||
                std::abs(element.cosine*element.cosine + element.sine*element.sine - 1.0) > 1e-10 ||
                element.sectionIndices.empty()) {
                reject("SYSTEM_ELEMENT_INVALID", element.id, "element",
                       "Element identity, connectivity, length, orientation or quadrature is invalid.");
                return failure;
            }
            const auto& first = model.nodes[element.nodes[0]];
            const auto& second = model.nodes[element.nodes[1]];
            const double dx = second.globalX-first.globalX;
            const double dy = second.globalY-first.globalY;
            const double geometryTolerance = 1e-10*std::max(1.0,element.length);
            if (std::abs(dx-element.length*element.cosine) > geometryTolerance ||
                std::abs(dy-element.length*element.sine) > geometryTolerance ||
                std::abs(second.x-first.x-element.length) > geometryTolerance) {
                reject("SYSTEM_ELEMENT_GEOMETRY", element.id, "length/cosine/sine",
                       "Element transformation disagrees with its endpoint coordinates.");
                return failure;
            }
            Vector6 global;
            for (int i=0; i<6; ++i) {
                const auto expected = (i<3 ? first.dofs[static_cast<std::size_t>(i)] :
                                       second.dofs[static_cast<std::size_t>(i-3)]);
                if (element.dofs[static_cast<std::size_t>(i)] != expected) {
                    reject("SYSTEM_ELEMENT_DOF", element.id, "dofs",
                           "Element degrees of freedom disagree with node connectivity.");
                    return failure;
                }
                global[i] = displacement[expected];
            }
            const Matrix6 t = transformation(element.cosine, element.sine);
            const Vector6 local = t*global;
            Vector6 localForce = Vector6::Zero();
            Matrix6 localTangent = Matrix6::Zero();
            double integratedLength = 0.0;
            for (const auto sectionIndex : element.sectionIndices) {
                if (sectionIndex >= model.sections.size() || visited[sectionIndex]) {
                    reject("SYSTEM_SECTION_CONNECTIVITY", element.id, "section_indices",
                           "Every actual Gauss section must belong to exactly one element.");
                    return failure;
                }
                const auto& section = model.sections[sectionIndex];
                const double expectedLength = section.weight*element.length/2.0;
                if (section.id.empty() || !sectionIds.insert(section.id).second ||
                    section.elementId != element.id || section.elementIndex != elementIndex ||
                    !std::isfinite(section.xi) || section.xi <= -1.0 || section.xi >= 1.0 ||
                    !std::isfinite(section.weight) || section.weight <= 0.0 ||
                    !std::isfinite(section.integrationLength) || section.integrationLength <= 0.0 ||
                    std::abs(section.integrationLength-expectedLength) > geometryTolerance ||
                    !std::isfinite(section.x) ||
                    std::abs(section.x-(first.x+(section.xi+1.0)*element.length/2.0)) > geometryTolerance) {
                    reject("SYSTEM_SECTION_GEOMETRY", section.id, "quadrature",
                           "Section ownership, coordinate, weight or integration length is inconsistent.");
                    return failure;
                }
                const StrainMatrix b = strainMatrix(section.xi, element.length);
                const Eigen::Vector2d strain = b*local;
                const SectionState initial;
                const auto& anchor = committed.sections.empty() ? initial : committed.sections[sectionIndex];
                auto trial = evaluateSection(section, anchor, strain[0], strain[1]);
                if (!trial.ok) {
                    failure.diagnostics = std::move(trial.diagnostics);
                    if (failure.diagnostics.empty()) {
                        reject("SYSTEM_SECTION_FAILURE", section.id, "section",
                               "Section trial failed without a diagnostic.");
                    }
                    return failure;
                }
                Eigen::Matrix2d tangent;
                tangent << trial.tangent[0],trial.tangent[1],trial.tangent[2],trial.tangent[3];
                const Eigen::Vector2d force(trial.N,trial.M);
                if (!tangent.allFinite() || !force.allFinite()) {
                    reject("SYSTEM_SECTION_NONFINITE", section.id, "force/tangent",
                           "Section force or consistent tangent is nonfinite.");
                    return failure;
                }
                localForce.noalias() += section.integrationLength*b.transpose()*force;
                localTangent.noalias() += section.integrationLength*b.transpose()*tangent*b;
                integratedLength += section.integrationLength;
                visited[sectionIndex] = true;
                result.state.sections[sectionIndex] = std::move(trial.state);
                result.sections[sectionIndex] = std::move(trial.result);
                result.diagnostics.insert(result.diagnostics.end(), trial.diagnostics.begin(), trial.diagnostics.end());
            }
            if (std::abs(integratedLength-element.length) > geometryTolerance ||
                !localForce.allFinite() || !localTangent.allFinite()) {
                reject("SYSTEM_ELEMENT_INTEGRATION", element.id, "integration",
                       "Element integration is nonfinite or its quadrature does not span the element.");
                return failure;
            }
            const Vector6 globalForce = t.transpose()*localForce;
            const Matrix6 globalTangent = t.transpose()*localTangent*t;
            if (!globalForce.allFinite() || !globalTangent.allFinite()) {
                reject("SYSTEM_TRANSFORMATION_NONFINITE", element.id, "transformation",
                       "Global transformed force or tangent overflowed.");
                return failure;
            }
            for (int i=0; i<6; ++i) {
                const int row = static_cast<int>(element.dofs[static_cast<std::size_t>(i)]);
                result.internalForce[row] += globalForce[i];
                for (int j=0; j<6; ++j) {
                    const double value = globalTangent(i,j);
                    if (value != 0.0) entries.emplace_back(row,
                        static_cast<int>(element.dofs[static_cast<std::size_t>(j)]),value);
                }
            }
            ElementForceResult end;
            end.id = element.id;
            std::copy(localForce.data(),localForce.data()+6,end.endForces.begin());
            // Full-gravity pattern metadata. The analysis layer scales these by the
            // candidate gravity factor and subtracts consistent nodal loads from
            // endForces before labelling them as actual end-equilibrium forces.
            end.distributedAxial = -element.sine*gravityPerLength;
            end.distributedTransverse = -element.cosine*gravityPerLength;
            end.source = "constitutive_resisting_before_gravity_subtraction";
            result.elementForces.push_back(std::move(end));
        }
        if (std::find(visited.begin(),visited.end(),false) != visited.end()) {
            reject("SYSTEM_UNUSED_SECTION", object, "sections", "A prepared section has no owning element.");
            return failure;
        }
        result.tangent.resize(static_cast<int>(count),static_cast<int>(count));
        result.tangent.setFromTriplets(entries.begin(),entries.end());
        result.tangent.makeCompressed();
        if (!result.internalForce.allFinite()) {
            reject("SYSTEM_ASSEMBLY_NONFINITE", object, "internal_force", "The assembled force overflowed.");
            return failure;
        }
        for (int i=0; i<result.tangent.nonZeros(); ++i) {
            if (!std::isfinite(result.tangent.valuePtr()[i])) {
                reject("SYSTEM_ASSEMBLY_NONFINITE", object, "tangent", "The assembled tangent overflowed.");
                return failure;
            }
        }
        result.ok = true;
        return result;
    } catch (const DomainError& error) {
        failure.diagnostics.push_back(error.diagnostic);
    } catch (const std::exception& error) {
        reject("SYSTEM_EXCEPTION", object, "assembly", error.what());
    }
    return failure;
}

LinearSolution solveScaledSystem(const Eigen::SparseMatrix<double>& matrix,
                                 const Eigen::VectorXd& rhs,
                                 const std::vector<double>& rowScale,
                                 const std::vector<double>& columnScale,
                                 double backwardTolerance) {
    LinearSolution result;
    const auto reject = [&](const std::string& code, const std::string& field,
                            const std::string& message) {
        result.diagnostics.push_back(systemError(code,"",field,message));
    };
    try {
        const auto n = matrix.rows();
        if (n <= 0 || n > std::numeric_limits<int>::max() || matrix.cols() != n || rhs.size() != n ||
            rowScale.size() != static_cast<std::size_t>(n) ||
            columnScale.size() != static_cast<std::size_t>(n) ||
            !rhs.allFinite() || !std::isfinite(backwardTolerance) || backwardTolerance <= 0.0) {
            reject("LINEAR_INPUT_INVALID", "matrix/rhs/scales/tolerance",
                   "The linear system must be square, finite and have matching positive physical scales.");
            return result;
        }
        for (Eigen::Index i=0; i<n; ++i) {
            if (!std::isfinite(rowScale[static_cast<std::size_t>(i)]) || rowScale[static_cast<std::size_t>(i)] <= 0.0 ||
                !std::isfinite(columnScale[static_cast<std::size_t>(i)]) || columnScale[static_cast<std::size_t>(i)] <= 0.0) {
                reject("LINEAR_SCALE_INVALID", "scales", "Each row and column scale must be finite and positive.");
                return result;
            }
        }
        std::vector<Eigen::Triplet<double>> entries;
        entries.reserve(static_cast<std::size_t>(matrix.nonZeros()));
        std::vector<long double> rowSums(static_cast<std::size_t>(n),0.0L);
        std::vector<bool> columns(static_cast<std::size_t>(n),false);
        for (int outer=0; outer<matrix.outerSize(); ++outer) {
            for (Eigen::SparseMatrix<double>::InnerIterator entry(matrix,outer); entry; ++entry) {
                const double value = entry.value();
                if (!std::isfinite(value)) {
                    reject("LINEAR_MATRIX_NONFINITE", "matrix", "The matrix contains a nonfinite coefficient.");
                    return result;
                }
                const long double extended = static_cast<long double>(value)*
                    columnScale[static_cast<std::size_t>(entry.col())]/
                    rowScale[static_cast<std::size_t>(entry.row())];
                const double scaled = static_cast<double>(extended);
                if (!std::isfinite(scaled) || (value != 0.0 && scaled == 0.0)) {
                    reject("LINEAR_SCALING_RANGE", "matrix/scales",
                           "Physical scaling overflowed or underflowed a nonzero matrix coefficient.");
                    return result;
                }
                if (scaled != 0.0) {
                    entries.emplace_back(static_cast<int>(entry.row()),static_cast<int>(entry.col()),scaled);
                    rowSums[static_cast<std::size_t>(entry.row())] += std::abs(static_cast<long double>(scaled));
                    columns[static_cast<std::size_t>(entry.col())] = true;
                }
            }
        }
        long double matrixNorm = 0.0L;
        for (Eigen::Index i=0; i<n; ++i) {
            if (rowSums[static_cast<std::size_t>(i)] == 0.0L || !columns[static_cast<std::size_t>(i)]) {
                reject("LINEAR_STRUCTURAL_SINGULARITY", "matrix",
                       "The scaled system has a zero row or column; check rigid modes, constraints and control response.");
                return result;
            }
            matrixNorm = std::max(matrixNorm,rowSums[static_cast<std::size_t>(i)]);
        }
        if (!std::isfinite(matrixNorm)) {
            reject("LINEAR_NORM_RANGE", "matrix/scales", "The scaled matrix norm exceeded numerical range.");
            return result;
        }
        Eigen::SparseMatrix<double> scaledMatrix(n,n);
        scaledMatrix.setFromTriplets(entries.begin(),entries.end());
        scaledMatrix.makeCompressed();
        Eigen::VectorXd scaledRhs(n);
        for (Eigen::Index i=0; i<n; ++i) {
            scaledRhs[i] = rhs[i]/rowScale[static_cast<std::size_t>(i)];
            if (!std::isfinite(scaledRhs[i]) || (rhs[i] != 0.0 && scaledRhs[i] == 0.0)) {
                reject("LINEAR_SCALING_RANGE", "rhs/scales", "Scaling the right-hand side exceeded numerical range.");
                return result;
            }
        }
        // SparseLU uses general pivoted LU: no symmetry or positive-definite
        // assumption is valid for the displacement-control augmented system.
        Eigen::SparseLU<Eigen::SparseMatrix<double>,Eigen::COLAMDOrdering<int>> solver;
        solver.analyzePattern(scaledMatrix);
        solver.factorize(scaledMatrix);
        if (solver.info() != Eigen::Success) {
            reject("LINEAR_FACTORIZATION_FAILURE", "matrix",
                   "General sparse LU failed: " + solver.lastErrorMessage());
            return result;
        }
        Eigen::VectorXd scaledSolution = solver.solve(scaledRhs);
        if (solver.info() != Eigen::Success || !scaledSolution.allFinite()) {
            reject("LINEAR_SOLVE_FAILURE", "solution", "Sparse LU did not produce a finite solution.");
            return result;
        }
        // Refinement reuses the same factorization and never changes the matrix,
        // adds stiffness, or relaxes the requested backward-error tolerance.
        for (int refinement=0; refinement<=2; ++refinement) {
            const Eigen::VectorXd residual = scaledRhs-scaledMatrix*scaledSolution;
            if (!residual.allFinite()) {
                reject("LINEAR_RESIDUAL_NONFINITE", "backward_error", "Scaled linear residual overflowed.");
                return result;
            }
            const long double denominator = matrixNorm*infinityNorm(scaledSolution)+infinityNorm(scaledRhs);
            const long double numerator = infinityNorm(residual);
            if (!std::isfinite(denominator)) {
                reject("LINEAR_NORM_RANGE", "backward_error", "The backward-error scale exceeded numerical range.");
                return result;
            }
            const long double error = denominator == 0.0L ? (numerator == 0.0L ? 0.0L :
                std::numeric_limits<long double>::infinity()) : numerator/denominator;
            result.backwardError = static_cast<double>(error);
            if (std::isfinite(result.backwardError) && error <= backwardTolerance) break;
            if (refinement == 2) {
                reject("LINEAR_BACKWARD_ERROR", "backward_error",
                       "The scaled solution does not satisfy the requested relative backward-error bound.");
                return result;
            }
            const Eigen::VectorXd correction = solver.solve(residual);
            if (solver.info() != Eigen::Success || !correction.allFinite()) {
                reject("LINEAR_REFINEMENT_FAILURE", "solution", "Sparse LU iterative refinement failed.");
                return result;
            }
            scaledSolution += correction;
        }
        result.solution.resize(n);
        for (Eigen::Index i=0; i<n; ++i) {
            result.solution[i] = scaledSolution[i]*columnScale[static_cast<std::size_t>(i)];
            if (!std::isfinite(result.solution[i]) ||
                (scaledSolution[i] != 0.0 && result.solution[i] == 0.0)) {
                result.solution.resize(0);
                reject("LINEAR_SOLUTION_RANGE", "solution/scales", "Unscaling the solution exceeded numerical range.");
                return result;
            }
        }
        const long double amplification = matrixNorm*infinityNorm(scaledSolution);
        const long double rhsNorm = infinityNorm(scaledRhs);
        if (rhsNorm > 0.0L && amplification > rhsNorm/std::sqrt(std::numeric_limits<double>::epsilon())) {
            result.diagnostics.push_back({"LINEAR_LARGE_AMPLIFICATION","warning","system","","conditioning",
                "Large scaled solution amplification: a small backward error does not establish forward accuracy."});
        }
        result.ok = true;
    } catch (const std::exception& error) {
        result.solution.resize(0);
        reject("LINEAR_EXCEPTION", "solve", error.what());
    }
    return result;
}

} // namespace lumafem
