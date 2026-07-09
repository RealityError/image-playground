import base64
import hashlib
import hmac
import json
import mimetypes
import os
import shutil
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from openai import APIConnectionError, APIError, APITimeoutError, DefaultHttpxClient, OpenAI
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile

from db import (
    delete_job,
    delete_owner_jobs,
    fail_running_generations,
    get_admin_dashboard,
    get_admin_job_detail,
    get_admin_overview,
    get_all_runtime_config,
    get_image_record,
    get_input_image,
    get_input_images,
    get_owner_job_detail,
    get_provider_profile_secret,
    get_runtime_config,
    init_db,
    is_job_deleted,
    is_owner_blocked,
    list_admin_gallery_images,
    list_admin_jobs,
    list_admin_owners,
    list_auth_events,
    list_history_images,
    list_provider_profiles,
    log_auth_event,
    log_generation_failed,
    log_generation_finished,
    log_generation_started,
    lookup_owner,
    save_input_image,
    set_owner_block,
    set_owner_label,
    set_runtime_config,
    soft_delete_image,
    soft_delete_job,
    soft_delete_owner_job,
    soft_delete_owner_jobs,
    delete_provider_profile,
    update_image_thumbnail_path,
    upsert_provider_profile,
)
from providers import (
    DEFAULT_PROVIDER_PARAMETERS,
    ProviderProfile,
    build_provider_request,
    provider_from_request_params,
    public_request_params,
    upstream_request_params,
)
from storage_paths import (
    BASE_DIR,
    GENERATED_DIR,
    THUMBNAIL_DIR,
    UPLOAD_DIR,
    resolve_storage_path,
    storage_path_for_db,
)

load_dotenv()

STATIC_DIR = Path(os.environ.get("STATIC_DIR", str(BASE_DIR.parent / "frontend" / "dist")))
LOGS_DIR = BASE_DIR / "logs"

for path in (STATIC_DIR, GENERATED_DIR, THUMBNAIL_DIR, UPLOAD_DIR, LOGS_DIR):
    path.mkdir(parents=True, exist_ok=True)

init_db()
fail_running_generations(completed_at=datetime.now().isoformat(timespec="seconds"), error_message="服务重启，任务已释放")

# Startup validation: ensure required secrets are configured
_REQUIRED_ENV_VARS = ["OWNER_SECRET", "COOKIE_SIGNING_SECRET", "ADMIN_PASSWORD"]
_missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v, "").strip()]
if _missing:
    import sys
    print(f"\n[ERROR] Missing required environment variables: {', '.join(_missing)}")
    print("Please copy .env.example to .env and fill in the values before starting.\n")
    sys.exit(1)

WEB_CLIENT_VERSION = os.getenv("WEB_CLIENT_VERSION", "20260512-playground-2") or "20260512-playground-2"
WEB_SESSION_COOKIE = "gpt_image_web_session"
WEB_OWNER_COOKIE = "gpt_image_owner"
ADMIN_COOKIE = "gpt_image_admin"
WEB_OWNER_TYPE = "passphrase"
WEB_ACTIVE_WINDOW_SECONDS = 90
DEFAULT_USER_CONCURRENCY_LIMIT = 3
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 3600
ADMIN_COOKIE_MAX_AGE_SECONDS = 12 * 3600
PUBLIC_API_VERSION = "v1"


state_lock = threading.Lock()
web_sessions: dict[str, dict[str, Any]] = {}
active_generations: dict[str, dict[str, Any]] = {}
active_counts: dict[str, dict[str, int]] = {"web": {}, "api": {}, "owner": {}}
web_job_states: dict[str, dict[str, Any]] = {}


def parse_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


COOKIE_SECURE = parse_bool_env(os.getenv("COOKIE_SECURE"), False)
BACKGROUND_GENERATION_WORKERS = max(1, int(os.getenv("BACKGROUND_GENERATION_WORKERS", "2")))


def get_user_concurrency_limit() -> int:
    val = get_runtime_config("user_concurrency_limit", "")
    if val:
        try:
            return max(1, int(val))
        except ValueError:
            pass
    return DEFAULT_USER_CONCURRENCY_LIMIT


def get_image_api_timeout() -> float:
    val = get_runtime_config("image_api_timeout", "")
    if val:
        try:
            return max(10, float(val))
        except ValueError:
            pass
    return float(get_env("IMAGE_API_TIMEOUT", "360") or "360")


def get_model_name() -> str:
    val = get_runtime_config("image_model", "")
    if val:
        return val
    return get_env("IMAGE_MODEL", "gpt-image-2") or "gpt-image-2"


def get_default_response_format() -> str | None:
    configured = clean_text(get_env("IMAGE_RESPONSE_FORMAT"))
    if configured:
        return configured
    return None


def build_env_provider_profile() -> ProviderProfile:
    api_key = get_env("IMAGE_API_KEY") or get_env("OPENAI_API_KEY") or ""
    base_url = get_env("IMAGE_API_BASE_URL") or ""
    model = get_model_name()
    return ProviderProfile(
        id="default",
        name="默认上游",
        provider_type="openai-compatible",
        base_url=base_url,
        api_key=api_key,
        enabled=True,
        default_model=model,
        models=[model],
        parameters=dict(DEFAULT_PROVIDER_PARAMETERS),
    )


def resolve_provider_profile(provider_id: str | None = None) -> ProviderProfile:
    normalized_id = clean_text(provider_id)
    if normalized_id:
        row = get_provider_profile_secret(normalized_id)
        if row is None:
            raise HTTPException(status_code=400, detail=f"Provider not found: {normalized_id}.")
        profile = ProviderProfile.from_mapping(row)
    else:
        rows = list_provider_profiles(include_disabled=False, include_secret=True)
        configured_row = next((row for row in rows if row.get("api_key")), None)
        profile = ProviderProfile.from_mapping(configured_row) if configured_row else build_env_provider_profile()

    if not profile.enabled:
        raise HTTPException(status_code=400, detail=f"Provider is disabled: {profile.id}.")
    if not profile.api_key:
        raise HTTPException(status_code=500, detail=f"Provider API key is not configured: {profile.id}.")
    return profile


def public_provider_profiles() -> list[dict[str, Any]]:
    rows = list_provider_profiles(include_disabled=False, include_secret=False)
    if rows:
        return [
            ProviderProfile.from_mapping(row).public_snapshot()
            for row in rows
            if row.get("api_key_configured")
        ]
    env_profile = build_env_provider_profile()
    if not env_profile.api_key:
        return []
    return [env_profile.public_snapshot()]


def get_min_passphrase_length() -> int:
    val = get_runtime_config("min_web_passphrase_length", "")
    if val:
        try:
            return max(4, int(val))
        except ValueError:
            pass
    return max(4, int(get_env("MIN_WEB_PASSPHRASE_LENGTH", "6") or "6"))
generation_executor = ThreadPoolExecutor(max_workers=BACKGROUND_GENERATION_WORKERS)


def web_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Web-Version": WEB_CLIENT_VERSION,
    }


def apply_web_headers(response: Response) -> Response:
    for key, value in web_cache_headers().items():
        response.headers[key] = value
    return response


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=160)
    n: int | None = Field(default=None, ge=1, le=8)
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    output_format: str | None = None
    output_compression: int | None = Field(default=None, ge=0, le=100)
    background: str | None = None
    partial_images: int | None = Field(default=None, ge=0, le=8)
    response_format: str | None = None
    moderation: str | None = None
    style: str | None = None
    user: str | None = None


class UnlockRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=256)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AdminOwnerLookupRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=256)


class AdminOwnerLabelRequest(BaseModel):
    owner_type: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class AdminOwnerBlockRequest(BaseModel):
    owner_type: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)
    blocked: bool
    reason: str | None = Field(default=None, max_length=1000)


class AdminJobDeleteRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)


class AdminJobsSoftDeleteRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)


class AdminImageTarget(BaseModel):
    job_id: str = Field(min_length=1, max_length=128)
    image_index: int = Field(ge=1, le=100)


class AdminImagesSoftDeleteRequest(BaseModel):
    images: list[AdminImageTarget] = Field(min_length=1, max_length=500)
    reason: str | None = Field(default=None, max_length=1000)


class AdminOwnerDeleteRequest(BaseModel):
    owner_type: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class AdminProviderProfileRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(default="openai-compatible", max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)
    clear_api_key: bool = False
    enabled: bool = True
    default_model: str = Field(min_length=1, max_length=160)
    models: list[str] = Field(default_factory=list, max_length=50)
    parameters: dict[str, list[str]] = Field(default_factory=dict)


class AdminOwnerTarget(BaseModel):
    owner_type: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)


class AdminOwnersBlockBatchRequest(BaseModel):
    owners: list[AdminOwnerTarget] = Field(min_length=1, max_length=200)
    blocked: bool
    reason: str | None = Field(default=None, max_length=1000)


class WebJobsDeleteRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class JobDeletedError(RuntimeError):
    pass


app = FastAPI(
    title="image-playground",
    version="0.4.0",
    description="Local image generation and editing service with passphrase-isolated web history and admin console.",
)


@app.middleware("http")
async def web_client_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/web/") and not path.startswith("/web/images/") and not path.startswith("/web/thumbs/") and not path.startswith("/web/input-images/") and not path.startswith("/web/input-mask/"):
        client_version = clean_text(request.headers.get("x-web-version"))
        if client_version != WEB_CLIENT_VERSION:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Web client version mismatch. Refresh required.",
                    "required_version": WEB_CLIENT_VERSION,
                },
                headers=web_cache_headers(),
            )

    response = await call_next(request)
    if path == "/" or path.startswith("/web/") or path == "/admin" or path.startswith("/admin/"):
        apply_web_headers(response)
    return response


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def build_job_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or default


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


SUPPORTED_IMAGE_SIZES = {
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "2048x1152",
    "1152x2048",
    "3840x3840",
    "3840x2160",
    "2160x3840",
}

ASPECT_RATIO_SIZE_MAP = {
    "auto": "auto",
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "16:9": "2048x1152",
    "9:16": "1152x2048",
}


def normalize_size_request(size_value: Any) -> str | None:
    text = clean_text(size_value)
    if text is None:
        return None
    normalized = text.lower()
    if normalized == "auto":
        return normalized
    if "×" in normalized:
        normalized = normalized.replace("×", "x")
    parts = normalized.split("x")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail=f"Unsupported size: {text}.")
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported size: {text}.") from exc
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail=f"Unsupported size: {text}.")
    if width % 16 != 0 or height % 16 != 0:
        raise HTTPException(status_code=400, detail="size width and height must be multiples of 16.")
    if max(width, height) > 3840:
        raise HTTPException(status_code=400, detail="size max edge must be <= 3840.")
    if max(width, height) / min(width, height) > 3:
        raise HTTPException(status_code=400, detail="size aspect ratio must be <= 3:1.")
    pixels = width * height
    if pixels < 655_360 or pixels > 8_294_400:
        raise HTTPException(status_code=400, detail="size total pixels must be between 655360 and 8294400.")
    normalized = f"{width}x{height}"
    return normalized


def resolve_size_request(size_value: Any, aspect_ratio_value: Any) -> str | None:
    size = clean_text(size_value)
    aspect_ratio = clean_text(aspect_ratio_value)
    if size and aspect_ratio:
        raise HTTPException(status_code=400, detail="size and aspect_ratio are mutually exclusive.")
    if aspect_ratio:
        normalized_ratio = aspect_ratio.lower()
        mapped = ASPECT_RATIO_SIZE_MAP.get(normalized_ratio)
        if mapped is None:
            raise HTTPException(status_code=400, detail=f"Unsupported aspect_ratio: {aspect_ratio}.")
        return mapped
    return normalize_size_request(size)


def parse_optional_int(value: Any, field_name: str, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid integer for {field_name}.") from exc
    if minimum is not None and parsed < minimum:
        raise HTTPException(status_code=400, detail=f"{field_name} must be >= {minimum}.")
    if maximum is not None and parsed > maximum:
        raise HTTPException(status_code=400, detail=f"{field_name} must be <= {maximum}.")
    return parsed


def get_owner_secret() -> str:
    value = get_env("OWNER_SECRET")
    if not value:
        raise RuntimeError("OWNER_SECRET is not set. Please configure .env before starting.")
    return value


def get_cookie_signing_secret() -> str:
    value = get_env("COOKIE_SIGNING_SECRET") or get_env("OWNER_SECRET")
    if not value:
        raise RuntimeError("COOKIE_SIGNING_SECRET is not set. Please configure .env before starting.")
    return value


def get_admin_password() -> str:
    value = get_env("ADMIN_PASSWORD")
    if not value:
        raise RuntimeError("ADMIN_PASSWORD is not set. Please configure .env before starting.")
    return value


def get_admin_page_path() -> str:
    path = clean_text(get_env("ADMIN_PAGE_PATH")) or "/admin"
    if not path.startswith("/"):
        path = "/" + path
    return path


def get_client(provider: ProviderProfile | None = None) -> OpenAI:
    active_provider = provider or resolve_provider_profile()
    api_key = active_provider.api_key or get_env("IMAGE_API_KEY") or get_env("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail=f"Provider API key is not configured: {active_provider.id}.")

    timeout = get_image_api_timeout()
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "http_client": DefaultHttpxClient(trust_env=False),
        "timeout": timeout,
        "max_retries": 0,
    }
    if active_provider.base_url:
        kwargs["base_url"] = active_provider.base_url
    return OpenAI(**kwargs)


def describe_image_upstream_error(exc: Exception, provider: ProviderProfile | None = None) -> str:
    base_url = provider.base_url if provider and provider.base_url else get_env("IMAGE_API_BASE_URL") or "https://api.openai.com/v1"
    timeout = get_image_api_timeout()
    if isinstance(exc, APITimeoutError):
        return f"upstream request timed out after {timeout:g}s while connecting to {base_url}"
    if isinstance(exc, APIConnectionError):
        return f"upstream connection failed for {base_url}: {exc}. Check network/proxy/DNS and IMAGE_API_BASE_URL."
    if isinstance(exc, APIError):
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        response_text = ""
        if response is not None:
            try:
                response_text = response.text
            except Exception:
                response_text = ""
        detail = response_text or str(exc)
        if status_code:
            return f"upstream API returned HTTP {status_code}: {detail}"
        return f"upstream API error: {detail}"
    return str(exc)


def get_client_ip(request: Request) -> str:
    for header_name in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        header_value = request.headers.get(header_name)
        if not header_value:
            continue
        return header_value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_or_create_web_session_id(request: Request) -> str:
    session_id = request.cookies.get(WEB_SESSION_COOKIE)
    return session_id or uuid.uuid4().hex


def touch_web_session(session_id: str, ip: str, owner_id: str | None = None) -> None:
    with state_lock:
        if session_id not in web_sessions:
            web_sessions[session_id] = {"ip": ip, "last_seen": time.time(), "owner_id": owner_id}
        else:
            web_sessions[session_id]["ip"] = ip
            web_sessions[session_id]["last_seen"] = time.time()
            if owner_id is not None:
                web_sessions[session_id]["owner_id"] = owner_id


def prune_web_sessions() -> None:
    cutoff = time.time() - WEB_ACTIVE_WINDOW_SECONDS
    expired_ids = [
        session_id
        for session_id, session in web_sessions.items()
        if float(session.get("last_seen", 0)) < cutoff
    ]
    for session_id in expired_ids:
        web_sessions.pop(session_id, None)


def get_live_stats(current_owner_id: str | None = None) -> dict[str, int]:
    with state_lock:
        prune_web_sessions()
        active_identities: set[str] = set()
        for session in web_sessions.values():
            session_owner_id = clean_text(session.get("owner_id"))
            if session_owner_id:
                active_identities.add(f"owner:{session_owner_id}")
                continue
            ip = clean_text(session.get("ip"))
            if ip:
                active_identities.add(f"ip:{ip}")
        for generation in active_generations.values():
            generation_owner_id = clean_text(generation.get("owner_id"))
            if generation_owner_id:
                active_identities.add(f"owner:{generation_owner_id}")
        normalized_owner_id = clean_text(current_owner_id)
        return {
            "active_spaces": len(active_identities),
            "active_users": len(active_identities),
            "active_generations": len(active_generations),
            "owner_active_generations": active_counts["owner"].get(normalized_owner_id, 0) if normalized_owner_id else 0,
            "user_concurrency_limit": get_user_concurrency_limit(),
        }


def get_runtime_status() -> dict[str, Any]:
    with state_lock:
        prune_web_sessions()
        active_identities: set[str] = set()
        for session in web_sessions.values():
            session_owner_id = clean_text(session.get("owner_id"))
            if session_owner_id:
                active_identities.add(f"owner:{session_owner_id}")
                continue
            ip = clean_text(session.get("ip"))
            if ip:
                active_identities.add(f"ip:{ip}")
        for generation in active_generations.values():
            generation_owner_id = clean_text(generation.get("owner_id"))
            if generation_owner_id:
                active_identities.add(f"owner:{generation_owner_id}")
        return {
            "live": {
                "active_users": len(active_identities),
                "active_spaces": len(active_identities),
                "active_generations": len(active_generations),
                "web_sessions": len(web_sessions),
                "web_active_slots": sum(active_counts["web"].values()),
                "api_active_slots": sum(active_counts["api"].values()),
                "owner_active_slots": sum(active_counts["owner"].values()),
            },
            "active_counts": {
                "web": dict(active_counts["web"]),
                "api": dict(active_counts["api"]),
                "owner": dict(active_counts["owner"]),
            },
            "workers": {
                "background_generation_workers": BACKGROUND_GENERATION_WORKERS,
                "web_concurrency_per_session": get_user_concurrency_limit(),
                "api_concurrency_per_ip": get_user_concurrency_limit(),
                "user_concurrency_limit": get_user_concurrency_limit(),
            },
        }


def same_origin_browser_request(request: Request) -> bool:
    host = request.headers.get("host", "")
    if not host:
        return False
    base = f"{request.url.scheme}://{host}"
    origin = (request.headers.get("origin") or "").rstrip("/")
    referer = request.headers.get("referer") or ""
    if origin:
        return origin == base.rstrip("/")
    if referer:
        return referer.startswith(base)
    return False


def require_web_marker(request: Request) -> tuple[str, str]:
    if request.headers.get("x-web-request") != "1":
        raise HTTPException(status_code=403, detail="Missing web request marker.")
    if not same_origin_browser_request(request):
        raise HTTPException(status_code=403, detail="Web endpoint only accepts same-origin browser requests.")
    session_id = get_or_create_web_session_id(request)
    ip = get_client_ip(request)
    touch_web_session(session_id, ip)
    return session_id, ip


def require_admin_marker(request: Request) -> str:
    if request.headers.get("x-admin-request") != "1":
        raise HTTPException(status_code=403, detail="Missing admin request marker.")
    if not same_origin_browser_request(request):
        raise HTTPException(status_code=403, detail="Admin endpoint only accepts same-origin browser requests.")
    return get_client_ip(request)


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign_cookie(kind: str, payload: dict[str, Any]) -> str:
    body = json.dumps({"kind": kind, **payload}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = b64url_encode(body)
    signature = hmac.new(
        get_cookie_signing_secret().encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def read_signed_cookie(raw_value: str | None, expected_kind: str) -> dict[str, Any] | None:
    if not raw_value:
        return None
    try:
        encoded, signature = raw_value.rsplit(".", 1)
    except ValueError:
        return None
    expected_signature = hmac.new(
        get_cookie_signing_secret().encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(b64url_decode(encoded))
    except Exception:
        return None
    if payload.get("kind") != expected_kind:
        return None
    return payload


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=WEB_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        max_age=COOKIE_MAX_AGE_SECONDS,
    )


def clear_cookie(response: Response, key: str) -> None:
    response.delete_cookie(key=key, samesite="strict", secure=COOKIE_SECURE)


def normalize_passphrase(value: Any) -> str:
    text = clean_text(value)
    if text is None:
        raise HTTPException(status_code=400, detail="Passphrase is required.")
    minimum = get_min_passphrase_length()
    if len(text) < minimum:
        raise HTTPException(status_code=400, detail=f"Passphrase must be at least {minimum} characters.")
    return text


def derive_owner_id(passphrase: str) -> str:
    digest = hmac.new(
        get_owner_secret().encode("utf-8"),
        passphrase.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def owner_hint(owner_id: str) -> str:
    return owner_id[:12]


def get_web_owner_cookie(request: Request, allow_missing: bool = False) -> tuple[str, str] | None:
    payload = read_signed_cookie(request.cookies.get(WEB_OWNER_COOKIE), "owner")
    if payload is None:
        if allow_missing:
            return None
        raise HTTPException(status_code=403, detail="Unlock required.")
    owner_type = clean_text(payload.get("owner_type"))
    owner_id = clean_text(payload.get("owner_id"))
    if not owner_type or not owner_id:
        if allow_missing:
            return None
        raise HTTPException(status_code=403, detail="Invalid owner session.")
    blocked = is_owner_blocked(owner_type, owner_id)
    if blocked is not None:
        raise HTTPException(status_code=403, detail="This passphrase space has been blocked.")
    return owner_type, owner_id


def require_web_owner(request: Request) -> tuple[str, str, str, str]:
    session_id, ip = require_web_marker(request)
    owner = get_web_owner_cookie(request)
    if owner is None:
        raise HTTPException(status_code=403, detail="Unlock required.")
    owner_type, owner_id = owner
    touch_web_session(session_id, ip, owner_id=owner_id)
    return session_id, ip, owner_type, owner_id


def require_web_image_owner(request: Request) -> tuple[str, str]:
    owner = get_web_owner_cookie(request)
    if owner is None:
        raise HTTPException(status_code=403, detail="Unlock required.")
    owner_type, owner_id = owner
    session_id = request.cookies.get(WEB_SESSION_COOKIE)
    if session_id:
        touch_web_session(session_id, get_client_ip(request), owner_id=owner_id)
    return owner_type, owner_id


def get_admin_cookie(request: Request, allow_missing: bool = False) -> dict[str, Any] | None:
    payload = read_signed_cookie(request.cookies.get(ADMIN_COOKIE), "admin")
    if payload is None:
        if allow_missing:
            return None
        raise HTTPException(status_code=401, detail="Admin login required.")
    return payload


def require_admin(request: Request) -> dict[str, Any]:
    require_admin_marker(request)
    payload = get_admin_cookie(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Admin login required.")
    return payload


def require_admin_image_access(request: Request) -> dict[str, Any]:
    payload = get_admin_cookie(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Admin login required.")
    return payload


def require_api_owner(request: Request) -> tuple[str, str, str]:
    auth = request.headers.get("authorization", "")
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = (request.headers.get("x-api-token") or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="API token is required. Use your web space passphrase as the token.")
    try:
        passphrase = normalize_passphrase(token)
    except HTTPException as exc:
        raise HTTPException(status_code=401, detail="Invalid API token.") from exc
    owner_id = derive_owner_id(passphrase)
    blocked = is_owner_blocked(WEB_OWNER_TYPE, owner_id)
    if blocked is not None:
        raise HTTPException(status_code=403, detail="This passphrase space has been blocked.")
    return get_client_ip(request), WEB_OWNER_TYPE, owner_id


def require_api_token(request: Request) -> str:
    ip, _, _ = require_api_owner(request)
    return ip


def acquire_generation_slot(slot_key: str, scope: str, owner_ip: str | None = None) -> str:
    limit = get_user_concurrency_limit()
    slot_id = uuid.uuid4().hex
    with state_lock:
        current = active_counts["owner"].get(slot_key, 0)
        if current >= limit:
            detail = f"Too many concurrent generations for this space. Limit is {limit}."
            raise HTTPException(status_code=429, detail=detail)
        active_counts["owner"][slot_key] = current + 1
        active_counts[scope][slot_key] = active_counts[scope].get(slot_key, 0) + 1
        active_generations[slot_id] = {
            "ip": owner_ip or slot_key,
            "scope": scope,
            "owner_id": slot_key,
            "started_at": time.time(),
        }
    return slot_id


def release_generation_slot(slot_key: str, scope: str, slot_id: str) -> None:
    with state_lock:
        active_generations.pop(slot_id, None)
        for bucket in (scope, "owner"):
            next_value = active_counts[bucket].get(slot_key, 1) - 1
            if next_value <= 0:
                active_counts[bucket].pop(slot_key, None)
            else:
                active_counts[bucket][slot_key] = next_value


@contextmanager
def reserve_generation_slot(slot_key: str, scope: str, owner_ip: str | None = None):
    slot_id = acquire_generation_slot(slot_key, scope, owner_ip)
    try:
        yield
    finally:
        release_generation_slot(slot_key, scope, slot_id)


def guess_extension(output_format: str | None, source_url: str | None, content_type: str | None) -> str:
    if output_format:
        normalized = output_format.lower().strip()
        if normalized == "jpeg":
            return "jpg"
        if normalized in {"png", "jpg", "webp"}:
            return normalized

    if source_url:
        suffix = Path(urlparse(source_url).path).suffix.lower().lstrip(".")
        if suffix in {"png", "jpg", "jpeg", "webp"}:
            return "jpg" if suffix == "jpeg" else suffix

    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            suffix = guessed.lstrip(".").lower()
            if suffix in {"png", "jpg", "jpeg", "webp"}:
                return "jpg" if suffix == "jpeg" else suffix

    return "png"


def build_image_url(scope: str, job_id: str, image_index: int) -> str:
    if scope == "web":
        return f"/web/images/{job_id}/{image_index}"
    if scope == "api":
        return f"/api/v1/images/{job_id}/{image_index}"
    if scope == "admin":
        return f"/admin/images/{job_id}/{image_index}"
    return f"/web/images/{job_id}/{image_index}"


def build_thumbnail_url(job_id: str, image_index: int) -> str:
    return f"/admin/thumbs/{job_id}/{image_index}"


def build_web_thumbnail_url(job_id: str, image_index: int) -> str:
    return f"/web/thumbs/{job_id}/{image_index}"


def build_api_thumbnail_url(job_id: str, image_index: int) -> str:
    return f"/api/v1/thumbs/{job_id}/{image_index}"


def save_bytes(raw: bytes, filename: str) -> Path:
    output_path = GENERATED_DIR / filename
    output_path.write_bytes(raw)
    return output_path


def read_image_dimensions(source_path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(source_path) as image:
            image = ImageOps.exif_transpose(image)
            return int(image.width), int(image.height)
    except Exception:
        return None


def thumbnail_path_for(job_id: str, image_index: int) -> Path:
    return THUMBNAIL_DIR / f"{job_id}_{image_index}.webp"


def create_thumbnail(source_path: Path, job_id: str, image_index: int) -> Path:
    thumbnail_path = thumbnail_path_for(job_id, image_index)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((420, 420), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")
        image.save(thumbnail_path, format="WEBP", quality=72, method=6)
    return thumbnail_path


def try_create_thumbnail(source_path: Path, job_id: str, image_index: int) -> str | None:
    try:
        return storage_path_for_db(create_thumbnail(source_path, job_id, image_index), {"thumbnails"})
    except Exception:
        return None


def ensure_thumbnail(record: dict[str, Any]) -> Path:
    existing = clean_text(record.get("thumbnail_path"))
    if existing:
        existing_path = resolve_storage_path(existing, {"thumbnails"})
        if existing_path is not None and existing_path.exists() and existing_path.is_file():
            return existing_path

    source_path = resolve_saved_file(record.get("saved_path"))
    thumbnail_path = create_thumbnail(source_path, str(record.get("job_id") or ""), int(record.get("image_index") or 0))
    update_image_thumbnail_path(
        str(record.get("job_id") or ""),
        int(record.get("image_index") or 0),
        storage_path_for_db(thumbnail_path, {"thumbnails"}),
    )
    return thumbnail_path


def materialize_upload(upload: StarletteUploadFile, prefix: str) -> tuple[Path, str]:
    raw = upload.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"Uploaded file {upload.filename or prefix} is empty.")
    suffix = Path(upload.filename or "").suffix.lower()
    if not suffix:
        suffix = mimetypes.guess_extension(upload.content_type or "") or ".bin"
    temp_path = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}{suffix}"
    temp_path.write_bytes(raw)
    return temp_path, hash_bytes(raw)


def persist_input_images(job_id: str, image_paths: list[Path], mask_path: Path | None, created_at: str) -> None:
    for idx, src_path in enumerate(image_paths, start=1):
        if not src_path.exists():
            continue
        suffix = src_path.suffix or ".png"
        dest = GENERATED_DIR / f"{job_id}_input_{idx}{suffix}"
        try:
            shutil.copy2(src_path, dest)
            save_input_image(
                job_id=job_id,
                image_index=idx,
                image_type="input",
                saved_path=storage_path_for_db(dest, {"generated"}),
                created_at=created_at,
            )
        except Exception:
            pass
    if mask_path and mask_path.exists():
        suffix = mask_path.suffix or ".png"
        dest = GENERATED_DIR / f"{job_id}_mask{suffix}"
        try:
            shutil.copy2(mask_path, dest)
            save_input_image(
                job_id=job_id,
                image_index=0,
                image_type="mask",
                saved_path=storage_path_for_db(dest, {"generated"}),
                created_at=created_at,
            )
        except Exception:
            pass


def local_image_payload(
    saved_path: Path,
    index: int,
    raw_size: int,
    source: str,
    image_item: Any,
    url: str,
) -> dict[str, Any]:
    if not saved_path.exists():
        raise FileNotFoundError(str(saved_path))
    payload = {
        "index": index,
        "url": url,
        "saved_path": storage_path_for_db(saved_path, {"generated"}),
        "size_bytes": raw_size,
        "source": source,
        "revised_prompt": getattr(image_item, "revised_prompt", None),
    }
    dimensions = read_image_dimensions(saved_path)
    if dimensions:
        payload["width"], payload["height"] = dimensions
    return payload


def serialize_image(
    image_item: Any,
    job_id: str,
    index: int,
    output_format: str | None,
    delivery_scope: str,
    source_hashes: set[str] | None = None,
) -> dict[str, Any] | None:
    known_hashes = source_hashes or set()
    if getattr(image_item, "b64_json", None):
        raw = base64.b64decode(image_item.b64_json)
        if known_hashes and hash_bytes(raw) in known_hashes:
            return None
        ext = guess_extension(output_format, None, None)
        filename = f"{job_id}_{index}.{ext}"
        saved_path = save_bytes(raw, filename)
        result = local_image_payload(
            saved_path,
            index,
            len(raw),
            "b64_json",
            image_item,
            build_image_url(delivery_scope, job_id, index),
        )
        result["thumbnail_path"] = try_create_thumbnail(saved_path, job_id, index)
        return result

    if getattr(image_item, "url", None):
        source_url = str(image_item.url)
        try:
            with urllib.request.urlopen(source_url, timeout=120) as response:
                raw = response.read()
                content_type = response.headers.get_content_type()
            if known_hashes and hash_bytes(raw) in known_hashes:
                return None
            ext = guess_extension(output_format, source_url, content_type)
            filename = f"{job_id}_{index}.{ext}"
            saved_path = save_bytes(raw, filename)
            result = local_image_payload(
                saved_path,
                index,
                len(raw),
                "remote_url_downloaded",
                image_item,
                build_image_url(delivery_scope, job_id, index),
            )
            result["thumbnail_path"] = try_create_thumbnail(saved_path, job_id, index)
            result["origin_url"] = source_url
            return result
        except Exception as exc:
            return {
                "index": index,
                "url": source_url,
                "saved_path": None,
                "size_bytes": None,
                "source": "remote_url_only",
                "origin_url": source_url,
                "revised_prompt": getattr(image_item, "revised_prompt", None),
                "download_error": str(exc),
            }

    return {
        "index": index,
        "url": None,
        "saved_path": None,
        "size_bytes": None,
        "source": "empty",
        "revised_prompt": getattr(image_item, "revised_prompt", None),
    }


def serialize_response_images(
    response_items: list[Any],
    job_id: str,
    output_format: str | None,
    delivery_scope: str,
    source_hashes: set[str] | None = None,
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for image_item in response_items:
        image = serialize_image(
            image_item,
            job_id,
            len(images) + 1,
            output_format,
            delivery_scope,
            source_hashes,
        )
        if image is not None:
            images.append(image)
    return images


def build_history_items(owner_type: str, owner_id: str, offset: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
    rows, has_more = list_history_images(owner_type, owner_id, offset, limit)
    items: list[dict[str, Any]] = []
    for row in rows:
        saved_path = row.get("saved_path")
        if not saved_path:
            continue
        local_path = resolve_storage_path(saved_path, {"generated"})
        if local_path is None or not local_path.exists():
            continue
        item: dict[str, Any] = {
            "image_id": f"{row.get('job_id', 'job')}_{row.get('image_index', 0)}",
            "job_id": row.get("job_id"),
            "created_at": row.get("created_at"),
            "completed_at": row.get("completed_at"),
            "operation": row.get("operation"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "url": build_image_url("web", str(row.get("job_id")), int(row.get("image_index", 0) or 0)),
            "thumbnail_url": build_web_thumbnail_url(str(row.get("job_id")), int(row.get("image_index", 0) or 0)),
            "filename": local_path.name,
            "size_bytes": row.get("size_bytes"),
            "prompt": row.get("prompt"),
            "provider_id": row.get("provider_id"),
            "provider_name_snapshot": row.get("provider_name_snapshot"),
            "provider_type": row.get("provider_type"),
            "request_params": parse_request_params(row.get("request_params_json")),
            "actual_params": parse_request_params(row.get("response_params_json")),
        }
        dimensions = read_image_dimensions(local_path)
        if dimensions:
            item["width"], item["height"] = dimensions
        input_count = int(row.get("input_image_count") or 0)
        mask_used = bool(row.get("mask_used"))
        if input_count > 0:
            item["input_image_count"] = input_count
            item["input_image_urls"] = [
                f"/web/input-images/{row.get('job_id')}/{i}"
                for i in range(1, input_count + 1)
            ]
        if mask_used:
            item["mask_url"] = f"/web/input-mask/{row.get('job_id')}"
        items.append(item)
    return items, has_more


def build_running_job_items(owner_type: str, owner_id: str) -> list[dict[str, Any]]:
    rows, _ = list_admin_jobs(
        offset=0,
        limit=20,
        status="running",
        scope="web",
        owner_type=owner_type,
        owner_id=owner_id,
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "job_id": row.get("job_id"),
                "created_at": row.get("created_at"),
                "operation": row.get("operation"),
                "prompt": row.get("prompt"),
                "model": row.get("model"),
                "request_params": parse_request_params(row.get("request_params_json")),
                "status": row.get("status"),
                "image_count": row.get("image_count") or 0,
            }
        )
    return items


def parse_request_params(raw_params: Any) -> dict[str, Any]:
    if not raw_params:
        return {}
    try:
        parsed = json.loads(str(raw_params))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def response_actual_params(response: Any, fallback_size: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in ("size", "quality", "background", "output_format"):
        value = getattr(response, key, None)
        if value is not None:
            params[key] = str(value)
    if fallback_size and "size" not in params:
        params["size"] = fallback_size
    created = getattr(response, "created", None)
    if created is not None:
        params["created"] = created
    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            if hasattr(usage, "model_dump"):
                params["usage"] = usage.model_dump(exclude_none=True)
            elif hasattr(usage, "dict"):
                params["usage"] = usage.dict(exclude_none=True)
            else:
                params["usage"] = dict(usage)
        except Exception:
            pass
    return params


def build_generate_params(payload: GenerateRequest) -> tuple[str, dict[str, Any]]:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required.")

    provider = resolve_provider_profile(payload.provider_id)
    requested_model = clean_text(payload.model)
    request_params: dict[str, Any] = {
        "model": requested_model or provider.default_model,
        "prompt": prompt,
    }

    normalized_size = resolve_size_request(payload.size, payload.aspect_ratio)
    if normalized_size is not None:
        request_params["size"] = normalized_size

    optional_values = {
        "n": payload.n,
        "quality": clean_text(payload.quality),
        "background": clean_text(payload.background),
        "output_format": clean_text(payload.output_format),
        "output_compression": payload.output_compression,
        "partial_images": payload.partial_images,
        "response_format": clean_text(payload.response_format) or get_default_response_format(),
        "moderation": clean_text(payload.moderation),
        "style": clean_text(payload.style),
        "user": clean_text(payload.user),
    }

    for key, value in optional_values.items():
        if value is not None:
            request_params[key] = value

    return prompt, build_provider_request(provider, request_params)


def parse_edit_form(form: FormData) -> tuple[str, dict[str, Any], list[StarletteUploadFile], StarletteUploadFile | None]:
    prompt = clean_text(form.get("prompt"))
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required.")

    image_uploads = [
        item
        for item in [*form.getlist("image"), *form.getlist("image[]")]
        if isinstance(item, StarletteUploadFile) and item.filename
    ]
    if not image_uploads:
        raise HTTPException(status_code=400, detail="At least one image file is required for edit.")

    mask_value = form.get("mask")
    mask_upload = mask_value if isinstance(mask_value, StarletteUploadFile) and mask_value.filename else None

    provider = resolve_provider_profile(clean_text(form.get("provider_id")))
    requested_model = clean_text(form.get("model"))
    request_params: dict[str, Any] = {
        "model": requested_model or provider.default_model,
        "prompt": prompt,
    }

    normalized_size = resolve_size_request(form.get("size"), form.get("aspect_ratio"))
    if normalized_size is not None:
        request_params["size"] = normalized_size

    optional_values: dict[str, Any] = {
        "n": parse_optional_int(form.get("n"), "n", 1, 8),
        "quality": clean_text(form.get("quality")),
        "background": clean_text(form.get("background")),
        "output_format": clean_text(form.get("output_format")),
        "output_compression": parse_optional_int(form.get("output_compression"), "output_compression", 0, 100),
        "partial_images": parse_optional_int(form.get("partial_images"), "partial_images", 0, 8),
        "response_format": clean_text(form.get("response_format")) or get_default_response_format(),
        "user": clean_text(form.get("user")),
    }

    for key, value in optional_values.items():
        if value is not None:
            request_params[key] = value

    return prompt, build_provider_request(provider, request_params), image_uploads, mask_upload


def build_api_owner(request_params: dict[str, Any], ip: str) -> tuple[str, str]:
    api_user = clean_text(request_params.get("user"))
    if api_user:
        return "api_user", api_user
    return "api_client_ip", ip


def create_job(
    *,
    prompt: str,
    scope: str,
    route: str,
    ip: str,
    user_agent: str,
    owner_type: str,
    owner_id: str,
    operation: str,
    request_params: dict[str, Any],
    image_uploads: list[StarletteUploadFile] | None = None,
    mask_upload: StarletteUploadFile | None = None,
    job_id: str | None = None,
    created_at: str | None = None,
    log_started: bool = True,
    prepared_image_paths: list[Path] | None = None,
    prepared_mask_path: Path | None = None,
    prepared_source_hashes: set[str] | None = None,
    cleanup_prepared_paths: bool = False,
) -> dict[str, Any]:
    provider = provider_from_request_params(request_params) or resolve_provider_profile()
    client = get_client(provider)
    job_id = job_id or build_job_id()
    created_at = created_at or now_iso()
    stored_request_params = public_request_params(request_params)

    temp_paths: list[Path] = []
    source_hashes: set[str] = set(prepared_source_hashes or set())
    open_handles: list[Any] = []
    if log_started:
        log_generation_started(
            job_id=job_id,
            created_at=created_at,
            scope=scope,
            route=route,
            client_ip=ip,
            user_agent=user_agent,
            owner_type=owner_type,
            owner_id=owner_id,
            prompt=prompt,
            model=str(stored_request_params.get("model") or provider.default_model),
            operation=operation,
            request_params_json=json.dumps(stored_request_params, ensure_ascii=False),
            input_image_count=len(image_uploads or []),
            mask_used=bool(mask_upload),
            provider_id=provider.id,
            provider_name_snapshot=provider.name,
            provider_type=provider.provider_type,
        )

    started_at = time.time()
    try:
        uses_image_inputs = operation in {"edit", "reference"}
        if uses_image_inputs:
            image_paths = list(prepared_image_paths or [])
            mask_path = prepared_mask_path
            if cleanup_prepared_paths:
                temp_paths.extend(image_paths)
                if mask_path is not None:
                    temp_paths.append(mask_path)
            if not image_paths:
                for idx, upload in enumerate(image_uploads or [], start=1):
                    image_path, image_hash = materialize_upload(upload, f"{job_id}_image_{idx}")
                    temp_paths.append(image_path)
                    image_paths.append(image_path)
                    source_hashes.add(image_hash)
                if mask_upload:
                    mask_path, _ = materialize_upload(mask_upload, f"{job_id}_mask")
                    temp_paths.append(mask_path)

            for image_path in image_paths:
                open_handles.append(image_path.open("rb"))

            call_params = upstream_request_params(request_params)
            if len(open_handles) == 1:
                call_params["image"] = open_handles[0]
            else:
                call_params["image"] = list(open_handles)

            if mask_path is not None:
                mask_handle = mask_path.open("rb")
                open_handles.append(mask_handle)
                call_params["mask"] = mask_handle

            response = client.images.edit(**call_params)

            for handle in open_handles:
                try:
                    handle.close()
                except Exception:
                    pass
            open_handles.clear()

            persist_input_images(job_id, image_paths, mask_path, created_at)
        else:
            response = client.images.generate(**upstream_request_params(request_params))
    except Exception as exc:
        error_message = describe_image_upstream_error(exc, provider)
        elapsed = round(time.time() - started_at, 2)
        log_generation_failed(
            job_id=job_id,
            completed_at=now_iso(),
            elapsed_seconds=elapsed,
            error_message=error_message,
        )
        raise HTTPException(status_code=502, detail=f"Image {operation} failed: {error_message}") from exc
    finally:
        for handle in open_handles:
            try:
                handle.close()
            except Exception:
                pass
        for temp_path in temp_paths:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    output_format = clean_text(request_params.get("output_format"))
    response_items = list(response.data or [])
    uses_image_inputs = operation in {"edit", "reference"}
    images = serialize_response_images(
        response_items,
        job_id,
        output_format,
        scope,
        source_hashes if uses_image_inputs and source_hashes else None,
    )
    if uses_image_inputs and not images:
        elapsed = round(time.time() - started_at, 2)
        operation_label = "edit" if operation == "edit" else "reference generation"
        error_message = f"Image {operation_label} failed: upstream returned no new images."
        if response_items and source_hashes:
            error_message = f"Image {operation_label} failed: upstream returned only the uploaded source images."
        log_generation_failed(
            job_id=job_id,
            completed_at=now_iso(),
            elapsed_seconds=elapsed,
            error_message=error_message,
        )
        raise HTTPException(status_code=502, detail=error_message)

    actual_size = None
    if images and images[0].get("width") and images[0].get("height"):
        actual_size = f"{images[0]['width']}x{images[0]['height']}"
    actual_params = response_actual_params(response, actual_size)

    if is_job_deleted(job_id):
        remove_saved_paths([
            str(path)
            for image in images
            for path in (image.get("saved_path"), image.get("thumbnail_path"))
            if path
        ])
        raise JobDeletedError(job_id)

    elapsed = round(time.time() - started_at, 2)
    completed_at = now_iso()
    log_generation_finished(
        job_id=job_id,
        completed_at=completed_at,
        elapsed_seconds=elapsed,
        images=images,
        response_params_json=json.dumps(actual_params, ensure_ascii=False) if actual_params else None,
    )

    return {
        "job_id": job_id,
        "created_at": created_at,
        "operation": operation,
        "prompt": prompt,
        "model": str(stored_request_params.get("model") or provider.default_model),
        "provider": provider.public_snapshot(),
        "request_params": stored_request_params,
        "actual_params": actual_params,
        "elapsed_seconds": elapsed,
        "image_count": len(images),
        "scope": scope,
        "request_ip": ip,
        "images": images,
    }


def serialize_owner_job(detail: dict[str, Any]) -> dict[str, Any]:
    request_params: dict[str, Any] = {}
    raw_params = detail.get("request_params_json")
    if raw_params:
        try:
            parsed = json.loads(str(raw_params))
            if isinstance(parsed, dict):
                request_params = parsed
        except json.JSONDecodeError:
            request_params = {}
    actual_params = parse_request_params(detail.get("response_params_json"))

    job_id = str(detail.get("job_id") or "")
    images: list[dict[str, Any]] = []
    if detail.get("deleted_at"):
        detail_images = []
    else:
        detail_images = detail.get("images", [])
    for image in detail_images:
        if image.get("deleted_at"):
            continue
        saved_path = image.get("saved_path")
        local_path = resolve_storage_path(saved_path, {"generated"})
        if local_path is None or not local_path.exists():
            continue
        image_index = int(image.get("image_index") or 0)
        dimensions = read_image_dimensions(local_path)
        images.append(
            {
                "url": build_image_url("web", job_id, image_index),
                "thumbnail_url": build_web_thumbnail_url(job_id, image_index),
                "image_index": image_index,
                "filename": local_path.name,
                "size_bytes": image.get("size_bytes"),
                "width": dimensions[0] if dimensions else None,
                "height": dimensions[1] if dimensions else None,
                "source": image.get("source"),
            }
        )

    return {
        "job_id": job_id,
        "created_at": detail.get("created_at"),
        "completed_at": detail.get("completed_at"),
        "operation": detail.get("operation"),
        "prompt": detail.get("prompt"),
        "provider_id": detail.get("provider_id"),
        "provider_name_snapshot": detail.get("provider_name_snapshot"),
        "provider_type": detail.get("provider_type"),
        "model": detail.get("model"),
        "request_params": request_params,
        "actual_params": actual_params,
        "elapsed_seconds": detail.get("elapsed_seconds"),
        "image_count": detail.get("image_count") or len(images),
        "scope": detail.get("scope"),
        "request_ip": detail.get("client_ip"),
        "status": detail.get("status"),
        "error_message": detail.get("error_message"),
        "deleted_at": detail.get("deleted_at"),
        "deleted_by": detail.get("deleted_by"),
        "deleted_reason": detail.get("deleted_reason"),
        "images": images,
    }


def serialize_public_api_job(result: dict[str, Any]) -> dict[str, Any]:
    job_id = str(result.get("job_id") or "")
    images: list[dict[str, Any]] = []
    for image in result.get("images", []):
        image_index = int(image.get("index") or image.get("image_index") or len(images) + 1)
        images.append(
            {
                "image_index": image_index,
                "url": build_image_url("api", job_id, image_index),
                "thumbnail_url": build_api_thumbnail_url(job_id, image_index),
                "size_bytes": image.get("size_bytes"),
                "width": image.get("width"),
                "height": image.get("height"),
                "source": image.get("source"),
                "revised_prompt": image.get("revised_prompt"),
            }
        )

    return {
        "ok": True,
        "status": "success",
        "api_version": PUBLIC_API_VERSION,
        "job_id": job_id,
        "created_at": result.get("created_at"),
        "operation": result.get("operation"),
        "model": result.get("model"),
        "provider": result.get("provider"),
        "request_params": result.get("request_params") or {},
        "actual_params": result.get("actual_params") or {},
        "elapsed_seconds": result.get("elapsed_seconds"),
        "image_count": len(images),
        "images": images,
    }


def api_catalog_payload() -> dict[str, Any]:
    return {
        "name": "image-playground",
        "api_version": PUBLIC_API_VERSION,
        "base_path": "/api/v1",
        "auth": {
            "type": "bearer",
            "token": "Use your web space passphrase.",
            "headers": ["Authorization: Bearer <your-space-passphrase>", "X-API-Token: <your-space-passphrase>"],
        },
        "limits": {
            "concurrency_per_space": get_user_concurrency_limit(),
            "shared_with_web": True,
            "prompt_max_length": 4000,
            "edit_images_min": 1,
            "edit_images_field": "image or image[]",
        },
        "parameters": {
            "supported": ["prompt", "provider_id", "model", "n", "size", "aspect_ratio", "quality", "response_format", "image", "mask"],
            "forwarded_upstream": ["model", "prompt", "n", "size", "quality", "response_format", "image", "mask"],
            "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536", "2048x1152", "1152x2048", "2048x2048", "3840x2160", "2160x3840"],
            "size_constraints": {
                "format": "WIDTHxHEIGHT",
                "multiple_of": 16,
                "max_edge": 3840,
                "max_pixels": 8294400,
                "max_aspect_ratio": "3:1",
            },
            "aspect_ratios": ASPECT_RATIO_SIZE_MAP,
            "quality": ["auto", "low", "medium", "high", "standard", "hd"],
        },
        "endpoints": [
            {
                "method": "POST",
                "path": "/api/v1/generate",
                "content_type": "application/json",
                "description": "Text-to-image generation.",
                "body": {"prompt": "string", "provider_id": "optional", "model": "optional", "size": "1024x1024", "quality": "auto", "response_format": "b64_json"},
            },
            {
                "method": "POST",
                "path": "/api/v1/edit",
                "content_type": "multipart/form-data",
                "description": "Image editing with one or more ordered input images.",
                "fields": {"prompt": "string", "provider_id": "optional", "model": "optional", "image": "file[]", "mask": "file optional", "size": "optional", "quality": "optional"},
            },
            {"method": "GET", "path": "/api/v1/images/{job_id}/{image_index}", "description": "Fetch generated image with API token."},
            {"method": "GET", "path": "/api/v1/thumbs/{job_id}/{image_index}", "description": "Fetch generated thumbnail with API token."},
        ],
    }


def api_docs_html() -> str:
    catalog = api_catalog_payload()
    sizes = "".join(f"<code>{size}</code>" for size in catalog["parameters"]["sizes"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>image-playground API</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f7f4ee; --ink:#171717; --muted:#6b665f; --card:rgba(255,255,255,.78); --line:rgba(23,23,23,.10); --accent:#0f766e; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --ink:#f4f4f5; --muted:#a1a1aa; --card:rgba(24,24,27,.78); --line:rgba(255,255,255,.10); --accent:#5eead4; }} }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"HarmonyOS Sans SC","Noto Sans SC",ui-sans-serif,sans-serif; background:radial-gradient(circle at 20% 0%, rgba(15,118,110,.18), transparent 34%), var(--bg); color:var(--ink); }}
    main {{ width:min(1120px, calc(100% - 32px)); margin:0 auto; padding:42px 0 64px; }}
    .hero {{ display:grid; gap:18px; padding:28px; border:1px solid var(--line); border-radius:32px; background:var(--card); backdrop-filter:blur(18px); box-shadow:0 24px 70px rgba(0,0,0,.10); }}
    h1 {{ margin:0; font-size:clamp(32px, 7vw, 72px); letter-spacing:-.06em; line-height:.92; }}
    h2 {{ margin:0 0 14px; font-size:18px; }}
    p {{ margin:0; color:var(--muted); line-height:1.7; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin-top:18px; }}
    .card {{ border:1px solid var(--line); border-radius:24px; background:var(--card); padding:18px; backdrop-filter:blur(14px); }}
    code, pre {{ font-family:"Maple Mono","Cascadia Code",ui-monospace,monospace; }}
    code {{ display:inline-block; margin:3px; padding:4px 7px; border-radius:999px; background:rgba(15,118,110,.10); color:var(--accent); font-size:12px; }}
    pre {{ overflow:auto; margin:12px 0 0; padding:14px; border-radius:16px; background:rgba(0,0,0,.78); color:#f8fafc; font-size:12px; line-height:1.55; }}
    .method {{ display:inline-flex; margin-bottom:10px; padding:4px 8px; border-radius:999px; background:var(--accent); color:var(--bg); font-weight:800; font-size:12px; }}
    a {{ color:var(--accent); }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <h1>image-playground API</h1>
        <p>面向程序调用的图片生成与编辑接口。网页端走 <code>/web/*</code>，公开 API 只走 <code>/api/v1/*</code>。API Token 使用你的网页空间口令，Web 和 API 共用同一个空间、历史归属、封禁和并发限制。</p>
      </div>
      <div class="grid">
        <div class="card"><h2>认证</h2><p><code>Authorization: Bearer 你的空间口令</code><br><code>X-API-Token: 你的空间口令</code></p></div>
        <div class="card"><h2>并发</h2><p>同一空间 Web + API 总共同时允许 {get_user_concurrency_limit()} 个生成/编辑任务。</p></div>
        <div class="card"><h2>尺寸</h2><p>{sizes}<br>也可传 <code>WIDTHxHEIGHT</code>，宽高需为 16 的倍数，最长边不超过 3840，总像素不超过 4K 级别。</p></div>
      </div>
    </section>
    <section class="grid">
      <div class="card">
        <span class="method">POST</span>
        <h2>/api/v1/generate</h2>
        <p>文生图，JSON 请求。</p>
        <pre>curl -X POST http://SERVER:30116/api/v1/generate \\
  -H "Authorization: Bearer YOUR_SPACE_PASSPHRASE" \\
  -H "Content-Type: application/json" \\
  -d '{{"prompt":"a white cat in a studio","size":"1024x1024","quality":"auto"}}'</pre>
      </div>
      <div class="card">
        <span class="method">POST</span>
        <h2>/api/v1/edit</h2>
        <p>图片编辑，multipart 请求。多图顺序按上传顺序传给上游；字段名支持 <code>image</code> 和 <code>image[]</code>。<code>mask</code> 可选并原样透传，具体透明/不透明区域语义由上游 OpenAI-compatible edit 接口决定。</p>
        <pre>curl -X POST http://SERVER:30116/api/v1/edit \\
  -H "Authorization: Bearer YOUR_SPACE_PASSPHRASE" \\
  -F "prompt=change the background to snow" \\
  -F "image=@one.png" \\
  -F "image=@two.png" \\
  -F "size=1024x1024"</pre>
      </div>
      <div class="card">
        <span class="method">GET</span>
        <h2>/api/v1</h2>
        <p>返回机器可读的接口目录。原 Markdown 文档保留在 <a href="/api/v1/docs.md">/api/v1/docs.md</a>。</p>
        <pre>{json.dumps({"ok": True, "status": "success", "job_id": "20260512_xxxxxxxx", "images": [{"url": "/api/v1/images/.../1", "thumbnail_url": "/api/v1/thumbs/.../1"}]}, ensure_ascii=False, indent=2)}</pre>
      </div>
    </section>
  </main>
</body>
</html>"""


def get_web_job_state(job_id: str) -> dict[str, Any] | None:
    with state_lock:
        payload = web_job_states.get(job_id)
        return dict(payload) if payload else None


def set_web_job_state(job_id: str, **values: Any) -> None:
    with state_lock:
        current = dict(web_job_states.get(job_id) or {})
        current.update(values)
        web_job_states[job_id] = current


def release_web_job_slot(job_id: str) -> bool:
    with state_lock:
        state = dict(web_job_states.get(job_id) or {})
        if not state or state.get("released"):
            return False
        slot_key = str(state.get("slot_key") or "")
        slot_id = str(state.get("slot_id") or "")
        if not slot_key or not slot_id:
            state["released"] = True
            web_job_states[job_id] = state
            return False

        state["released"] = True
        web_job_states[job_id] = state
        active_generations.pop(slot_id, None)
        for bucket in ("web", "owner"):
            next_value = active_counts[bucket].get(slot_key, 1) - 1
            if next_value <= 0:
                active_counts[bucket].pop(slot_key, None)
            else:
                active_counts[bucket][slot_key] = next_value
        return True


def mark_web_job_failed(job_id: str, started_at: float, message: str) -> None:
    completed_at = now_iso()
    log_generation_failed(
        job_id=job_id,
        completed_at=completed_at,
        elapsed_seconds=round(time.time() - started_at, 2),
        error_message=message,
    )
    set_web_job_state(
        job_id,
        status="failed",
        error_message=message,
        updated_at=completed_at,
    )
    release_web_job_slot(job_id)


def enqueue_web_job(
    *,
    slot_key: str,
    prompt: str,
    route: str,
    ip: str,
    user_agent: str,
    owner_type: str,
    owner_id: str,
    operation: str,
    request_params: dict[str, Any],
    prepared_image_paths: list[Path] | None = None,
    prepared_mask_path: Path | None = None,
    prepared_source_hashes: set[str] | None = None,
    input_image_count: int = 0,
    mask_used: bool = False,
) -> dict[str, Any]:
    provider = provider_from_request_params(request_params) or resolve_provider_profile()
    stored_request_params = public_request_params(request_params)
    slot_id = acquire_generation_slot(slot_key, "web", owner_ip=ip)
    job_id = build_job_id()
    created_at = now_iso()
    started_at = time.time()
    log_generation_started(
        job_id=job_id,
        created_at=created_at,
        scope="web",
        route=route,
        client_ip=ip,
        user_agent=user_agent,
        owner_type=owner_type,
        owner_id=owner_id,
        prompt=prompt,
        model=str(stored_request_params.get("model") or provider.default_model),
        operation=operation,
        request_params_json=json.dumps(stored_request_params, ensure_ascii=False),
        input_image_count=input_image_count,
        mask_used=mask_used,
        provider_id=provider.id,
        provider_name_snapshot=provider.name,
        provider_type=provider.provider_type,
    )
    set_web_job_state(
        job_id,
        status="running",
        error_message="",
        updated_at=created_at,
        started_at=started_at,
        slot_key=slot_key,
        slot_id=slot_id,
        released=False,
    )

    def run() -> None:
        try:
            state_after = get_web_job_state(job_id) or {}
            if state_after.get("status") != "running":
                if state_after.get("status") == "failed":
                    log_generation_failed(
                        job_id=job_id,
                        completed_at=now_iso(),
                        elapsed_seconds=round(time.time() - started_at, 2),
                        error_message=str(state_after.get("error_message") or "任务已结束"),
                    )
                return
            result = create_job(
                prompt=prompt,
                scope="web",
                route=route,
                ip=ip,
                user_agent=user_agent,
                owner_type=owner_type,
                owner_id=owner_id,
                operation=operation,
                request_params=request_params,
                job_id=job_id,
                created_at=created_at,
                log_started=False,
                prepared_image_paths=prepared_image_paths,
                prepared_mask_path=prepared_mask_path,
                prepared_source_hashes=prepared_source_hashes,
                cleanup_prepared_paths=True,
            )
            if (get_web_job_state(job_id) or {}).get("status") != "running":
                return
            set_web_job_state(
                job_id,
                status="success",
                error_message="",
                updated_at=now_iso(),
                completed_at=now_iso(),
            )
            release_web_job_slot(job_id)
        except JobDeletedError:
            set_web_job_state(
                job_id,
                status="failed",
                error_message="用户已删除",
                updated_at=now_iso(),
            )
            release_web_job_slot(job_id)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            mark_web_job_failed(job_id, started_at, str(detail))
        finally:
            for path in list(prepared_image_paths or []) + ([prepared_mask_path] if prepared_mask_path else []):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
            release_web_job_slot(job_id)

    generation_executor.submit(run)
    return {
        "job_id": job_id,
        "created_at": created_at,
        "operation": operation,
        "prompt": prompt,
        "model": str(stored_request_params.get("model") or provider.default_model),
        "provider": provider.public_snapshot(),
        "request_params": stored_request_params,
        "actual_params": {},
        "scope": "web",
        "request_ip": ip,
        "status": "running",
        "image_count": 0,
        "images": [],
    }


def resolve_saved_file(saved_path_value: str | None) -> Path:
    saved_path = clean_text(saved_path_value)
    if not saved_path:
        raise HTTPException(status_code=404, detail="Image file not found.")
    path = resolve_storage_path(saved_path, {"generated"})
    if path is None:
        raise HTTPException(status_code=403, detail="Invalid image path.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found.")
    return path


def resolve_thumbnail_file(thumbnail_path_value: str | None) -> Path:
    thumbnail_path = clean_text(thumbnail_path_value)
    if not thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail file not found.")
    path = resolve_storage_path(thumbnail_path, {"thumbnails"})
    if path is None:
        raise HTTPException(status_code=403, detail="Invalid thumbnail path.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail file not found.")
    return path


def serve_image_file(saved_path_value: str | None) -> FileResponse:
    path = resolve_saved_file(saved_path_value)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


def serve_thumbnail_file(record: dict[str, Any]) -> FileResponse:
    path = ensure_thumbnail(record)
    return FileResponse(path, media_type="image/webp", filename=path.name)


def remove_saved_paths(saved_paths: list[str]) -> int:
    removed = 0
    for raw_path in saved_paths:
        path = resolve_storage_path(raw_path, {"generated", "thumbnails"})
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except Exception:
            continue
    return removed


def directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def database_size_bytes() -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = BASE_DIR / f"service.db{suffix}"
        if candidate.exists():
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
    return total


def build_system_status() -> dict[str, Any]:
    try:
        disk_usage = shutil.disk_usage(str(BASE_DIR))
        total_bytes = disk_usage.total
        free_bytes = disk_usage.free
        used_bytes = disk_usage.used
    except Exception:
        total_bytes = free_bytes = used_bytes = 0
    providers = public_provider_profiles()
    active_provider = providers[0] if providers else None
    return {
        **get_runtime_status(),
        "version": {
            "web_client_version": WEB_CLIENT_VERSION,
            "api_version": PUBLIC_API_VERSION,
            "model": active_provider.get("default_model") if active_provider else get_model_name(),
            "provider": active_provider,
            "admin_page_path": get_admin_page_path(),
        },
        "storage": {
            "generated_bytes": directory_size_bytes(GENERATED_DIR),
            "thumbnails_bytes": directory_size_bytes(THUMBNAIL_DIR),
            "uploads_bytes": directory_size_bytes(UPLOAD_DIR),
            "logs_bytes": directory_size_bytes(LOGS_DIR),
            "database_bytes": database_size_bytes(),
        },
        "disk": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "used_percent": round((used_bytes / total_bytes) * 100, 2) if total_bytes else 0,
        },
    }


def serialize_admin_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job)
    prompt = str(payload.get("prompt") or "")
    payload["prompt_preview"] = prompt[:120]
    payload["owner_hint"] = owner_hint(str(payload.get("owner_id") or "")) if payload.get("owner_id") else ""
    return payload


def serialize_admin_gallery_image(row: dict[str, Any]) -> dict[str, Any]:
    job_id = str(row.get("job_id") or "")
    image_index = int(row.get("image_index") or 0)
    prompt = str(row.get("prompt") or "")
    owner_id_value = str(row.get("owner_id") or "")
    is_deleted = bool(row.get("deleted_at") or row.get("image_deleted_at"))
    files_removed = bool(row.get("files_removed_at") or row.get("image_files_removed_at"))
    return {
        "job_id": job_id,
        "image_index": image_index,
        "thumbnail_url": None if is_deleted or files_removed else build_thumbnail_url(job_id, image_index),
        "original_url": None if is_deleted or files_removed else build_image_url("admin", job_id, image_index),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
        "scope": row.get("scope"),
        "operation": row.get("operation"),
        "client_ip": row.get("client_ip"),
        "owner_type": row.get("owner_type"),
        "owner_id": owner_id_value,
        "owner_hint": owner_hint(owner_id_value) if owner_id_value else "",
        "owner_label": row.get("owner_label") or "",
        "owner_note": row.get("owner_note") or "",
        "blocked_reason": row.get("blocked_reason"),
        "prompt": prompt,
        "prompt_preview": prompt[:180],
        "size_bytes": row.get("size_bytes"),
        "source": row.get("source"),
        "model": row.get("model"),
        "elapsed_seconds": row.get("elapsed_seconds"),
        "image_count": row.get("image_count"),
        "input_image_count": row.get("input_image_count"),
        "mask_used": bool(row.get("mask_used")),
        "deleted_at": row.get("deleted_at"),
        "deleted_by": row.get("deleted_by"),
        "deleted_reason": row.get("deleted_reason"),
        "files_removed_at": row.get("files_removed_at"),
        "image_deleted_at": row.get("image_deleted_at"),
        "image_deleted_by": row.get("image_deleted_by"),
        "image_deleted_reason": row.get("image_deleted_reason"),
        "image_files_removed_at": row.get("image_files_removed_at"),
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    stats = get_live_stats()
    return {"status": "ok", "time": now_iso(), **stats}


@app.get("/")
def index(request: Request) -> FileResponse:
    session_id = get_or_create_web_session_id(request)
    response = FileResponse(STATIC_DIR / "index.html")
    set_session_cookie(response, session_id)
    return response


@app.get("/assets/{asset_path:path}")
def static_asset(asset_path: str) -> FileResponse:
    path = (STATIC_DIR / "assets" / asset_path).resolve()
    assets_root = (STATIC_DIR / "assets").resolve()
    if assets_root not in path.parents or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(path)


@app.get("/manifest.webmanifest")
def web_manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest")


@app.get("/pwa-icon.svg")
def web_icon() -> FileResponse:
    return FileResponse(STATIC_DIR / "pwa-icon.svg")


@app.get("/sw.js")
def web_service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js")


@app.get(get_admin_page_path())
def admin_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/web/session")
def web_session(request: Request) -> dict[str, Any]:
    session_id = get_or_create_web_session_id(request)
    ip = get_client_ip(request)
    owner = get_web_owner_cookie(request, allow_missing=True)
    owner_hint_value = ""
    if owner is not None:
        owner_hint_value = owner_hint(owner[1])
        touch_web_session(session_id, ip, owner_id=owner[1])
    else:
        touch_web_session(session_id, ip)
    stats = get_live_stats(owner[1] if owner else None)
    return {
        "unlocked": owner is not None,
        "owner_hint": owner_hint_value,
        **stats,
    }


@app.post("/web/unlock")
def web_unlock(request: Request, payload: UnlockRequest) -> JSONResponse:
    session_id, ip = require_web_marker(request)
    passphrase = normalize_passphrase(payload.passphrase)
    owner_id = derive_owner_id(passphrase)
    blocked = is_owner_blocked(WEB_OWNER_TYPE, owner_id)
    if blocked is not None:
        log_auth_event(
            created_at=now_iso(),
            scope="web",
            event_type="unlock_denied_blocked",
            success=False,
            owner_type=WEB_OWNER_TYPE,
            owner_id=owner_id,
            client_ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            detail=clean_text(blocked.get("reason")) or "blocked",
        )
        raise HTTPException(status_code=403, detail="This passphrase space has been blocked.")

    touch_web_session(session_id, ip, owner_id=owner_id)
    log_auth_event(
        created_at=now_iso(),
        scope="web",
        event_type="unlock",
        success=True,
        owner_type=WEB_OWNER_TYPE,
        owner_id=owner_id,
        client_ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        detail="unlock success",
    )
    response = JSONResponse({"ok": True, "owner_hint": owner_hint(owner_id), **get_live_stats(owner_id)})
    set_session_cookie(response, session_id)
    response.set_cookie(
        key=WEB_OWNER_COOKIE,
        value=sign_cookie(
            "owner",
            {
                "owner_type": WEB_OWNER_TYPE,
                "owner_id": owner_id,
                "issued_at": now_iso(),
            },
        ),
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        max_age=COOKIE_MAX_AGE_SECONDS,
    )
    return response


@app.post("/web/lock")
def web_lock(request: Request) -> JSONResponse:
    session_id, ip = require_web_marker(request)
    owner = get_web_owner_cookie(request, allow_missing=True)
    if owner is not None:
        log_auth_event(
            created_at=now_iso(),
            scope="web",
            event_type="lock",
            success=True,
            owner_type=owner[0],
            owner_id=owner[1],
            client_ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            detail="lock success",
        )
    touch_web_session(session_id, ip, owner_id=None)
    response = JSONResponse({"ok": True})
    clear_cookie(response, WEB_OWNER_COOKIE)
    return response


@app.get("/web/stats")
def web_stats(request: Request) -> dict[str, Any]:
    session_id = get_or_create_web_session_id(request)
    ip = get_client_ip(request)
    owner = get_web_owner_cookie(request, allow_missing=True)
    touch_web_session(session_id, ip, owner_id=owner[1] if owner else None)
    stats = get_live_stats(owner[1] if owner else None)
    return {
        "unlocked": owner is not None,
        "owner_hint": owner_hint(owner[1]) if owner else "",
        **stats,
    }


@app.post("/web/ping")
def web_ping(request: Request) -> dict[str, Any]:
    session_id = get_or_create_web_session_id(request)
    ip = get_client_ip(request)
    owner = get_web_owner_cookie(request, allow_missing=True)
    touch_web_session(session_id, ip, owner_id=owner[1] if owner else None)
    stats = get_live_stats(owner[1] if owner else None)
    return {
        "unlocked": owner is not None,
        "owner_hint": owner_hint(owner[1]) if owner else "",
        **stats,
    }


@app.get("/web/history")
def web_history(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=60),
) -> dict[str, Any]:
    _, _, owner_type, owner_id = require_web_owner(request)
    items, has_more = build_history_items(owner_type, owner_id, offset, limit)
    return {
        "items": items,
        "jobs": build_running_job_items(owner_type, owner_id),
        "offset": offset,
        "next_offset": offset + len(items),
        "has_more": has_more,
    }


@app.get("/web/providers")
def web_providers(request: Request) -> dict[str, Any]:
    _, _, _, _ = require_web_owner(request)
    providers = public_provider_profiles()
    return {"items": providers, "default_provider_id": providers[0]["id"] if providers else ""}


@app.post("/web/generate")
def web_generate(request: Request, payload: GenerateRequest) -> dict[str, Any]:
    session_id, ip, owner_type, owner_id = require_web_owner(request)
    prompt, request_params = build_generate_params(payload)
    return enqueue_web_job(
        slot_key=owner_id,
        prompt=prompt,
        route="/web/generate",
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        owner_type=owner_type,
        owner_id=owner_id,
        operation="generate",
        request_params=request_params,
    )


@app.get("/web/jobs/{job_id}")
def web_job_status(request: Request, job_id: str) -> dict[str, Any]:
    _, _, owner_type, owner_id = require_web_owner(request)
    detail = get_owner_job_detail(job_id, owner_type, owner_id)
    if detail is not None:
        if detail.get("deleted_at"):
            raise HTTPException(status_code=404, detail="Job not found.")
        payload = serialize_owner_job(detail)
        memory_state = get_web_job_state(job_id)
        if payload.get("status") in {"success", "failed"}:
            if payload.get("status") == "failed":
                release_web_job_slot(job_id)
            return payload
        if payload.get("status") == "running" and memory_state is None:
            log_generation_failed(
                job_id=job_id,
                completed_at=now_iso(),
                elapsed_seconds=0,
                error_message="任务状态丢失，已释放队列",
            )
            payload["status"] = "failed"
            payload["error_message"] = "任务状态丢失，已释放队列"
            return payload
        if memory_state and payload.get("status") == "running" and memory_state.get("status") in {"failed", "success"}:
            payload["status"] = memory_state.get("status")
            payload["error_message"] = memory_state.get("error_message") or payload.get("error_message")
        return payload

    memory_state = get_web_job_state(job_id)
    if memory_state is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "job_id": job_id,
        "status": memory_state.get("status") or "running",
        "error_message": memory_state.get("error_message") or "",
        "actual_params": {},
        "images": [],
        "image_count": 0,
    }


@app.post("/web/jobs/{job_id}/cancel")
def web_job_cancel(request: Request, job_id: str) -> dict[str, Any]:
    _, _, owner_type, owner_id = require_web_owner(request)
    detail = get_owner_job_detail(job_id, owner_type, owner_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if detail.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Job not found.")

    if detail.get("status") != "running":
        return serialize_owner_job(detail)

    state = get_web_job_state(job_id) or {}
    started_at = float(state.get("started_at") or time.time())
    mark_web_job_failed(job_id, started_at, "用户已停止")
    updated = get_owner_job_detail(job_id, owner_type, owner_id)
    if updated is None:
        return {"job_id": job_id, "status": "failed", "error_message": "用户已停止", "images": [], "image_count": 0}
    return serialize_owner_job(updated)


@app.post("/web/jobs/{job_id}/delete")
def web_job_delete(request: Request, job_id: str) -> dict[str, Any]:
    _, _, owner_type, owner_id = require_web_owner(request)
    deleted_at = now_iso()
    saved_paths, deleted = soft_delete_owner_job(
        job_id,
        owner_type,
        owner_id,
        deleted_at=deleted_at,
        deleted_by="web",
        deleted_reason="user deleted from web",
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found.")
    removed_files = remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "job_id": job_id,
        "deleted_at": deleted_at,
        "removed_files": removed_files,
    }


@app.post("/web/jobs/delete-batch")
def web_jobs_delete_batch(request: Request, payload: WebJobsDeleteRequest) -> dict[str, Any]:
    _, _, owner_type, owner_id = require_web_owner(request)
    deleted_at = now_iso()
    deleted_job_ids: list[str] = []
    removed_files = 0
    for job_id in payload.job_ids:
        normalized_job_id = clean_text(job_id)
        if not normalized_job_id:
            continue
        saved_paths, deleted = soft_delete_owner_job(
            normalized_job_id,
            owner_type,
            owner_id,
            deleted_at=deleted_at,
            deleted_by="web",
            deleted_reason="user batch deleted from web",
        )
        if deleted:
            deleted_job_ids.append(normalized_job_id)
            removed_files += remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "deleted_job_ids": deleted_job_ids,
        "deleted_count": len(deleted_job_ids),
        "removed_files": removed_files,
    }


@app.post("/web/edit")
async def web_edit(request: Request) -> dict[str, Any]:
    session_id, ip, owner_type, owner_id = require_web_owner(request)
    form = await request.form()
    prompt, request_params, image_uploads, mask_upload = parse_edit_form(form)
    job_prefix = build_job_id()
    prepared_image_paths: list[Path] = []
    prepared_source_hashes: set[str] = set()
    prepared_mask_path: Path | None = None
    try:
        for idx, upload in enumerate(image_uploads, start=1):
            image_path, image_hash = materialize_upload(upload, f"{job_prefix}_image_{idx}")
            prepared_image_paths.append(image_path)
            prepared_source_hashes.add(image_hash)
        if mask_upload:
            prepared_mask_path, _ = materialize_upload(mask_upload, f"{job_prefix}_mask")
        return enqueue_web_job(
            slot_key=owner_id,
            prompt=prompt,
            route="/web/edit",
            ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            owner_type=owner_type,
            owner_id=owner_id,
            operation="edit",
            request_params=request_params,
            prepared_image_paths=prepared_image_paths,
            prepared_mask_path=prepared_mask_path,
            prepared_source_hashes=prepared_source_hashes,
            input_image_count=len(prepared_image_paths),
            mask_used=prepared_mask_path is not None,
        )
    except Exception:
        for path in prepared_image_paths + ([prepared_mask_path] if prepared_mask_path else []):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


@app.post("/web/image")
async def web_image_reference(request: Request) -> dict[str, Any]:
    session_id, ip, owner_type, owner_id = require_web_owner(request)
    form = await request.form()
    prompt, request_params, image_uploads, mask_upload = parse_edit_form(form)
    if mask_upload is not None:
        raise HTTPException(status_code=400, detail="Use /web/edit when a mask is provided.")
    job_prefix = build_job_id()
    prepared_image_paths: list[Path] = []
    prepared_source_hashes: set[str] = set()
    try:
        for idx, upload in enumerate(image_uploads, start=1):
            image_path, image_hash = materialize_upload(upload, f"{job_prefix}_image_{idx}")
            prepared_image_paths.append(image_path)
            prepared_source_hashes.add(image_hash)
        return enqueue_web_job(
            slot_key=owner_id,
            prompt=prompt,
            route="/web/image",
            ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            owner_type=owner_type,
            owner_id=owner_id,
            operation="reference",
            request_params=request_params,
            prepared_image_paths=prepared_image_paths,
            prepared_source_hashes=prepared_source_hashes,
            input_image_count=len(prepared_image_paths),
            mask_used=False,
        )
    except Exception:
        for path in prepared_image_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


@app.get("/web/images/{job_id}/{image_index}")
def web_image(request: Request, job_id: str, image_index: int) -> FileResponse:
    owner_type, owner_id = require_web_image_owner(request)
    record = get_image_record(job_id, image_index)
    if (
        record is None
        or record.get("scope") != "web"
        or record.get("deleted_at")
        or record.get("image_deleted_at")
        or record.get("files_removed_at")
        or record.get("image_files_removed_at")
    ):
        raise HTTPException(status_code=404, detail="Image not found.")
    if record.get("owner_type") != owner_type or record.get("owner_id") != owner_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this image.")
    return serve_image_file(record.get("saved_path"))


@app.get("/web/thumbs/{job_id}/{image_index}")
def web_thumbnail(request: Request, job_id: str, image_index: int) -> FileResponse:
    owner_type, owner_id = require_web_image_owner(request)
    record = get_image_record(job_id, image_index)
    if (
        record is None
        or record.get("scope") != "web"
        or record.get("deleted_at")
        or record.get("image_deleted_at")
        or record.get("files_removed_at")
        or record.get("image_files_removed_at")
    ):
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    if record.get("owner_type") != owner_type or record.get("owner_id") != owner_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this image.")
    return serve_thumbnail_file(record)


@app.get("/web/input-images/{job_id}/{image_index}")
def web_input_image(request: Request, job_id: str, image_index: int) -> FileResponse:
    owner_type, owner_id = require_web_image_owner(request)
    record = get_image_record(job_id, 1)
    if record is None or record.get("scope") != "web":
        raise HTTPException(status_code=404, detail="Not found.")
    if record.get("owner_type") != owner_type or record.get("owner_id") != owner_id:
        raise HTTPException(status_code=403, detail="Not allowed.")
    input_record = get_input_image(job_id, image_index, "input")
    if not input_record:
        raise HTTPException(status_code=404, detail="Input image not found.")
    path = resolve_storage_path(input_record["saved_path"], {"generated"})
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Input image file not found.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@app.get("/web/input-mask/{job_id}")
def web_input_mask(request: Request, job_id: str) -> FileResponse:
    owner_type, owner_id = require_web_image_owner(request)
    record = get_image_record(job_id, 1)
    if record is None or record.get("scope") != "web":
        raise HTTPException(status_code=404, detail="Not found.")
    if record.get("owner_type") != owner_type or record.get("owner_id") != owner_id:
        raise HTTPException(status_code=403, detail="Not allowed.")
    input_record = get_input_image(job_id, 0, "mask")
    if not input_record:
        raise HTTPException(status_code=404, detail="Mask not found.")
    path = resolve_storage_path(input_record["saved_path"], {"generated"})
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Mask file not found.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@app.post("/api/v1/generate")
def api_generate(request: Request, payload: GenerateRequest) -> dict[str, Any]:
    ip, owner_type, owner_id = require_api_owner(request)
    prompt, request_params = build_generate_params(payload)
    with reserve_generation_slot(owner_id, "api", owner_ip=ip):
        result = create_job(
            prompt=prompt,
            scope="api",
            route="/api/v1/generate",
            ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            owner_type=owner_type,
            owner_id=owner_id,
            operation="generate",
            request_params=request_params,
        )
    return serialize_public_api_job(result)


@app.post("/api/v1/edit")
async def api_edit(request: Request) -> dict[str, Any]:
    ip, owner_type, owner_id = require_api_owner(request)
    form = await request.form()
    prompt, request_params, image_uploads, mask_upload = parse_edit_form(form)
    with reserve_generation_slot(owner_id, "api", owner_ip=ip):
        result = create_job(
            prompt=prompt,
            scope="api",
            route="/api/v1/edit",
            ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            owner_type=owner_type,
            owner_id=owner_id,
            operation="edit",
            request_params=request_params,
            image_uploads=image_uploads,
            mask_upload=mask_upload,
        )
    return serialize_public_api_job(result)


@app.get("/api/v1/images/{job_id}/{image_index}")
def api_image(request: Request, job_id: str, image_index: int) -> FileResponse:
    _, owner_type, owner_id = require_api_owner(request)
    record = get_image_record(job_id, image_index)
    if (
        record is None
        or record.get("scope") != "api"
        or record.get("owner_type") != owner_type
        or record.get("owner_id") != owner_id
        or record.get("deleted_at")
        or record.get("image_deleted_at")
        or record.get("files_removed_at")
        or record.get("image_files_removed_at")
    ):
        raise HTTPException(status_code=404, detail="Image not found.")
    return serve_image_file(record.get("saved_path"))


@app.get("/api/v1/thumbs/{job_id}/{image_index}")
def api_thumbnail(request: Request, job_id: str, image_index: int) -> FileResponse:
    _, owner_type, owner_id = require_api_owner(request)
    record = get_image_record(job_id, image_index)
    if (
        record is None
        or record.get("scope") != "api"
        or record.get("owner_type") != owner_type
        or record.get("owner_id") != owner_id
        or record.get("deleted_at")
        or record.get("image_deleted_at")
        or record.get("files_removed_at")
        or record.get("image_files_removed_at")
    ):
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return serve_thumbnail_file(record)


@app.get("/admin/session")
def admin_session(request: Request) -> dict[str, Any]:
    require_admin_marker(request)
    payload = get_admin_cookie(request, allow_missing=True)
    return {"authenticated": payload is not None}


@app.post("/admin/login")
def admin_login(request: Request, payload: AdminLoginRequest) -> JSONResponse:
    ip = require_admin_marker(request)
    password = clean_text(payload.password) or ""
    if password != get_admin_password():
        log_auth_event(
            created_at=now_iso(),
            scope="admin",
            event_type="login",
            success=False,
            client_ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            detail="invalid password",
        )
        raise HTTPException(status_code=401, detail="Invalid admin password.")

    log_auth_event(
        created_at=now_iso(),
        scope="admin",
        event_type="login",
        success=True,
        client_ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        detail="login success",
    )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=ADMIN_COOKIE,
        value=sign_cookie("admin", {"issued_at": now_iso()}),
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        max_age=ADMIN_COOKIE_MAX_AGE_SECONDS,
    )
    return response


@app.post("/admin/logout")
def admin_logout(request: Request) -> JSONResponse:
    ip = require_admin_marker(request)
    if get_admin_cookie(request, allow_missing=True) is not None:
        log_auth_event(
            created_at=now_iso(),
            scope="admin",
            event_type="logout",
            success=True,
            client_ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            detail="logout success",
        )
    response = JSONResponse({"ok": True})
    clear_cookie(response, ADMIN_COOKIE)
    return response


@app.get("/admin/overview")
def admin_overview(request: Request) -> dict[str, Any]:
    require_admin(request)
    stats = get_live_stats()
    overview = get_admin_overview()
    return {**overview, **stats}


@app.get("/admin/dashboard")
def admin_dashboard(request: Request) -> dict[str, Any]:
    require_admin(request)
    payload = get_admin_dashboard()
    payload["live"] = get_live_stats()
    return payload


@app.get("/admin/gallery")
def admin_gallery(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=36, ge=1, le=120),
    search: str | None = None,
    scope: str | None = None,
    operation: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    include_deleted: bool = Query(default=False),
    deleted: str | None = None,
) -> dict[str, Any]:
    require_admin(request)
    rows, total = list_admin_gallery_images(
        offset=offset,
        limit=limit,
        search=clean_text(search),
        scope=clean_text(scope),
        operation=clean_text(operation),
        owner_type=clean_text(owner_type),
        owner_id=clean_text(owner_id),
        include_deleted=include_deleted,
        deleted=clean_text(deleted),
    )
    items = [serialize_admin_gallery_image(row) for row in rows]
    return {
        "items": items,
        "offset": offset,
        "next_offset": offset + len(items),
        "total": total,
        "has_more": offset + len(items) < total,
    }


@app.get("/admin/jobs")
def admin_jobs(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    search: str | None = None,
    status: str | None = None,
    scope: str | None = None,
    operation: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    deleted: str | None = None,
) -> dict[str, Any]:
    require_admin(request)
    rows, total = list_admin_jobs(
        offset=offset,
        limit=limit,
        search=clean_text(search),
        status=clean_text(status),
        scope=clean_text(scope),
        operation=clean_text(operation),
        owner_type=clean_text(owner_type),
        owner_id=clean_text(owner_id),
        deleted=clean_text(deleted),
    )
    items = [serialize_admin_job(row) for row in rows]
    return {
        "items": items,
        "offset": offset,
        "next_offset": offset + len(items),
        "total": total,
        "has_more": offset + len(items) < total,
    }


@app.get("/admin/jobs/{job_id}")
def admin_job_detail(request: Request, job_id: str) -> dict[str, Any]:
    require_admin(request)
    detail = get_admin_job_detail(job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    request_params_json = detail.get("request_params_json")
    if request_params_json:
        try:
            detail["request_params"] = json.loads(request_params_json)
        except Exception:
            detail["request_params"] = request_params_json
    else:
        detail["request_params"] = {}
    detail["actual_params"] = parse_request_params(detail.get("response_params_json"))
    detail["owner_hint"] = owner_hint(str(detail.get("owner_id") or "")) if detail.get("owner_id") else ""
    images = []
    for image in detail.get("images", []):
        image_payload = dict(image)
        deleted = bool(detail.get("deleted_at") or image_payload.get("deleted_at"))
        files_removed = bool(detail.get("files_removed_at") or image_payload.get("files_removed_at"))
        if deleted or files_removed:
            image_payload["url"] = None
            image_payload["thumbnail_url"] = None
        else:
            image_index = int(image_payload.get("image_index", 0) or 0)
            image_payload["url"] = build_image_url("admin", job_id, image_index)
            image_payload["thumbnail_url"] = build_thumbnail_url(job_id, image_index)
        images.append(image_payload)
    detail["images"] = images
    return detail


@app.post("/admin/jobs/delete")
def admin_delete_job(request: Request, payload: AdminJobDeleteRequest) -> dict[str, Any]:
    require_admin(request)
    saved_paths, deleted = delete_job(payload.job_id)
    removed_files = remove_saved_paths(saved_paths)
    return {
        "ok": deleted,
        "job_id": payload.job_id,
        "removed_files": removed_files,
    }


@app.post("/admin/jobs/soft-delete")
def admin_soft_delete_jobs(request: Request, payload: AdminJobsSoftDeleteRequest) -> dict[str, Any]:
    require_admin(request)
    deleted_at = now_iso()
    reason = clean_text(payload.reason) or "admin soft delete"
    deleted_job_ids: list[str] = []
    removed_files = 0
    for raw_job_id in payload.job_ids:
        job_id = clean_text(raw_job_id)
        if not job_id:
            continue
        saved_paths, deleted = soft_delete_job(
            job_id,
            deleted_at=deleted_at,
            deleted_by="admin",
            deleted_reason=reason,
        )
        if deleted:
            deleted_job_ids.append(job_id)
            removed_files += remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "deleted_job_ids": deleted_job_ids,
        "deleted_count": len(deleted_job_ids),
        "removed_files": removed_files,
    }


@app.post("/admin/images/soft-delete")
def admin_soft_delete_images(request: Request, payload: AdminImagesSoftDeleteRequest) -> dict[str, Any]:
    require_admin(request)
    deleted_at = now_iso()
    reason = clean_text(payload.reason) or "admin image soft delete"
    deleted_images: list[dict[str, Any]] = []
    removed_files = 0
    for image in payload.images:
        saved_paths, deleted = soft_delete_image(
            image.job_id,
            image.image_index,
            deleted_at=deleted_at,
            deleted_by="admin",
            deleted_reason=reason,
        )
        if deleted:
            deleted_images.append({"job_id": image.job_id, "image_index": image.image_index})
            removed_files += remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "deleted_images": deleted_images,
        "deleted_count": len(deleted_images),
        "removed_files": removed_files,
    }


@app.get("/admin/owners")
def admin_owners(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    search: str | None = None,
    owner_type: str | None = None,
    blocked_only: bool = Query(default=False),
) -> dict[str, Any]:
    require_admin(request)
    rows, total = list_admin_owners(
        offset=offset,
        limit=limit,
        search=clean_text(search),
        owner_type=clean_text(owner_type),
        blocked_only=blocked_only,
    )
    for row in rows:
        row["owner_hint"] = owner_hint(str(row.get("owner_id") or "")) if row.get("owner_id") else ""
    return {
        "items": rows,
        "offset": offset,
        "next_offset": offset + len(rows),
        "total": total,
        "has_more": offset + len(rows) < total,
    }


@app.post("/admin/owner-lookup")
def admin_owner_lookup(request: Request, payload: AdminOwnerLookupRequest) -> dict[str, Any]:
    require_admin(request)
    passphrase = normalize_passphrase(payload.passphrase)
    owner_id = derive_owner_id(passphrase)
    owner = lookup_owner(WEB_OWNER_TYPE, owner_id)
    return {
        "owner_type": WEB_OWNER_TYPE,
        "owner_id": owner_id,
        "owner_hint": owner_hint(owner_id),
        "owner": owner,
    }


@app.post("/admin/owners/label")
def admin_owner_label(request: Request, payload: AdminOwnerLabelRequest) -> dict[str, Any]:
    require_admin(request)
    set_owner_label(
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        label=clean_text(payload.label) or "",
        note=clean_text(payload.note) or "",
        updated_at=now_iso(),
    )
    owner = lookup_owner(payload.owner_type, payload.owner_id)
    return {"ok": True, "owner": owner}


@app.post("/admin/owners/block")
def admin_owner_block(request: Request, payload: AdminOwnerBlockRequest) -> dict[str, Any]:
    require_admin(request)
    set_owner_block(
        owner_type=payload.owner_type,
        owner_id=payload.owner_id,
        blocked=payload.blocked,
        reason=clean_text(payload.reason) or "",
        timestamp=now_iso(),
    )
    owner = lookup_owner(payload.owner_type, payload.owner_id)
    return {"ok": True, "owner": owner}


@app.post("/admin/owners/block-batch")
def admin_owner_block_batch(request: Request, payload: AdminOwnersBlockBatchRequest) -> dict[str, Any]:
    require_admin(request)
    timestamp = now_iso()
    reason = clean_text(payload.reason) or ""
    updated: list[dict[str, Any]] = []
    for owner in payload.owners:
        set_owner_block(
            owner_type=owner.owner_type,
            owner_id=owner.owner_id,
            blocked=payload.blocked,
            reason=reason,
            timestamp=timestamp,
        )
        updated_owner = lookup_owner(owner.owner_type, owner.owner_id)
        if updated_owner:
            updated.append(updated_owner)
    return {"ok": True, "updated_count": len(updated), "owners": updated}


@app.post("/admin/owners/delete")
def admin_owner_delete(request: Request, payload: AdminOwnerDeleteRequest) -> dict[str, Any]:
    require_admin(request)
    saved_paths, deleted_jobs = soft_delete_owner_jobs(
        payload.owner_type,
        payload.owner_id,
        deleted_at=now_iso(),
        deleted_by="admin",
        deleted_reason=clean_text(payload.reason) or "admin owner soft delete",
    )
    removed_files = remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "owner_type": payload.owner_type,
        "owner_id": payload.owner_id,
        "deleted_jobs": deleted_jobs,
        "removed_files": removed_files,
    }


@app.post("/admin/owners/hard-delete")
def admin_owner_hard_delete(request: Request, payload: AdminOwnerDeleteRequest) -> dict[str, Any]:
    require_admin(request)
    saved_paths, deleted_jobs = delete_owner_jobs(payload.owner_type, payload.owner_id)
    removed_files = remove_saved_paths(saved_paths)
    return {
        "ok": True,
        "owner_type": payload.owner_type,
        "owner_id": payload.owner_id,
        "deleted_jobs": deleted_jobs,
        "removed_files": removed_files,
    }


@app.get("/admin/providers")
def admin_list_providers(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {
        "items": list_provider_profiles(include_disabled=True, include_secret=False),
        "defaults": {
            "provider_type": "openai-compatible",
            "parameters": DEFAULT_PROVIDER_PARAMETERS,
        },
    }


@app.post("/admin/providers")
def admin_save_provider(request: Request, payload: AdminProviderProfileRequest) -> dict[str, Any]:
    require_admin(request)
    try:
        raw_payload = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        profile = upsert_provider_profile(raw_payload, updated_at=now_iso())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "profile": profile}


@app.delete("/admin/providers/{provider_id}")
def admin_delete_provider(request: Request, provider_id: str) -> dict[str, Any]:
    require_admin(request)
    deleted = delete_provider_profile(provider_id)
    return {"ok": deleted, "provider_id": provider_id}


@app.get("/admin/auth-events")
def admin_auth_events(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    scope: str | None = None,
    success: bool | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    require_admin(request)
    rows, total = list_auth_events(
        offset=offset,
        limit=limit,
        scope=clean_text(scope),
        success=success,
        search=clean_text(search),
    )
    return {
        "items": rows,
        "offset": offset,
        "next_offset": offset + len(rows),
        "total": total,
        "has_more": offset + len(rows) < total,
    }


@app.get("/admin/system")
def admin_system(request: Request) -> dict[str, Any]:
    require_admin(request)
    return build_system_status()


CONFIGURABLE_KEYS = {
    "user_concurrency_limit": {"type": "int", "min": 1, "max": 20, "label": "用户并发上限"},
    "image_api_timeout": {"type": "float", "min": 10, "max": 3600, "label": "API超时(秒)"},
    "image_model": {"type": "str", "label": "模型名称"},
    "min_web_passphrase_length": {"type": "int", "min": 4, "max": 64, "label": "口令最短长度"},
}


@app.get("/admin/config")
def admin_get_config(request: Request) -> dict[str, Any]:
    require_admin(request)
    saved = get_all_runtime_config()
    result: dict[str, Any] = {}
    for key, meta in CONFIGURABLE_KEYS.items():
        if key in saved:
            result[key] = saved[key]
        elif key == "user_concurrency_limit":
            result[key] = str(DEFAULT_USER_CONCURRENCY_LIMIT)
        elif key == "image_api_timeout":
            result[key] = get_env("IMAGE_API_TIMEOUT", "360") or "360"
        elif key == "image_model":
            result[key] = get_env("IMAGE_MODEL", "gpt-image-2") or "gpt-image-2"
        elif key == "min_web_passphrase_length":
            result[key] = get_env("MIN_WEB_PASSPHRASE_LENGTH", "6") or "6"
        else:
            result[key] = ""
    return {"config": result, "schema": CONFIGURABLE_KEYS}


@app.post("/admin/config")
def admin_set_config(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_admin(request)
    updated: list[str] = []
    for key, value in payload.items():
        if key not in CONFIGURABLE_KEYS:
            continue
        meta = CONFIGURABLE_KEYS[key]
        str_value = str(value).strip()
        if meta["type"] == "int":
            try:
                int_val = int(str_value)
                int_val = max(meta.get("min", 1), min(meta.get("max", 9999), int_val))
                str_value = str(int_val)
            except ValueError:
                continue
        elif meta["type"] == "float":
            try:
                float_val = float(str_value)
                float_val = max(meta.get("min", 0), min(meta.get("max", 99999), float_val))
                str_value = str(int(float_val)) if float_val == int(float_val) else str(float_val)
            except ValueError:
                continue
        set_runtime_config(key, str_value)
        updated.append(key)
    return {"updated": updated}


@app.get("/admin/images/{job_id}/{image_index}")
def admin_image(request: Request, job_id: str, image_index: int) -> FileResponse:
    require_admin_image_access(request)
    record = get_image_record(job_id, image_index)
    if record is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return serve_image_file(record.get("saved_path"))


@app.get("/admin/thumbs/{job_id}/{image_index}")
def admin_thumbnail(request: Request, job_id: str, image_index: int) -> FileResponse:
    require_admin_image_access(request)
    record = get_image_record(job_id, image_index)
    if record is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return serve_thumbnail_file(record)


@app.get("/api/v1", include_in_schema=False)
def api_catalog(request: Request) -> dict[str, Any]:
    return api_catalog_payload()


@app.get("/api/v1/docs", include_in_schema=False)
def api_docs() -> HTMLResponse:
    return HTMLResponse(api_docs_html())


@app.get("/api/v1/docs.md", include_in_schema=False)
def api_docs_markdown() -> FileResponse:
    return FileResponse(BASE_DIR / "API.md")
