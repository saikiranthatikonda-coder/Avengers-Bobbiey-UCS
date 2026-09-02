# ═══ Bobbiey UCS — restore the private migration bundle (Windows) ═══
#
# Puts your secrets/config/data back after cloning the repo on a new machine.
# NON-DESTRUCTIVE: anything it would replace is backed up to .restore-backup\
# first, and nothing is ever deleted.
#
#   .\restore-data.ps1 -Bundle "D:\bobbiey-migration"
#   .\restore-data.ps1 -Bundle "D:\bobbiey-migration" -IncludeClaudeMemory
#
# See MIGRATION.md for the full procedure.

param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [switch]$IncludeClaudeMemory,
    [switch]$NewNodeIdentity   # use if the OLD laptop will keep running as a node
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$proj = $PSScriptRoot

$src = Join-Path $Bundle "private-data"
if (-not (Test-Path -LiteralPath $src)) {
    Write-Host "[restore] ERROR: no 'private-data' folder inside $Bundle" -ForegroundColor Red
    Write-Host "[restore] point -Bundle at the folder that CONTAINS private-data\" -ForegroundColor Yellow
    exit 1
}

$backup = Join-Path $proj (".restore-backup\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
[System.IO.Directory]::CreateDirectory($backup) | Out-Null

Write-Host "[restore] project : $proj" -ForegroundColor Cyan
Write-Host "[restore] bundle  : $src" -ForegroundColor Cyan
Write-Host "[restore] backups : $backup" -ForegroundColor Cyan
Write-Host ""

$restored = 0; $backedUp = 0; $skipped = 0
foreach ($f in (Get-ChildItem -LiteralPath $src -File)) {
    $dest = Join-Path $proj $f.Name

    if ($NewNodeIdentity -and $f.Name -eq "node_id.txt") {
        Write-Host "  skip     node_id.txt (new identity requested)" -ForegroundColor DarkGray
        $skipped++
        continue
    }

    # never overwrite without keeping a copy
    if (Test-Path -LiteralPath $dest) {
        [System.IO.File]::Copy($dest, (Join-Path $backup $f.Name), $true)
        $backedUp++
    }
    [System.IO.File]::Copy($f.FullName, $dest, $true)
    Write-Host ("  restored {0}" -f $f.Name) -ForegroundColor Green
    $restored++
}

Write-Host ""
Write-Host "[restore] $restored file(s) restored, $backedUp backed up, $skipped skipped" -ForegroundColor Cyan

# ── optional: restore the Claude conversation context ──
if ($IncludeClaudeMemory) {
    $ctx = Join-Path $Bundle "claude-context"
    if (Test-Path -LiteralPath $ctx) {
        # Claude Code keys memory by the directory you launch it from; this is the
        # default for a user profile named the same as the old machine's.
        $memRoot = Join-Path $env:USERPROFILE ".claude\projects"
        Write-Host ""
        Write-Host "[restore] Claude memory files are in: $ctx" -ForegroundColor Cyan
        Write-Host "[restore] copy the *.md files into your new machine's memory folder under:" -ForegroundColor Yellow
        Write-Host "          $memRoot\<project-key>\memory\" -ForegroundColor Yellow
        Write-Host "          (launch Claude Code once in the project to create that folder,)" -ForegroundColor Yellow
        Write-Host "          (then drop bobbiey-ucs-project.md + MEMORY.md in and restart it.)" -ForegroundColor Yellow
        Get-ChildItem -LiteralPath $ctx -Filter *.md | ForEach-Object { Write-Host ("          - " + $_.Name) }
    }
}

# ── sanity checks ──
Write-Host ""
Write-Host "[restore] verifying…" -ForegroundColor Cyan
$critical = @(".env", "credentials.json", "token.json", "operator_memory.json")
foreach ($c in $critical) {
    if (Test-Path -LiteralPath (Join-Path $proj $c)) { Write-Host "  OK   $c" -ForegroundColor Green }
    else { Write-Host "  MISSING  $c" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "[restore] NEXT: open .env and fix CLAUDE_BIN for this machine, then run start-jarvis.cmd" -ForegroundColor Cyan
Write-Host "[restore] find the new path with:  where.exe claude" -ForegroundColor DarkGray
