from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.cloud import asset_v1
from google.oauth2 import service_account
from google.protobuf.json_format import MessageToDict


class GCPDashboardError(Exception):
    """Raised for predictable dashboard failures with user-friendly text."""


COMPUTE_INSTANCE_RE = re.compile(
    r"projects/(?P<project>[^/]+)/zones/(?P<zone>[^/]+)/instances/(?P<instance>[^/]+)"
)

BASE_MONTHLY_COSTS = {
    "compute.googleapis.com/Instance": 96.0,
    "sqladmin.googleapis.com/Instance": 80.0,
    "container.googleapis.com/Cluster": 180.0,
    "container.googleapis.com/NodePool": 120.0,
    "run.googleapis.com/Service": 24.0,
    "redis.googleapis.com/Instance": 60.0,
    "dataproc.googleapis.com/Cluster": 140.0,
    "cloudfunctions.googleapis.com/CloudFunction": 18.0,
    "storage.googleapis.com/Bucket": 8.0,
    "pubsub.googleapis.com/Topic": 4.0,
}

ASSISTANT_RULES = [
    (r"\bvm\b|virtual machine|instance|compute engine|compute", "compute.googleapis.com/Instance"),
    (r"database|sql|cloud sql|cloud-sql", "sqladmin.googleapis.com/Instance"),
    (r"gke|kubernetes|cluster|k8s", "container.googleapis.com/Cluster"),
    (r"cloud run|serverless|run", "run.googleapis.com/Service"),
    (r"redis|memorystore|cache", "redis.googleapis.com/Instance"),
    (r"bucket|storage|cloud storage|gcs", "storage.googleapis.com/Bucket"),
    (r"disk|persistent disk|pd|volume", "compute.googleapis.com/Disk"),
    (r"function|cloud function|cloud-function|cf", "cloudfunctions.googleapis.com/CloudFunction"),
    (r"topic|pub/sub|pubsub|messaging", "pubsub.googleapis.com/Topic"),
    (r"subscription|sub", "pubsub.googleapis.com/Subscription"),
    (r"vpc|network|virtual network", "compute.googleapis.com/Network"),
    (r"firewall|security rule", "compute.googleapis.com/Firewall"),
    (r"load balancer|lb|health check", "compute.googleapis.com/ForwardingRule"),
    (r"dataproc|hadoop|spark", "dataproc.googleapis.com/Cluster"),
    (r"composer|orchestration|airflow", "composer.googleapis.com/Environment"),
    (r"app engine|appengine|gae", "appengine.googleapis.com/Application"),
    (r"cloud monitoring|monitoring", "monitoring.googleapis.com"),
    (r"cloud logging|logging", "logging.googleapis.com"),
]


def load_credentials_from_json(json_text: str) -> tuple[service_account.Credentials, str | None, str | None]:
    """Build service account credentials from uploaded JSON content."""
    try:
        info = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise GCPDashboardError(f"Invalid JSON file: {exc.msg}") from exc

    required_keys = {"client_email", "private_key", "project_id", "type"}
    missing = sorted(required_keys - set(info.keys()))
    if missing:
        raise GCPDashboardError(
            "Service account JSON is missing required fields: " + ", ".join(missing)
        )

    if info.get("type") != "service_account":
        raise GCPDashboardError("Uploaded JSON is not a service account key file.")

    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    return credentials, info.get("project_id"), info.get("client_email")


def _to_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except Exception:
        return {}


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _format_timestamp(value: str | None) -> str:
    parsed = _parse_iso8601(value)
    if parsed is None:
        return value or ""
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _classify_asset_type(asset_type: str) -> str:
    lowered = asset_type.lower()
    if "compute.googleapis.com/instance" in lowered:
        return "Compute Engine"
    if "sqladmin.googleapis.com/instance" in lowered:
        return "Cloud SQL"
    if "container.googleapis.com/cluster" in lowered or "container.googleapis.com/nodepool" in lowered:
        return "Kubernetes / GKE"
    if "run.googleapis.com/service" in lowered:
        return "Cloud Run"
    if "redis.googleapis.com/instance" in lowered:
        return "Memorystore / Redis"
    if "dataproc.googleapis.com/cluster" in lowered:
        return "Dataproc"
    if "storage.googleapis.com/bucket" in lowered:
        return "Cloud Storage"
    return "Other"


def _friendly_resource_name(asset_type: str) -> str:
    lowered = asset_type.lower()
    if "compute.googleapis.com/instance" in lowered:
        return "VM Instance"
    if "compute.googleapis.com/disk" in lowered:
        return "Persistent Disk"
    if "compute.googleapis.com/network" in lowered:
        return "VPC Network"
    if "compute.googleapis.com/firewall" in lowered:
        return "Firewall Rule"
    if "compute.googleapis.com/forwardingrule" in lowered:
        return "Load Balancer Rule"
    if "storage.googleapis.com/bucket" in lowered:
        return "Storage Bucket"
    if "container.googleapis.com/cluster" in lowered:
        return "Kubernetes Engine"
    if "container.googleapis.com/nodepool" in lowered:
        return "Kubernetes Node Pool"
    if "sqladmin.googleapis.com/instance" in lowered:
        return "Database"
    if "spanner.googleapis.com/instance" in lowered:
        return "Spanner Database"
    if "redis.googleapis.com/instance" in lowered:
        return "Redis Cache"
    if "run.googleapis.com/service" in lowered:
        return "Cloud Run Service"
    if "iam.googleapis.com/serviceaccount" in lowered:
        return "Service Account"
    if "iam.googleapis.com/role" in lowered:
        return "IAM Role"
    if "cloudkms.googleapis.com/keyring" in lowered:
        return "KMS Key Ring"
    if "cloudkms.googleapis.com/cryptokey" in lowered:
        return "KMS Crypto Key"
    return "Other Resource"


def estimate_monthly_cost(resource: dict[str, Any]) -> float:
    asset_type = resource.get("asset_type") or ""
    base_cost = BASE_MONTHLY_COSTS.get(asset_type, 0.0)
    labels = _to_dict(resource.get("labels"))
    additional = _to_dict(resource.get("additional_attributes"))
    location = str(resource.get("location") or "").lower()

    multiplier = 1.0
    if location.startswith("us-") or location.startswith("northamerica-"):
        multiplier *= 1.0
    elif location.startswith("europe-") or location.startswith("asia-"):
        multiplier *= 1.08

    if asset_type == "compute.googleapis.com/Instance":
        machine_type = str(additional.get("machine_type") or labels.get("machine_type") or "").lower()
        if any(size in machine_type for size in ["e2-standard-2", "n2-standard-2", "standard-2"]):
            multiplier *= 1.6
        elif any(size in machine_type for size in ["e2-standard-4", "n2-standard-4", "standard-4"]):
            multiplier *= 2.8
        elif any(size in machine_type for size in ["e2-standard-8", "n2-standard-8", "standard-8"]):
            multiplier *= 5.0

    if resource.get("state") and str(resource.get("state")).upper() != "RUNNING":
        multiplier *= 0.35

    if str(labels.get("env", "")).lower() in {"prod", "production"}:
        multiplier *= 1.12

    return round(base_cost * multiplier, 2)


def extract_owner_hint(resource: dict[str, Any]) -> str:
    labels = _to_dict(resource.get("labels"))
    tags = _to_dict(resource.get("tags"))
    candidates = [
        labels.get("owner"),
        labels.get("team"),
        labels.get("service"),
        labels.get("app"),
        tags.get("owner"),
        tags.get("team"),
        tags.get("service"),
        tags.get("app"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    parent = resource.get("parent_full_resource_name") or resource.get("parent") or ""
    if parent:
        return str(parent).split("/")[-1]
    return "Unknown"


def normalize_resource(resource: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(resource)
    normalized["asset_class"] = _classify_asset_type(str(resource.get("asset_type") or ""))
    normalized["resource_name"] = _friendly_resource_name(str(resource.get("asset_type") or ""))
    normalized["estimated_monthly_cost"] = estimate_monthly_cost(resource)
    normalized["owner_hint"] = extract_owner_hint(resource)
    normalized["created_at"] = _format_timestamp(str(resource.get("create_time") or ""))
    normalized["updated_at"] = _format_timestamp(str(resource.get("update_time") or ""))
    normalized["labels"] = _to_dict(resource.get("labels"))
    normalized["tags"] = _to_dict(resource.get("tags"))
    normalized["additional_attributes"] = _to_dict(resource.get("additional_attributes"))
    normalized["query_text"] = " ".join(
        [
            str(normalized.get("name") or ""),
            str(normalized.get("display_name") or ""),
            str(normalized.get("asset_type") or ""),
            str(normalized.get("location") or ""),
            str(normalized.get("owner_hint") or ""),
            str(normalized.get("labels") or ""),
        ]
    ).lower()
    return normalized


def enrich_bucket_metadata(resource: dict[str, Any], credentials: service_account.Credentials) -> dict[str, Any]:
    """Fetch real Cloud Storage bucket metadata to get accurate modification times."""
    asset_type = str(resource.get("asset_type") or "")
    if "storage.googleapis.com/bucket" not in asset_type.lower():
        return resource

    bucket_name = str(resource.get("display_name") or resource.get("name") or "").split("/")[-1]
    if not bucket_name:
        return resource

    try:
        session = AuthorizedSession(credentials)
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket_name}"
        response = session.get(url, timeout=10)
        if response.status_code == 200:
            bucket_meta = response.json()
            updated_time = bucket_meta.get("updated") or bucket_meta.get("timeCreated")
            if updated_time:
                resource["updated_at"] = _format_timestamp(updated_time)
    except Exception:
        pass

    return resource



def fetch_live_resources(
    credentials: service_account.Credentials,
    scope: str,
    query: str = "",
    page_size: int = 200,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Fetch resources visible to the provided service account from Cloud Asset API."""
    if not scope.startswith(("projects/", "folders/", "organizations/")):
        raise GCPDashboardError(
            "Scope must start with projects/, folders/, or organizations/."
        )

    client = asset_v1.AssetServiceClient(credentials=credentials)
    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        query=query.strip(),
        page_size=page_size,
    )

    resources: list[dict[str, Any]] = []
    try:
        pager = client.search_all_resources(request=request)
        for item in pager:
            resources.append(
                normalize_resource(
                    MessageToDict(
                        item._pb,
                        preserving_proto_field_name=True,
                        use_integers_for_enums=False,
                    )
                )
            )
            if len(resources) >= limit:
                break
    except Exception as exc:  # pragma: no cover - cloud error mapping
        raise GCPDashboardError(
            "Could not fetch resources. Check API enablement and IAM permissions. "
            f"Details: {exc}"
        ) from exc

    # Enrich bucket resources with real Cloud Storage metadata for accurate update times
    resources = [
        enrich_bucket_metadata(resource, credentials) if "storage.googleapis.com/bucket" in str(resource.get("asset_type") or "").lower() else resource
        for resource in resources
    ]

    return resources


def filter_likely_running(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return active resources while keeping stateless assets like buckets visible."""
    excluded_states = {
        "READY",
        "STOPPED",
        "TERMINATED",
        "SUSPENDED",
        "PAUSED",
        "DELETED",
    }

    filtered: list[dict[str, Any]] = []
    for resource in resources:
        state = (resource.get("state") or "").upper().strip()
        asset_type = str(resource.get("asset_type") or "")

        if state in excluded_states:
            continue
        if state == "RUNNING":
            filtered.append(resource)
            continue

        # Keep stateless resources (e.g., buckets, IAM, networks) even when state is empty.
        if not state and asset_type != "compute.googleapis.com/Instance":
            filtered.append(resource)

    return filtered


def filter_resources_by_nl_query(resources: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
    """Apply a simple natural-language filter to the in-memory resource list."""
    text = (prompt or "").strip().lower()
    if not text:
        return resources

    filtered = list(resources)
    matched_rule = False
    for pattern, asset_type in ASSISTANT_RULES:
        if re.search(pattern, text):
            if asset_type == "monitoring.googleapis.com" or asset_type == "logging.googleapis.com":
                filtered = [row for row in filtered if asset_type in str(row.get("asset_type") or "").lower()]
            else:
                filtered = [row for row in filtered if asset_type.lower() in str(row.get("asset_type") or "").lower()]
            matched_rule = True
            break

    region_match = re.search(r"(?:in|from|region)\s+([a-z0-9-]+)", text)
    if region_match:
        region = region_match.group(1)
        filtered = [row for row in filtered if region in str(row.get("location") or "").lower()]

    if any(word in text for word in ["production", "prod"]):
        filtered = [row for row in filtered if "prod" in str(row.get("query_text") or "") or "production" in str(row.get("query_text") or "")]

    if any(word in text for word in ["expensive", "costly", "highest cost"]):
        filtered = sorted(filtered, key=lambda row: float(row.get("estimated_monthly_cost") or 0), reverse=True)

    if any(word in text for word in ["unused", "idle"]):
        filtered = [row for row in filtered if str(row.get("state") or "").upper() != "RUNNING"]

    return filtered


def summarize_costs(resources: list[dict[str, Any]]) -> dict[str, Any]:
    by_service: dict[str, float] = defaultdict(float)
    by_location: dict[str, float] = defaultdict(float)
    for resource in resources:
        by_service[str(resource.get("asset_class") or "Other")] += float(resource.get("estimated_monthly_cost") or 0)
        by_location[str(resource.get("location") or "Global")] += float(resource.get("estimated_monthly_cost") or 0)

    sorted_resources = sorted(resources, key=lambda row: float(row.get("estimated_monthly_cost") or 0), reverse=True)
    return {
        "estimated_monthly_cost": round(sum(float(row.get("estimated_monthly_cost") or 0) for row in resources), 2),
        "daily_spending": round(sum(float(row.get("estimated_monthly_cost") or 0) for row in resources) / 30.0, 2),
        "by_service": dict(sorted(by_service.items(), key=lambda item: item[1], reverse=True)),
        "by_location": dict(sorted(by_location.items(), key=lambda item: item[1], reverse=True)),
        "expensive_resources": sorted_resources[:10],
    }


def parse_compute_instance_resource(resource: dict[str, Any]) -> dict[str, str] | None:
    candidate = str(resource.get("name") or "")
    match = COMPUTE_INSTANCE_RE.search(candidate)
    if not match:
        candidate = str(resource.get("parent_full_resource_name") or "") + "/" + str(resource.get("display_name") or "")
        match = COMPUTE_INSTANCE_RE.search(candidate)
    if not match:
        return None
    return match.groupdict()


def fetch_compute_instance_details(credentials: service_account.Credentials, project: str, zone: str, instance: str) -> dict[str, Any]:
    session = AuthorizedSession(credentials)
    url = f"https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{instance}"
    response = session.get(url, timeout=30)
    if response.status_code >= 400:
        raise GCPDashboardError(
            f"Could not read Compute Engine details for {instance}. {response.status_code}: {response.text}"
        )
    return response.json()


def fetch_monitoring_time_series(
    credentials: service_account.Credentials,
    project: str,
    instance_id: str,
    zone: str,
    metric_type: str,
    seconds: int = 3600,
    aligner: str | None = None,
    reducer: str | None = None,
    group_by_fields: list[str] | None = None,
    alignment_period_seconds: int = 60,
    extra_filter: str | None = None,
) -> list[dict[str, Any]]:
    session = AuthorizedSession(credentials)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(seconds=seconds)
    filter_text = (
        f'metric.type = "{metric_type}" AND resource.type = "gce_instance" '
        f'AND resource.label."instance_id" = "{instance_id}" '
        f'AND resource.label."zone" = "{zone}"'
    )
    if extra_filter:
        filter_text = f"{filter_text} AND {extra_filter}"
    url = f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
    params = {
        "filter": filter_text,
        "interval.startTime": start_time.isoformat().replace("+00:00", "Z"),
        "interval.endTime": end_time.isoformat().replace("+00:00", "Z"),
        "view": "FULL",
    }
    if aligner:
        params["aggregation.alignmentPeriod"] = f"{max(60, alignment_period_seconds)}s"
        params["aggregation.perSeriesAligner"] = aligner
    if reducer:
        params["aggregation.crossSeriesReducer"] = reducer
        params["aggregation.groupByFields"] = group_by_fields or ["resource.label.instance_id"]
    response = session.get(url, params=params, timeout=30)
    if response.status_code >= 400:
        raise GCPDashboardError(
            f"Could not read Cloud Monitoring data for {metric_type}. {response.status_code}: {response.text}"
        )
    payload = response.json()
    return payload.get("timeSeries", [])


def flatten_time_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in series:
        metric = item.get("metric", {})
        resource = item.get("resource", {})
        for point in item.get("points", []):
            value = point.get("value", {})
            point_time = point.get("interval", {}).get("endTime") or point.get("interval", {}).get("startTime")
            numeric = None
            for key in ("doubleValue", "int64Value"):
                if key in value:
                    numeric = value.get(key)
                    break
            rows.append(
                {
                    "metric": metric.get("type", ""),
                    "resource": resource.get("labels", {}),
                    "time": point_time,
                    "value": numeric,
                }
            )
    rows.sort(key=lambda row: str(row.get("time") or ""))
    return rows
