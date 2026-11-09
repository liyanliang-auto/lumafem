[CmdletBinding()]
param([string]$OutputDirectory = '', [string]$CaseDir = '')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRevision = 'CR-NUM-002'
$expectedKernelVersion = '0.3.1-cr-num-002-a05'
$expectedReaderCapability = 3
$expectedSchemaVersion = 1
function Copy-Tree([string]$Source, [string]$Destination, [string[]]$Excluded = @()) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.Name -in $Excluded -or $entry.Name -eq '__pycache__') { continue }
        $target = Join-Path $Destination $entry.Name
        if ($entry.PSIsContainer) { Copy-Tree $entry.FullName $target $Excluded }
        else { Copy-Item -LiteralPath $entry.FullName -Destination $target }
    }
}
function Copy-QtRuntime([string]$Source, [string]$Destination) {
    # This workbench imports Widgets/Core/Gui, uses the QWidget VTK adapter and
    # writes PNG through Qt/VTK. QtSql, QML, multimedia and their optional vendor
    # plugins are not product capabilities and must not enter the dependency scan.
    $modules = @('QtCore', 'QtGui', 'QtWidgets')
    $plugins = @('platforms/qwindows.dll', 'platforms/qoffscreen.dll',
                 'platforms/qminimal.dll', 'styles/qmodernwindowsstyle.dll')
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($name in @('__init__.py', '_config.py', '_git_pyside_version.py',
                        'py.typed', 'pyside6.abi3.dll', 'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
                        'PySide6_Essentials.json', 'PySide6_Addons.json')) {
        $path = Join-Path $Source $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required Qt runtime file missing: $name" }
        Copy-Item -LiteralPath $path -Destination $Destination
    }
    foreach ($module in $modules) {
        foreach ($extension in @('.pyd', '.pyi')) {
            $path = Join-Path $Source ($module + $extension)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required Qt module missing: $module$extension" }
            Copy-Item -LiteralPath $path -Destination $Destination
        }
    }
    Copy-Tree (Join-Path $Source 'support') (Join-Path $Destination 'support')
    foreach ($plugin in $plugins) {
        $path = Join-Path $Source ('plugins/' + $plugin)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required Qt plugin missing: $plugin" }
        $target = Join-Path $Destination ('plugins/' + $plugin)
        New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
        Copy-Item -LiteralPath $path -Destination $target
    }
    return @{ version=1; modules=$modules; plugins=$plugins; image_formats='PNG built into QtGui';
              unsupported=@('QtSql and SQL drivers', 'QML', 'multimedia', 'web engine', 'network plugins');
              required_dependency_policy='Every included binary is inspected; unresolved non-system dependencies are fatal.' }
}
try {
    $pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run scripts/bootstrap.ps1 first.' }
    $bindings = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'build/python') -Filter '_lumafem*.pyd' -ErrorAction SilentlyContinue)
    $cli = Join-Path $projectRoot 'build/bin/lumafem_cli.exe'
    if ($bindings.Count -ne 1 -or -not (Test-Path -LiteralPath $cli)) { throw 'Native artifacts are missing or ambiguous. Run scripts/build.ps1 first.' }
    # A version label alone cannot establish a same-source build. Ask Ninja to
    # inspect dependencies without building, then check timestamps conservatively.
    $ninja = Join-Path $projectRoot '.venv/Scripts/ninja.exe'
    if (-not (Test-Path -LiteralPath $ninja)) { throw 'Ninja is missing. Run scripts/bootstrap.ps1 and scripts/build.ps1 first.' }
    $pending = & $ninja -C (Join-Path $projectRoot 'build') -n lumafem_cli _lumafem 2>&1
    if ($LASTEXITCODE -ne 0 -or ($pending -join "`n") -notmatch 'ninja: no work to do\.' -or ($pending -join "`n") -match '\[\d+/\d+\]') {
        throw 'Native build has pending work or cannot be checked. Run scripts/build.ps1 before packaging.'
    }
    $nativeSources = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src'),(Join-Path $projectRoot 'include'),(Join-Path $projectRoot 'cmake') -Recurse -File)
    $nativeSources += Get-Item -LiteralPath (Join-Path $projectRoot 'CMakeLists.txt'),(Join-Path $projectRoot 'CMakePresets.json')
    $oldestBinary = (@((Get-Item -LiteralPath $cli), $bindings[0]) | Sort-Object LastWriteTimeUtc | Select-Object -First 1).LastWriteTimeUtc
    if (@($nativeSources | Where-Object LastWriteTimeUtc -GT $oldestBinary).Count) { throw 'Native sources are newer than the artifacts. Run scripts/build.ps1 first.' }
    $identityProbe = @'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path[:0] = [str(root / 'build' / 'python'), str(root / 'python')]
import _lumafem as native
from lumafem import contracts
if pathlib.Path(native.__file__).resolve().parent != (root / 'build' / 'python').resolve():
    raise RuntimeError('Wrong native module')
if native.kernel_version != contracts.KERNEL_VERSION or native.kernel_version != sys.argv[2]:
    raise RuntimeError('Stale native kernel identity')
if native.reader_capability != contracts.READER_CAPABILITY or native.reader_capability != int(sys.argv[3]):
    raise RuntimeError('Reader capability mismatch')
if native.schema_version != contracts.SCHEMA_VERSION or native.schema_version != int(sys.argv[4]):
    raise RuntimeError('SQL schema mismatch')
print(json.dumps(dict(kernel_version=native.kernel_version, reader_capability=native.reader_capability,
                     schema_version=native.schema_version, numerics_contract=contracts.NUMERICS_CONTRACT)))
'@
    $nativeIdentityText = & $pythonExe -c $identityProbe $projectRoot $expectedKernelVersion $expectedReaderCapability $expectedSchemaVersion
    if ($LASTEXITCODE -ne 0) { throw 'Built native identity/import check failed. Complete the same-source build before packaging.' }
    $nativeIdentity = $nativeIdentityText | ConvertFrom-Json
    if ($nativeIdentity.numerics_contract -ne $buildRevision) { throw 'Source numerical contract does not match the package revision.' }
    $expectedCliHash = '14317d70df444429a8c02eb14d60060a8d43c4194b6abbe6064e650101e6e17f'
    $expectedBindingHash = 'b626d09b114e98b28048d4ae5ba4777979f9cdb625fc4c5a11e1864ef6ba49ea'
    if ((Get-FileHash -LiteralPath $cli -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedCliHash -or
        (Get-FileHash -LiteralPath $bindings[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedBindingHash) {
        throw 'Numerical CLI/PYD differ from the protected A05 baseline. Packaging is refused.'
    }
    $launcherBuild = Join-Path $projectRoot 'build/windows_launcher'
    & $pythonExe (Join-Path $projectRoot 'scripts/launcher_manifest.py') verify --project-root $projectRoot --build-dir $launcherBuild
    if ($LASTEXITCODE -ne 0) { throw 'Launcher provenance/freshness verification failed. Run scripts/build.ps1 first.' }
    $dumpbinCommand = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
    if (-not $dumpbinCommand) { throw 'Run packaging from the MSVC x64 developer environment so dumpbin.exe can inspect runtime dependencies.' }
    $dumpbin = $dumpbinCommand.Source
    $infoText = & $pythonExe -c 'import json,sys,struct; print(json.dumps({"base":sys.base_prefix,"version":sys.version,"bits":struct.calcsize("P")*8}))'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the provisioned runtime.' }
    $info = $infoText | ConvertFrom-Json
    if ($info.bits -ne 64 -or -not $info.version.StartsWith('3.10.')) { throw 'CPython 3.10 x64 is required.' }
    $distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
    if (-not $OutputDirectory) { $OutputDirectory = Join-Path $distRoot ('LumaFEM-' + $buildRevision + '-GUI-ICON-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    $targetRoot = [IO.Path]::GetFullPath($OutputDirectory)
    if (-not $targetRoot.StartsWith($distRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be a new child directory of this repository dist/.' }
    if ((Split-Path $targetRoot -Parent) -ne $distRoot -or (Split-Path $targetRoot -Leaf) -notlike ('LumaFEM-' + $buildRevision + '-GUI-ICON-?*')) { throw 'Output must be a new dist/LumaFEM-CR-NUM-002-GUI-ICON-* directory.' }
    if (Test-Path -LiteralPath $targetRoot) { throw 'Output already exists. Choose a new directory; existing packages are never overwritten.' }
    $caseProfile = $null
    if ($CaseDir) {
        $CaseDir = [IO.Path]::GetFullPath($CaseDir)
        if (-not (Test-Path -LiteralPath (Join-Path $CaseDir 'NC0.json') -PathType Leaf)) { throw '-CaseDir must contain NC0.json; this option explicitly copies local case data.' }
        $caseDocument = Get-Content -LiteralPath (Join-Path $CaseDir 'NC0.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $mapping = $caseDocument.model.discretization.length_mapping
        if (-not $mapping) { $mapping = 'gauss_weight_v1' }
        $caseProfile = @{ interpretation='declared input metadata, not validation';
                          revision_id=$caseDocument.revision_id; length_mapping=$mapping;
                          compression_criterion=$caseDocument.model.compression_criterion;
                          absent_criterion_semantics='route_c_point_v1';
                          source_sha256=(Get-FileHash -LiteralPath (Join-Path $CaseDir 'NC0.json') -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    New-Item -ItemType Directory -Path $targetRoot | Out-Null
    'Package generation incomplete.' | Set-Content -LiteralPath (Join-Path $targetRoot 'INCOMPLETE.txt')
    $runtime = Join-Path $targetRoot 'runtime'
    New-Item -ItemType Directory -Path $runtime | Out-Null
    foreach ($pattern in @('python.exe','pythonw.exe','python3*.dll','vcruntime*.dll','LICENSE*')) {
        foreach ($file in Get-ChildItem -LiteralPath $info.base -Filter $pattern -File) { Copy-Item -LiteralPath $file.FullName -Destination $runtime }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $runtime 'python310.dll'))) { throw 'Base CPython runtime lacks python310.dll.' }
    # Copy the runtime, never the whole Conda installation, envs, packages or base site-packages.
    Copy-Tree (Join-Path $info.base 'Lib') (Join-Path $runtime 'Lib') @('site-packages')
    if (Test-Path -LiteralPath (Join-Path $info.base 'DLLs')) { Copy-Tree (Join-Path $info.base 'DLLs') (Join-Path $runtime 'DLLs') }
    $excludedPackages = @('PySide6') + @(Get-ChildItem -LiteralPath (Join-Path $projectRoot '.venv/Lib/site-packages') | Where-Object Name -Match '(?i)opensees' | ForEach-Object Name)
    Copy-Tree (Join-Path $projectRoot '.venv/Lib/site-packages') (Join-Path $runtime 'Lib/site-packages') $excludedPackages
    $qtPolicy = Copy-QtRuntime (Join-Path $projectRoot '.venv/Lib/site-packages/PySide6') (Join-Path $runtime 'Lib/site-packages/PySide6')
    $qtPolicy | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $targetRoot 'qt-runtime-policy.json') -Encoding UTF8
    Copy-Tree (Join-Path $projectRoot 'python/lumafem') (Join-Path $targetRoot 'app/lumafem')
    New-Item -ItemType Directory -Path (Join-Path $targetRoot 'native'),(Join-Path $targetRoot 'bin'),(Join-Path $targetRoot 'licenses') | Out-Null
    Copy-Item -LiteralPath $bindings[0].FullName -Destination (Join-Path $targetRoot 'native')
    Copy-Item -LiteralPath $cli -Destination (Join-Path $targetRoot 'bin')
    Copy-Item -LiteralPath (Join-Path $launcherBuild 'bin/LumaFEM.exe') -Destination $targetRoot
    Copy-Item -LiteralPath (Join-Path $launcherBuild 'launcher-build-manifest.json') -Destination $targetRoot
    foreach ($name in @('THIRD_PARTY_NOTICES.md','requirements-desktop.txt')) { Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $targetRoot }
    foreach ($dependency in @('eigen','nlohmann_json')) {
        $source = Join-Path $projectRoot ('build/_deps/' + $dependency + '-src')
        $licenses = @(Get-ChildItem -LiteralPath $source -File -ErrorAction SilentlyContinue | Where-Object Name -Match '^(COPYING|LICENSE|Copyright)')
        if (-not $licenses) { throw "Dependency license texts missing for $dependency; complete the native build first." }
        $destination = Join-Path $targetRoot ('licenses/' + $dependency)
        New-Item -ItemType Directory -Path $destination | Out-Null
        foreach ($file in $licenses) { Copy-Item -LiteralPath $file.FullName -Destination $destination }
    }
    # Put the required VC runtime next to the standalone CLI and on the Python
    # DLL path, rather than accidentally relying on a copy inside another wheel.
    $crtSource = Join-Path $projectRoot '.venv/Lib/site-packages/shiboken6'
    foreach ($name in @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                        'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll')) {
        $path = Join-Path $crtSource $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required VC runtime file missing: $name" }
        New-Item -ItemType Directory -Force -Path (Join-Path $runtime 'DLLs') | Out-Null
        Copy-Item -LiteralPath $path -Destination (Join-Path $runtime 'DLLs')
        Copy-Item -LiteralPath $path -Destination (Join-Path $targetRoot 'bin')
    }
    # Inspect imported DLLs and copy only their transitive base-runtime dependencies.
    $searchDirs = @($info.base, (Join-Path $info.base 'DLLs'), (Join-Path $info.base 'Library/bin'),
                   (Join-Path $projectRoot '.venv/Lib/site-packages/PySide6'))
    $vcRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path $dumpbin) '../../../../../../Redist/MSVC'))
    if (Test-Path -LiteralPath $vcRoot) { $searchDirs += @(Get-ChildItem -Path (Join-Path $vcRoot '*/x64/Microsoft.VC*.CRT') -Directory | ForEach-Object FullName) }
    $candidates = @{}
    foreach ($directory in $searchDirs) {
        if (Test-Path -LiteralPath $directory) { foreach ($file in Get-ChildItem -LiteralPath $directory -Filter '*.dll' -File) { $candidates[$file.Name.ToLowerInvariant()] = $file.FullName } }
    }
    $queue = [Collections.Generic.Queue[string]]::new()
    $known = @{}
    foreach ($file in Get-ChildItem -LiteralPath $targetRoot -Recurse -File | Where-Object Extension -In @('.dll','.pyd','.exe')) { $queue.Enqueue($file.FullName); $known[$file.Name.ToLowerInvariant()] = $true }
    $copied = @()
    while ($queue.Count) {
        $binary = $queue.Dequeue()
        $dependencies = & $dumpbin /NOLOGO /DEPENDENTS $binary
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect runtime dependencies: $binary" }
        foreach ($line in $dependencies) {
            if ($line -notmatch '^\s+([A-Za-z0-9_.+-]+\.dll)\s*$') { continue }
            $name = $matches[1].ToLowerInvariant()
            if ($known.ContainsKey($name) -or $name -match '^(api-ms-win-|ext-ms-win-)') { continue }
            if ($candidates.ContainsKey($name)) {
                $destination = Join-Path $runtime ('DLLs/' + $name)
                New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
                Copy-Item -LiteralPath $candidates[$name] -Destination $destination
                $known[$name] = $true; $queue.Enqueue($destination)
                $copied += @{ source=$candidates[$name]; path=('runtime/DLLs/' + $name) }
            } elseif (-not (Test-Path -LiteralPath (Join-Path $env:SystemRoot ('System32/' + $name)))) { throw "Unresolved runtime DLL $name required by $binary" }
        }
    }
    @{ base_python=$info.base; copied_dependencies=$copied } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $targetRoot 'runtime-sources.json') -Encoding UTF8
    if ($CaseDir) {
        New-Item -ItemType Directory -Path (Join-Path $targetRoot 'cases') | Out-Null
        foreach ($name in @('NC0.json','synthetic_channels.csv','synthetic_channels.metadata.json')) {
            $path = Join-Path $CaseDir $name
            if (Test-Path -LiteralPath $path -PathType Leaf) { Copy-Item -LiteralPath $path -Destination (Join-Path $targetRoot 'cases') }
        }
    }
    $launcher = @'
[CmdletBinding()]
param([string]$Project = '', [string]$CaseDir = '', [Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArguments)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$runtime = Join-Path $root 'runtime'
$env:PYTHONHOME = $runtime
$env:PYTHONPATH = (Join-Path $root 'app') + ';' + (Join-Path $root 'native')
$env:PATH = $runtime + ';' + (Join-Path $runtime 'DLLs') + ';' + (Join-Path $runtime 'Lib/site-packages/PySide6') + ';' + (Join-Path $env:SystemRoot 'System32')
$env:QT_API = 'pyside6'
$env:QT_PLUGIN_PATH = Join-Path $runtime 'Lib/site-packages/PySide6/plugins'
$bootstrap = "import os,runpy; root=os.environ['PYTHONHOME']; handles=[os.add_dll_directory(os.path.join(root,'DLLs'))]; runpy.run_module('lumafem',run_name='__main__')"
$arguments = @('-c',$bootstrap)
if ($Project) { $arguments += @('--project',[IO.Path]::GetFullPath($Project)) }
if (-not $CaseDir -and (Test-Path -LiteralPath (Join-Path $root 'cases/NC0.json'))) { $CaseDir = Join-Path $root 'cases' }
if ($CaseDir) { $arguments += @('--case-dir',[IO.Path]::GetFullPath($CaseDir)) }
if ($AppArguments) { $arguments += $AppArguments }
function ConvertTo-CrtArgument([AllowEmptyString()][string]$Value) {
    # Microsoft CRT argv rules: always quote; double each run of backslashes
    # before a literal quote (plus its escape) and before the closing quote.
    $quoted = [Text.StringBuilder]::new()
    [void]$quoted.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$quoted.Append([char]'\', (2 * $slashes + 1))
        } else {
            [void]$quoted.Append([char]'\', $slashes)
        }
        [void]$quoted.Append($character)
        $slashes = 0
    }
    [void]$quoted.Append([char]'\', (2 * $slashes))
    [void]$quoted.Append('"')
    return $quoted.ToString()
}
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = Join-Path $runtime 'python.exe'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WorkingDirectory = [IO.Directory]::GetCurrentDirectory()
$start.Arguments = (($arguments | ForEach-Object { ConvertTo-CrtArgument $_ }) -join ' ')
$child = [Diagnostics.Process]::Start($start)
if ($null -eq $child) { throw 'Cannot start bundled Python. Check runtime/python.exe and use the legacy script for diagnostics.' }
try {
    $child.WaitForExit()
    $childExitCode = $child.ExitCode
} finally { $child.Dispose() }
exit $childExitCode
'@
    $launcher | Set-Content -LiteralPath (Join-Path $targetRoot 'Launch-LumaFEM.ps1') -Encoding UTF8
    '@echo off', 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-LumaFEM.ps1" %*' | Set-Content -LiteralPath (Join-Path $targetRoot 'LumaFEM.cmd') -Encoding ASCII
    @'
LumaFEM local CR-NUM-002 GUI-ICON development package.
Double-click LumaFEM.exe, or from PowerShell run:
  & './LumaFEM.exe' -Project 'D:/projects/example.lumafem'
  & './LumaFEM.exe' -CaseDir 'D:/cases/example'
Relative Project/CaseDir paths resolve from the caller's working directory.
If cases/NC0.json is included, its directory is the default case. Otherwise
select an existing project/case explicitly. Case files are included only when
the package builder explicitly supplied -CaseDir.
Legacy Launch-LumaFEM.ps1 and LumaFEM.cmd remain available, including for
diagnostics. The script accepts -Project, -CaseDir and remaining AppArguments;
from PowerShell a string array may be supplied explicitly with -AppArguments.
The EXE waits for normal GUI close and returns the child exit status. Visible
launcher failures include an instruction to use the legacy diagnostic script.
Keep the complete directory together when moving it; keep licenses/metadata.
Approved mesh/contour artwork is illustrative branding, not a computed result.
Packaging does not establish runtime acceptance or scientific qualification.
The physical gauge criterion remains an uncalibrated assistant assumption:
total self-weight strain, whole-beam both-edge complete mechanical windows.
Old point Route-C remains separate. Known old 0.2/0.3 frozen snapshots are
analysis-read-only; reanalysis requires an explicit new revision/preparation.
Reader capability3 differs from SQL schema1. Inspect actual prepared metadata.
Prior STRICT failure, C unverified and unrelated D partial remain unchanged.
Qt runtime libraries remain replaceable. No installer or pinning guarantee.
'@ | Set-Content -LiteralPath (Join-Path $targetRoot 'PACKAGE-README.txt') -Encoding UTF8
    $sourceFiles = [Collections.Generic.List[object]]::new()
    foreach ($file in $nativeSources | Sort-Object FullName -Unique) {
        $sourceFiles.Add(@{ path=$file.FullName.Substring($projectRoot.Length + 1).Replace('\','/'); sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() })
    }
    $integrationSources = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'python/lumafem'),(Join-Path $projectRoot 'tools/windows_launcher'),(Join-Path $projectRoot 'scripts') -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -ne '.pyc' })
    $integrationSources += Get-Item -LiteralPath (Join-Path $projectRoot 'README.md'),(Join-Path $projectRoot 'requirements-desktop.txt'),(Join-Path $projectRoot 'THIRD_PARTY_NOTICES.md')
    $integrationFiles = @($integrationSources | Sort-Object FullName -Unique | ForEach-Object {
        @{ path=$_.FullName.Substring($projectRoot.Length + 1).Replace('\','/'); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
    $manifestFiles = [Collections.Generic.List[object]]::new()
    $record = @{ application='LumaFEM'; package_kind='local-development'; build_revision=$buildRevision;
                 expected_kernel_version=$expectedKernelVersion; numerics_contract=$buildRevision;
                 expected_reader_capability=$expectedReaderCapability; sql_schema_version=$expectedSchemaVersion;
                 observed_native_identity=$nativeIdentity; native_build_check='Ninja dry run clean plus source timestamps and native import identity; not scientific acceptance';
                 native_source_files=$sourceFiles;
                 integration_source_files=$integrationFiles;
                 protected_native_hashes=@{ cli=$expectedCliHash; binding=$expectedBindingHash };
                 launcher_provenance='launcher-build-manifest.json version1; strict helper verify plus independent Ninja no-work before copy';
                 expected_predictor_policy=@{ id='accepted_secant_onset_seeded_probe_subspace_v3'; status='candidate_requires_external_validation'; maximum_modal_modes=2; candidates_per_modal_direction=4; maximum_predictor_attempts=15; maximum_prepeak_force_probes=6; maximum_prepeak_base_evaluations=1; interpretation='expected A05 onset bridge and preserved A04 postpeak policy; inspect actual prepared descriptor during verification' };
                 criterion_contract=@{ supported_modes=@('route_c_point_v1','physical_gauge_mean_v1'); new_template_mode='physical_gauge_mean_v1'; operator_version='eb_compatible_edge_window_v1'; reference='total_from_S0'; domain='all complete windows over whole physical beam, both edges'; provenance='uncalibrated_assistant_modeling_assumption'; absent_source_mode='route_c_point_v1' };
                 python=$info.version; includes_local_case=[bool]$CaseDir; case_profile=$caseProfile;
                 validation_status='NOT_ESTABLISHED_BY_PACKAGING'; files=$manifestFiles }
    foreach ($file in Get-ChildItem -LiteralPath $targetRoot -Recurse -File | Where-Object Name -ne 'INCOMPLETE.txt' | Sort-Object FullName) {
        $relative = $file.FullName.Substring($targetRoot.Length + 1).Replace('\','/')
        $manifestFiles.Add(@{ path=$relative; bytes=$file.Length; sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() })
    }
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $targetRoot 'package-manifest.json') -Encoding UTF8
    Remove-Item -LiteralPath (Join-Path $targetRoot 'INCOMPLETE.txt')
    Write-Host "Local package created: $targetRoot"
} catch { Write-Error $_; exit 1 }
