# AWS EC2 Deployment Guide

## ✅ EC2 vs ECS - Why Choose EC2?

| Feature | EC2 | ECS |
|---------|-----|-----|
| **Setup Complexity** | ⭐ Easy | ⭐⭐⭐ Complex |
| **Cost** | $8-15/month | $40-60/month |
| **Control** | ✅ Full | ⚠️ Limited |
| **Learning Curve** | Easy | Medium |
| **Auto-scaling** | Manual | Automatic |
| **Maintenance** | Manual patching | Managed |

**EC2 is best if:** You want simple, cheap, and full control
**ECS is best if:** You need automatic scaling and managed infrastructure

---

## 🚀 Deploy to EC2 in 5 Minutes

### Step 1: Launch EC2 Instance

**Via AWS Console:**
1. Go to **EC2 Dashboard** → **Launch Instances**
2. Choose **Ubuntu Server 22.04 LTS** (free tier eligible)
3. Instance Type: **t2.micro** (free) or **t3.small** ($7-8/month)
4. Configure Storage: **20 GB** (default)
5. Security Group: Create new
   - Name: `gcp-dashboard-sg`
   - Add Rule: Custom TCP 8501 from `0.0.0.0/0` (anywhere)
   - Add Rule: SSH 22 from `0.0.0.0/0` (for access)
6. Review and **Launch**
7. Create/Select key pair and download `.pem` file

---

### Step 2: Connect to Instance

**From PowerShell:**

```powershell
# First, set permissions on your .pem file
$keyPath = "C:\path\to\your\key.pem"
icacls $keyPath /inheritance:r /grant:r "$($env:username):(f)"

# SSH into the instance
$publicIP = "your-instance-public-ip"
ssh -i $keyPath ubuntu@$publicIP
```

**Or use AWS Systems Manager:**
- Go to EC2 Console → Instance
- Click "Connect" tab → "EC2 Instance Connect"
- Browser-based terminal opens

---

### Step 3: Prepare Instance (Choose One)

#### Option A: Automated Setup (Recommended)

**When launching instance in Step 1:**
1. In **Advanced Details** section
2. Paste the content of `ec2-user-data.sh` into **User Data** box
3. Launch instance
4. Wait 2-3 minutes
5. Dashboard is ready!

#### Option B: Manual Setup

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker ubuntu

# Clone repository
cd ~
git clone https://github.com/GaneshPeddireddy18/GCP---V-1.git gcp-dashboard
cd gcp-dashboard

# Build image
docker build -t gcp-dashboard:latest .

# Run container
docker run -d \
  --name gcp-dashboard \
  -p 8501:8501 \
  --restart always \
  gcp-dashboard:latest

# Check status
docker ps
```

---

### Step 4: Access Dashboard

```
http://YOUR_PUBLIC_IP:8501
```

**Find public IP:**
- AWS Console → EC2 → Instances
- Look for "Public IPv4 address" column

---

## 📊 Cost Comparison

| Instance Type | Monthly Cost | Performance |
|---------------|------------|------------|
| **t2.micro** | Free tier (12 months) | Slow, bursty |
| **t2.small** | ~$8-10 | Better |
| **t3.small** | ~$7-8 | Best value |
| **t3.medium** | ~$14 | Good for prod |

**Recommended:** `t3.small` (~$7-8/month)

---

## 🔐 Security Best Practices

### 1. Restrict SSH Access

```bash
# Only allow SSH from your IP
# In Security Group:
# SSH (22): Your-IP/32  (not 0.0.0.0/0)
```

### 2. Store GCP Credentials Safely

**Option A: AWS Secrets Manager**
```bash
aws secretsmanager create-secret \
  --name gcp-service-account \
  --secret-string file://path/to/key.json
```

**Option B: SSM Parameter Store**
```bash
aws ssm put-parameter \
  --name /gcp-dashboard/service-account \
  --value file://path/to/key.json \
  --type SecureString
```

**Option C: Upload to EC2 Directly**
```powershell
# From your machine
scp -i $keyPath ./gcp-key.json ubuntu@$publicIP:~/gcp-dashboard/
```

### 3. Enable EC2 IMDSv2

In instance details, ensure IMDSv2 is enabled (default in new instances).

---

## 🛠️ Manage Your Dashboard

### View Logs

```bash
# Real-time logs
docker logs -f gcp-dashboard

# Last 100 lines
docker logs --tail 100 gcp-dashboard
```

### Restart Dashboard

```bash
# If something goes wrong
docker restart gcp-dashboard

# Full restart
docker stop gcp-dashboard
docker rm gcp-dashboard
docker run -d \
  --name gcp-dashboard \
  -p 8501:8501 \
  --restart always \
  gcp-dashboard:latest
```

### Update Dashboard

```bash
# Pull latest code
cd ~/gcp-dashboard
git pull origin master

# Rebuild image
docker build -t gcp-dashboard:latest .

# Stop old container
docker stop gcp-dashboard
docker rm gcp-dashboard

# Run new version
docker run -d \
  --name gcp-dashboard \
  -p 8501:8501 \
  --restart always \
  gcp-dashboard:latest
```

### SSH Back Into Instance

```powershell
# From your local machine
ssh -i key.pem ubuntu@PUBLIC_IP

# View dashboard status
docker ps
docker logs gcp-dashboard
```

---

## 📈 Performance Tuning

### Increase Instance Size

If dashboard is slow:

```bash
# Stop container
docker stop gcp-dashboard

# Remove container
docker rm gcp-dashboard

# Run with more memory
docker run -d \
  --name gcp-dashboard \
  -p 8501:8501 \
  --memory="2g" \
  --restart always \
  gcp-dashboard:latest
```

### Or Upgrade EC2 Instance

1. Stop instance
2. Change instance type to `t3.medium`
3. Start instance

---

## 🧮 Instance Type Selection

**Free Tier (12 months):**
- `t2.micro` - 1 vCPU, 1GB RAM
- For testing only
- May be slow

**Budget ($5-10/month):**
- `t3.small` - 2 vCPUs, 2GB RAM
- Good for light usage
- Recommended

**Production ($15-30/month):**
- `t3.medium` - 2 vCPUs, 4GB RAM
- For production
- Handles spikes well

**High Performance ($50+/month):**
- `m5.large` - 2 vCPUs, 8GB RAM
- For high traffic

---

## 🔄 Auto-Start on Reboot

Container will auto-restart because of `--restart always` flag in run command.

To verify:
```bash
docker inspect gcp-dashboard | grep -i restartpolicy
```

---

## 💾 Backup Your Data

### Backup Dashboard Files

```bash
# Create backup
tar -czf ~/dashboard-backup.tar.gz ~/gcp-dashboard/

# Download to local machine
# From PowerShell:
scp -i key.pem ubuntu@PUBLIC_IP:~/dashboard-backup.tar.gz ./
```

### Backup EC2 Instance

1. Go to EC2 Console
2. Right-click instance → **Image and templates** → **Create image**
3. Creates snapshot you can use to launch new instances

---

## 🆘 Troubleshooting

**Dashboard not accessible?**
```bash
# Check if running
docker ps

# Check logs
docker logs gcp-dashboard

# Check firewall rules
sudo ufw status
```

**Port 8501 blocked?**
```bash
# Check if port is listening
sudo netstat -tlnp | grep 8501

# Or
sudo lsof -i :8501
```

**Out of memory?**
```bash
# Check memory
free -h

# Check Docker memory usage
docker stats

# Increase EC2 instance size
```

**Container keeps crashing?**
```bash
# View error logs
docker logs gcp-dashboard

# Run without daemon mode to see errors
docker run -i -t gcp-dashboard:latest

# Press Ctrl+C to stop
```

---

## ✅ Deployment Checklist

- [ ] EC2 instance launched
- [ ] Security group configured (port 8501 open)
- [ ] Docker installed
- [ ] Repository cloned
- [ ] Image built successfully
- [ ] Container running
- [ ] Dashboard accessible at public IP
- [ ] GCP credentials securely stored
- [ ] Auto-restart enabled
- [ ] Backups configured

---

## 📝 Monthly Maintenance

### Weekly
- Check logs: `docker logs gcp-dashboard`
- Monitor CPU/memory: `docker stats`

### Monthly
- Update base image: `apt-get update && apt-get upgrade`
- Rebuild Docker image
- Pull latest code: `git pull`

### Quarterly
- Security patches
- Backup instance

---

## 🚀 Next Steps

1. **Choose instance type** (t3.small recommended)
2. **Launch EC2 instance**
3. **Use user data script for auto-setup** (easiest)
4. **Access dashboard at public IP:8501**
5. **Set up monitoring** (CloudWatch)

---

## 📚 Related Guides

- **Performance Optimization:** [PERFORMANCE_OPTIMIZATION.md](PERFORMANCE_OPTIMIZATION.md)
- **Docker Reference:** [Dockerfile](Dockerfile)
- **All Deployment Options:** [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md)

---

## 💡 Quick Commands Reference

```bash
# SSH into instance
ssh -i key.pem ubuntu@PUBLIC_IP

# View dashboard status
docker ps

# View logs
docker logs -f gcp-dashboard

# Restart
docker restart gcp-dashboard

# Stop all
docker stop gcp-dashboard

# Update code
git pull && docker build -t gcp-dashboard:latest . && docker restart gcp-dashboard

# Check resource usage
docker stats

# See all running containers
docker ps -a

# Remove stopped containers
docker prune
```

---

**Status:** ✅ Ready for EC2 Deployment
**Last Updated:** May 11, 2026
**Estimated Setup Time:** 5-10 minutes
