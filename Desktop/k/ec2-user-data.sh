#!/bin/bash

# AWS EC2 User Data Script - Auto-deploys GCP Dashboard
# This script runs when EC2 instance starts

set -e

echo "Starting EC2 setup for GCP Dashboard..."

# Update system
apt-get update
apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Add ubuntu user to docker group
usermod -aG docker ubuntu

# Clone your repository (adjust URL)
cd /home/ubuntu
git clone https://github.com/GaneshPeddireddy18/GCP---V-1.git gcp-dashboard
cd gcp-dashboard

# Build Docker image
docker build -t gcp-dashboard:latest .

# Run the container
docker run -d \
  --name gcp-dashboard \
  -p 8501:8501 \
  --restart always \
  gcp-dashboard:latest

echo "✅ GCP Dashboard is running!"
echo "Access it at: http://$(hostname -I | awk '{print $1}'):8501"

# Log status
docker ps | grep gcp-dashboard
