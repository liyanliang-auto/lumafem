[CmdletBinding()]
param([string]$Python = '', [switch]$SkipDesktop)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$venvPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        if (-not $Python) {
            $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($candidate -and $candidate.Source -notmatch 'WindowsApps') { $Python = $candidate.Source }
            elseif (Test-Path -LiteralPath 'D:/software/miniconda310/python.exe') { $Python = 'D:/software/miniconda310/python.exe' }
            else { throw 'Supply -Python with a CPython 3.10 x64 executable path.' }
        }
        Invoke-Checked $Python @('-c', 'import sys,struct; assert sys.version_info[:2] == (3,10) and struct.calcsize("P")==8, "CPython 3.10 x64 is required"')
        Invoke-Checked $Python @('-m', 'venv', (Join-Path $projectRoot '.venv'))
    }
    Invoke-Checked $venvPython @('-c', 'import sys,struct; assert sys.version_info[:2] == (3,10) and struct.calcsize("P")==8, "CPython 3.10 x64 is required"')
    $env:PIP_CACHE_DIR = Join-Path $projectRoot '.cache/pip'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', 'pip==24.3.1')
    if ($SkipDesktop) {
        Invoke-Checked $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', 'cmake==3.30.5', 'ninja==1.11.1.1', 'pybind11==2.13.6')
    } else {
        Invoke-Checked $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $projectRoot 'requirements-desktop.txt'))
    }
    $cacheDir = Join-Path $projectRoot '.cache/dependencies'
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    $archives = @(
        @{ Name='eigen-3.4.0.tar.gz'; Url='https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz'; Hash='8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72' },
        @{ Name='json-3.11.3.tar.xz'; Url='https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz'; Hash='d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d' }
    )
    foreach ($archive in $archives) {
        $archivePath = Join-Path $cacheDir $archive.Name
        if (-not (Test-Path -LiteralPath $archivePath)) {
            Invoke-WebRequest -Uri $archive.Url -OutFile $archivePath -UseBasicParsing -TimeoutSec 120
        }
        if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $archive.Hash) {
            throw "Dependency archive hash mismatch: $($archive.Name). Remove the damaged cached archive and retry."
        }
    }
    $recordDir = Join-Path $projectRoot '.cache/toolchain'
    New-Item -ItemType Directory -Force -Path $recordDir | Out-Null
    & $venvPython -m pip freeze | Set-Content -LiteralPath (Join-Path $recordDir 'installed-packages.txt') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'Could not record installed versions.' }
    Write-Host 'Local dependencies provisioned. Run scripts/build.ps1 next.'
} catch { Write-Error $_; exit 1 }
