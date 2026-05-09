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
    parse_compute_instance_resource,
    summarize_costs,
)


CATEGORY_MAP: dict[str, list[str]] = {
    "Compute": ["compute.googleapis.com/Instance", "compute.googleapis.com/Disk", "compute.googleapis.com/ForwardingRule"],
    "Databases": ["sqladmin.googleapis.com/Instance", "spanner.googleapis.com/Instance"],
    "Networking": ["compute.googleapis.com/Network", "compute.googleapis.com/Firewall", "compute.googleapis.com/ForwardingRule"],
    "Storage": ["storage.googleapis.com/Bucket"],
    "Kubernetes": ["container.googleapis.com/Cluster"],
    "Security": ["cloudkms.googleapis.com/KeyRing", "cloudkms.googleapis.com/CryptoKey"],
    "Billing": ["billingbudgets.googleapis.com/Budget"],
    "IAM": ["iam.googleapis.com/ServiceAccount", "iam.googleapis.com/ServiceAccountKey", "iam.googleapis.com/Role"],
}


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


st.set_page_config(page_title="GCP Live Resource Dashboard", page_icon="☁", layout="wide")

st.title("AI-Powered Cloud Infrastructure Intelligence Platform")
st.caption("Fast live GCP inventory, cost signals, and action views in one place.")

with st.sidebar:
    st.header("Configuration")
    st.markdown(
        """
Upload a service account JSON and fetch your live GCP inventory.
        """
    )
    st.info("Secure session: key stays in memory only.")
    
    uploaded_file = st.file_uploader("Upload service account JSON", type=["json"])
    
    if not uploaded_file:
        st.stop()

    try:
        file_text = uploaded_file.getvalue().decode("utf-8")
        credentials, default_project_id, service_account_email = load_credentials_from_json(file_text)
    except UnicodeDecodeError:
        st.error("File is not valid UTF-8 JSON.")
        st.stop()
    except GCPDashboardError as exc:
        st.error(str(exc))
        st.stop()

    st.success("Service account key loaded successfully.")
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
    only_running = st.checkbox("Only show likely running resources", value=False)
    st.caption("Turn it off to include all resource states.")
    max_rows = st.slider("Maximum rows", min_value=100, max_value=10000, value=2000, step=100)
    
    st.divider()
    
    auto_refresh_enabled = st.checkbox("Auto refresh live data", value=True)
    refresh_seconds = st.selectbox("Refresh interval", [15, 30, 60], index=1)
    
    st.divider()
    
    if st.button("Fetch Live Resources", type="primary"):
        st.session_state.fetch_clicked = True
    
    st.divider()
    
    st.subheader("Navigation")
    page_selected = st.radio(
        "Select Page",
        ["Overview", "Cost Analytics", "Live Resources", "Monitoring", "AI Assistant"],
        index=0,
        label_visibility="collapsed"
    )

if "resources" not in st.session_state:
    st.session_state.resources = []
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

def load_resources() -> list[dict[str, object]]:
    with st.spinner("Fetching live resources from GCP..."):
        resources = fetch_live_resources(
            credentials=credentials,
            scope=scope.strip(),
            query=query,
            limit=max_rows,
        )

    if only_running:
        resources = filter_likely_running(resources)

    return resources

# Handle Fetch Live Resources button from sidebar
if st.session_state.get("fetch_clicked"):
    try:
        resources = load_resources()
    except GCPDashboardError as exc:
        st.session_state.connection_state = "Error"
        st.error(str(exc))
        st.stop()

    if not resources:
        st.session_state.connection_state = "Connected"
        st.session_state.last_refresh_at = datetime.now(timezone.utc)
        st.warning("No resources found with the current scope/query.")
        st.session_state.fetch_clicked = False
        st.stop()

    st.session_state.resources = resources
    st.session_state.fetch_clicked = False
    st.session_state.connection_state = "Connected"
    st.session_state.last_refresh_at = datetime.now(timezone.utc)

if auto_refresh_enabled and st.session_state.resources:
    st_autorefresh(interval=refresh_seconds * 1000, key="gcp_live_refresh")
    try:
        st.session_state.resources = load_resources()
        st.session_state.connection_state = "Connected"
        st.session_state.last_refresh_at = datetime.now(timezone.utc)
    except GCPDashboardError as exc:
        st.session_state.connection_state = "Error"
        st.warning(str(exc))

resources = st.session_state.resources

df = pd.DataFrame(resources) if resources else pd.DataFrame()

if not resources:
    st.info("Upload a service account JSON and click 'Fetch Live Resources' in the sidebar to begin.")
else:
    st.markdown(
        """
        <style>
        .stApp {
            background: radial-gradient(circle at top, rgba(59, 130, 246, 0.12), transparent 26%), linear-gradient(180deg, #0b1020 0%, #111827 60%, #0f172a 100%);
            color: #e5e7eb;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #111827 0%, #0f172a 100%);
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
        }
        .hero-card, .feature-card, .section-card {
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 18px;
            padding: 18px 20px;
            box-shadow: 0 18px 36px rgba(0, 0, 0, 0.22);
            backdrop-filter: blur(14px);
        }
        .hero-card h2, .section-card h3 {
            margin: 0 0 8px 0;
        }
        .hero-badge {
            display: inline-block;
            padding: 6px 10px;
            border-radius: 999px;
            background: rgba(96, 165, 250, 0.16);
            color: #bfdbfe;
            font-size: 0.78rem;
            margin-bottom: 10px;
        }
        .feature-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-top: 12px;
        }
        .feature-card strong {
            display: block;
            font-size: 1.02rem;
            margin-bottom: 6px;
            color: #f8fafc;
        }
        .feature-card span {
            color: #cbd5e1;
            font-size: 0.92rem;
            line-height: 1.45;
        }
        div[data-testid="stMetric"] {
            background: rgba(15, 23, 42, 0.68);
            border: 1px solid rgba(148, 163, 184, 0.16);
            border-radius: 16px;
            padding: 14px 16px;
            box-shadow: 0 14px 28px rgba(0, 0, 0, 0.16);
        }
        div[data-testid="stMetric"] label {
            color: #cbd5e1 !important;
        }
        .stButton > button {
            border-radius: 14px;
            border: 1px solid rgba(96, 165, 250, 0.22);
            background: linear-gradient(135deg, #3b82f6 0%, #0ea5e9 100%);
            color: white;
            font-weight: 600;
        }
        .stButton > button:hover {
            filter: brightness(1.08);
            border-color: rgba(96, 165, 250, 0.42);
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

    selected_type = page_selected

    # OVERVIEW PAGE
    if selected_type == "Overview":
        st.subheader("Overview")
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
        st.dataframe(df_filtered, use_container_width=True, hide_index=True)

        csv_data = df_filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name="gcp_live_resources.csv",
            mime="text/csv",
        )

    # COST ANALYTICS PAGE
    elif selected_type == "Cost Analytics":
        st.subheader("Cost Analytics")
        st.caption("Estimated spend based on live inventory.")

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
        st.dataframe(
            df.sort_values("estimated_monthly_cost", ascending=False)[["display_name", "asset_class", "location", "estimated_monthly_cost", "owner_hint"]].head(10),
            use_container_width=True,
            hide_index=True,
        )

    # LIVE RESOURCES PAGE
    elif selected_type == "Live Resources":
        st.subheader("Live Resources")
        st.caption("Choose a category to inspect matching resources.")

        summary_cols = st.columns(4)
        for idx, (cat_label, asset_list) in enumerate(CATEGORY_MAP.items()):
            if idx >= 4:
                break
            count = int(df[df["asset_type"].isin(asset_list)].shape[0]) if not df.empty else 0
            category_cost = float(df[df["asset_type"].isin(asset_list)]["estimated_monthly_cost"].sum()) if not df.empty else 0.0
            summary_cols[idx].markdown(
                f'<div class="feature-card"><strong>{cat_label}</strong><span>{count} items · ${category_cost:.2f}/mo</span></div>',
                unsafe_allow_html=True,
            )

        cols = st.columns(2)
        for idx, (cat_label, asset_list) in enumerate(CATEGORY_MAP.items()):
            count = int(df[df["asset_type"].isin(asset_list)].shape[0]) if not df.empty else 0

            col = cols[idx % 2]
            label = f"{cat_label} ({count})"
            if col.button(label, use_container_width=True, key=f"cat_btn_{idx}"):
                st.session_state.selected_live_resource = cat_label
                st.rerun()

        # Show selected category details
        if st.session_state.get("selected_live_resource"):
            selected_cat = st.session_state.selected_live_resource
            types = CATEGORY_MAP.get(selected_cat, [])
            filtered_df = df[df["asset_type"].isin(types)] if not df.empty else pd.DataFrame()

            st.divider()
            st.subheader(f"{selected_cat} — {len(filtered_df)} items")
            detail_a, detail_b, detail_c = st.columns(3)
            detail_a.metric("Count", len(filtered_df))
            if not filtered_df.empty:
                detail_b.metric("Estimated Monthly Cost", f"${filtered_df['estimated_monthly_cost'].sum():.2f}")
                detail_c.metric("Top Asset Type", filtered_df["asset_type"].mode().iloc[0])
                st.dataframe(
                    filtered_df[["display_name", "asset_type", "location", "state", "estimated_monthly_cost", "owner_hint"]],
                    use_container_width=True,
                    hide_index=True,
                )
                csv_data = filtered_df.to_csv(index=False).encode("utf-8")
                st.download_button(f"Download {selected_cat} CSV", data=csv_data, file_name=f"gcp_{selected_cat.lower().replace(' ', '_')}.csv")
            else:
                detail_b.metric("Estimated Monthly Cost", "$0.00")
                detail_c.metric("Top Asset Type", "None")
                st.info("No items found in this category.")

            if st.button("← Back to Live Resources"):
                st.session_state.selected_live_resource = None
                st.rerun()

    # MONITORING PAGE
    elif selected_type == "Monitoring":
        st.subheader("Monitoring")
        st.caption("Live VM metrics when Cloud Monitoring is available.")
        compute_candidates = [row for row in resources if str(row.get("asset_type") or "") == "compute.googleapis.com/Instance"]
        if not compute_candidates:
            st.info("No Compute Engine instances found in the current result set.")
        else:
            label_map = {f"{row.get('display_name') or row.get('name')} ({row.get('location')})": row for row in compute_candidates}
            selected_label = st.selectbox("Choose a VM", list(label_map.keys()))
            selected = label_map[selected_label]
            selected_info = parse_compute_instance_resource(selected)

            if not selected_info:
                st.warning("Could not parse the VM identifier for monitoring.")
            else:
                if st.button("Load Monitoring Metrics"):
                    try:
                        instance_details = fetch_compute_instance_details(
                            credentials,
                            selected_info["project"],
                            selected_info["zone"],
                            selected_info["instance"],
                        )
                        instance_id = str(instance_details.get("id", ""))
                        monitoring_metrics = {
                            "CPU": "compute.googleapis.com/instance/cpu/utilization",
                            "Network Out": "compute.googleapis.com/instance/network/sent_bytes_count",
                            "Network In": "compute.googleapis.com/instance/network/received_bytes_count",
                            "Disk Read": "compute.googleapis.com/instance/disk/read_bytes_count",
                            "Disk Write": "compute.googleapis.com/instance/disk/write_bytes_count",
                        }
                        graph_columns = st.columns(2)
                        for index, (title, metric_type) in enumerate(monitoring_metrics.items()):
                            with st.spinner(f"Loading {title}..."):
                                try:
                                    series = fetch_monitoring_time_series(
                                        credentials,
                                        selected_info["project"],
                                        instance_id,
                                        selected_info["zone"],
                                        metric_type,
                                    )
                                    points = flatten_time_series(series)
                                    target = graph_columns[index % 2]
                                    with target:
                                        st.markdown(f"##### {title}")
                                        if points:
                                            metric_frame = pd.DataFrame(points)
                                            metric_frame["time"] = pd.to_datetime(metric_frame["time"], errors="coerce")
                                            metric_frame["value"] = pd.to_numeric(metric_frame["value"], errors="coerce")
                                            metric_frame = metric_frame.dropna(subset=["time", "value"])
                                            st.line_chart(metric_frame.set_index("time")["value"])
                                        else:
                                            st.info("No monitoring samples found for this metric.")
                                except GCPDashboardError as exc:
                                    with graph_columns[index % 2]:
                                        st.warning(str(exc))
                    except GCPDashboardError as exc:
                        st.warning(str(exc))

    # AI ASSISTANT PAGE
    elif selected_type == "AI Assistant":
        st.subheader("AI Assistant")
        st.caption("Ask for cost ideas, risky resources, or cleanup suggestions.")

        for message in st.session_state.assistant_messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        prompt = st.chat_input("Ask the cloud assistant")
        if prompt:
            expensive = df.sort_values("estimated_monthly_cost", ascending=False).head(5)
            expensive_text = ", ".join(
                f"{row.display_name or row.name} (${row.estimated_monthly_cost:.2f})"
                for row in expensive.itertuples()
            )
            recommendations = []
            if not df[df["owner_hint"].eq("Unknown")].empty:
                recommendations.append("Add owner labels/tags to reduce blind spots.")
            if not df[df["state"].isin(["ACTIVE", "RUNNING", "READY", "UP"])].empty:
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
                idle_count = len(df[~df["state"].isin(["ACTIVE", "RUNNING", "READY", "UP"])])
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
