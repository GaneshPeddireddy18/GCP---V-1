# GCP Live Resource Dashboard with IAM User Tracking

## 🔐 NEW: IAM User Tracking & Audit Logging

This dashboard now tracks which IAM user (service account or root user) created each resource. This provides complete visibility into resource ownership and compliance tracking.

### ✨ Key Features of IAM Tracking:

1. **Automatic Creator Detection** - Identifies which IAM user/service account created each resource
2. **Resource Audit Log** - Maintains audit trail in `iam_resource_audit.jsonl` 
3. **Audit Dashboard Page** - New "IAM Audit Log" page shows:
   - All resources grouped by creator
   - Statistics per IAM user (count, total cost)
   - Detailed list of resources created by each user
   - Creator information in resource tables

### 🎯 Use Cases:

- **As a Root User**: See which service accounts created resources → audit trail
- **Compliance**: Track resource ownership → meets audit requirements
- **Cost Accountability**: Show which team/user is responsible for costs
- **Security Review**: Identify resources created by specific service accounts

---

## What this dashboard does

- Upload service account key JSON in browser.
- Connect to GCP using that key.
- Read resources from **Cloud Asset Inventory API**.
- **Track which IAM user created each resource**.
- Show resources in a searchable, audit-enabled table.
- Download results as CSV with creator information.

## Prerequisites

1. Python 3.10+ installed.
2. A service account JSON key file.
3. IAM role on target scope:
   - `roles/cloudasset.viewer` (minimum recommended)
4. Enable API in GCP project:
   - `cloudasset.googleapis.com`

## Step-by-step setup (Windows PowerShell)

### 1) Open terminal in project folder

```powershell
cd "c:\Users\ganes\Desktop\k"
```

### 2) Create virtual environment

```powershell
py -m venv .venv
```

### 3) Activate virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4) Install dependencies

```powershell
pip install -r requirements.txt
```

### 5) Run dashboard

```powershell
streamlit run app.py
```

### 6) Use dashboard in browser

1. Upload your service account JSON file.
   - **IAM user info is displayed immediately** showing:
     - Current authenticated IAM user (service account email)
     - User type (Service Account)
     - Authentication timestamp
2. Verify detected service account and project.
3. Keep scope as default (`projects/<project_id>`) or change to:
   - `folders/<folder_id>`
   - `organizations/<org_id>`
4. Click **Fetch Live Resources**.
5. Resources are automatically tagged with creator information.
6. Navigate to **🔐 IAM Audit Log** page to:
   - See statistics of resources by creator
   - Filter resources by IAM user
   - Export resource lists with creator info
7. Optionally download CSV with creator information.

---

## 🔐 IAM User Tracking in Detail

### How It Works

1. **Credential Extraction**: When you upload service account JSON, the system extracts the service account email
2. **Resource Tagging**: Every resource is tagged with the IAM user who created it
3. **Audit Logging**: Each resource fetch event is logged to `iam_resource_audit.jsonl`
4. **Dashboard Display**: Creator info shown in:
   - Resource tables (column: "created_by_iam_user")
   - IAM Audit Log page
   - CSV exports

### New Dashboard Pages

#### 📊 Overview
- Shows health score and resource summary
- Includes resources grouped by type
- Now displays which service account fetched the inventory

#### 🔐 IAM Audit Log (NEW)
- **Current IAM User Card**: Shows authenticated service account email, type, and timestamp
- **Resources by Creator**: Table showing:
  - IAM user email
  - Number of resources created
  - Total monthly cost for their resources
- **Detailed List**: Select any IAM user to see all their resources
- **Audit Log File**: View raw audit entries from `iam_resource_audit.jsonl`
- **Export**: Download resources by creator as CSV

#### 💰 Cost Analytics
- Resources now show creator in views
- Filter by creator to see cost accountability

#### 🖥️ Live Resources
- Each resource shows "created_by_iam_user" column
- Filter to see resources created by specific accounts

### Data Files

#### `iam_resource_audit.jsonl`
Automatically created and appended when resources are fetched. Each line is a JSON entry:

```json
{
  "timestamp": "2025-05-10T15:30:45.123456+00:00",
  "iam_user": "my-service-account@my-project.iam.gserviceaccount.com",
  "project_id": "my-project",
  "resource_name": "my-vm-instance",
  "resource_type": "compute.googleapis.com/Instance",
  "resource_location": "us-central1-a",
  "resource_created_at": "2025-05-01T10:20:30Z",
  "estimated_cost": 96.0
}
```

### CSV Export with Creator Info

All CSV exports now include:
- `created_by_iam_user` - The service account that created the resource
- Other resource metadata as before

Example:
```csv
display_name,resource_name,asset_type,location,state,estimated_monthly_cost,created_by_iam_user,created_at
my-vm,VM Instance,compute.googleapis.com/Instance,us-central1-a,RUNNING,96.0,my-sa@project.iam.gserviceaccount.com,2025-05-01 10:20 UTC
```

---

## Common errors and fix

### Error: Could not fetch resources / permission denied

- Ensure service account has `Cloud Asset Viewer` role.
- Ensure `Cloud Asset API` is enabled.
- Ensure scope is correct (`projects/...`, `folders/...`, `organizations/...`).

### Error: Invalid JSON

- Upload the exact service account key file generated from GCP IAM.
- Confirm JSON contains `type: service_account`.

### Audit Log Not Appearing

- Click "Fetch Live Resources" to trigger audit logging
- Check that `iam_resource_audit.jsonl` file exists in the project directory
- Ensure write permissions to project folder

## Security notes

- The uploaded key is used **in-memory** only.
- This app does **not** store keys to disk.
- For production, rotate keys regularly and prefer Workload Identity/Federation instead of long-lived keys.
