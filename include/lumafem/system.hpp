#pragma once
#include "lumafem/results.hpp"
#include <Eigen/Core>
#include <Eigen/SparseCore>

namespace lumafem {
/// Value-owned committed structure state; section order follows PreparedModel.sections.
struct SystemState {
    std::vector<double> displacement; ///< Full global nodal vector.
    std::vector<SectionState> sections; ///< Complete independent material histories.
    double loadFactor = 0.0; ///< Accepted proportional load multiplier.
    double gravityFactor = 0.0; ///< Accepted constant load multiplier.
};
/// Candidate assembly; no support force row is silently replaced by a control equation.
struct SystemResponse {
    bool ok = false; ///< Entire assembled candidate valid.
    Eigen::SparseMatrix<double> tangent; ///< Full global consistent tangent.
    Eigen::VectorXd internalForce; ///< Full internal nodal force vector.
    SystemState state; ///< Full value-owned candidate history.
    std::vector<SectionResult> sections; ///< Actual Gauss results.
    std::vector<ElementForceResult> elementForces; ///< Local resisting end forces before external subtraction.
    std::vector<Diagnostic> diagnostics; ///< Located assembly failure.
};
/// General sparse solve with explicit physical row/column scaling.
struct LinearSolution {
    bool ok = false; ///< Finite solution meeting backward error tolerance.
    Eigen::VectorXd solution; ///< Unscaled original-system solution.
    double backwardError = missing; ///< Scaled relative backward error.
    std::vector<Diagnostic> diagnostics; ///< Singular/nonfinite/factorization evidence.
};
/// Evaluate all material points from the same unchanged accepted anchor.
SystemResponse evaluateSystem(const PreparedModel&, const SystemState& committed,
                              const std::vector<double>& displacement);
/// Solve A*x=rhs by general SparseLU; scales are positive physical characteristic magnitudes.
/// Convention: As(i,j)=A(i,j)*columnScale[j]/rowScale[i], bs(i)=rhs(i)/rowScale[i].
LinearSolution solveScaledSystem(const Eigen::SparseMatrix<double>& matrix, const Eigen::VectorXd& rhs,
                                 const std::vector<double>& rowScale,
                                 const std::vector<double>& columnScale, double backwardTolerance);
} // namespace lumafem
