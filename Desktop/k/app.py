from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from gcp_asset_service import (
    GCPDashboardError,
    fetch_compute_instance_details,
    fetch_live_resources,
    fetch_monitoring_time_series,
    filter_likely_running,
    filter_resources_by_nl_query,
    flatten_time_series,
    load_credentials_from_json,
    get_iam_user_info,
    parse_compute_instance_resource,
    summarize_costs,
)
from aws_service import (
    AWSServiceError,
    assume_role_session,
    fetch_all_resources,
    get_aws_account_info,
)
from cache_manager import CacheManager, MetadataCache


CATEGORY_MAP: dict[str, list[str]] = {
    "Compute": ["compute.googleapis.com/Instance", "compute.googleapis.com/Disk", "compute.googleapis.com/ForwardingRule", "aws.ec2.Instance", "aws.lambda.Function"],
    "Databases": ["sqladmin.googleapis.com/Instance", "spanner.googleapis.com/Instance", "aws.rds.Database"],
    "Networking": ["compute.googleapis.com/Network", "compute.googleapis.com/Firewall", "compute.googleapis.com/ForwardingRule"],
    "Storage": ["storage.googleapis.com/Bucket", "aws.s3.Bucket"],
    "Kubernetes": ["container.googleapis.com/Cluster"],
    "Security": ["cloudkms.googleapis.com/KeyRing", "cloudkms.googleapis.com/CryptoKey"],
    "Billing": ["billingbudgets.googleapis.com/Budget"],
    "IAM": ["iam.googleapis.com/ServiceAccount", "iam.googleapis.com/ServiceAccountKey", "iam.googleapis.com/Role"],
}

NAVIGATION_PAGES: dict[str, str] = {
    "overview": "📊 Overview",
    "cost": "💰 Cost Analytics",
    "live": "🖥️ Live Resources",
    "clockpluse": "🕒 ClockPlus",
    "monitoring": "📈 Monitoring",
    "iam_audit": "🔐 IAM Audit Log",
    "assistant": "🤖 AI Assistant",
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "Compute": "Virtual machines, disks, and forwarding rules that power workloads and traffic entry points.",
    "Databases": "Managed database services for structured application data and transactions.",
    "Networking": "VPCs, firewalls, and load balancers that control connectivity and exposure.",
    "Storage": "Cloud buckets that store files, backups, and application assets.",
    "Kubernetes": "GKE clusters and node pools for container orchestration and scaling.",
    "Security": "KMS resources used for encryption keys and protection controls.",
    "Billing": "Budgets and cost guardrails that help track spend and alerts.",
    "IAM": "Service accounts, keys, and roles that define access and permissions.",
}

SERVICE_EXPLANATIONS: dict[str, str] = {
    "Compute": "Virtual machines used to run applications and workloads in GCP.",
    "Databases": "Managed relational databases that reduce administration overhead.",
    "Networking": "Connectivity controls such as VPCs, firewalls, and load balancers.",
    "Storage": "Object storage for files, backups, and application data.",
    "Kubernetes": "Container orchestration for deploying and scaling services.",
    "Security": "Encryption and key management resources that protect data.",
    "Billing": "Budget resources that monitor and control cloud spend.",
    "IAM": "Identity and access resources that govern permissions.",
}

DID_YOU_KNOW_TIPS = [
    "Public buckets are one of the most common cloud security risks.",
    "Stopped VMs can still keep attached disks billed.",
    "Auto-scaling helps reduce compute costs during quiet periods.",
    "Unused static IPs may continue to generate charges.",
    "Lifecycle rules in Cloud Storage can reduce long-term spend.",
]

AI_QUICK_PROMPTS = [
    "Show expensive services",
    "Find idle resources",
    "Check security risks",
    "Analyze compute usage",
]


@st.cache_data(show_spinner=False)
def build_dashboard_context(resources: list[dict[str, object]]) -> dict[str, object]:
    df = pd.DataFrame(resources) if resources else pd.DataFrame()
    selected_asset_types = {asset_type for values in CATEGORY_MAP.values() for asset_type in values}
    filtered_resources = [r for r in resources if str(r.get("asset_type") or "") in selected_asset_types]
    df_filtered = pd.DataFrame(filtered_resources) if filtered_resources else pd.DataFrame()

    category_rows = []
    for category_name, asset_types in CATEGORY_MAP.items():
        category_frame = df_filtered[df_filtered["asset_type"].isin(asset_types)] if not df_filtered.empty else pd.DataFrame()
        category_rows.append(
            {
                "category": category_name,
                "count": len(category_frame),
                "estimated_monthly_cost": float(category_frame["estimated_monthly_cost"].sum()) if not category_frame.empty else 0.0,
            }
        )

    category_summary_df = pd.DataFrame(category_rows)
    cost_summary = summarize_costs(filtered_resources)
    top_service = "None"
    health_score = 0
    if not df_filtered.empty:
        top_service_series = df_filtered.groupby("asset_class")["estimated_monthly_cost"].sum().sort_values(ascending=False)
        if not top_service_series.empty:
            top_service = str(top_service_series.index[0])
        active_ratio = len(filter_likely_running(filtered_resources)) / max(len(filtered_resources), 1)
        known_owner_ratio = 1.0 - (df_filtered["owner_hint"].eq("Unknown").mean() if not df_filtered.empty else 1.0)
        health_score = int(round(min(100, max(0, (active_ratio * 55) + (known_owner_ratio * 45)))))

    return {
        "df": df,
        "df_filtered": df_filtered,
        "filtered_resources": filtered_resources,
        "category_summary_df": category_summary_df,
        "cost_summary": cost_summary,
        "top_service": top_service,
        "unique_projects": df_filtered["project"].nunique() if not df_filtered.empty else 0,
        "likely_running_count": len(filter_likely_running(filtered_resources)),
        "health_score": health_score,
    }


def format_elapsed_time(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "Not refreshed yet"
    delta_seconds = max(int((datetime.now(timezone.utc) - timestamp).total_seconds()), 0)
    if delta_seconds < 60:
        return f"{delta_seconds}s ago"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m ago"
    return f"{delta_seconds // 3600}h ago"


def build_resource_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a stable resource table with owner and timestamp columns."""
    if df.empty:
        return df

    table = df.copy()
    if "display_name" not in table:
        table["display_name"] = ""
    if "resource_name" not in table:
        table["resource_name"] = "Other Resource"
    if "asset_type" not in table:
        table["asset_type"] = ""
    if "location" not in table:
        table["location"] = ""
    if "state" not in table:
        table["state"] = ""
    if "estimated_monthly_cost" not in table:
        table["estimated_monthly_cost"] = 0.0
    if "usage_status" not in table:
        table["usage_status"] = "Unknown"
    if "attachment_target" not in table:
        table["attachment_target"] = "Unattached"
    if "owner_hint" not in table:
        table["owner_hint"] = "Unknown"
    if "created_by_iam_user" not in table:
        table["created_by_iam_user"] = "Unknown"
    if "updated_at" not in table:
        table["updated_at"] = ""
    if "created_at" not in table:
        table["created_at"] = ""
    if "monitoring" not in table:
        table["monitoring"] = "-"

    table["owner_hint"] = table["owner_hint"].fillna("Unknown")
    table["usage_status"] = table["usage_status"].fillna("Unknown")
    table["attachment_target"] = table["attachment_target"].fillna("Unattached")
    table["created_by_iam_user"] = table["created_by_iam_user"].fillna("Unknown")
    table["resource_name"] = table["resource_name"].replace("", pd.NA).fillna("Other Resource")
    table["updated_at"] = table["updated_at"].replace("", pd.NA).fillna("Not available")
    table["created_at"] = table["created_at"].replace("", pd.NA).fillna("Not available")
    table["monitoring"] = table.apply(
        lambda row: "Show"
        if str(row.get("asset_type") or "") == "compute.googleapis.com/Instance"
        and str(row.get("state") or "").upper() == "RUNNING"
        else "-",
        axis=1,
    )

    return table[
        [
            "display_name",
            "resource_name",
            "asset_type",
            "location",
            "state",
            "monitoring",
            "estimated_monthly_cost",
            "usage_status",
            "attachment_target",
            "owner_hint",
            "created_by_iam_user",
            "updated_at",
            "created_at",
        ]
    ]


def build_clockplus_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    tracked_types = {
        "compute.googleapis.com/Snapshot",
        "compute.googleapis.com/Disk",
        "compute.googleapis.com/Address",
    }
    frame = df[df["asset_type"].isin(tracked_types)].copy()
    if frame.empty:
        return frame

    for column in ["display_name", "resource_name", "asset_type", "location", "estimated_monthly_cost", "usage_status", "created_by_iam_user", "created_at", "updated_at"]:
        if column not in frame:
            frame[column] = "" if column not in {"estimated_monthly_cost"} else 0.0

    frame["clockplus_status"] = frame["usage_status"].replace({"Unknown": "Not connected"})
    return frame[
        [
            "display_name",
            "resource_name",
            "asset_type",
            "location",
            "clockplus_status",
            "attachment_target",
            "estimated_monthly_cost",
            "created_by_iam_user",
            "created_at",
            "updated_at",
        ]
    ]


def build_clockplus_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "asset_type" not in df or "usage_status" not in df:
        return pd.DataFrame()

    rows = []
    type_labels = {
        "compute.googleapis.com/Snapshot": "Snapshots",
        "compute.googleapis.com/Disk": "Persistent Disks",
        "compute.googleapis.com/Address": "Static External IPs",
    }
    for asset_type, label in type_labels.items():
        subset = df[df["asset_type"] == asset_type]
        if subset.empty:
            continue
        connected = int((subset["usage_status"] == "Connected").sum())
        not_connected = int((subset["usage_status"] == "Not connected").sum())
        rows.append(
            {
                "resource_group": label,
                "total": len(subset),
                "connected": connected,
                "not_connected": not_connected,
                "estimated_monthly_cost": float(subset["estimated_monthly_cost"].sum()),
            }
        )

    return pd.DataFrame(rows)


def build_metric_chart_frame(points: list[dict[str, object]], aggregate: str = "mean", scale: float = 1.0) -> pd.DataFrame:
    if not points:
        return pd.DataFrame()

    metric_frame = pd.DataFrame(points)
    if metric_frame.empty or "time" not in metric_frame or "value" not in metric_frame:
        return pd.DataFrame()

    metric_frame["time"] = pd.to_datetime(metric_frame["time"], errors="coerce")
    metric_frame["value"] = pd.to_numeric(metric_frame["value"], errors="coerce")
    metric_frame = metric_frame.dropna(subset=["time", "value"])
    if metric_frame.empty:
        return pd.DataFrame()

    # Cloud Monitoring may return several sub-series with slightly different timestamps.
    # Bucket to 1-minute boundaries before aggregation to avoid zig-zag artifacts.
    metric_frame["time_bucket"] = metric_frame["time"].dt.floor("min")

    if aggregate == "sum":
        grouped = metric_frame.groupby("time_bucket", as_index=False)["value"].sum()
    elif aggregate == "max":
        grouped = metric_frame.groupby("time_bucket", as_index=False)["value"].max()
    else:
        grouped = metric_frame.groupby("time_bucket", as_index=False)["value"].mean()

    grouped = grouped.rename(columns={"time_bucket": "time"}).sort_values("time")
    grouped["value"] = grouped["value"] * float(scale)
    return grouped


def build_infrastructure_summary(resources: list[dict[str, object]], health_score: int, cost_summary: dict[str, object], likely_running_count: int) -> list[str]:
    if not resources:
        return [
            "No live resources loaded yet.",
            "Upload a service account JSON and fetch inventory to see live guidance.",
        ]

    active_text = f"{likely_running_count} active resources detected."
    cost_text = f"Estimated daily spend: ${float(cost_summary.get('daily_spending', 0.0)):.2f}."
    health_text = (
        "Healthy infrastructure overall."
        if health_score >= 80
        else "Infrastructure is stable, but a few items need attention."
        if health_score >= 60
        else "Several risks or unknowns need review."
    )
    region_count = len({str(row.get('location') or 'Global') for row in resources})
    region_text = f"Resources are distributed across {region_count} region(s)."
    return [active_text, cost_text, health_text, region_text]


def build_cost_insights(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return ["Cost insights will appear after inventory is loaded."]

    insights: list[str] = []
    by_service = df.groupby("asset_class", as_index=False)["estimated_monthly_cost"].sum().sort_values("estimated_monthly_cost", ascending=False)
    if not by_service.empty:
        top_service_row = by_service.iloc[0]
        total_cost = float(df["estimated_monthly_cost"].sum()) or 1.0
        share = round((float(top_service_row["estimated_monthly_cost"]) / total_cost) * 100, 1)
        insights.append(f"{top_service_row['asset_class']} contributes {share}% of estimated cost.")

    top_resource = df.sort_values("estimated_monthly_cost", ascending=False).head(1)
    if not top_resource.empty:
        row = top_resource.iloc[0]
        insights.append(f"Highest-cost resource: {row.get('display_name') or row.get('name')} at ${float(row.get('estimated_monthly_cost') or 0):.2f}/mo.")

    if (df["asset_type"] == "compute.googleapis.com/ForwardingRule").any():
        insights.append("Unused public IP or load balancer resources may add unnecessary networking spend.")

    return insights[:3]


def build_security_recommendations(df: pd.DataFrame) -> list[tuple[str, str]]:
    if df.empty:
        return [("Low", "Security recommendations will appear after inventory is loaded.")]

    recommendations: list[tuple[str, str]] = []
    unknown_owner_count = int(df["owner_hint"].eq("Unknown").sum()) if "owner_hint" in df else 0
    if unknown_owner_count:
        recommendations.append(("Medium", f"{unknown_owner_count} resources do not have a clear owner tag."))

    if (df["asset_type"] == "compute.googleapis.com/Firewall").any():
        recommendations.append(("High", "Review firewall rules for overly broad ingress access."))

    if df["asset_type"].isin(["iam.googleapis.com/ServiceAccountKey", "iam.googleapis.com/Role"]).any():
        recommendations.append(("Medium", "Check service account keys and IAM roles for broad permissions."))

    idle_count = int((~df["state"].isin(["RUNNING"])).sum()) if "state" in df else 0
    if idle_count:
        recommendations.append(("Low", f"{idle_count} resources appear idle or stopped and should be reviewed."))

    return recommendations[:3] or [("Low", "No obvious security issues were detected from the current inventory.")]


def build_performance_guidance(df: pd.DataFrame) -> list[str]:
    if df.empty:
        return ["Performance guidance appears after inventory loads."]

    guidance = [
        "Low CPU usage usually means VMs can be right-sized.",
        "Autoscaling helps absorb spikes without overprovisioning.",
    ]
    if (df["asset_type"] == "storage.googleapis.com/Bucket").any():
        guidance.append("Storage lifecycle rules can reduce long-term bucket costs.")
    if (df["asset_type"] == "compute.googleapis.com/Instance").any():
        guidance.append("Stopped VMs still keep disks billed until they are cleaned up.")
    return guidance[:3]


st.set_page_config(page_title="Multi-Cloud Dashboard", page_icon="☁", layout="wide")

st.title("AI-Powered Multi-Cloud Infrastructure Intelligence Platform")
st.caption("Fast live inventory from GCP & AWS, cost signals, and action views in one place.")

credentials = None
default_project_id = None
service_account_email = None
account_alias = None
uploaded_file = None
file_text = None
scope = "projects/YOUR_PROJECT_ID"
query = ""
only_running = True
max_rows = 2000
auto_refresh_enabled = False
refresh_seconds = 10
page_selected = "overview"

with st.sidebar:
    st.header("Cloud Configuration")
    
    # Cloud Provider Selection
    cloud_provider = st.radio(
        "Select Cloud Provider",
        ["GCP", "AWS"],
        horizontal=True,
    )
    
    st.divider()
    
    if cloud_provider == "GCP":
        st.markdown(
            """
Upload a service account JSON and fetch your live GCP inventory.
            """
        )
        st.info("Secure session: key stays in memory only.")
        
        uploaded_file = st.file_uploader("Upload service account JSON", type=["json"], key="gcp_uploader")

        if uploaded_file:
            try:
                file_text = uploaded_file.getvalue().decode("utf-8")
                credentials, default_project_id, service_account_email = load_credentials_from_json(file_text)
            except UnicodeDecodeError:
                st.error("File is not valid UTF-8 JSON.")
            except GCPDashboardError as exc:
                st.error(str(exc))
            else:
                st.success("Service account key loaded successfully.")

                # Display IAM user information
                iam_user_info = get_iam_user_info(credentials)
                st.info(f"🔐 **IAM User:** {iam_user_info['email']}\n\n**Type:** {iam_user_info['user_type']}")

                st.metric("Service Account", service_account_email or "Unknown")
                st.metric("Default Project", default_project_id or "Unknown")

                default_scope = f"projects/{default_project_id}" if default_project_id else "projects/YOUR_PROJECT_ID"
                scope = st.text_input(
                    "Scope",
                    value=default_scope,
                    help="Use projects/<id>, folders/<id>, or organizations/<id>",
                )

                query = st.text_input(
                    "Optional query filter",
                    value="",
                    help="Example: state:RUNNING OR location:us-central1",
                )
                only_running = True
                st.caption("RUNNING-only mode: READY, STOPPED, and TERMINATED resources are hidden.")
    
    else:  # AWS
        st.markdown(
            """
Provide an IAM role ARN to fetch your live AWS inventory.
            """
        )
        st.info("No access keys in the dashboard. It uses the AWS credentials available on the machine to assume the role ARN you provide.")

        if "aws_session" not in st.session_state:
            st.session_state.aws_session = None
        if "aws_role_arn" not in st.session_state:
            st.session_state.aws_role_arn = ""
        if "aws_profile_name" not in st.session_state:
            st.session_state.aws_profile_name = ""
        if "aws_external_id" not in st.session_state:
            st.session_state.aws_external_id = ""
        if "aws_account_id" not in st.session_state:
            st.session_state.aws_account_id = ""
        if "aws_account_alias" not in st.session_state:
            st.session_state.aws_account_alias = ""

        with st.form("aws_role_form", clear_on_submit=False):
            aws_role_arn_input = st.text_input(
                "AWS IAM Role ARN",
                value=st.session_state.aws_role_arn,
                placeholder="arn:aws:iam::123456789012:role/ReadOnlyDashboardRole",
                help="This role must trust the AWS identity running the dashboard and allow sts:AssumeRole.",
            )
            aws_profile_name_input = st.text_input(
                "AWS Profile Name (optional)",
                value=st.session_state.aws_profile_name,
                placeholder="default",
                help="Use this if your local machine has an AWS CLI profile configured. Leave blank on EC2 instance profile.",
            )
            aws_external_id_input = st.text_input(
                "External ID (optional)",
                value=st.session_state.aws_external_id,
                placeholder="Only if your role trust policy requires it",
                help="Leave blank unless your role requires an ExternalId.",
            )
            aws_submit = st.form_submit_button("Get Data", type="primary")

        if aws_submit:
            st.session_state.aws_role_arn = aws_role_arn_input.strip()
            st.session_state.aws_profile_name = aws_profile_name_input.strip()
            st.session_state.aws_external_id = aws_external_id_input.strip()

            if not st.session_state.aws_role_arn:
                st.error("Enter an AWS IAM Role ARN.")
                st.session_state.aws_session = None
            else:
                try:
                    aws_session_result, account_id = assume_role_session(
                        st.session_state.aws_role_arn,
                        external_id=st.session_state.aws_external_id,
                        profile_name=st.session_state.aws_profile_name,
                    )
                    account_info = get_aws_account_info(aws_session_result)
                    st.session_state.aws_session = aws_session_result
                    st.session_state.aws_account_id = account_id
                    st.session_state.aws_account_alias = account_info.get("account_alias", account_id)
                    st.success("AWS role assumed successfully.")
                except AWSServiceError as exc:
                    st.session_state.aws_session = None
                    st.session_state.aws_account_id = ""
                    st.session_state.aws_account_alias = ""
                    st.error(str(exc))

        if st.session_state.aws_account_id:
            st.metric("Account ID", st.session_state.aws_account_id)
            st.metric("Account Alias", st.session_state.aws_account_alias or st.session_state.aws_account_id)
        elif not st.session_state.aws_role_arn:
            st.warning("Enter an AWS IAM Role ARN and click Get Data, or press Enter inside the form.")

        query = st.text_input(
            "Optional resource name filter",
            value="",
            help="Filter resources by name (case-insensitive)",
        )
        only_running = True
        st.caption("RUNNING-only mode: Filters for running/active resources only. The dashboard uses the machine's AWS identity or AWS profile to assume the ARN, not dashboard keys.")

    st.markdown("### 🚀 Navigation")
    page_key = st.radio(
        "Select Page",
        list(NAVIGATION_PAGES.keys()),
        index=0,
        format_func=lambda key: NAVIGATION_PAGES[key],
        key="nav_page",
        label_visibility="collapsed",
    )
    page_selected = page_key
    
    st.divider()
    
    if (cloud_provider == "GCP" and credentials) or (cloud_provider == "AWS" and st.session_state.get("aws_session") is not None):
        max_rows = st.slider("Maximum rows", min_value=100, max_value=10000, value=2000, step=100)

        st.divider()

        auto_refresh_enabled = st.checkbox("Auto refresh live data", value=False)
        refresh_seconds = st.selectbox("Refresh interval (lower = more network usage)", [30, 60, 120, 300], index=1)

        st.divider()

        if st.button("Fetch Live Resources", type="primary"):
            st.session_state.fetch_clicked = True

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Refresh Data", help="Re-fetch all resource metadata to see updated timestamps"):
                st.session_state.resources = []
                st.session_state.last_refresh_at = None
                st.session_state.fetch_clicked = True
                st.rerun()
        with col2:
            if st.button("⚡ Quick Refresh", help="Instantly refresh timestamps for object changes"):
                st.session_state.fetch_clicked = True
                st.rerun()

        st.divider()

        st.caption("💡 Live updates: Auto-refresh interval can be adjusted. Smart caching reduces network usage. Click Quick Refresh for instant updates after object changes.")
    else:
        if cloud_provider == "GCP":
            st.warning("Upload a service account JSON to unlock live resource fetching and monitoring.")
        else:
            st.warning("Enter an AWS IAM Role ARN to unlock live resource fetching and monitoring.")

if "resources" not in st.session_state:
    st.session_state.resources = []
if "raw_resources" not in st.session_state:
    st.session_state.raw_resources = []
if "assistant_messages" not in st.session_state:
    st.session_state.assistant_messages = [
        {
            "role": "assistant",
            "content": "Ask me things like: show expensive resources, find production servers, or how to reduce cost.",
        }
    ]
if "selected_resource_type" not in st.session_state:
    st.session_state.selected_resource_type = "Overview"
if "fetch_clicked" not in st.session_state:
    st.session_state.fetch_clicked = False
if "selected_live_resource" not in st.session_state:
    st.session_state.selected_live_resource = None
if "last_refresh_at" not in st.session_state:
    st.session_state.last_refresh_at = None
if "connection_state" not in st.session_state:
    st.session_state.connection_state = "Disconnected"
if "cache_manager" not in st.session_state:
    st.session_state.cache_manager = CacheManager(ttl_seconds=600)  # 10-minute cache
if "metadata_cache" not in st.session_state:
    st.session_state.metadata_cache = MetadataCache()

def load_resources() -> list[dict[str, object]]:
    if cloud_provider == "GCP":
        if credentials is None:
            raise GCPDashboardError("Upload a service account JSON before fetching live resources.")
        with st.spinner("Fetching live resources from GCP..."):
            resources = fetch_live_resources(
                credentials=credentials,
                scope=scope.strip(),
                query=query,
                limit=max_rows,
            )
    else:  # AWS
        aws_session_local = st.session_state.get("aws_session")
        if aws_session_local is None:
            if not st.session_state.get("aws_role_arn", "").strip():
                raise AWSServiceError("Enter an AWS IAM Role ARN before fetching live resources.")
            aws_session_local, _ = assume_role_session(
                st.session_state.get("aws_role_arn", ""),
                external_id=st.session_state.get("aws_external_id", ""),
                profile_name=st.session_state.get("aws_profile_name", ""),
            )
        with st.spinner("Fetching live resources from AWS..."):
            resources = fetch_all_resources(aws_session_local)
            
            # Apply query filter if provided (filter by name)
            if query.strip():
                query_lower = query.strip().lower()
                resources = [r for r in resources if query_lower in str(r.get("name", "")).lower()]
            
            # Limit results
            if len(resources) > max_rows:
                resources = resources[:max_rows]

    return resources

def filter_resources_by_cloud(resources: list[dict[str, object]]) -> list[dict[str, object]]:
    """Filter resources based on cloud provider - show running resources only."""
    if cloud_provider == "GCP":
        return filter_likely_running(resources)
    else:  # AWS
        # Filter AWS resources for running/active states
        running_states = {"running", "available", "active"}
        return [
            r for r in resources
            if str(r.get("state", "")).lower() in running_states
        ]


# Handle Fetch Live Resources button from sidebar
if st.session_state.get("fetch_clicked"):
    try:
        resources = load_resources()
    except (GCPDashboardError, AWSServiceError) as exc:
        st.session_state.connection_state = "Error"
        st.error(str(exc))
        st.stop()

    if not resources:
        st.session_state.connection_state = "Connected"
        st.session_state.last_refresh_at = datetime.now(timezone.utc)
        st.warning("No resources found with the current scope/query.")
        st.session_state.fetch_clicked = False
        st.stop()

    st.session_state.raw_resources = resources
    st.session_state.resources = filter_resources_by_cloud(resources)
    st.session_state.fetch_clicked = False
    st.session_state.connection_state = "Connected"
    st.session_state.last_refresh_at = datetime.now(timezone.utc)

if auto_refresh_enabled and st.session_state.raw_resources:
    st_autorefresh(interval=refresh_seconds * 1000, key="cloud_live_refresh")
    try:
        st.session_state.raw_resources = load_resources()
        st.session_state.resources = filter_resources_by_cloud(st.session_state.raw_resources)
        st.session_state.connection_state = "Connected"
        st.session_state.last_refresh_at = datetime.now(timezone.utc)
    except (GCPDashboardError, AWSServiceError) as exc:
        st.session_state.connection_state = "Error"
        st.warning(str(exc))

selected_type = page_selected

resources = filter_resources_by_cloud(st.session_state.raw_resources)

df = pd.DataFrame(resources) if resources else pd.DataFrame()

if not resources and selected_type != "clockpluse":
    st.info("Upload a service account JSON and click 'Fetch Live Resources' in the sidebar to begin.")
else:
    st.markdown(
        """
        <style>
        * {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Oxygen', 'Ubuntu', 'Cantarell', sans-serif;
        }
        .stApp {
            background: linear-gradient(135deg, #030712 0%, #0f172a 50%, #1a1f3a 100%);
            color: #e5e7eb;
            letter-spacing: 0.3px;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0f172a 0%, #0a1428 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 3rem;
            padding-left: 2rem;
            padding-right: 2rem;
        }
        .hero-card {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.85) 0%, rgba(31, 41, 55, 0.72) 100%);
            border: 1px solid rgba(59, 130, 246, 0.25);
            border-radius: 24px;
            padding: 32px 28px;
            box-shadow: 0 24px 48px rgba(0, 0, 0, 0.35), inset 0 1px 1px rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(20px);
            margin-bottom: 24px;
        }
        .feature-card {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.78) 0%, rgba(25, 35, 55, 0.65) 100%);
            border: 1px solid rgba(96, 165, 250, 0.18);
            border-radius: 16px;
            padding: 20px 24px;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            backdrop-filter: blur(16px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .feature-card:hover {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(25, 35, 55, 0.88) 100%);
            border-color: rgba(96, 165, 250, 0.35);
            box-shadow: 0 20px 48px rgba(59, 130, 246, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.1);
            transform: translateY(-2px);
        }
        .section-card {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.75) 0%, rgba(25, 35, 55, 0.62) 100%);
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 20px;
            padding: 24px 28px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.28);
            backdrop-filter: blur(18px);
            margin-top: 20px;
            margin-bottom: 20px;
        }
        .hero-card h2 {
            margin: 0 0 12px 0;
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.5px;
        }
        .section-card h3 {
            margin: 0 0 12px 0;
            font-size: 1.5rem;
            font-weight: 700;
            color: #f1f5f9;
            letter-spacing: -0.3px;
        }
        .hero-badge {
            display: inline-block;
            padding: 8px 14px;
            border-radius: 999px;
            background: linear-gradient(135deg, rgba(96, 165, 250, 0.2) 0%, rgba(139, 92, 246, 0.15) 100%);
            border: 1px solid rgba(96, 165, 250, 0.25);
            color: #bfdbfe;
            font-size: 0.8rem;
            font-weight: 600;
            margin-bottom: 16px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }
        .feature-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-top: 12px;
        }
        .feature-card strong {
            display: block;
            font-size: 1rem;
            margin-bottom: 8px;
            color: #f1f5f9;
            font-weight: 700;
            letter-spacing: -0.2px;
        }
        .feature-card span {
            color: #cbd5e1;
            font-size: 0.9rem;
            font-weight: 500;
            line-height: 1.45;
        }
        div[data-testid="stMetric"] {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.8) 0%, rgba(25, 35, 55, 0.65) 100%);
            border: 1px solid rgba(96, 165, 250, 0.12);
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: 0 16px 32px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(12px);
            transition: all 0.3s ease;
            animation: fadeInScale 0.6s ease-out;
        }
        div[data-testid="stMetric"]:hover {
            border-color: rgba(96, 165, 250, 0.25);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(25, 35, 55, 0.8) 100%);
        }
        div[data-testid="stMetric"] label {
            color: #cbd5e1 !important;
            font-size: 0.85rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.5px !important;
            text-transform: uppercase !important;
        }
        div[data-testid="stMetric"] > div:last-child {
            font-size: 2rem !important;
            font-weight: 700 !important;
            color: #ffffff !important;
        }
        .stButton > button {
            border-radius: 12px;
            border: 1px solid rgba(96, 165, 250, 0.25);
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            color: white;
            font-weight: 700;
            font-size: 0.95rem;
            letter-spacing: 0.5px;
            padding: 10px 20px !important;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #0ea5e9 0%, #06b6d4 100%);
            border-color: rgba(96, 165, 250, 0.5);
            box-shadow: 0 12px 24px rgba(59, 130, 246, 0.3);
            transform: translateY(-1px);
        }
        @keyframes fadeInScale {
            from {
                opacity: 0;
                transform: scale(0.95);
            }
            to {
                opacity: 1;
                transform: scale(1);
            }
        }
        @keyframes slideInFromLeft {
            from {
                opacity: 0;
                transform: translateX(-12px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        @keyframes slideInFromRight {
            from {
                opacity: 0;
                transform: translateX(12px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        @keyframes pulseGlow {
            0%, 100% {
                box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
            }
            50% {
                box-shadow: 0 12px 32px rgba(59, 130, 246, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.12);
            }
        }
        @keyframes buttonPop {
            0% {
                transform: scale(1);
            }
            50% {
                transform: scale(1.05);
            }
            100% {
                transform: scale(1);
            }
        }
        div[data-testid="stMetric"] {
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.8) 0%, rgba(25, 35, 55, 0.65) 100%);
            border: 1px solid rgba(96, 165, 250, 0.12);
            border-radius: 14px;
            padding: 18px 20px;
            box-shadow: 0 16px 32px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(12px);
            transition: all 0.3s ease;
            animation: fadeInScale 0.6s ease-out;
        }
        .feature-card {
            animation: fadeInScale 0.5s ease-out;
        }
        .hero-card {
            animation: fadeInScale 0.7s ease-out;
        }
        .section-card {
            animation: fadeInScale 0.6s ease-out;
        }
        [data-testid="stAlert"] {
            animation: pulseGlow 3s ease-in-out infinite;
        }
        .stButton > button {
            animation: fadeInScale 0.4s ease-out;
        }
        .stChatMessage {
            animation: slideInFromLeft 0.4s ease-out;
        }
        .stChatMessage[data-testid*="assistant"] {
            animation: slideInFromRight 0.4s ease-out;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    dashboard = build_dashboard_context(resources)
    df = dashboard["df"]
    df_filtered = dashboard["df_filtered"]
    filtered_resources_for_overview = dashboard["filtered_resources"]
    category_summary_df = dashboard["category_summary_df"]
    cost_summary = dashboard["cost_summary"]
    top_service = dashboard["top_service"]
    unique_projects = dashboard["unique_projects"]
    likely_running_count = dashboard["likely_running_count"]
    health_score = dashboard["health_score"]

    status_left, status_mid, status_right, status_tail = st.columns([1.15, 1.15, 1.05, 1.05])
    status_left.markdown(
        f'<div class="feature-card"><strong>Cloud Status</strong><span>{"🟢 Connected to GCP" if st.session_state.connection_state == "Connected" else "🟡 Waiting for refresh" if st.session_state.connection_state == "Disconnected" else "🔴 Refresh error"}</span></div>',
        unsafe_allow_html=True,
    )
    status_mid.markdown(
        f'<div class="feature-card"><strong>Live Sync</strong><span>{"Active" if st.session_state.resources else "Ready"} · refresh {refresh_seconds}s</span></div>',
        unsafe_allow_html=True,
    )
    status_right.markdown(
        f'<div class="feature-card"><strong>Last Refresh</strong><span>{format_elapsed_time(st.session_state.last_refresh_at)}</span></div>',
        unsafe_allow_html=True,
    )
    status_tail.markdown(
        f'<div class="feature-card"><strong>Health Score</strong><span>{health_score}/100</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="hero-card">
            <div class="hero-badge">Focused live view</div>
            <h2>See your core GCP services at a glance</h2>
            <p style="margin:0;color:#cbd5e1;line-height:1.6;">This dashboard keeps only the categories you asked for: Compute, Databases, Networking, Storage, Kubernetes, Security, Billing, and IAM.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    hero_a, hero_b, hero_c = st.columns(3)
    hero_a.markdown('<div class="feature-card"><strong>Live inventory</strong><span>Fetch resources from Cloud Asset Inventory in one click.</span></div>', unsafe_allow_html=True)
    hero_b.markdown('<div class="feature-card"><strong>Clean scope</strong><span>Only the services you care about are counted and shown.</span></div>', unsafe_allow_html=True)
    hero_c.markdown('<div class="feature-card"><strong>Faster decisions</strong><span>Use cost, monitoring, and assistant views to act quickly.</span></div>', unsafe_allow_html=True)

    metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns(4)
    metrics_col1.metric("Total Resources", len(df_filtered))
    metrics_col2.metric("Unique Asset Types", df_filtered["asset_type"].nunique() if not df_filtered.empty else 0)
    metrics_col3.metric("Estimated Monthly Cost", f"${cost_summary['estimated_monthly_cost']:.2f}")
    metrics_col4.metric("Estimated Daily Spend", f"${cost_summary['daily_spending']:.2f}")

    st.caption("Live data refreshes on demand and powers the summary views below.")
    
    summary_lines = build_infrastructure_summary(resources, health_score, cost_summary, likely_running_count)
    cost_insights = build_cost_insights(df_filtered)
    security_recommendations = build_security_recommendations(df_filtered)
    performance_guidance = build_performance_guidance(df_filtered)

    summary_cards = st.columns(4)
    summary_cards[0].markdown(f'<div class="feature-card"><strong>Today\'s summary</strong><span>{summary_lines[0]} {summary_lines[1]}</span></div>', unsafe_allow_html=True)
    summary_cards[1].markdown(f'<div class="feature-card"><strong>Cloud health</strong><span>{summary_lines[2]}</span></div>', unsafe_allow_html=True)
    summary_cards[2].markdown(f'<div class="feature-card"><strong>Cost insight</strong><span>{cost_insights[0] if cost_insights else "Cost insights will appear after inventory is loaded."}</span></div>', unsafe_allow_html=True)
    summary_cards[3].markdown(f'<div class="feature-card"><strong>Security focus</strong><span>{security_recommendations[0][1]}</span></div>', unsafe_allow_html=True)

    sec_col1, sec_col2 = st.columns([2, 1])
    with sec_col1:
        st.markdown('<div class="section-card"><h3>📊 Infrastructure Overview</h3><p style="margin:0;color:#cbd5e1;font-size:0.95rem;">Real-time snapshot of your core GCP services.</p></div>', unsafe_allow_html=True)
    with sec_col2:
        st.markdown(f'<div class="feature-card"><strong>🔐 Security Score</strong><span>{health_score}/100 • {"Excellent" if health_score >= 80 else "Good" if health_score >= 60 else "Fair"}</span></div>', unsafe_allow_html=True)

    selected_type = page_selected
    
    # Add activity feed as a collapsible section
    with st.expander("📋 Recent Activity & Events", expanded=False):
        activity_items = [
            ("🟢 Data Refresh", f"Latest resources synced {format_elapsed_time(st.session_state.last_refresh_at)}", "Connected"),
            (f"📊 Overview Loaded", f"{len(df_filtered)} resources tracked across 8 categories", "Active"),
            (f"💰 Cost Analysis", f"${cost_summary['estimated_monthly_cost']:.2f}/month estimated spend", "Analyzed"),
        ]
        if not df_filtered.empty:
            top_cost = df_filtered.nlargest(1, "estimated_monthly_cost").iloc[0]
            activity_items.append((
                f"⚠️ High Cost Item",
                f"{top_cost.get('display_name', 'Resource')} at ${top_cost.get('estimated_monthly_cost', 0):.2f}/mo",
                "Alert"
            ))
        
        for icon_event, description, status in activity_items[:5]:
            st.markdown(f'<div style="padding:12px;border-left:2px solid rgba(96,165,250,0.3);margin-bottom:8px;"><div style="font-weight:600;color:#f1f5f9;">{icon_event}</div><div style="font-size:0.85rem;color:#cbd5e1;">{description}</div></div>', unsafe_allow_html=True)

    # OVERVIEW PAGE
    if selected_type == "overview":
        st.subheader("Overview")
        st.markdown(
            '<div class="section-card"><h3>Today\'s Infrastructure Summary</h3><p style="margin:0;color:#cbd5e1;">This view explains what the inventory means so new users can understand the cloud picture faster.</p></div>',
            unsafe_allow_html=True,
        )
        overview_tip = DID_YOU_KNOW_TIPS[datetime.now(timezone.utc).day % len(DID_YOU_KNOW_TIPS)]
        overview_info_a, overview_info_b, overview_info_c = st.columns(3)
        overview_info_a.markdown(f'<div class="feature-card"><strong>Inventory health</strong><span>{summary_lines[2]}</span></div>', unsafe_allow_html=True)
        overview_info_b.markdown(f'<div class="feature-card"><strong>Daily spend</strong><span>{summary_lines[1]}</span></div>', unsafe_allow_html=True)
        overview_info_c.markdown(f'<div class="feature-card"><strong>Did you know?</strong><span>{overview_tip}</span></div>', unsafe_allow_html=True)
        overview_col1, overview_col2, overview_col3 = st.columns(3)
        overview_col1.metric("Unique Projects", unique_projects)
        overview_col2.metric("Likely Running", likely_running_count)
        overview_col3.metric("Top Service", top_service)

        st.markdown('<div class="section-card"><h3>Category Snapshot</h3><p style="margin:0;color:#cbd5e1;">A quick read on the eight tracked categories.</p></div>', unsafe_allow_html=True)
        if not category_summary_df.empty:
            chart_col, table_col = st.columns([1.3, 1])
            with chart_col:
                category_chart = px.bar(
                    category_summary_df.sort_values("count", ascending=False),
                    x="category",
                    y="count",
                    color="category",
                    title=None,
                )
                category_chart.update_layout(
                    showlegend=False,
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=360,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e5e7eb"),
                )
                st.plotly_chart(category_chart, use_container_width=True)
            with table_col:
                st.dataframe(
                    category_summary_df.sort_values("count", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )

        st.subheader("Live Resource List")
        st.dataframe(build_resource_display_frame(df_filtered), use_container_width=True, hide_index=True)

        csv_data = df_filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name="gcp_live_resources.csv",
            mime="text/csv",
        )

    # COST ANALYTICS PAGE
    elif selected_type == "cost":
        st.subheader("Cost Analytics")
        st.markdown(
            '<div class="section-card"><h3>Cost insights</h3><p style="margin:0;color:#cbd5e1;">These notes explain where spend comes from and what to review first.</p></div>',
            unsafe_allow_html=True,
        )
        cost_tip_a, cost_tip_b = st.columns(2)
        cost_tip_a.markdown(f'<div class="feature-card"><strong>Primary cost driver</strong><span>{cost_insights[0] if cost_insights else "Cost insights will appear after inventory is loaded."}</span></div>', unsafe_allow_html=True)
        cost_tip_b.markdown(f'<div class="feature-card"><strong>Optimization guidance</strong><span>{performance_guidance[0]}</span></div>', unsafe_allow_html=True)

        service_costs = df.groupby("asset_class", as_index=False)["estimated_monthly_cost"].sum().sort_values("estimated_monthly_cost", ascending=False)
        location_costs = df.groupby("location", as_index=False)["estimated_monthly_cost"].sum().sort_values("estimated_monthly_cost", ascending=False)

        pie_left, pie_right = st.columns(2)
        with pie_left:
            st.markdown("#### Spend by service")
            if not service_costs.empty:
                fig = px.pie(service_costs, names="asset_class", values="estimated_monthly_cost", hole=0.42)
                st.plotly_chart(fig, use_container_width=True)
        with pie_right:
            st.markdown("#### Spend by location")
            if not location_costs.empty:
                fig = px.bar(location_costs.head(10), x="location", y="estimated_monthly_cost")
                st.plotly_chart(fig, use_container_width=True)

        trend_frame = df.copy()
        trend_frame["created_at_dt"] = pd.to_datetime(trend_frame["created_at"], errors="coerce")
        trend_frame = trend_frame.dropna(subset=["created_at_dt"])
        if not trend_frame.empty:
            trend_frame["create_day"] = trend_frame["created_at_dt"].dt.date
            trend = trend_frame.groupby("create_day", as_index=False)["estimated_monthly_cost"].sum()
            st.markdown("#### Cost trend by creation date")
            st.line_chart(trend.set_index("create_day"))

        st.markdown("#### Highest-cost resources")
        highest_cost_frame = build_resource_display_frame(df.sort_values("estimated_monthly_cost", ascending=False).head(10))
        st.dataframe(
            highest_cost_frame[["display_name", "resource_name", "location", "estimated_monthly_cost", "owner_hint", "updated_at", "created_at"]],
            use_container_width=True,
            hide_index=True,
        )

    # LIVE RESOURCES PAGE
    elif selected_type == "live":
        st.subheader("Live Resources")
        st.markdown(
            '<div class="section-card"><h3>Service guide</h3><p style="margin:0;color:#cbd5e1;">Hover the category buttons for a simple explanation of each service group. This helps beginners understand what they are looking at.</p></div>',
            unsafe_allow_html=True,
        )
        st.caption("Choose a category to inspect matching resources.")

        summary_cols = st.columns(4)
        for idx, (cat_label, asset_list) in enumerate(CATEGORY_MAP.items()):
            if idx >= 4:
                break
            count = int(df[df["asset_type"].isin(asset_list)].shape[0]) if not df.empty else 0
            category_cost = float(df[df["asset_type"].isin(asset_list)]["estimated_monthly_cost"].sum()) if not df.empty else 0.0
            summary_cols[idx].markdown(
                f'<div class="feature-card"><strong>{cat_label}</strong><span>{CATEGORY_DESCRIPTIONS.get(cat_label, "") } {count} items · ${category_cost:.2f}/mo</span></div>',
                unsafe_allow_html=True,
            )

        cols = st.columns(2)
        for idx, (cat_label, asset_list) in enumerate(CATEGORY_MAP.items()):
            count = int(df[df["asset_type"].isin(asset_list)].shape[0]) if not df.empty else 0

            col = cols[idx % 2]
            label = f"{cat_label} ({count})"
            if col.button(label, use_container_width=True, key=f"cat_btn_{idx}", help=CATEGORY_DESCRIPTIONS.get(cat_label, "")):
                st.session_state.selected_live_resource = cat_label
                st.rerun()

        # Show selected category details
        if st.session_state.get("selected_live_resource"):
            selected_cat = st.session_state.selected_live_resource
            types = CATEGORY_MAP.get(selected_cat, [])
            filtered_rows = [row for row in resources if str(row.get("asset_type") or "") in types]
            filtered_df = pd.DataFrame(filtered_rows) if filtered_rows else pd.DataFrame()

            st.divider()
            st.subheader(f"{selected_cat} — {len(filtered_df)} items")
            detail_a, detail_b, detail_c = st.columns(3)
            detail_a.metric("Count", len(filtered_df))
            if not filtered_df.empty:
                detail_b.metric("Estimated Monthly Cost", f"${filtered_df['estimated_monthly_cost'].sum():.2f}")
                detail_c.metric("Top Asset Type", filtered_df["asset_type"].mode().iloc[0])
                st.dataframe(
                    build_resource_display_frame(filtered_df),
                    use_container_width=True,
                    hide_index=True,
                )

                monitor_candidates = [
                    row
                    for row in filtered_rows
                    if str(row.get("asset_type") or "") == "compute.googleapis.com/Instance"
                    and str(row.get("state") or "").upper() == "RUNNING"
                ]
                if monitor_candidates:
                    st.markdown("#### Monitoring")
                    st.caption("Monitoring column shows Show for running VMs. Select one VM and click Show Monitoring.")
                    monitor_label_map = {
                        f"{row.get('display_name') or row.get('name')} ({row.get('location')})": row
                        for row in monitor_candidates
                    }
                    selected_monitor_label = st.selectbox(
                        "Monitoring target",
                        list(monitor_label_map.keys()),
                        key=f"live_monitor_target_{selected_cat}",
                    )
                    if st.button("Show Monitoring", key=f"live_show_monitoring_{selected_cat}"):
                        selected_monitor = monitor_label_map[selected_monitor_label]
                        selected_info = parse_compute_instance_resource(selected_monitor)
                        if not selected_info:
                            st.warning("Could not parse VM identifier for monitoring.")
                        else:
                            try:
                                instance_details = fetch_compute_instance_details(
                                    credentials,
                                    selected_info["project"],
                                    selected_info["zone"],
                                    selected_info["instance"],
                                )
                                instance_id = str(instance_details.get("id", ""))
                                monitoring_metrics = {
                                    "CPU Utilization": {
                                        "metric": "compute.googleapis.com/instance/cpu/utilization",
                                        "aligner": "ALIGN_MEAN",
                                        "reducer": "REDUCE_MEAN",
                                        "aggregate": "mean",
                                        "scale": 100.0,
                                    },
                                    "Memory Utilization": {
                                        "metric": "agent.googleapis.com/memory/percent_used",
                                        "aligner": "ALIGN_MEAN",
                                        "reducer": "REDUCE_MEAN",
                                        "aggregate": "mean",
                                        "scale": 1.0,
                                        "extra_filter": 'metric.label."state" = "used"',
                                    },
                                    "Network Traffic": {
                                        "metric": "compute.googleapis.com/instance/network/received_bytes_count",
                                        "aligner": "ALIGN_RATE",
                                        "reducer": "REDUCE_SUM",
                                        "aggregate": "sum",
                                        "scale": 1.0,
                                    },
                                    "Disk Space Utilization": {
                                        "metric": "agent.googleapis.com/disk/percent_used",
                                        "aligner": "ALIGN_MEAN",
                                        "reducer": "REDUCE_MEAN",
                                        "aggregate": "mean",
                                        "scale": 1.0,
                                        "extra_filter": None,
                                    },
                                }
                                graph_columns = st.columns(2)
                                for index, (title, metric_cfg) in enumerate(monitoring_metrics.items()):
                                    try:
                                        series = fetch_monitoring_time_series(
                                            credentials,
                                            selected_info["project"],
                                            instance_id,
                                            selected_info["zone"],
                                            metric_cfg["metric"],
                                            aligner=metric_cfg["aligner"],
                                            reducer=metric_cfg["reducer"],
                                            group_by_fields=["resource.label.instance_id"],
                                        )
                                        points = flatten_time_series(series)
                                        with graph_columns[index % 2]:
                                            st.markdown(f"##### {title}")
                                            metric_frame = build_metric_chart_frame(
                                                points,
                                                aggregate=metric_cfg["aggregate"],
                                                scale=float(metric_cfg["scale"]),
                                            )
                                            if not metric_frame.empty:
                                                st.line_chart(metric_frame.set_index("time")["value"])
                                            else:
                                                st.info("No samples found for this metric.")
                                    except GCPDashboardError as exc:
                                        with graph_columns[index % 2]:
                                            st.warning(str(exc))
                            except GCPDashboardError as exc:
                                st.warning(str(exc))

                csv_data = filtered_df.to_csv(index=False).encode("utf-8")
                st.download_button(f"Download {selected_cat} CSV", data=csv_data, file_name=f"gcp_{selected_cat.lower().replace(' ', '_')}.csv")
            else:
                detail_b.metric("Estimated Monthly Cost", "$0.00")
                detail_c.metric("Top Asset Type", "None")
                st.info("No items found in this category.")

            if st.button("← Back to Live Resources"):
                st.session_state.selected_live_resource = None
                st.rerun()

    # CLOCKPLUS PAGE
    elif selected_type == "clockpluse":
        st.subheader("ClockPlus")
        st.markdown(
            '<div class="section-card"><h3>Orphaned cost guardrail</h3><p style="margin:0;color:#cbd5e1;">Track snapshots, persistent disks, and static external IPs, then see whether each one is connected or not connected.</p></div>',
            unsafe_allow_html=True,
        )

        tracked_types = {
            "compute.googleapis.com/Snapshot": "Snapshots",
            "compute.googleapis.com/Disk": "Persistent Disks",
            "compute.googleapis.com/Address": "Static External IPs",
        }
        raw_df = pd.DataFrame(st.session_state.raw_resources) if st.session_state.raw_resources else pd.DataFrame()
        clockplus_df = raw_df[raw_df["asset_type"].isin(tracked_types.keys())].copy() if not raw_df.empty else pd.DataFrame()
        connected_count = int((clockplus_df["usage_status"] == "Connected").sum()) if not clockplus_df.empty and "usage_status" in clockplus_df else 0
        not_connected_count = int((clockplus_df["usage_status"] == "Not connected").sum()) if not clockplus_df.empty and "usage_status" in clockplus_df else 0
        total_clockplus_cost = float(clockplus_df["estimated_monthly_cost"].sum()) if not clockplus_df.empty else 0.0
        clockplus_summary_df = build_clockplus_summary(clockplus_df)

        metric_a, metric_b, metric_c, metric_d = st.columns(4)
        metric_a.metric("Tracked Resources", len(clockplus_df))
        metric_b.metric("Connected", connected_count)
        metric_c.metric("Not Connected", not_connected_count)
        metric_d.metric("Estimated Monthly Cost", f"${total_clockplus_cost:.2f}")

        st.caption("Connected means attached to a VM or service. Snapshots are standalone, so they always appear as not applicable for VM attachment.")

        if clockplus_df.empty:
            st.info("No snapshots, persistent disks, or static external IPs were found in the current inventory.")
        else:
            st.markdown("#### Resource type summary")
            if clockplus_summary_df.empty:
                st.info("No ClockPlus resources were found.")
            else:
                st.dataframe(clockplus_summary_df, use_container_width=True, hide_index=True)

            type_tabs = st.tabs(["All", "Snapshots", "Persistent Disks", "Static External IPs"])
            tab_frames = {
                "All": build_clockplus_frame(clockplus_df),
                "Snapshots": build_clockplus_frame(clockplus_df[clockplus_df["asset_type"] == "compute.googleapis.com/Snapshot"]),
                "Persistent Disks": build_clockplus_frame(clockplus_df[clockplus_df["asset_type"] == "compute.googleapis.com/Disk"]),
                "Static External IPs": build_clockplus_frame(clockplus_df[clockplus_df["asset_type"] == "compute.googleapis.com/Address"]),
            }

            for tab, label in zip(type_tabs, tab_frames.keys()):
                with tab:
                    frame = tab_frames[label]
                    if frame.empty:
                        st.info(f"No {label.lower()} found.")
                    else:
                        connected_here = int((frame["clockplus_status"] == "Connected").sum())
                        not_connected_here = int((frame["clockplus_status"] == "Not connected").sum())
                        stat_a, stat_b, stat_c = st.columns(3)
                        stat_a.metric("Total", len(frame))
                        stat_b.metric("Connected", connected_here)
                        stat_c.metric("Not Connected", not_connected_here)
                        st.markdown("##### Attachment targets")
                        st.dataframe(
                            frame.sort_values(["clockplus_status", "estimated_monthly_cost"], ascending=[True, False]),
                            use_container_width=True,
                            hide_index=True,
                        )
                        csv_data = frame.to_csv(index=False).encode("utf-8")
                        st.download_button(
                            f"Download {label} CSV",
                            data=csv_data,
                            file_name=f"clockplus_{label.lower().replace(' ', '_')}.csv",
                            mime="text/csv",
                        )

    # MONITORING PAGE
    elif selected_type == "monitoring":
        st.subheader("Monitoring")
        st.markdown(
            '<div class="section-card"><h3>Monitoring guide</h3><p style="margin:0;color:#cbd5e1;">Live telemetry helps you see whether a VM is healthy, overloaded, or underused.</p></div>',
            unsafe_allow_html=True,
        )
        st.caption("Live VM metrics when Cloud Monitoring is available.")
        st.info("Monitoring APIs connected. Refresh metrics below to inspect CPU, network, and disk activity.")
        compute_candidates = [row for row in resources if str(row.get("asset_type") or "") == "compute.googleapis.com/Instance"]
        if not compute_candidates:
            st.info("No Compute Engine instances found in the current result set.")
        else:
            label_map = {f"{row.get('display_name') or row.get('name')} ({row.get('location')})": row for row in compute_candidates}
            selected_label = st.selectbox("Choose a VM", list(label_map.keys()), help="Pick a Compute Engine VM to inspect live telemetry.")
            selected = label_map[selected_label]
            selected_info = parse_compute_instance_resource(selected)

            if not selected_info:
                st.warning("Could not parse the VM identifier for monitoring.")
            else:
                if st.button("Load Monitoring Metrics", help="Refresh live CPU, network, and disk metrics for the selected VM."):
                    st.success("Live telemetry is active for the selected VM.")
                    try:
                        instance_details = fetch_compute_instance_details(
                            credentials,
                            selected_info["project"],
                            selected_info["zone"],
                            selected_info["instance"],
                        )
                        instance_id = str(instance_details.get("id", ""))
                        monitoring_metrics = {
                            "CPU Utilization": {
                                "metric": "compute.googleapis.com/instance/cpu/utilization",
                                "aligner": "ALIGN_MEAN",
                                "reducer": "REDUCE_MEAN",
                                "aggregate": "mean",
                                "scale": 100.0,
                            },
                            "Memory Utilization": {
                                "metric": "agent.googleapis.com/memory/percent_used",
                                "aligner": "ALIGN_MEAN",
                                "reducer": "REDUCE_MEAN",
                                "aggregate": "mean",
                                "scale": 1.0,
                                "extra_filter": 'metric.label."state" = "used"',
                            },
                            "Network Traffic": {
                                "metric": "compute.googleapis.com/instance/network/received_bytes_count",
                                "aligner": "ALIGN_RATE",
                                "reducer": "REDUCE_SUM",
                                "aggregate": "sum",
                                "scale": 1.0,
                            },
                            "Disk Space Utilization": {
                                "metric": "agent.googleapis.com/disk/percent_used",
                                "aligner": "ALIGN_MEAN",
                                "reducer": "REDUCE_MEAN",
                                "aggregate": "mean",
                                "scale": 1.0,
                                "extra_filter": None,
                            },
                        }
                        graph_columns = st.columns(2)
                        for index, (title, metric_cfg) in enumerate(monitoring_metrics.items()):
                            with st.spinner(f"Loading {title}..."):
                                try:
                                    series = fetch_monitoring_time_series(
                                        credentials,
                                        selected_info["project"],
                                        instance_id,
                                        selected_info["zone"],
                                        metric_cfg["metric"],
                                        aligner=metric_cfg["aligner"],
                                        reducer=metric_cfg["reducer"],
                                        group_by_fields=["resource.label.instance_id"],
                                        extra_filter=metric_cfg.get("extra_filter"),
                                    )
                                    points = flatten_time_series(series)
                                    target = graph_columns[index % 2]
                                    with target:
                                        st.markdown(f"##### {title}")
                                        metric_frame = build_metric_chart_frame(
                                            points,
                                            aggregate=metric_cfg["aggregate"],
                                            scale=float(metric_cfg["scale"]),
                                        )
                                        if not metric_frame.empty:
                                            st.line_chart(metric_frame.set_index("time")["value"])
                                        else:
                                            st.info("No monitoring samples found for this metric.")
                                except GCPDashboardError as exc:
                                    with graph_columns[index % 2]:
                                        st.warning(str(exc))
                    except GCPDashboardError as exc:
                        st.warning(str(exc))

    # AI ASSISTANT PAGE
    elif selected_type == "assistant":
        st.subheader("AI Assistant")
        st.markdown(
            '<div class="section-card"><h3>AI recommendation starter</h3><p style="margin:0;color:#cbd5e1;">Use a quick prompt below or ask your own question. The assistant explains costs, risks, and optimization ideas in simple language.</p></div>',
            unsafe_allow_html=True,
        )
        st.caption("Ask for cost ideas, risky resources, or cleanup suggestions.")
        if "ai_prompt_seed" not in st.session_state:
            st.session_state.ai_prompt_seed = ""
        ai_prompt_cols = st.columns(2)
        for idx, suggestion in enumerate(AI_QUICK_PROMPTS):
            if ai_prompt_cols[idx % 2].button(suggestion, key=f"ai_quick_{idx}", help="Use this as a starter prompt"):
                st.session_state.ai_prompt_seed = suggestion

        for message in st.session_state.assistant_messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        prompt = st.chat_input("Ask the cloud assistant")
        if not prompt and st.session_state.ai_prompt_seed:
            prompt = st.session_state.ai_prompt_seed
            st.session_state.ai_prompt_seed = ""
        if prompt:
            expensive = df.sort_values("estimated_monthly_cost", ascending=False).head(5)
            expensive_text = ", ".join(
                f"{row.display_name or row.name} (${row.estimated_monthly_cost:.2f})"
                for row in expensive.itertuples()
            )
            recommendations = []
            if not df[df["owner_hint"].eq("Unknown")].empty:
                recommendations.append("Add owner labels/tags to reduce blind spots.")
            if not df[~df["state"].isin(["RUNNING"])].empty:
                recommendations.append("Review stopped or idle resources and delete what is no longer needed.")
            if any("europe" in str(row.location).lower() or "asia" in str(row.location).lower() for row in df.itertuples()):
                recommendations.append("Consider moving always-on workloads to a cheaper region if latency permits.")
            if len(df[df["asset_class"] == "Kubernetes / GKE"]) > 0:
                recommendations.append("Check GKE autoscaling and node pool sizes for waste.")
            if len(df[df["asset_class"] == "Compute Engine"]) > 0:
                recommendations.append("Right-size VMs if CPU is consistently low and switch to smaller machine families.")

            prompt_lower = prompt.lower()
            if any(term in prompt_lower for term in ["expensive", "cost", "money"]):
                answer = f"Top expensive resources right now: {expensive_text}. Estimated monthly cost is ${cost_summary['estimated_monthly_cost']:.2f}."
            elif any(term in prompt_lower for term in ["unused", "idle", "delete"]):
                idle_count = len(df[~df["state"].isin(["RUNNING"])])
                answer = f"I found {idle_count} non-active resources. Start by reviewing them for deletion or shutdown."
            elif any(term in prompt_lower for term in ["reduce cost", "optimize", "save money"]):
                answer = "Cost optimization ideas: " + " ".join(recommendations[:4])
            else:
                answer = "I can summarize spend, suggest cost savings, find idle items, and help you search by service or region."
                if recommendations:
                    answer += " Current recommendations: " + " ".join(recommendations[:3])

            st.session_state.assistant_messages.append({"role": "user", "content": prompt})
            st.session_state.assistant_messages.append({"role": "assistant", "content": answer})
            st.rerun()

    # IAM AUDIT LOG PAGE
    elif selected_type == "iam_audit":
        st.subheader("🔐 IAM Audit Log - Resource Creator Tracking")
        st.markdown(
            '<div class="section-card"><h3>Track who created what</h3><p style="margin:0;color:#cbd5e1;">View the IAM users/service accounts that created each resource. This helps track resource ownership and maintain audit trails.</p></div>',
            unsafe_allow_html=True,
        )
        
        # Display current IAM user
        iam_user_info = get_iam_user_info(credentials)
        col1, col2, col3 = st.columns(3)
        col1.metric("Current IAM User", iam_user_info["email"])
        col2.metric("User Type", iam_user_info["user_type"])
        col3.metric("Authenticated", iam_user_info["timestamp"][:10])
        
        st.divider()
        
        # Show resources grouped by IAM user
        st.markdown("#### Resources by Creator")
        
        if not df_filtered.empty and "created_by_iam_user" in df_filtered.columns:
            df_with_creator = df_filtered.copy()
            
            # Group by IAM user
            creator_stats = df_with_creator.groupby("created_by_iam_user", as_index=False).agg({
                "display_name": "count",
                "estimated_monthly_cost": "sum",
            }).rename(columns={
                "display_name": "Resource Count",
                "estimated_monthly_cost": "Total Monthly Cost"
            })
            
            st.dataframe(creator_stats.sort_values("Resource Count", ascending=False), use_container_width=True, hide_index=True)
            
            st.divider()
            
            # Detailed view
            st.markdown("#### Detailed Resource List by Creator")
            
            unique_creators = sorted(df_with_creator["created_by_iam_user"].unique())
            selected_creator = st.selectbox(
                "Select IAM user to view their resources",
                unique_creators,
                help="View all resources created by this IAM user"
            )
            
            if selected_creator:
                creator_resources = df_with_creator[df_with_creator["created_by_iam_user"] == selected_creator]
                st.info(f"👤 **{selected_creator}** created **{len(creator_resources)}** resources totaling **${creator_resources['estimated_monthly_cost'].sum():.2f}**/month")
                
                display_cols = ["display_name", "resource_name", "asset_type", "location", "state", "estimated_monthly_cost", "created_at", "owner_hint"]
                available_cols = [col for col in display_cols if col in creator_resources.columns]
                
                st.dataframe(
                    creator_resources[available_cols].sort_values("estimated_monthly_cost", ascending=False),
                    use_container_width=True,
                    hide_index=True,
                )
                
                # Download audit data
                csv_data = creator_resources[available_cols].to_csv(index=False).encode("utf-8")
                st.download_button(
                    f"Download {selected_creator.split('@')[0]} resources",
                    data=csv_data,
                    file_name=f"iam_audit_{selected_creator.split('@')[0]}.csv",
                    mime="text/csv",
                )
        else:
            st.info("No resource creation audit data available yet. Fetch resources to see creator information.")
        
        # Audit log file view
        st.markdown("#### Audit Log Entries")
        try:
            import json
            audit_file = "iam_resource_audit.jsonl"
            with open(audit_file, "r") as f:
                audit_entries = [json.loads(line) for line in f if line.strip()]
                if audit_entries:
                    audit_df = pd.DataFrame(audit_entries[-50:])  # Last 50 entries
                    st.dataframe(audit_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)
                else:
                    st.info("No audit log entries found yet.")
        except FileNotFoundError:
            st.info("Audit log file not yet created. Resources will be logged when they are created or fetched.")

    footer_left, footer_right = st.columns([1.6, 1])
    footer_left.markdown(
        f'<div class="section-card"><h3>System status</h3><p style="margin:0;color:#cbd5e1;">API Status: Operational · Last Sync: {format_elapsed_time(st.session_state.last_refresh_at)} · Connected Services: {len(df_filtered)} · Dashboard Version: v2.1</p></div>',
        unsafe_allow_html=True,
    )
    footer_right.markdown(
        f'<div class="section-card"><h3>Operational note</h3><p style="margin:0;color:#cbd5e1;">{summary_lines[3] if len(summary_lines) > 3 else "Resource visibility updates automatically when refresh is enabled."}</p></div>',
        unsafe_allow_html=True,
    )
