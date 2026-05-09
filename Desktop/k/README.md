# GCP Live Resource Dashboard (Service Account JSON Upload)

This dashboard lets any GCP user upload a **service account JSON key** and view **live cloud resources** in one place.

## What this dashboard does

- Upload service account key JSON in browser.
- Connect to GCP using that key.
- Read resources from **Cloud Asset Inventory API**.
- Show resources in a searchable table.
- Download results as CSV.

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
2. Verify detected service account and project.
3. Keep scope as default (`projects/<project_id>`) or change to:
   - `folders/<folder_id>`
   - `organizations/<org_id>`
4. Click **Fetch Live Resources**.
5. Optionally download CSV.

## Common errors and fix

### Error: Could not fetch resources / permission denied

- Ensure service account has `Cloud Asset Viewer` role.
- Ensure `Cloud Asset API` is enabled.
- Ensure scope is correct (`projects/...`, `folders/...`, `organizations/...`).

### Error: Invalid JSON

- Upload the exact service account key file generated from GCP IAM.
- Confirm JSON contains `type: service_account`.

## Security notes

- The uploaded key is used **in-memory** only.
- This app does **not** store keys to disk.
- For production, rotate keys regularly and prefer Workload Identity/Federation instead of long-lived keys.
