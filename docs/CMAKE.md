# CMake 工程与 Windows 构建

本文按当前 `CMakeLists.txt`、`CMakePresets.json`、`cmake/`、`tests/CMakeLists.txt`、`tools/windows_launcher/` 及 PowerShell 脚本核对（2026-11-09）。[返回 README](../README.md)。当前数值身份为 `0.3.1-cr-num-002-a05`、schema1/reader3；根 `project(LumaFEM VERSION 0.1.0)` 的 CMake 项目版本不是运行时数值版本，不能据此改写结果身份。

## 工程结构与目标

根工程是 C++17、无语言扩展、启用位置无关代码的工程。Qt/VTK 界面来自 Python 轮子，没有根 CMake Qt Widgets 应用目标，也不在此编译 Qt/VTK 源码。

```text
CMakeLists.txt                  核心、JSON适配、CLI、可选绑定、CTest与部分安装
CMakePresets.json               Ninja Release / core 配置、构建与测试预设
cmake/Dependencies.cmake        Eigen/JSON固定归档及头文件接口目标
include/lumafem/                核心模型、材料、结果与分析接口
src/core/                      数值核心
src/adapters/json_codec.cpp     JSON编解码
src/cli/main.cpp               无界面CLI
src/bindings/module.cpp         pybind11模块
python/lumafem/                 应用、SQLite存储、Qt/VTK界面
python/lumafem/gui/assets/      批准PNG、ICO及公开来源摘要
tests/CMakeLists.txt            测试目标与CTest注册
tools/windows_launcher/        独立Windows C++/RC工程
scripts/                       依赖安装、构建、启动、打包及来源核验
```

| 目标 | 类型与职责 | 直接链接 |
| --- | --- | --- |
| `lumafem_core` | STATIC；model、material、section、system、physical_gauge、analysis | `Eigen3::Eigen` |
| `lumafem_codec` | STATIC；JSON 适配层 | `lumafem_core`、`nlohmann_json::nlohmann_json` |
| `lumafem_cli` | 原生无界面 EXE；prepare/run/view | `lumafem_codec` |
| `_lumafem` | 可选 pybind11 扩展模块 | `lumafem_codec`（另由 pybind11 配置 Python 扩展） |
| 原生测试/驱动 | `tests/` 下的行为、能量、标距、兼容与诊断程序 | 核心或 codec |
| `LumaFEM` | **独立工程**的 WIN32 GUI 启动 EXE；不属于根目标图 | `shell32`、`user32` |

核心定义 `EIGEN_MPL2_ONLY`，MSVC 下还有 `NOMINMAX`；核心编译选项为 `/W4 /permissive- /utf-8 /bigobj`，非 MSVC 分支为 `-Wall -Wextra -Wpedantic`。核心不链接 Python、Qt、VTK、SQLite 或 OpenSees。Python 侧 SQLite 存储不对应一个 CMake SQLite target。

## 工具链与依赖准备

当前受验证配置：Windows x64、CPython 3.10 x64、MSVC x64 C++ 工具链及 Windows SDK。资源启动器还需要 `rc.exe`，打包需要 `dumpbin.exe`。不应把存在非 MSVC 编译分支解释为其他平台桌面已验证。

```powershell
# 仓库根目录；替换为实际CPython 3.10 x64路径
./scripts/bootstrap.ps1 -Python 'C:/Python310/python.exe'
```

`bootstrap.ps1`：

1. 创建或核验 `.venv/` 的 Python 版本/位数；固定 pip 24.3.1。
2. 安装 [requirements-desktop.txt](../requirements-desktop.txt) 的固定版本，包括 CMake 3.30.5、Ninja 1.11.1.1、pybind11 2.13.6、PySide6/shiboken6 6.7.3、VTK 9.3.1 等。
3. 下载并校验 Eigen 3.4.0、nlohmann/json 3.11.3 到 `.cache/dependencies/`；已有归档同样检查 SHA-256，损坏则拒绝。
4. 将安装版本写到 `.cache/toolchain/installed-packages.txt`。

仅准备原生工具时可用 `-SkipDesktop`，它安装 CMake/Ninja/pybind11 并仍缓存两份头文件归档，不安装桌面依赖；不能据此运行 GUI 或 Python 桌面测试。

`cmake/Dependencies.cmake` 使用 FetchContent：优先取上述本地缓存，不存在时使用源码中固定的官方 URL；两种路径都校验同一归档哈希。依赖通过自定义 INTERFACE 目标导入头文件，不构建上游示例、基准或测试，也没有提供仓库专用的系统 Eigen/JSON 查找开关。首次准备需要相应网络或已有完整缓存。

绑定开启时执行 `find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)`，从该解释器 `python -m pybind11 --cmakedir` 查找 **EXACT 2.13.6**；预设指向本仓库 `.venv/Scripts/python.exe`。CMake 的 Python 3.10 最低要求与安装脚本严格限定 3.10 x64 的策略不同，受验证桌面配置以后者为准。

## 配置与常规构建

推荐入口会自动查找 Visual Studio 并导入 `vcvars64.bat` 环境：

```powershell
./scripts/build.ps1 -Jobs 3
# 只构建不含Python绑定和GUI启动器的原生工程
./scripts/build.ps1 -CoreOnly -Jobs 3
# 自动发现失败时，指定Visual Studio安装根目录
./scripts/build.ps1 -VisualStudioPath 'C:/Program Files/Microsoft Visual Studio/2022/Community'
```

`-Jobs` 默认 3，必须为正；脚本设置 `.venv/Scripts` 工具路径和 `VSLANG=1033`，执行配置及并行构建。普通分支之后另行配置、构建启动器并写入启动器清单。`-CoreOnly` 选择 `windows-core`，不等同于 `BUILD_TESTING=OFF`。

| 预设 | 生成器 / 类型 | 构建目录 | 绑定 / 测试 |
| --- | --- | --- | --- |
| `windows-release` | Ninja / Release | `build/` | `LUMAFEM_BUILD_PYTHON=ON`、`BUILD_TESTING=ON` |
| `windows-core` | 继承上项 | `build/core/` | 绑定 OFF；测试仍继承 ON |

两项配置都有同名 build/test preset，启用 `CMAKE_EXPORT_COMPILE_COMMANDS`。预设文件格式是 version 6；根工程虽然声明最低 CMake 3.24，读取 version 6 预设需要支持该格式的版本，使用固定 3.30.5 可避免差异。

需要直接运行 CMake 时，先在 **MSVC x64 开发 PowerShell**中进入仓库根目录，并将本地 Ninja 加入当前进程 PATH：

```powershell
$env:PATH = (Join-Path (Get-Location) '.venv/Scripts') + ';' + $env:PATH
./.venv/Scripts/cmake.exe --preset windows-release
./.venv/Scripts/cmake.exe --build --preset windows-release --parallel 3
```

直接构建根工程只得到根目标，不会自动生成 GUI 启动器清单；完整桌面打包应使用 `build.ps1`，或执行下文独立启动器流程。

实际选项只有本仓库声明的 `LUMAFEM_BUILD_PYTHON`（默认 ON）及 `include(CTest)` 提供的 `BUILD_TESTING`（默认 ON）；例如无 Python、无测试的隔离原生构建可在已初始化工具链环境中配置到新目录：

```powershell
./.venv/Scripts/cmake.exe -S . -B build/native-minimal -G Ninja -DCMAKE_BUILD_TYPE=Release -DLUMAFEM_BUILD_PYTHON=OFF -DBUILD_TESTING=OFF
./.venv/Scripts/cmake.exe --build build/native-minimal --parallel 3
```

此目录不是桌面打包脚本预期的 `build/`，不能直接用于现有 package 流程。没有 `LUMAFEM_BUILD_GUI`、OpenMP/MPI 或三维实体求解选项。

### MSVC/Ninja 头文件依赖

根工程与独立启动器均探测 `/showIncludes` 的实际**原始字节**前缀，并让每次编译通过 `cmake -E env VSLANG=1033` 使用一致语言请求，保留调用者已有 compiler launcher。1033 请求可能回退本机语言，不要求另装语言包。前缀探测失败会停止配置，不允许静默漏记头依赖；这也覆盖直接 Ninja 调用。不能删去此机制来掩盖本地化依赖或新旧 ABI 混用。

## 产物与启动

| 输出 | 用途 |
| --- | --- |
| `build/bin/lumafem_cli.exe` | 原生 CLI；`--help`、prepare/run/view |
| `build/python/_lumafem*.pyd` | 当前 CPython 原生绑定（受验证为 cp310-win_amd64） |
| `build/tests/` | 原生测试及科学诊断驱动 |
| `build/compile_commands.json` | 编译命令数据库 |
| `build/_deps/` | FetchContent 展开的头文件依赖 |
| `build/windows_launcher/bin/LumaFEM.exe` | 待组装的 GUI 启动器 |
| `build/windows_launcher/launcher-build-manifest.json` | 启动器本次构建的精确来源 |

`windows-core` 产物相应位于 `build/core/`；绑定关闭时没有 PYD。根工程显式设置 CLI 和绑定输出，绑定及绑定开启时的 CLI 还统一多配置目录；当前预设实际使用单配置 Ninja Release。

源码 GUI 由 [launch.ps1](../scripts/launch.ps1) 设置 `PYTHONPATH=python;build/python` 与 `QT_API=pyside6` 后启动：

```powershell
./scripts/launch.ps1 -Project 'D:/projects/example.lumafem'
./scripts/launch.ps1 -CaseDir 'D:/cases/example'
./build/bin/lumafem_cli.exe --help
```

不传路径时默认案例目录为 `development_doc/runtime/CR-NUM-002`。案例生成器需要已有本地种子，公开克隆不附带此种子；见 [README](../README.md)。不要双击孤立的 `build/windows_launcher/bin/LumaFEM.exe` 代替源码 GUI：它按 EXE 位置寻找包内相邻脚本及运行时。

## 测试与本地夹具

`include(CTest)` 后仅在 `BUILD_TESTING` 开启且 `tests/CMakeLists.txt` 存在时进入测试目录。

- 原生目标包括 `lumafem_core_tests`、`lumafem_cr_energy_tests`、`lumafem_physical_gauge_tests`，另有科学驱动、弱杆、A05 onset、bracket、matrix reconstruction 及 A04/A05 codec 程序；可执行程序数量不等于 CTest 用例数量。
- `tests/CMakeLists.txt` 为部分内部行为测试读取生产 `analysis.cpp`，仅把 `class Analysis {` 替换为 `struct Analysis {` 写入构建目录 `analysis_access.inc`。重新配置可能触发测试专用 EXE 重编译，不修改生产源，也不能将测试 PE 变化冒充数值 CLI/PYD 变化。
- 绑定开启时注册实际 Python/Qt/VTK/SQLite 工作流、兼容、后处理、主题、图标及 Windows 启动器测试。当前 Release 共 22 个 CTest 目标；后处理目标有 10 个方法，图标/启动器新增 7 个方法，三个计数不能混用。

```powershell
# 只列清单，不执行
./.venv/Scripts/ctest.exe --preset windows-release --show-only
# 仅在已准备夹具、隔离输出的验证工作区执行完整套件
./.venv/Scripts/ctest.exe --preset windows-release --output-on-failure
# 不需要本地科研夹具的三个原生目标
./.venv/Scripts/ctest.exe --preset windows-release -R '^(core_behavior|cr_energy_identity|physical_gauge_analytic)$' --output-on-failure
```

`windows-core` 也有 test preset，保留原生测试，其中部分仍需本地夹具。完整套件引用 Git 忽略的 `specs/` 历史 prepared/弱杆/旧快照和 `development_doc/runtime/` 案例；例如 bracket、codec、onset、physical_extended 和来源保护测试依赖这些文件。全新克隆只具备源码不能重建全部科学验收，生成本地默认数据也不能代替历史冻结证据。若需执行，应使用专用验证副本及各脚本已有输出隔离入口，先核对实际测试写出位置；不能向封存的旧 COMPLETE/FAILED 流水线重写证据。

当前 CTest 时限依源码分别设置：后处理/主题/GUI 工作流 300 秒，来源保护 600 秒，`physical_extended` 600 秒，其他 Python 目标为 60/120/180 秒等。`physical_extended` 曾在旧 300 秒外层时限失败，后经独立评审仅把外层 watchdog 改为 600 秒；数值求解预算、断言和资格政策不变，旧失败不追认通过。

已封存的 GUI 主体 19/19、后处理 20/20、图标 22/22 最终回归和独立 APPROVE/PASS 见 README。本文不重新执行这些流水线或扩大科学资格，也不声明行/分支覆盖率或性能通过。

## 独立 Windows GUI 启动器与图标资源

[tools/windows_launcher/CMakeLists.txt](../tools/windows_launcher/CMakeLists.txt) 是独立 `project(LumaFEMWindowsLauncher LANGUAGES CXX RC)`，要求 **MSVC、x64 编译目标、Ninja**，不通过根 `add_subdirectory` 接入。

资源流程：

1. 从 `python/lumafem/gui/assets/lumafem.ico` 取批准多尺寸 ICO，缺失即配置失败。
2. `configure_file(launcher.rc.in … @ONLY)` 生成构建目录 `launcher.rc`，以资源 ID 1 嵌入 ICO。目录 `CMAKE_CONFIGURE_DEPENDS` 与 RC 的 `OBJECT_DEPENDS` 同时跟踪 ICO/模板，资源改动须触发重配置/编译/链接。
3. `add_executable(LumaFEM WIN32 …)` 构建 GUI 子系统 EXE，使用 C++17、Unicode、`shell32/user32`；CRT 为 Release `/MT`、Debug `/MTd`。这不改变数值核心 CRT，也不静态链接 Qt。
4. Qt 窗口图标由 `branding.py` 从模块相对路径解码批准 PNG，设置应用标识及窗口图标；它与 EXE 的 RC/ICO 是两条资源路径。图案是品牌示意。

常规 `build.ps1` 自动执行以下等价流程；手动流程仍须在已初始化 MSVC x64 环境、使用本仓库 CMake/Ninja：

```powershell
./.venv/Scripts/cmake.exe -S tools/windows_launcher -B build/windows_launcher -G Ninja -DCMAKE_BUILD_TYPE=Release
./.venv/Scripts/cmake.exe --build build/windows_launcher --target LumaFEM --parallel 3
./.venv/Scripts/python.exe ./scripts/launcher_manifest.py write --project-root . --build-dir build/windows_launcher
./.venv/Scripts/python.exe ./scripts/launcher_manifest.py verify --project-root . --build-dir build/windows_launcher
```

`write` **会再 clean-first 重建独立 LumaFEM 目标**，防止较新替换 PE 被 no-op 构建放行；不清理或重建数值核心。随后原子写入 version1 清单，记录所有启动器源、批准 PNG/ICO/来源摘要、构建/核验脚本、生成 RC、实际 PE、CMakeCache/Ninja 图、工具路径/哈希/身份以及 Ninja 无待构建证明。`verify` 重新收集并精确比较，拒绝缺失、资源变化、替换 PE、额外源码或工具链漂移；它不是数字签名或科学资格。

实际同源启动器重链曾产生不同原始哈希，独立解析证明只差 COFF/debug 时间戳字段；并未宣称跨构建逐字节可复现。每个包必须对应自己的实际 PE 与清单，不能把新 PE 覆盖到旧已测包。

EXE 根据自身目录查找 `Launch-LumaFEM.ps1` 与 `runtime/python.exe/python310.dll`，使用绝对系统 PowerShell 路径、明确参数引用及 `CREATE_NO_WINDOW`，等待并返回子进程状态。包内生成的 PS 脚本再用 CRT 参数引用和 `ProcessStartInfo` 调用包内 Python。相对工程/案例路径按调用者目录解析，失败有可见诊断；不修改第三方 Python PE，不安装文件关联或注册表项。

## install 与完整桌面打包的区别

根 CMake `install` 只安装 CLI 到 `bin/`、可选绑定到 `python/`，以及依赖声明和 notices；它没有安装整个 Python 应用、解释器、Qt/VTK 运行时、启动器或完整桌面资源。

完整开发包由 [package.ps1](../scripts/package.ps1) 组装，须先完成同源构建，并在有 `dumpbin.exe` 的 MSVC x64 开发环境中执行：

```powershell
./scripts/package.ps1
# 可选：显式复制本地默认/合成案例
./scripts/package.ps1 -CaseDir ./development_doc/runtime/CR-NUM-002
```

脚本的实际边界：

- 要求唯一绑定及 CLI；检查原生 Ninja 无待构建、源码时间、导入模块路径、kernel/schema/reader 身份和 CR-NUM-002 合同。
- 另核对受保护 A05 CLI/PYD 的固定精确哈希。因此重新编译得到不同原生 PE 的其他机器/工具链可能被拒绝；构建成功并不保证可通过当前已验收身份门，不能删守卫绕过。
- 调用启动器清单 `verify`，再复制实际启动器及清单；不为过期构建静默重新配置或补造来源。
- 只允许新的一级 `dist/LumaFEM-CR-NUM-002-GUI-ICON-*` 目录；可传 `-OutputDirectory`，但仍受目录规则约束，拒绝覆盖旧包。组装失败保留 `INCOMPLETE.txt`。
- 包含 Python 运行时、应用/native/bin、必要 Qt Core/Gui/Widgets 与选定插件、VTK 等依赖及许可；用 `dumpbin /DEPENDENTS` 检查所含 PE 的传递依赖，未解析的非系统 DLL 会失败。排除 OpenSees，不复制整个 Conda 安装。
- 只有显式 `-CaseDir` 才复制 `NC0.json` 及存在的 synthetic CSV/元数据；默认排除科研输入。清单记录实际绑定身份、源码/集成文件/内容哈希与声明判据；声明不代替运行验收。

包入口是目录根 `LumaFEM.exe`，保留旧 PS/CMD 入口。移动须保持整个目录，旧包/正式 PDF/封存证据不覆盖。构建、运行测试和组装包各自不能单独证明任意工程的科学有效性；STRICT/C/D 等边界见 [README](../README.md#科学验证限制)。
