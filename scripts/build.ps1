[CmdletBinding()]
param([switch]$CoreOnly, [int]$Jobs = 3, [string]$VisualStudioPath = '')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolsDir = Join-Path $projectRoot '.venv/Scripts'
$cmakeExe = Join-Path $toolsDir 'cmake.exe'
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}
try {
    if ($Jobs -lt 1) { throw '-Jobs must be positive.' }
    if (-not (Test-Path -LiteralPath $cmakeExe)) { throw 'Run scripts/bootstrap.ps1 before building.' }
    if (-not $VisualStudioPath) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
        if (Test-Path -LiteralPath $vswhere) {
            $VisualStudioPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        }
        if (-not $VisualStudioPath -and (Test-Path -LiteralPath 'D:/software/VS2019/VC/Auxiliary/Build/vcvars64.bat')) { $VisualStudioPath = 'D:/software/VS2019' }
    }
    if ($VisualStudioPath) {
        $vcvars = Join-Path $VisualStudioPath 'VC/Auxiliary/Build/vcvars64.bat'
        if (-not (Test-Path -LiteralPath $vcvars)) { throw 'Visual Studio x64 C++ toolchain was not found.' }
        if ($vcvars.Contains('"') -or $vcvars.Contains('%')) { throw 'Unsupported characters in toolchain path.' }
        $toolchainVariables = 'INCLUDE LIB LIBPATH PATH WindowsSdkDir WindowsSDKVersion UniversalCRTSdkDir UCRTVersion VCToolsInstallDir VCINSTALLDIR VSINSTALLDIR VSCMD_ARG_TGT_ARCH'
        $environmentLines = & $env:ComSpec /d /s /c ('""' + $vcvars + '" >nul && for %V in (' + $toolchainVariables + ') do @set %V"')
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the Visual Studio x64 environment.' }
        foreach ($line in $environmentLines) {
            if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
        }
    }
    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'MSVC cl.exe is unavailable. Supply -VisualStudioPath.' }
    $env:VSLANG = '1033'
    $env:PATH = $toolsDir + ';' + $env:PATH
    $preset = if ($CoreOnly) { 'windows-core' } else { 'windows-release' }
    Push-Location $projectRoot
    try {
        Invoke-Checked $cmakeExe @('--preset', $preset)
        Invoke-Checked $cmakeExe @('--build', '--preset', $preset, '--parallel', "$Jobs")
        if (-not $CoreOnly) {
            $launcherBuild = Join-Path $projectRoot 'build/windows_launcher'
            Invoke-Checked $cmakeExe @('-S', (Join-Path $projectRoot 'tools/windows_launcher'), '-B', $launcherBuild, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release')
            Invoke-Checked $cmakeExe @('--build', $launcherBuild, '--target', 'LumaFEM', '--parallel', "$Jobs")
            Invoke-Checked (Join-Path $toolsDir 'python.exe') @((Join-Path $PSScriptRoot 'launcher_manifest.py'), 'write', '--project-root', $projectRoot, '--build-dir', $launcherBuild)
        }
    } finally { Pop-Location }
} catch { Write-Error $_; exit 1 }
