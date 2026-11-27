<#
.SYNOPSIS
  Installs the kanboard skill into ~/.claude/skills so it is available in every project.

.DESCRIPTION
  Copies SKILL.md, references/ and scripts/ to $HOME\.claude\skills\kanboard.
  This folder stays the source of truth - re-run the script after editing it.
  No credentials are copied: the skill reads them at run time from a .kanboard.env
  or dev-paths.ps1 in the repo, or from KANBOARD_* environment variables.

.PARAMETER Target
  Alternative install directory (e.g. a project's .claude\skills for a
  project-scoped install).

.PARAMETER Force
  Overwrite an existing install without asking.

.EXAMPLE
  .\install.ps1
.EXAMPLE
  .\install.ps1 -Target D:\myrepo\.claude\skills\kanboard
.EXAMPLE
  .\install.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Target = (Join-Path $HOME ".claude\skills\kanboard"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$items = @("SKILL.md", "references", "scripts")

foreach ($i in $items) {
    if (-not (Test-Path (Join-Path $src $i))) {
        throw "missing in source: $i (run this from the skill folder)"
    }
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Warning "python not found on PATH - the skill needs Python 3.9+ (stdlib only)"
} else {
    $v = & $py.Source -c "import sys;print(sys.version.split()[0])"
    Write-Host "python: $($py.Source) ($v)"
}

if ((Test-Path $Target) -and -not $Force -and -not $WhatIfPreference) {
    Write-Host ""
    Write-Host "Target already exists: $Target"
    $answer = Read-Host "Overwrite SKILL.md, references/ and scripts/ there? [y/N]"
    if ($answer -notmatch '^[yYjJ]') {
        Write-Host "aborted, nothing changed"
        return
    }
}

if ($PSCmdlet.ShouldProcess($Target, "install kanboard skill")) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    foreach ($i in $items) {
        $dest = Join-Path $Target $i
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item (Join-Path $src $i) -Destination $Target -Recurse -Force
        Write-Host "  copied $i"
    }
    Get-ChildItem $Target -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "installed to $Target"
    Write-Host "verify from a repo that has dev-paths.ps1 with Kanboard credentials:"
    Write-Host "  python `"$Target\scripts\kanboard.py`" call getVersion"
}
