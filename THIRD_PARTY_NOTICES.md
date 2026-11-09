# Third-party dependencies

The build keeps the numerical core independent of Qt, VTK, SQLite and Python.
Versions are pinned for this Windows x64 / CPython 3.10 development configuration.
These notices are an inventory, not a replacement for the upstream license texts.
The local package must retain the license texts shipped in the dependency archives
and installed distribution metadata, including all Qt component notices.

| Component | Version | License / upstream source |
| --- | --- | --- |
| Eigen | 3.4.0 | MPL-2.0; https://libeigen.gitlab.io/ ; `COPYING.MPL2`, `COPYING.README` in source archive |
| nlohmann/json | 3.11.3 | MIT; https://github.com/nlohmann/json/tree/v3.11.3 ; `LICENSE.MIT` |
| pybind11 | 2.13.6 | BSD-3-Clause; https://github.com/pybind/pybind11/tree/v2.13.6 |
| CPython | 3.10 | PSF license; https://docs.python.org/3.10/license.html |
| Qt for Python / PySide6, shiboken6 | 6.7.3 | LGPL-3.0 / GPL / commercial, with component-specific terms; https://doc.qt.io/qtforpython-6/licenses.html |
| VTK | 9.3.1 | BSD-3-Clause plus third-party notices; https://gitlab.kitware.com/vtk/vtk/-/tree/v9.3.1 |
| NumPy | 1.26.4 | BSD-3-Clause plus bundled library notices; https://numpy.org/doc/1.26/license.html |
| Matplotlib | 3.9.2 | PSF-style license plus bundled notices; https://matplotlib.org/3.9.2/project/license.html |
| CMake | 3.30.5 | BSD-3-Clause plus third-party notices; https://cmake.org/licensing/ |
| Ninja | 1.11.1.1 Python package | Apache-2.0; https://github.com/scikit-build/ninja-python-distributions |
| SQLite (Python standard library) | Runtime-reported | Public domain; https://sqlite.org/copyright.html |
| contourpy | 1.3.0 | BSD-3-Clause; https://github.com/contourpy/contourpy |
| cycler | 0.12.1 | BSD-3-Clause; https://github.com/matplotlib/cycler |
| fonttools | 4.55.0 | MIT; https://github.com/fonttools/fonttools |
| kiwisolver | 1.4.7 | BSD-3-Clause; https://github.com/nucleic/kiwi |
| packaging | 24.2 | Apache-2.0 / BSD-2-Clause; https://github.com/pypa/packaging |
| Pillow | 11.0.0 | HPND plus bundled library notices; https://pillow.readthedocs.io/en/stable/about.html |
| pyparsing | 3.2.0 | MIT; https://github.com/pyparsing/pyparsing |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD-3-Clause; https://github.com/dateutil/dateutil |
| six | 1.16.0 | MIT; https://github.com/benjaminp/six |

Eigen is compiled with `EIGEN_MPL2_ONLY`. No optional GPL Eigen extensions are
enabled. Qt and VTK are loaded from replaceable Python wheels; they are not
statically linked into the numerical kernel. Native headers are downloaded from
official tagged release archives with fixed SHA-256 checksums. Installed package
versions are recorded under the ignored `.cache/toolchain/` directory.

Packaging must include upstream license texts and permit replacing the LGPL Qt
libraries. Do not bundle a license key or private research input in a package.
