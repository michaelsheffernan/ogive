<#
.SYNOPSIS
    Project tasks for Ogive. Usage: .\tasks.ps1 <task>

.DESCRIPTION
    setup   Create .venv and install ogive (editable) with dev tools
    test    Run the tests (pytest)
    lint    Check style and common bugs (ruff). Changes nothing.
    fmt     Auto-fix and format code (ruff). Changes files.
    check   lint + test. Run before every commit.
    clean   Delete caches and out/
    mcpb    Build the Claude Desktop bundle out/ogive-VERSION.mcpb (needs Node.js)
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "test", "lint", "fmt", "check", "clean", "mcpb", "help")]
    [string]$Task = "help"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PythonVersion = "3.14"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"  # Windows defaults to cp1252; force UTF-8 for file I/O

# Run a program and stop if it fails. Native programs signal failure by exit code, not by throwing.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $ErrorActionPreference = "Continue"  # stderr output (e.g. pip notices) is not a failure
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Failed (exit $LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Assert-Venv {
    if (-not (Test-Path $VenvPython)) { throw "No .venv yet. Run: .\tasks.ps1 setup" }
}

function Invoke-Setup {
    if (-not (Test-Path $VenvPython)) {
        Invoke-Native "py" @("-$PythonVersion", "-m", "venv", ".venv")
    }
    Invoke-Native $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Native $VenvPython @("-m", "pip", "install", "-e", ".[dev,openrocket,mcp]")
}

function Invoke-Test {
    Assert-Venv
    Invoke-Native $VenvPython @("-m", "pytest")
}

function Invoke-Lint {
    Assert-Venv
    Invoke-Native $VenvPython @("-m", "ruff", "check", ".")
    Invoke-Native $VenvPython @("-m", "ruff", "format", "--check", ".")
}

function Invoke-Fmt {
    Assert-Venv
    # Format first: it wraps long lines the linter can't fix, so they don't block the fix step.
    Invoke-Native $VenvPython @("-m", "ruff", "format", ".")
    Invoke-Native $VenvPython @("-m", "ruff", "check", "--fix", ".")
}

function Invoke-Clean {
    $targets = @(".pytest_cache", ".ruff_cache", "out")
    $targets += Get-ChildItem -Path src, tests -Recurse -Directory -Filter "__pycache__" |
        ForEach-Object { $_.FullName }
    foreach ($t in $targets) {
        if ($t -and (Test-Path $t)) {
            Remove-Item -Recurse -Force $t
            Write-Host "removed $t"
        }
    }
}

switch ($Task) {
    "setup" { Invoke-Setup }
    "test"  { Invoke-Test }
    "lint"  { Invoke-Lint }
    "fmt"   { Invoke-Fmt }
    "check" { Invoke-Lint; Invoke-Test }
    "clean" { Invoke-Clean }
    "mcpb"  { Assert-Venv; Invoke-Native $VenvPython @("scripts/build_mcpb.py") }
    "help"  { Get-Help $PSCommandPath -Detailed }
}
