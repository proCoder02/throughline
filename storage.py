"""Cloudflare R2 object storage -- profile pictures + chat/DM attachments.

Gated behind R2_STORAGE_ENABLED in .env, same pattern as
commerce/swiggy_adapter.py's SWIGGY_MCP_ENABLED: when the flag is off (the
default -- no real R2 credentials exist until an operator sets them up),
every /uploads/* and /profile/picture route 404s via
storage_feature_check() rather than raising on a missing bucket/credentials,
and boto3 itself is only imported lazily inside _client() so a machine
without it installed (or without R2 configured at all) never fails to start
the app over this file. See MEDIA_STORAGE_PLAN.md for the full design.
"""
import os
import uuid

from flask import jsonify


def is_enabled() -> bool:
    return os.getenv("R2_STORAGE_ENABLED", "false").strip().lower() == "true"


def storage_feature_check():
    """Call at the top of every route in this feature; returns a (response,
    status) tuple to return immediately if the feature is off, else None."""
    if not is_enabled():
        return jsonify({"error": "Object storage is not enabled."}), 404
    return None


class StorageError(Exception):
    pass


def _client():
    import boto3  # lazy: only ever imported once the feature is actually enabled

    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


# purpose -> key prefix + allowed content-types, so /uploads/presign can't be
# used to stage an arbitrary file type under a prefix it doesn't belong to.
# chat-media/* (not profile-pictures/) is the prefix an R2 Object Lifecycle
# Rule should target for auto-expiry -- see MEDIA_STORAGE_PLAN.md step 4.
_PURPOSES = {
    "profile_picture": {"prefix": "profile-pictures", "types": {"image/jpeg", "image/png", "image/webp"}},
    "chat_image": {"prefix": "chat-media/images", "types": {"image/jpeg", "image/png", "image/webp", "image/gif"}},
    "chat_video": {"prefix": "chat-media/videos", "types": {"video/mp4", "video/quicktime", "video/webm"}},
    "chat_file": {"prefix": "chat-media/files", "types": None},  # None = any type allowed
}

# Hard server-side ceilings regardless of what the client claims -- enforced
# in confirm_upload() via a HEAD request against the real uploaded object,
# since (per MEDIA_STORAGE_PLAN.md's flagged caveat) a presigned PUT url's
# ContentLengthRange is NOT actually enforced by S3-compatible storage the
# way a presigned POST policy would be. An oversized object that slips
# through is deleted immediately rather than kept and just rejected.
MAX_BYTES = {
    "profile_picture": 8 * 1024 * 1024,
    "chat_image": 15 * 1024 * 1024,
    "chat_video": 200 * 1024 * 1024,
    "chat_file": 50 * 1024 * 1024,
}

MAX_THUMBNAIL_DATA_URL_CHARS = 60_000  # ~45KB decoded -- generous over the ~10-20KB a real 160px JPEG thumbnail should be


def presign_upload(purpose: str, content_type: str, user_id: int):
    if purpose not in _PURPOSES:
        raise StorageError(f"Unknown purpose: {purpose}")
    spec = _PURPOSES[purpose]
    if spec["types"] is not None and content_type not in spec["types"]:
        raise StorageError(f"content_type {content_type!r} not allowed for purpose {purpose!r}")

    ext = content_type.split("/")[-1].replace("quicktime", "mov")
    key = f"{spec['prefix']}/{user_id}/{uuid.uuid4().hex}.{ext}"
    url = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": os.environ["R2_BUCKET_NAME"], "Key": key, "ContentType": content_type},
        ExpiresIn=600,  # 10 minutes to actually perform the PUT
    )
    return key, url


def confirm_upload(object_key: str, purpose: str):
    """HEADs the object to confirm it was actually uploaded and enforce the
    real size ceiling server-side. Deletes + raises if it's over MAX_BYTES
    for this purpose (a client can request any content-length it wants
    since the presigned URL itself doesn't cap it -- this is the actual
    enforcement point)."""
    if purpose not in _PURPOSES:
        raise StorageError(f"Unknown purpose: {purpose}")
    head = _client().head_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=object_key)
    size = head["ContentLength"]
    if size > MAX_BYTES[purpose]:
        delete_object(object_key)
        raise StorageError(f"Upload exceeds the {MAX_BYTES[purpose]} byte limit for {purpose}")
    return public_url(object_key)


def public_url(object_key: str) -> str:
    base = os.environ["R2_PUBLIC_URL_BASE"].rstrip("/")
    return f"{base}/{object_key}"


DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024  # cap for in-memory reads (e.g. attachment-summary text extraction)


def download_object(object_key: str) -> bytes | None:
    """Fetches an object's bytes for server-side processing (currently: the
    Cognitive Sharing attachment-summary feature reading a shared document's
    content -- see app.py's _generate_attachment_summary). Returns None
    rather than raising on any failure (missing object, oversized, network
    error) since every caller treats a summary as best-effort, never
    required for the send itself to succeed. Size is checked via HEAD
    first so an unexpectedly huge object is never pulled into memory."""
    try:
        client = _client()
        bucket = os.environ["R2_BUCKET_NAME"]
        head = client.head_object(Bucket=bucket, Key=object_key)
        if head["ContentLength"] > DOWNLOAD_MAX_BYTES:
            return None
        obj = client.get_object(Bucket=bucket, Key=object_key)
        return obj["Body"].read()
    except Exception:
        return None


def delete_object(object_key: str):
    try:
        _client().delete_object(Bucket=os.environ["R2_BUCKET_NAME"], Key=object_key)
    except Exception:
        # Best-effort cleanup (e.g. replacing a profile picture) -- a failed
        # delete of the OLD object should never block saving the new one.
        pass


def key_from_public_url(url: str) -> str | None:
    """Reverses public_url() -- used to find the R2 key for an existing
    users.profile_picture_url so it can be deleted when replaced."""
    if not url:
        return None
    base = os.environ.get("R2_PUBLIC_URL_BASE", "").rstrip("/")
    if base and url.startswith(base + "/"):
        return url[len(base) + 1:]
    return None
