# PowerShell script to execute the MeshBank Service Mesh mTLS Test Suite
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

Write-Host "Running MeshBank Service Mesh & mTLS Test Suite..." -ForegroundColor Cyan
& "$projectRoot\.venv\Scripts\python.exe" "$scriptDir\test-mesh-mtls.py"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Service Mesh mTLS tests FAILED with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
} else {
    Write-Host "Service Mesh mTLS tests PASSED successfully!" -ForegroundColor Green
}
