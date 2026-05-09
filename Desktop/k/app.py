from __future__ import annotations

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


st.set_page_config(page_title="GCP Live Resource Dashboard", page_icon="☁", layout="wide")

st.title("GCP Live Resource Dashboard")
st.caption("Upload a service account JSON key to view live resources from Cloud Asset Inventory.")

with st.sidebar:
    st.header("Setup Checklist")
    st.markdown(
        """
1. Enable **Cloud Asset API** in target GCP project.
2. Grant role **Cloud Asset Viewer** (or broader read role) to service account.
3. Upload service account JSON key here.
4. Click **Fetch Live Resources**.
        """
    )
    st.info("Security: Uploaded key is used in memory only and not written to disk.")
    st.caption("Cost analytics are estimated from live inventory. Monitoring graphs require Cloud Monitoring API and compute instances.")

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
col1, col2 = st.columns(2)
col1.metric("Service Account", service_account_email or "Unknown")
col2.metric("Default Project", default_project_id or "Unknown")

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
st.caption("Keep this off to see buckets, storage, networking, and other non-running resources.")
max_rows = st.slider("Maximum rows", min_value=100, max_value=10000, value=2000, step=100)
auto_refresh_enabled = st.sidebar.checkbox("Auto refresh live data", value=True)
refresh_seconds = st.sidebar.selectbox("Refresh interval", [15, 30, 60], index=1)

if "resources" not in st.session_state:
    st.session_state.resources = []
if "assistant_messages" not in st.session_state:
    st.session_state.assistant_messages = [
        {
            "role": "assistant",
            "content": "Ask me things like: show expensive resources, find production servers, or how to reduce cost.",
        }
    ]

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

if st.button("Fetch Live Resources", type="primary"):
    try:
        resources = load_resources()
    except GCPDashboardError as exc:
        st.error(str(exc))
        st.stop()

    if not resources:
        st.warning("No resources found with the current scope/query.")
        st.stop()

    st.session_state.resources = resources

if auto_refresh_enabled and st.session_state.resources:
    st_autorefresh(interval=refresh_seconds * 1000, key="gcp_live_refresh")
    try:
        st.session_state.resources = load_resources()
    except GCPDashboardError as exc:
        st.warning(str(exc))

resources = st.session_state.resources

df = pd.DataFrame(resources)

if not resources:
    st.info("Upload a service account JSON and click Fetch Live Resources to begin.")
else:
    metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns(4)
    cost_summary = summarize_costs(resources)
    metrics_col1.metric("Total Resources", len(df))
    metrics_col2.metric("Unique Asset Types", df["asset_type"].nunique())
    metrics_col3.metric("Estimated Monthly Cost", f"${cost_summary['estimated_monthly_cost']:.2f}")
    metrics_col4.metric("Estimated Daily Spend", f"${cost_summary['daily_spending']:.2f}")

    st.caption("Resources are refreshed from Cloud Asset Inventory on demand, then analyzed locally for cost, search, and recommendations.")

    tab_overview, tab_cost, tab_search, tab_ownership, tab_monitoring, tab_assistant = st.tabs(
        ["Overview", "Cost Analytics", "Natural Language Search", "Ownership", "Monitoring", "AI Assistant"]
    )

    with tab_overview:
        overview_col1, overview_col2, overview_col3 = st.columns(3)
        overview_col1.metric("Unique Projects", df["project"].nunique())
        overview_col2.metric("Likely Running", len(filter_likely_running(resources)))
        overview_top_service = df.groupby("asset_class")["estimated_monthly_cost"].sum().sort_values(ascending=False)
        overview_col3.metric("Top Service", overview_top_service.index[0] if not overview_top_service.empty else "None")

        st.subheader("Live Resource List")
        st.dataframe(df, use_container_width=True, hide_index=True)

        csv_data = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name="gcp_live_resources.csv",
            mime="text/csv",
        )

    with tab_cost:
        st.subheader("Cloud Cost Analytics")
        st.caption("This is an estimated view built from live inventory. For exact billing, connect Cloud Billing export later.")

        service_costs = df.groupby("asset_class", as_index=False)["estimated_monthly_cost"].sum().sort_values("estimated_monthly_cost", ascending=False)
        location_costs = df.groupby("location", as_index=False)["estimated_monthly_cost"].sum().sort_values("estimated_monthly_cost", ascending=False)

        pie_left, pie_right = st.columns(2)
        with pie_left:
            st.markdown("#### Service-wise spending")
            if not service_costs.empty:
                fig = px.pie(service_costs, names="asset_class", values="estimated_monthly_cost", hole=0.42)
                st.plotly_chart(fig, use_container_width=True)
        with pie_right:
            st.markdown("#### Location-wise spending")
            if not location_costs.empty:
                fig = px.bar(location_costs.head(10), x="location", y="estimated_monthly_cost")
                st.plotly_chart(fig, use_container_width=True)

        trend_frame = df.copy()
        trend_frame["created_at_dt"] = pd.to_datetime(trend_frame["created_at"], errors="coerce")
        trend_frame = trend_frame.dropna(subset=["created_at_dt"])
        if not trend_frame.empty:
            trend_frame["create_day"] = trend_frame["created_at_dt"].dt.date
            trend = trend_frame.groupby("create_day", as_index=False)["estimated_monthly_cost"].sum()
            st.markdown("#### Estimated cost trend by resource creation date")
            st.line_chart(trend.set_index("create_day"))

        st.markdown("#### Most expensive resources")
        st.dataframe(
            df.sort_values("estimated_monthly_cost", ascending=False)[["display_name", "asset_class", "location", "estimated_monthly_cost", "owner_hint"]].head(10),
            use_container_width=True,
            hide_index=True,
        )

    with tab_search:
        st.subheader("Natural Language Search")
        st.caption('Try phrases like "Show all VMs in us-central1", "Find expensive databases", or "Show production servers".')
        nl_query = st.text_input("Search in plain English", value="")
        filtered = filter_resources_by_nl_query(resources, nl_query) if nl_query else resources
        st.metric("Matched Resources", len(filtered))
        if nl_query:
            st.dataframe(pd.DataFrame(filtered), use_container_width=True, hide_index=True)
        else:
            st.info("Type a question above to convert it into filters automatically.")

    with tab_ownership:
        st.subheader("Resource Ownership Tracking")
        ownership_frame = df[["display_name", "asset_type", "owner_hint", "created_at", "updated_at", "labels", "tags"]].copy()
        st.dataframe(ownership_frame.sort_values("created_at", ascending=False), use_container_width=True, hide_index=True)
        unlabeled = df[df["owner_hint"].eq("Unknown")]
        if not unlabeled.empty:
            st.warning(f"{len(unlabeled)} resources do not show an obvious owner label or tag yet.")

    with tab_monitoring:
        st.subheader("Real-Time Monitoring")
        st.caption("This panel loads live CPU, network, and disk graphs for Compute Engine instances when Cloud Monitoring API is available.")
        compute_candidates = [row for row in resources if str(row.get("asset_type") or "") == "compute.googleapis.com/Instance"]
        if not compute_candidates:
            st.info("No Compute Engine instances found in the current result set.")
        else:
            label_map = {f"{row.get('display_name') or row.get('name')} ({row.get('location')})": row for row in compute_candidates}
            selected_label = st.selectbox("Choose a VM", list(label_map.keys()))
            selected = label_map[selected_label]
            selected_info = parse_compute_instance_resource(selected)

            if not selected_info:
                st.warning("Could not parse the VM resource name into project/zone/instance. Monitoring graphs are skipped.")
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
                            "CPU Utilization": "compute.googleapis.com/instance/cpu/utilization",
                            "Network Sent": "compute.googleapis.com/instance/network/sent_bytes_count",
                            "Network Received": "compute.googleapis.com/instance/network/received_bytes_count",
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

    with tab_assistant:
        st.subheader("AI Cloud Assistant")
        st.caption("Ask about expensive resources, risky services, unused items, or how to reduce cost. This assistant uses live dashboard data and rule-based recommendations.")

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
                answer = "I can summarize expensive resources, recommend cost optimizations, find idle items, and help search by region or service."
                if recommendations:
                    answer += " Current recommendations: " + " ".join(recommendations[:3])

            st.session_state.assistant_messages.append({"role": "user", "content": prompt})
            st.session_state.assistant_messages.append({"role": "assistant", "content": answer})
            st.rerun()
