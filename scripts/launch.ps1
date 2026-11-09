[CmdletBinding()]
param([string]$Project = '', [string]$CaseDir = '', [Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArguments)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
try {
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run scripts/bootstrap.ps1 and scripts/build.ps1 first.' }
    $nativeDir = Join-Path $projectRoot 'build/python'
    if (-not (Get-ChildItem -LiteralPath $nativeDir -Filter '_lumafem*.pyd' -ErrorAction SilentlyContinue)) { throw 'Native core binding is missing. Run scripts/build.ps1.' }
    $env:PYTHONPATH = (Join-Path $projectRoot 'python') + ';' + $nativeDir + $(if ($env:PYTHONPATH) { ';' + $env:PYTHONPATH } else { '' })
    $env:QT_API = 'pyside6'
    if (-not $CaseDir) { $CaseDir = Join-Path $projectRoot 'development_doc/runtime/CR-NUM-002' }
    $arguments = @('-m', 'lumafem')
    if ($Project) { $arguments += @('--project', [IO.Path]::GetFullPath($Project)) }
    if ($CaseDir) { $arguments += @('--case-dir', [IO.Path]::GetFullPath($CaseDir)) }
    if ($AppArguments) { $arguments += $AppArguments }
    Push-Location $projectRoot
    try { & $pythonExe @arguments; if ($LASTEXITCODE -ne 0) { throw "LumaFEM exited with code $LASTEXITCODE" } }
    finally { Pop-Location }
} catch { Write-Error $_; exit 1 }
