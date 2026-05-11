# Multi-Cloud Dashboard Guide

## 🌐 Overview

Your dashboard now supports **both GCP and AWS**! Switch between clouds, upload credentials, and see live resources from either platform.

---

## ✨ Features

### Cloud Provider Selection
- **GCP**: Upload service account JSON file
- **AWS**: Enter an IAM role ARN

### Supported Resources

#### GCP Resources
- Compute Engine VM instances
- Managed Databases (Cloud SQL, Cloud Spanner)
- Networking (VPCs, Firewalls)
- Cloud Storage buckets
- Kubernetes clusters (GKE)
- Cloud KMS keys
- IAM service accounts

#### AWS Resources
- **EC2 Instances** - Virtual machines across all regions
- **RDS Databases** - Managed relational databases
- **S3 Buckets** - Object storage
- **Lambda Functions** - Serverless functions (with runtime & memory info)
- All resources with real-time status and metadata

---

## 🚀 Quick Start

### GCP Setup
1. Open the dashboard at `http://localhost:8501`
2. In the sidebar, select **GCP** from "Select Cloud Provider"
3. Click "Upload service account JSON"
4. Select your GCP service account JSON file
5. Enter the scope (e.g., `projects/YOUR_PROJECT_ID`)
6. Click "Fetch Live Resources"

### AWS Setup
1. Open the dashboard at `http://localhost:8501`
2. In the sidebar, select **AWS** from "Select Cloud Provider"
3. Enter your **AWS IAM Role ARN**
4. Make sure the machine running the dashboard already has AWS credentials available to assume that role
5. Click "Fetch Live Resources"

---

## 🔐 Security Best Practices

### GCP
- Service account stays in memory only during the session
- Session ends when you close the browser
- JSON file is never saved to disk
- Use `roles/cloudasset.viewer` role for read-only access

### AWS
- No access keys are entered in the dashboard UI
- Credentials stay in memory only during the session
- Recommended: run the dashboard on EC2 with an instance profile or other base AWS identity
- That base identity must allow `sts:AssumeRole` on the role ARN you enter

### Recommended AWS Policy (Custom - Most Secure)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeRegions",
        "rds:DescribeDBInstances",
        "s3:ListAllMyBuckets",
        "s3:GetBucketLocation",
        "lambda:ListFunctions"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## 📊 Dashboard Pages

All pages work with both GCP and AWS:

| Page | Purpose |
|------|---------|
| **Overview** | Summary of resources, costs, health score |
| **Cost Analytics** | Cost breakdown by category and service |
| **Live Resources** | Detailed table of all resources |
| **ClockPlus** | Tracks idle resources (disk snapshots, static IPs) |
| **Monitoring** | Real-time metrics for compute instances (GCP only) |
| **IAM Audit Log** | User/service account information |
| **AI Assistant** | Ask questions about resources and costs |

---

## 🎛️ Filtering & Refresh

### Query Filtering
- **GCP**: Use `state:RUNNING` or `location:us-central1` style queries
- **AWS**: Filter by resource name (case-insensitive substring match)

### Auto-Refresh
- Disabled by default (recommended)
- Interval: 30s to 300s (30 seconds to 5 minutes)
- Lower = more frequent API calls (higher cost)

### Refresh Buttons
- **Refresh Data** - Full re-fetch of all resources
- **Quick Refresh** - Update timestamps only (AWS shows latest state)

---

## 🔄 Switching Between Clouds

1. In the sidebar, click the other cloud provider button
2. Dashboard resets to show "Disconnected" state
3. Upload new credentials for the selected cloud
4. Click "Fetch Live Resources"

**Note:** Your GCP and AWS resources are kept separate in different session states.

---

## ⚙️ Configuration Details

### GCP Configuration
```
Scope: projects/YOUR_PROJECT_ID
       (or: folders/FOLDER_ID, organizations/ORG_ID)
       
Query: state:RUNNING
       location:us-central1
       resourceType:compute.googleapis.com/Instance
```

### AWS Configuration
```
Regions: Automatically fetches from all AWS regions
         (US, EU, APAC, etc.)
         
Query: Filters by resource name (e.g., "prod", "staging")
```

---

## 📈 Resource Display

Both clouds show:
- **Name**: Resource identifier
- **Type**: Service type (EC2, RDS, S3, etc.)
- **State**: running, stopped, active, etc.
- **Location**: Region (AWS) or Zone (GCP)
- **Timestamps**: Created and updated times

---

## 🐛 Troubleshooting

### GCP Issues
- **"Upload a service account JSON..."** → Upload your JSON file
- **"No resources found"** → Check scope and query filter
- **"Permission denied"** → Ensure service account has `roles/cloudasset.viewer`

### AWS Issues
- **"No base AWS credentials found"** → Run the dashboard on EC2 with an instance profile or configure AWS CLI credentials locally
- **"Failed to assume role"** → Verify the role ARN and trust policy allow the dashboard identity to assume it
- **"AccessDenied"** → Ensure IAM user has read-only policies
- **"No resources found"** → Might mean genuinely no resources in your account
- **"Region not available"** → Some regions may be restricted; check AWS account settings

### Both Clouds
- **Slow responses** → Disable auto-refresh or increase refresh interval
- **Timeout errors** → Network issue; try fetching again
- **Memory issues** → Reduce max_rows slider if fetching too many resources

---

## 📝 API Usage & Costs

### GCP
- Uses **Cloud Asset Inventory API** (free quota: 1,000 reads/day)
- Per read after quota: ~$0.00001
- List operations are free

### AWS
- Uses **EC2, RDS, S3, Lambda APIs** (free tier available)
- No additional charges for API calls (included in AWS free tier)
- Higher usage may increase data transfer costs

---

## 🔗 Dependencies

New AWS library added:
- `boto3==1.35.0` - AWS SDK for Python

All other dependencies remain the same:
- `streamlit==1.44.1`
- `pandas==2.2.3`
- `plotly==5.24.0`
- `google-auth==2.39.0`
- `google-cloud-asset==3.30.0`

---

## ✅ Next Steps

1. **Test GCP**: Upload your GCP service account and explore resources
2. **Test AWS**: Enter AWS credentials and fetch AWS resources
3. **Configure Filtering**: Try different queries for each cloud
4. **Set Up Alerts**: Use "Quick Refresh" to monitor resources in real-time
5. **Deploy to EC2**: Follow the EC2_QUICK_START.md guide to deploy this dashboard

---

## 📚 Additional Resources

- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [GCP Service Account Security](https://cloud.google.com/iam/docs/service-accounts)
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Boto3 Documentation](https://boto3.amazonaws.com/)

---

**Version**: Multi-Cloud Edition (GCP + AWS)  
**Last Updated**: 2026-05-11  
**Status**: Production Ready ✅
