"""AWS cloud resource fetching and monitoring service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class AWSServiceError(Exception):
    """Custom exception for AWS service errors."""

    pass


def load_credentials_from_dict(creds_dict: dict[str, str]) -> tuple[Any, str]:
    """
    Load AWS credentials from dictionary.
    
    Args:
        creds_dict: Dict with 'access_key_id' and 'secret_access_key'
    
    Returns:
        Tuple of (session, account_id)
    """
    try:
        access_key = creds_dict.get("access_key_id", "").strip()
        secret_key = creds_dict.get("secret_access_key", "").strip()

        if not access_key or not secret_key:
            raise AWSServiceError("Missing access_key_id or secret_access_key.")

        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

        # Verify credentials and get account ID
        sts_client = session.client("sts", region_name="us-east-1")
        account_id = sts_client.get_caller_identity()["Account"]

        return session, account_id
    except (KeyError, ValueError) as e:
        raise AWSServiceError(f"Invalid credentials format: {str(e)}")
    except NoCredentialsError:
        raise AWSServiceError("AWS credentials not found or invalid.")
    except ClientError as e:
        raise AWSServiceError(f"Failed to verify credentials: {str(e)}")


def fetch_ec2_instances(session: boto3.Session, regions: list[str] | None = None) -> list[dict[str, object]]:
    """
    Fetch EC2 instances from specified regions.
    
    Args:
        session: Boto3 session object
        regions: List of AWS regions to check (None = all regions)
    
    Returns:
        List of EC2 instance resource dicts
    """
    if regions is None:
        ec2_client = session.client("ec2", region_name="us-east-1")
        try:
            region_response = ec2_client.describe_regions()
            regions = [r["RegionName"] for r in region_response["Regions"]]
        except ClientError as e:
            raise AWSServiceError(f"Failed to list regions: {str(e)}")

    resources = []
    for region in regions:
        try:
            ec2_client = session.client("ec2", region_name=region)
            response = ec2_client.describe_instances()

            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    resources.append(
                        {
                            "id": instance.get("InstanceId", "Unknown"),
                            "name": next(
                                (tag["Value"] for tag in instance.get("Tags", []) if tag["Key"] == "Name"),
                                instance.get("InstanceId", "Unnamed"),
                            ),
                            "asset_type": "aws.ec2.Instance",
                            "state": instance.get("State", {}).get("Name", "Unknown"),
                            "instance_type": instance.get("InstanceType", "Unknown"),
                            "location": region,
                            "launch_time": instance.get("LaunchTime").isoformat() if instance.get("LaunchTime") else None,
                            "resource_class": "Compute",
                            "provider": "AWS",
                        }
                    )
        except ClientError as e:
            print(f"Error fetching EC2 instances in {region}: {str(e)}")
            continue

    return resources


def fetch_rds_databases(session: boto3.Session, regions: list[str] | None = None) -> list[dict[str, object]]:
    """
    Fetch RDS database instances.
    
    Args:
        session: Boto3 session object
        regions: List of AWS regions to check (None = all regions)
    
    Returns:
        List of RDS database resource dicts
    """
    if regions is None:
        ec2_client = session.client("ec2", region_name="us-east-1")
        try:
            region_response = ec2_client.describe_regions()
            regions = [r["RegionName"] for r in region_response["Regions"]]
        except ClientError as e:
            raise AWSServiceError(f"Failed to list regions: {str(e)}")

    resources = []
    for region in regions:
        try:
            rds_client = session.client("rds", region_name=region)
            response = rds_client.describe_db_instances()

            for db in response.get("DBInstances", []):
                resources.append(
                    {
                        "id": db.get("DBInstanceIdentifier", "Unknown"),
                        "name": db.get("DBInstanceIdentifier", "Unnamed"),
                        "asset_type": "aws.rds.Database",
                        "state": db.get("DBInstanceStatus", "Unknown"),
                        "engine": db.get("Engine", "Unknown"),
                        "location": region,
                        "create_time": db.get("InstanceCreateTime").isoformat() if db.get("InstanceCreateTime") else None,
                        "resource_class": "Database",
                        "provider": "AWS",
                    }
                )
        except ClientError as e:
            print(f"Error fetching RDS databases in {region}: {str(e)}")
            continue

    return resources


def fetch_s3_buckets(session: boto3.Session) -> list[dict[str, object]]:
    """
    Fetch S3 buckets.
    
    Args:
        session: Boto3 session object
    
    Returns:
        List of S3 bucket resource dicts
    """
    resources = []
    try:
        s3_client = session.client("s3", region_name="us-east-1")
        response = s3_client.list_buckets()

        for bucket in response.get("Buckets", []):
            try:
                location = s3_client.get_bucket_location(Bucket=bucket["Name"])
                region = location.get("LocationConstraint") or "us-east-1"
            except ClientError:
                region = "Unknown"

            resources.append(
                {
                    "id": bucket["Name"],
                    "name": bucket["Name"],
                    "asset_type": "aws.s3.Bucket",
                    "state": "Active",
                    "location": region,
                    "create_time": bucket.get("CreationDate").isoformat() if bucket.get("CreationDate") else None,
                    "resource_class": "Storage",
                    "provider": "AWS",
                }
            )
    except ClientError as e:
        print(f"Error fetching S3 buckets: {str(e)}")

    return resources


def fetch_lambda_functions(session: boto3.Session, regions: list[str] | None = None) -> list[dict[str, object]]:
    """
    Fetch Lambda functions.
    
    Args:
        session: Boto3 session object
        regions: List of AWS regions to check (None = all regions)
    
    Returns:
        List of Lambda function resource dicts
    """
    if regions is None:
        ec2_client = session.client("ec2", region_name="us-east-1")
        try:
            region_response = ec2_client.describe_regions()
            regions = [r["RegionName"] for r in region_response["Regions"]]
        except ClientError as e:
            raise AWSServiceError(f"Failed to list regions: {str(e)}")

    resources = []
    for region in regions:
        try:
            lambda_client = session.client("lambda", region_name=region)
            response = lambda_client.list_functions()

            for func in response.get("Functions", []):
                resources.append(
                    {
                        "id": func.get("FunctionArn", "Unknown"),
                        "name": func.get("FunctionName", "Unnamed"),
                        "asset_type": "aws.lambda.Function",
                        "state": "Active",
                        "runtime": func.get("Runtime", "Unknown"),
                        "memory": func.get("MemorySize", 0),
                        "location": region,
                        "create_time": datetime.fromtimestamp(func.get("LastModified", 0) / 1000, tz=timezone.utc).isoformat()
                        if func.get("LastModified")
                        else None,
                        "resource_class": "Compute",
                        "provider": "AWS",
                    }
                )
        except ClientError as e:
            print(f"Error fetching Lambda functions in {region}: {str(e)}")
            continue

    return resources


def fetch_all_resources(session: boto3.Session) -> list[dict[str, object]]:
    """
    Fetch all supported AWS resources.
    
    Args:
        session: Boto3 session object
    
    Returns:
        List of all resource dicts
    """
    all_resources = []

    try:
        all_resources.extend(fetch_ec2_instances(session))
    except AWSServiceError as e:
        print(f"Error fetching EC2: {str(e)}")

    try:
        all_resources.extend(fetch_rds_databases(session))
    except AWSServiceError as e:
        print(f"Error fetching RDS: {str(e)}")

    try:
        all_resources.extend(fetch_s3_buckets(session))
    except AWSServiceError as e:
        print(f"Error fetching S3: {str(e)}")

    try:
        all_resources.extend(fetch_lambda_functions(session))
    except AWSServiceError as e:
        print(f"Error fetching Lambda: {str(e)}")

    return all_resources


def get_aws_account_info(session: boto3.Session) -> dict[str, str]:
    """
    Get AWS account information.
    
    Args:
        session: Boto3 session object
    
    Returns:
        Dict with account_id and account_alias
    """
    try:
        sts_client = session.client("sts", region_name="us-east-1")
        identity = sts_client.get_caller_identity()
        account_id = identity.get("Account", "Unknown")

        iam_client = session.client("iam", region_name="us-east-1")
        try:
            aliases = iam_client.list_account_aliases()
            alias = aliases["AccountAliases"][0] if aliases["AccountAliases"] else account_id
        except ClientError:
            alias = account_id

        return {"account_id": account_id, "account_alias": alias}
    except ClientError as e:
        raise AWSServiceError(f"Failed to get account info: {str(e)}")
