#!/usr/bin/env powershell

<#
.SYNOPSIS
    SSH into EC2 instance and manage GCP Dashboard
.DESCRIPTION
    Deploy, update, or manage dashboard on EC2 instance
.EXAMPLE
    .\manage-ec2.ps1 -PublicIP "54.123.45.67" -KeyPath "C:\keys\key.pem" -Action deploy
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$PublicIP,
    
    [Parameter(Mandatory=$true)]
    [string]$KeyPath,
    
    [Parameter(Mandatory=$false)]
    [ValidateSet("deploy", "logs", "restart", "status", "update", "shell")]
    [string]$Action = "status",
    
    [Parameter(Mandatory=$false)]
    [string]$Username = "ubuntu"
)

$ErrorActionPreference = "Stop"

# Colors
$Green = "`e[92m"
$Yellow = "`e[93m"
$Red = "`e[91m"
$Reset = "`e[0m"

function Write-Success { param([string]$Message)
    Write-Host "$Green✅ $Message$Reset" }

function Write-Info { param([string]$Message)
    Write-Host "$Yellow➜ $Message$Reset" }

function Write-Error2 { param([string]$Message)
    Write-Host "$Red❌ $Message$Reset" }

# Validate
if (-not (Test-Path $KeyPath)) {
    Write-Error2 "Key file not found: $KeyPath"
    exit 1
}

# Set key permissions
$keyItem = Get-Item $KeyPath
icacls $keyItem.FullName /inheritance:r /grant:r "$($env:username):(f)" 2>$null | Out-Null

Write-Info "Connecting to: $Username@$PublicIP"
Write-Host ""

# Execute action
switch ($Action) {
    "deploy" {
        Write-Info "Deploying dashboard (wait 2-3 minutes for setup)..."
        ssh -i $KeyPath "$Username@$PublicIP" @"
sudo bash << 'EOF'
apt-get update && apt-get upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
usermod -aG docker ubuntu
cd /home/ubuntu
git clone https://github.com/GaneshPeddireddy18/GCP---V-1.git gcp-dashboard
cd gcp-dashboard
docker build -t gcp-dashboard:latest .
docker run -d --name gcp-dashboard -p 8501:8501 --restart always gcp-dashboard:latest
echo "✅ Dashboard deployed!"
echo "Access at: http://$PublicIP`:8501"
EOF
"@
        Write-Success "Dashboard is deploying"
        Write-Info "Wait 2-3 minutes, then visit: http://$PublicIP`:8501"
    }
    
    "logs" {
        Write-Info "Fetching logs..."
        ssh -i $KeyPath "$Username@$PublicIP" "docker logs -f gcp-dashboard"
    }
    
    "restart" {
        Write-Info "Restarting dashboard..."
        ssh -i $KeyPath "$Username@$PublicIP" "docker restart gcp-dashboard"
        Write-Success "Dashboard restarted"
    }
    
    "status" {
        Write-Info "Checking status..."
        ssh -i $KeyPath "$Username@$PublicIP" "docker ps"
    }
    
    "update" {
        Write-Info "Updating dashboard code..."
        ssh -i $KeyPath "$Username@$PublicIP" @"
cd ~/gcp-dashboard
git pull origin master
docker build -t gcp-dashboard:latest .
docker restart gcp-dashboard
echo "✅ Update complete!"
"@
        Write-Success "Dashboard updated"
    }
    
    "shell" {
        Write-Info "Opening SSH shell (type 'exit' to disconnect)..."
        ssh -i $KeyPath "$Username@$PublicIP"
    }
}

Write-Host ""
