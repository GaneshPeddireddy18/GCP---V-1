# AWS EC2 Quick Start (5 Minutes)

## 🎯 Simplest EC2 Deployment

### Step 1: Launch EC2 Instance (3 min)

1. Go to **AWS Console** → **EC2** → **Launch Instances**
2. Choose **Ubuntu 22.04 LTS**
3. Instance Type: **t3.small** (~$7/month)
4. Storage: **20 GB** (default)
5. **Advanced Details** section:
   - Paste content of `ec2-user-data.sh` into **User data** field
6. **Security Group:**
   - Name: `gcp-dashboard`
   - Add rule: Custom TCP **8501** from `0.0.0.0/0`
7. **Key Pair:** Create or use existing
8. Click **Launch**

### Step 2: Wait (2 min)

Instance is auto-setting up Docker and running dashboard.

Check status: **EC2 Console** → **Instances** → Your instance

Wait for "Running" status ✅

### Step 3: Access Dashboard (Instant)

1. Copy **Public IPv4 address** from instance details
2. Visit: `http://PUBLIC_IP:8501`
3. ✅ Dashboard is live!

---

## 📝 That's it!

**Total time: 5 minutes**

---

## 📋 One-Time Setup

If you want to do it manually instead of auto-setup:

```bash
# SSH into instance first
ssh -i your-key.pem ubuntu@YOUR_PUBLIC_IP

# Then run these commands:
sudo apt-get update && sudo apt-get upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu
cd ~
git clone https://github.com/GaneshPeddireddy18/GCP---V-1.git gcp-dashboard
cd gcp-dashboard
docker build -t gcp-dashboard:latest .
docker run -d --name gcp-dashboard -p 8501:8501 --restart always gcp-dashboard:latest
```

---

## 🔄 Update Dashboard Later

```bash
ssh -i your-key.pem ubuntu@YOUR_PUBLIC_IP
cd ~/gcp-dashboard
git pull
docker build -t gcp-dashboard:latest .
docker restart gcp-dashboard
```

---

## 💰 Cost

- **Instance:** ~$7-8/month (t3.small)
- **Storage:** ~$1/month
- **Data transfer:** ~$0.50/month
- **Total:** ~$8-10/month

---

## ✅ Quick Checklist

- [ ] EC2 instance launched
- [ ] Public IP visible
- [ ] Dashboard accessible
- [ ] Can upload GCP key.json

---

## 🆘 Help

**Dashboard not showing?**
1. Check instance is "Running"
2. Check security group allows port 8501
3. Try `http://IP:8501` (include port!)
4. SSH and check: `docker ps`

---

**For detailed guide:** See [EC2_DEPLOYMENT_GUIDE.md](EC2_DEPLOYMENT_GUIDE.md)

**Status:** ✅ Ready to deploy
