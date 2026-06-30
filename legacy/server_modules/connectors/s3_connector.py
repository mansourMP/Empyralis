from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import boto3 as _boto3
except Exception:  # pragma: no cover - optional dependency
    _boto3 = None


def _credentials(credentials: Dict[str, Any]) -> Dict[str, Any]:
    access_key_id = str(
        credentials.get("aws_access_key_id")
        or credentials.get("access_key_id")
        or credentials.get("access_key")
        or ""
    ).strip()
    secret_access_key = str(
        credentials.get("aws_secret_access_key")
        or credentials.get("secret_access_key")
        or credentials.get("secret_key")
        or ""
    ).strip()
    region = str(
        credentials.get("region")
        or credentials.get("aws_region")
        or credentials.get("region_name")
        or ""
    ).strip() or "us-east-1"
    session_token = str(credentials.get("aws_session_token") or credentials.get("session_token") or "").strip()
    endpoint_url = str(credentials.get("endpoint_url") or "").strip()
    if not access_key_id:
        raise RuntimeError("AWS access key id is required.")
    if not secret_access_key:
        raise RuntimeError("AWS secret access key is required.")
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
        "aws_session_token": session_token or None,
        "region_name": region,
        "endpoint_url": endpoint_url or None,
    }


def _client(
    credentials: Dict[str, Any],
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> Any:
    if client is not None:
        return client
    module = boto3_module or _boto3
    if module is None:
        raise RuntimeError("boto3 is not installed. Add the 'boto3' package to use this connector.")
    config = _credentials(credentials)
    return module.client(
        "s3",
        aws_access_key_id=config["aws_access_key_id"],
        aws_secret_access_key=config["aws_secret_access_key"],
        aws_session_token=config["aws_session_token"],
        region_name=config["region_name"],
        endpoint_url=config["endpoint_url"],
    )


def validate_s3_credentials(
    credentials: Dict[str, Any],
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    buckets = sdk_client.list_buckets()
    bucket_items = buckets.get("Buckets") if isinstance(buckets, dict) else []
    bucket_list = bucket_items if isinstance(bucket_items, list) else []
    config = _credentials(credentials)
    hint = config["aws_access_key_id"][-4:] if config["aws_access_key_id"] else ""
    normalized = dict(credentials)
    normalized["auth_mode"] = "access_key"
    normalized["region"] = config["region_name"]
    if hint:
        normalized["access_key_hint"] = hint
    return {
        "ok": True,
        "status": 200,
        "message": "S3 connector is valid.",
        "auth_mode": "access_key",
        "region": config["region_name"],
        "access_key_hint": hint or None,
        "bucket_count": len(bucket_list),
        "profile": {
            "region": config["region_name"],
            "bucketCount": len(bucket_list),
            "accessKeyHint": hint or None,
        },
        "credentials": normalized,
    }


def list_buckets(
    credentials: Dict[str, Any],
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> List[Dict[str, Any]]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    response = sdk_client.list_buckets()
    buckets = response.get("Buckets") if isinstance(response, dict) else []
    out: List[Dict[str, Any]] = []
    for item in buckets if isinstance(buckets, list) else []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": item.get("Name"),
                "creation_date": item.get("CreationDate"),
            }
        )
    return out


def list_objects(
    credentials: Dict[str, Any],
    bucket: str,
    *,
    prefix: str = "",
    limit: int = 100,
    client: Any = None,
    boto3_module: Any = None,
) -> List[Dict[str, Any]]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    response = sdk_client.list_objects_v2(
        Bucket=str(bucket or "").strip(),
        Prefix=str(prefix or ""),
        MaxKeys=max(1, min(int(limit or 100), 1000)),
    )
    contents = response.get("Contents") if isinstance(response, dict) else []
    out: List[Dict[str, Any]] = []
    for item in contents if isinstance(contents, list) else []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "key": item.get("Key"),
                "size": item.get("Size"),
                "etag": item.get("ETag"),
                "last_modified": item.get("LastModified"),
                "storage_class": item.get("StorageClass"),
            }
        )
    return out


def upload_file(
    credentials: Dict[str, Any],
    local_path: str,
    bucket: str,
    key: str,
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    path = Path(str(local_path or "")).expanduser()
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Local file does not exist: {path}")
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    sdk_client.upload_file(str(path), str(bucket or "").strip(), str(key or "").strip())
    return {
        "ok": True,
        "bucket": str(bucket or "").strip(),
        "key": str(key or "").strip(),
        "local_path": str(path),
    }


def download_file(
    credentials: Dict[str, Any],
    bucket: str,
    key: str,
    local_path: str,
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    target = Path(str(local_path or "")).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    sdk_client.download_file(str(bucket or "").strip(), str(key or "").strip(), str(target))
    return {
        "ok": True,
        "bucket": str(bucket or "").strip(),
        "key": str(key or "").strip(),
        "local_path": str(target),
    }


def delete_object(
    credentials: Dict[str, Any],
    bucket: str,
    key: str,
    *,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    response = sdk_client.delete_object(Bucket=str(bucket or "").strip(), Key=str(key or "").strip())
    return {
        "ok": True,
        "bucket": str(bucket or "").strip(),
        "key": str(key or "").strip(),
        "delete_marker": response.get("DeleteMarker") if isinstance(response, dict) else None,
        "version_id": response.get("VersionId") if isinstance(response, dict) else None,
    }


def get_presigned_url(
    credentials: Dict[str, Any],
    bucket: str,
    key: str,
    *,
    expires_in: int = 3600,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    url = sdk_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": str(bucket or "").strip(), "Key": str(key or "").strip()},
        ExpiresIn=max(1, int(expires_in or 3600)),
    )
    return {
        "ok": True,
        "bucket": str(bucket or "").strip(),
        "key": str(key or "").strip(),
        "expires_in": max(1, int(expires_in or 3600)),
        "url": url,
    }


def create_bucket(
    credentials: Dict[str, Any],
    bucket: str,
    *,
    region: Optional[str] = None,
    client: Any = None,
    boto3_module: Any = None,
) -> Dict[str, Any]:
    sdk_client = _client(credentials, client=client, boto3_module=boto3_module)
    bucket_name = str(bucket or "").strip()
    if not bucket_name:
        raise RuntimeError("Bucket name is required.")
    region_name = str(region or _credentials(credentials)["region_name"] or "").strip() or "us-east-1"
    payload: Dict[str, Any] = {"Bucket": bucket_name}
    if region_name and region_name != "us-east-1":
        payload["CreateBucketConfiguration"] = {"LocationConstraint": region_name}
    sdk_client.create_bucket(**payload)
    return {
        "ok": True,
        "bucket": bucket_name,
        "region": region_name,
    }
