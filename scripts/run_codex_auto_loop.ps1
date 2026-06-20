param(
    [string]$Mode = "dry_run",
    [string]$TaskFile,
    [int]$MaxRounds,
    [int]$MaxRuntimeMinutes,
    [int]$MaxChangedFiles,
    [int]$MaxDiffLines,
    [string[]]$TargetedTests,
    [string]$DryRunExternalCalls = "true"
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $RepoRoot "scripts\run_codex_auto_loop.py"

$Args = @($ScriptPath, "--mode", $Mode, "--dry-run-external-calls", $DryRunExternalCalls)
if ($TaskFile) { $Args += @("--task-file", $TaskFile) }
if ($PSBoundParameters.ContainsKey("MaxRounds")) { $Args += @("--max-rounds", "$MaxRounds") }
if ($PSBoundParameters.ContainsKey("MaxRuntimeMinutes")) { $Args += @("--max-runtime-minutes", "$MaxRuntimeMinutes") }
if ($PSBoundParameters.ContainsKey("MaxChangedFiles")) { $Args += @("--max-changed-files", "$MaxChangedFiles") }
if ($PSBoundParameters.ContainsKey("MaxDiffLines")) { $Args += @("--max-diff-lines", "$MaxDiffLines") }
if ($TargetedTests) { $Args += @("--targeted-tests") + $TargetedTests }

Push-Location $RepoRoot
try {
    $Output = & python @Args
    $Output | ForEach-Object { Write-Host $_ }
} finally {
    Pop-Location
}
