"""AWS S3 backend: immutable events + per-record objects + manifest.

Layout under a private bucket/prefix::

    {prefix}manifest.json                       schema version, updated with If-Match
    {prefix}events/{op_id}.json                 immutable, written with If-None-Match: *
    {prefix}records/{type}/{id}.json            current record, conditional on ETag
    {prefix}tombstones/{type}/{id}.json         deletion markers (prevent resurrection)

Rules implemented:
* Never sync a live SQLite file by overwriting one object.
* Conditional writes (If-None-Match / If-Match) for concurrency-sensitive operations;
  412/409 responses become ConflictError so callers re-read and reconcile.
* ETags are treated as opaque concurrency tokens, not as content hashes.
* No multi-object atomicity is assumed: the event is written first (idempotent by
  op_id), then the record; `rebuild()` can replay events.
* Deleting a record writes a tombstone and a delete marker (versioned buckets keep
  prior versions). `purge_versions` removes every version permanently and reports
  what it removed — the two are reported separately.
* Server-side encryption is requested on every write.

Credentials come from the standard AWS chain (env, profile, SSO, instance role).
Lucid never stores AWS credentials.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from .base import ConflictError, OutcomeUnknown, PermanentError, TransientError, Versioned

SCHEMA = 1


def _code(e: Exception) -> tuple[str, int]:
    resp = getattr(e, "response", None) or {}
    err = resp.get("Error", {})
    return str(err.get("Code", "")), int(resp.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0)


class S3Backend:
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "lucid/", client: Any = None, *, sse: str = "AES256",
                 kms_key_id: str | None = None):
        if client is None:
            import boto3  # optional dependency
            client = boto3.client("s3")
        self.c = client
        self.bucket = bucket
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.sse = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key_id} if kms_key_id else \
            {"ServerSideEncryption": sse}

    def capabilities(self) -> dict:
        return {"authoritative": "S3 objects", "multi_object_transactions": False, "conditional_writes": True,
                "versioning": "bucket setting (recommended on)", "offline": "local queue via outbox",
                "deletion": "tombstone + delete marker; purge_versions removes prior versions"}

    # ----------------------------------------------------------------- errors
    def _wrap(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # botocore exceptions without importing botocore
            code, status = _code(e)
            name = type(e).__name__
            if code in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
                raise ConflictError(f"{code or status}") from e
            if code in ("SlowDown", "Throttling", "RequestTimeout", "InternalError", "ServiceUnavailable") or \
                    status in (429, 500, 502, 503, 504) or name in ("EndpointConnectionError", "ConnectTimeoutError"):
                raise TransientError(f"{code or name}") from e
            if name in ("ReadTimeoutError", "ConnectionClosedError", "ResponseStreamingError"):
                raise OutcomeUnknown(f"{name}: request may have been applied") from e
            if code in ("ExpiredToken", "ExpiredTokenException", "InvalidAccessKeyId", "AccessDenied",
                        "NoSuchBucket", "SignatureDoesNotMatch"):
                raise PermanentError(f"{code}: check AWS credentials/permissions for s3://{self.bucket}/{self.prefix}") from e
            raise

    def _key(self, *parts: str) -> str:
        for p in parts:
            if ".." in p or p.startswith("/"):
                raise ValueError("unsafe key component")
        return self.prefix + "/".join(parts)

    def _put(self, key: str, body: dict, **cond) -> str:
        r = self._wrap(self.c.put_object, Bucket=self.bucket, Key=key, ContentType="application/json",
                       Body=json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), **self.sse, **cond)
        return r["ETag"]

    def _get(self, key: str) -> Versioned | None:
        try:
            r = self._wrap(self.c.get_object, Bucket=self.bucket, Key=key)
        except Exception as e:
            code, status = _code(e.__cause__ or e)
            if code in ("NoSuchKey", "404") or status == 404:
                return None
            raise
        return Versioned(json.loads(r["Body"].read()), r["ETag"])

    # ----------------------------------------------------------------- API
    def ensure_manifest(self) -> Versioned:
        m = self._get(self._key("manifest.json"))
        if m:
            if m.body.get("schema") != SCHEMA:
                raise PermanentError(f"remote schema {m.body.get('schema')} != {SCHEMA}; run migration")
            return m
        try:
            etag = self._put(self._key("manifest.json"), {"schema": SCHEMA, "format": "lucid.s3"}, IfNoneMatch="*")
            return Versioned({"schema": SCHEMA, "format": "lucid.s3"}, etag)
        except ConflictError:
            return self._get(self._key("manifest.json"))  # created concurrently

    def append_event(self, op_id: str, event: dict) -> dict:
        key = self._key("events", f"{op_id}.json")
        try:
            etag = self._put(key, {"op_id": op_id, **event}, IfNoneMatch="*")
            return {"key": key, "etag": etag, "created": True}
        except ConflictError:
            existing = self._get(key)
            if existing and existing.body.get("op_id") == op_id:
                return {"key": key, "etag": existing.version, "created": False}  # idempotent replay
            raise

    def lookup_op(self, op_id: str) -> dict | None:
        v = self._get(self._key("events", f"{op_id}.json"))
        return {"key": self._key("events", f"{op_id}.json"), "etag": v.version} if v else None

    def put_record(self, rtype: str, rid: str, body: dict, expected_version: str | None = None,
                   create_only: bool = False) -> str:
        if self._get(self._key("tombstones", rtype, f"{rid}.json")):
            raise ConflictError(f"{rtype}/{rid} is tombstoned; refusing to resurrect")
        cond = {"IfMatch": expected_version} if expected_version else ({"IfNoneMatch": "*"} if create_only else {})
        return self._put(self._key("records", rtype, f"{rid}.json"), body, **cond)

    def get_record(self, rtype: str, rid: str) -> Versioned | None:
        return self._get(self._key("records", rtype, f"{rid}.json"))

    def list_records(self, rtype: str) -> Iterator[str]:
        pfx = self._key("records", rtype) + "/"
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": pfx}
            if token:
                kw["ContinuationToken"] = token
            r = self._wrap(self.c.list_objects_v2, **kw)
            for o in r.get("Contents", []):
                yield o["Key"][len(pfx):-len(".json")]
            if not r.get("IsTruncated"):
                break
            token = r["NextContinuationToken"]

    def delete_record(self, rtype: str, rid: str, reason: str) -> dict:
        self._put(self._key("tombstones", rtype, f"{rid}.json"), {"type": rtype, "id": rid, "reason": reason})
        key = self._key("records", rtype, f"{rid}.json")
        r = self._wrap(self.c.delete_object, Bucket=self.bucket, Key=key)
        return {"key": key, "delete_marker": bool(r.get("DeleteMarker")),
                "note": "prior object versions remain in a versioned bucket until purge_versions is run"}

    def purge_versions(self, rtype: str, rid: str) -> dict:
        key = self._key("records", rtype, f"{rid}.json")
        r = self._wrap(self.c.list_object_versions, Bucket=self.bucket, Prefix=key)
        removed = []
        for v in r.get("Versions", []) + r.get("DeleteMarkers", []):
            if v["Key"] == key:
                self._wrap(self.c.delete_object, Bucket=self.bucket, Key=key, VersionId=v["VersionId"])
                removed.append(v["VersionId"])
        return {"key": key, "versions_removed": removed}

    def tombstoned(self, rtype: str) -> set[str]:
        pfx = self._key("tombstones", rtype) + "/"
        r = self._wrap(self.c.list_objects_v2, Bucket=self.bucket, Prefix=pfx)
        return {o["Key"][len(pfx):-len(".json")] for o in r.get("Contents", [])}
