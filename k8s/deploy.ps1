#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Full deploy of MeshBank to a local kind Kubernetes cluster.

.DESCRIPTION
  Idempotent: safe to re-run. Run from the repository root.

  Steps
  -----
  1. Create the kind cluster (skips if already exists).
  2. Load the six meshbank Docker images into the cluster.
  3. Apply namespace, ConfigMap, Secrets (injecting real JWT key material).
  4. Apply PostgreSQL, wait for it to be Ready.
  5. Apply all six banking service Deployments + Services.
  6. Wait for every Deployment to roll out.
  7. Print the NodePort URLs for manual smoke-testing.

.PARAMETER SkipBuild
  If set, skips `docker compose build` (assumes images already exist locally).

.EXAMPLE
  .\k8s\deploy.ps1
  .\k8s\deploy.ps1 -SkipBuild
#>
param (
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# Make failing native commands (kind, kubectl, docker) stop the script (PowerShell 7.3+).
$PSNativeCommandUseErrorActionPreference = $true

$ROOT = Split-Path -Parent $PSScriptRoot
Push-Location $ROOT

Write-Host "`n=== MeshBank Kubernetes Deploy ===" -ForegroundColor Cyan

# ── 0. Pre-flight ─────────────────────────────────────────────────────────────
foreach ($tool in @("kind", "kubectl", "docker")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Error "$tool not found in PATH. Please install it first."
    }
}

# ── 1. Verify JWT key material ────────────────────────────────────────────────
$pubKey  = "secrets\jwt\public\dev-local-1.pem"
$privKey = "secrets\jwt\private\dev-local-1.pem"
if (-not (Test-Path $pubKey) -or -not (Test-Path $privKey)) {
    Write-Host "JWT keys not found. Generating dev keys..." -ForegroundColor Yellow
    .\.venv\Scripts\python.exe -m common.keys --dir secrets/jwt --kid dev-local-1
}

# ── 2. Build images if needed ─────────────────────────────────────────────────
if (-not $SkipBuild) {
    Write-Host "`n[1/7] Building Docker images..." -ForegroundColor Cyan
    docker compose build --quiet
    # The canary is a separate release artifact that reports version v2.
    docker build --quiet -f services/payment-service/Dockerfile --build-arg SERVICE_VERSION=v2 -t meshbank/payment-service:v2 .
} else {
    Write-Host "`n[1/7] Skipping image build (--SkipBuild set)" -ForegroundColor Gray
}

# ── 3. Create kind cluster ────────────────────────────────────────────────────
Write-Host "`n[2/7] Creating kind cluster 'meshbank'..." -ForegroundColor Cyan
$existing = (kind get clusters 2>&1) -join "`n"
if ($existing -match "meshbank") {
    Write-Host "  Cluster 'meshbank' already exists, skipping." -ForegroundColor Gray
} else {
    kind create cluster --config k8s\kind-cluster.yaml --wait 90s
}
kubectl config use-context kind-meshbank

# ── 4. Load images into kind ──────────────────────────────────────────────────
Write-Host "`n[3/7] Loading Docker images into kind cluster..." -ForegroundColor Cyan
foreach ($img in @("postgres:15-alpine", "nginx:alpine")) {
    $PSNativeCommandUseErrorActionPreference = $false   # probe: failure is expected
    docker image inspect $img *> $null
    $missing = $LASTEXITCODE -ne 0
    $PSNativeCommandUseErrorActionPreference = $true
    if ($missing) { docker pull --quiet $img }
}
$images = @(
    "postgres:15-alpine",
    "nginx:alpine",
    "meshbank/payment-service:v2",
    "meshbank/auth-service:dev",
    "meshbank/account-service:dev",
    "meshbank/transaction-service:dev",
    "meshbank/payment-service:dev",
    "meshbank/fraud-service:dev",
    "meshbank/notification-service:dev"
)
foreach ($img in $images) {
    Write-Host "  Loading $img"
    kind load docker-image $img --name meshbank
}

# ── 5. Generate Service Mesh mTLS Certificates & Configs ──────────────────────
Write-Host "`n[4/7] Generating Mesh CA, Workload certs, and applying configs..." -ForegroundColor Cyan
Push-Location scripts
..\.venv\Scripts\python.exe generate_mesh_certs.py
..\.venv\Scripts\python.exe generate_mesh_sidecars.py --check
Pop-Location
$existingDeployments = @()
$PSNativeCommandUseErrorActionPreference = $false   # probe: failure is expected
foreach ($d in @("auth-service", "account-service", "transaction-service", "payment-service",
                 "payment-service-canary", "fraud-service", "notification-service", "payment-router")) {
    kubectl get deployment $d -n meshbank *> $null
    if ($LASTEXITCODE -eq 0) { $existingDeployments += $d }
}
$PSNativeCommandUseErrorActionPreference = $true
kubectl apply -f k8s\namespace.yaml
kubectl apply -f k8s\configmap.yaml
kubectl apply -f k8s\secrets.yaml
kubectl apply -f k8s\mesh-certs.yaml
kubectl apply -f k8s\mesh-sidecar-configs.yaml
kubectl apply -f k8s\mesh-router-config.yaml
kubectl apply -f k8s\network-policy-mesh.yaml

# Inject actual JWT public and private keys from files
Write-Host "  Creating/updating secret 'jwt-public-keys' from $pubKey..."
kubectl create secret generic jwt-public-keys -n meshbank --from-file="dev-local-1.pem=$pubKey" --dry-run=client -o yaml | kubectl apply -f -

Write-Host "  Creating/updating secret 'jwt-signing-key' from $privKey..."
kubectl create secret generic jwt-signing-key -n meshbank --from-file="dev-local-1.pem=$privKey" --dry-run=client -o yaml | kubectl apply -f -

# Objects from earlier versions of these manifests.
kubectl delete networkpolicy mesh-default-policy -n meshbank --ignore-not-found
kubectl delete secret mesh-ca-cert -n meshbank --ignore-not-found
$PSNativeCommandUseErrorActionPreference = $false
$selector = kubectl get deployment payment-service -n meshbank -o jsonpath='{.spec.selector.matchLabels.app}' 2>$null
$PSNativeCommandUseErrorActionPreference = $true
if ($selector -eq "payment-service") {
    Write-Host "  Replacing payment-service deployment (selector changed; selectors are immutable)"
    kubectl delete deployment payment-service -n meshbank --wait=true
}

# ── 6. Deploy PostgreSQL, wait for ready ──────────────────────────────────────
Write-Host "`n[5/7] Deploying PostgreSQL..." -ForegroundColor Cyan
kubectl apply -f k8s\postgres.yaml
Write-Host "  Waiting for postgres rollout..."
kubectl rollout status deployment/postgres -n meshbank --timeout=120s

# ── 7. Deploy banking services ────────────────────────────────────────────────
Write-Host "`n[6/7] Deploying banking services (with Service Mesh sidecars & Canary)..." -ForegroundColor Cyan
kubectl apply -f k8s\auth-service.yaml
kubectl apply -f k8s\account-service.yaml
kubectl apply -f k8s\fraud-notification.yaml
kubectl apply -f k8s\transaction-service.yaml
kubectl apply -f k8s\payment-service.yaml
kubectl apply -f k8s\payment-canary.yaml
kubectl apply -f k8s\nodeports.yaml
# Fixed image tags and subPath configs are only read at container start.
foreach ($d in $existingDeployments) { kubectl rollout restart deployment/$d -n meshbank }

# Wait for all deployments
Write-Host "`n[7/7] Waiting for all deployments to be ready (up to 3 min)..." -ForegroundColor Cyan
$deployments = @(
    "auth-service", "account-service", "fraud-service",
    "notification-service", "transaction-service", "payment-service",
    "payment-service-canary", "payment-router"
)
foreach ($d in $deployments) {
    Write-Host "  Waiting: $d"
    kubectl rollout status deployment/$d -n meshbank --timeout=180s
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host "`n=== All services deployed! ===`n" -ForegroundColor Green
kubectl get pods -n meshbank
Write-Host ""
Write-Host "NodePort access (from Windows host):" -ForegroundColor Cyan
Write-Host "  Auth        http://localhost:30001/ready"
Write-Host "  Account     http://localhost:30002/ready"
Write-Host "  Transaction http://localhost:30003/ready"
Write-Host "  Payment     http://localhost:30004/ready"
Write-Host "  Fraud       http://localhost:30005/ready"
Write-Host "  Notification http://localhost:30006/ready"
Write-Host ""
Write-Host "Run the banking workflow test:"
Write-Host "  `$env:MESHBANK_HOST='localhost'; `$env:AUTH_PORT=30001; `$env:ACCT_PORT=30002; `$env:TXN_PORT=30003; .\.venv\Scripts\python.exe k8s\k8s-smoke-test.py"

Pop-Location
