# PowerShell wrapper for MeshBank Phase 2 Traffic Management & Canary Deployments
param (
    [Parameter(Position=0)]
    [string]$Command = "demo-rollout",
    [int]$Stable = 80,
    [int]$Canary = 20,
    [int]$Count = 100
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$python = "$projectRoot\.venv\Scripts\python.exe"
$targetScript = "$scriptDir\canary-traffic.py"

Write-Host "MeshBank Phase 2: Traffic Management & Canary Deployment" -ForegroundColor Cyan

switch ($Command) {
    "set-split" {
        & $python $targetScript --set-split $Stable $Canary
    }
    "verify" {
        & $python $targetScript --verify $Count
    }
    "header" {
        & $python $targetScript --test-header
    }
    "demo-rollout" {
        & $python $targetScript --demo-rollout
    }
    "demo-rollback" {
        & $python $targetScript --demo-rollback
    }
    Default {
        & $python $targetScript --demo-rollout
    }
}
