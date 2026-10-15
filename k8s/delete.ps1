#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Tears down the MeshBank kind Kubernetes cluster.

.DESCRIPTION
  Deletes the kind cluster named 'meshbank' and reverts kubectl context.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "Tearing down MeshBank kind cluster..." -ForegroundColor Cyan
kind delete cluster --name meshbank
Write-Host "MeshBank cluster deleted." -ForegroundColor Green
