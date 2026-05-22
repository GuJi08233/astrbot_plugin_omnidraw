"""图片 Provider 基类。"""
import aiohttp
import base64
import json
import mimetypes
import os
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional, List
from urllib.parse import urljoin, urlparse
from astrbot.api import logger
from ..models import ProviderConfig

_KEY_ROTATION_LOCK = threading.Lock()
_KEY_ROTATION_INDEX: Dict[str, int] = {}


def normalize_base_url(base_url: str) -> str:
    return str(base_url or "").rstrip("/")


def is_complete_endpoint_url(base_url: str) -> bool:
    """Return True only for full URLs that point at a concrete request path."""
    parsed = urlparse(normalize_base_url(base_url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    path = parsed.path.rstrip("/")
    if not path:
        return False
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return False
    version_like = re.compile(r"^v\d+(?:beta\d*)?$", re.IGNORECASE)
    if len(segments) == 1 and version_like.fullmatch(segments[0]):
        return False
    if segments[-1].lower() == "api" or version_like.fullmatch(segments[-1]):
        return False
    return True


def _has_endpoint_path(base_url: str, endpoint_suffixes: Iterable[str]) -> bool:
    lowered = base_url.lower()
    return any(lowered.endswith(suffix) for suffix in endpoint_suffixes)


def _replace_endpoint_path(base_url: str, endpoint_suffix: str, replacement_suffix: str) -> str:
    if base_url.lower().endswith(endpoint_suffix):
        return base_url[: -len(endpoint_suffix)] + replacement_suffix
    return base_url


def strip_known_endpoint_path(base_url: str) -> str:
    base_url = normalize_base_url(base_url)
    for suffix in (
        "/chat/completions",
        "/responses",
        "/images/generations",
        "/images/edits",
        "/videos/generations",
    ):
        if base_url.lower().endswith(suffix):
            return base_url[: -len(suffix)]
    return base_url


def response_base_url(base_url: str) -> str:
    api_root = strip_known_endpoint_path(base_url)
    return api_root[:-3] if api_root.endswith("/v1") else api_root


def resolve_response_url(value: str, base_url: str) -> str:
    image_ref = str(value or "").strip()
    if image_ref.startswith("http") or image_ref.startswith("data:"):
        return image_ref
    return urljoin(response_base_url(base_url).rstrip("/") + "/", image_ref.lstrip("/"))


def build_chat_completions_endpoint(base_url: str) -> str:
    base_url = normalize_base_url(base_url)
    if not base_url:
        return ""
    if _has_endpoint_path(base_url, ["/chat/completions"]):
        return base_url
    base_url = _replace_endpoint_path(base_url, "/responses", "/chat/completions")
    if _has_endpoint_path(base_url, ["/chat/completions"]):
        return base_url
    return f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"


def build_image_generations_endpoint(base_url: str) -> str:
    base_url = normalize_base_url(base_url)
    if not base_url:
        return ""
    if _has_endpoint_path(base_url, ["/images/generations"]):
        return base_url
    base_url = _replace_endpoint_path(base_url, "/images/edits", "/images/generations")
    if _has_endpoint_path(base_url, ["/images/generations"]):
        return base_url
    return f"{base_url}/images/generations"


def build_image_edits_endpoint(base_url: str) -> str:
    base_url = normalize_base_url(base_url)
    if not base_url:
        return ""
    if _has_endpoint_path(base_url, ["/images/generations", "/images/edits"]):
        return base_url
    return f"{base_url}/images/edits"


def build_video_generations_endpoint(base_url: str) -> str:
    base_url = normalize_base_url(base_url)
    if not base_url:
        return ""
    if _has_endpoint_path(base_url, ["/videos/generations"]):
        return base_url
    return f"{base_url}/videos/generations"


def next_api_key(provider_id: str, api_keys: List[str]) -> str:
    keys = [str(key).strip() for key in api_keys if str(key).strip()]
    if not provider_id or not keys:
        return ""
    with _KEY_ROTATION_LOCK:
        idx = _KEY_ROTATION_INDEX.get(provider_id, 0)
        key = keys[idx % len(keys)]
        _KEY_ROTATION_INDEX[provider_id] = (idx + 1) % len(keys)
        return key


def guess_image_content_type(image_path_or_url: str, content_type: str = "", fallback: str = "image/png") -> str:
    media_type = str(content_type or "").strip().split(";", 1)[0].strip()
    if media_type.startswith("image/"):
        return media_type
    source = str(image_path_or_url or "")
    lowered = source.lower()
    if lowered.startswith("data:"):
        header = source.split(",", 1)[0]
        media_type = header[5:].split(";", 1)[0].strip()
        if media_type.startswith("image/"):
            return media_type
    if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
        return "image/jpeg"
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".gif"):
        return "image/gif"
    if lowered.endswith(".avif"):
        return "image/avif"
    if lowered.endswith(".bmp"):
        return "image/bmp"
    if lowered.endswith(".tif") or lowered.endswith(".tiff"):
        return "image/tiff"
    guessed = mimetypes.guess_type(source)[0] or ""
    return guessed if guessed.startswith("image/") else fallback


SENSITIVE_LOG_KEY_MARKERS = ("key", "token", "secret", "authorization", "password")
IMAGE_LOG_KEY_MARKERS = ("image", "b64", "base64", "binary_data")
DATA_IMAGE_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
BEARER_TOKEN_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|authorization|password)\b\s*[:=]\s*['\"]?[^'\"\s,;}]+"
)
OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b")


def extract_error_message(payload: Any) -> str:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return summarize_text_for_log(payload, max_string_length=240)

    if not isinstance(payload, dict):
        if isinstance(payload, (list, tuple)):
            return summarize_payload_json_for_log(payload, max_string_length=240)
        return summarize_text_for_log(str(payload), max_string_length=240)

    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "msg", "detail", "error_msg", "code"):
            value = error.get(key)
            if value:
                if isinstance(value, (dict, list, tuple)):
                    return summarize_payload_json_for_log(value, max_string_length=240)
                return summarize_text_for_log(str(value), max_string_length=240)
        return summarize_payload_json_for_log(error, max_string_length=240)
    if error:
        return summarize_text_for_log(str(error), max_string_length=240)

    for key in ("message", "msg", "detail", "error_msg"):
        if payload.get(key):
            value = payload[key]
            if isinstance(value, (dict, list, tuple)):
                return summarize_payload_json_for_log(value, max_string_length=240)
            return summarize_text_for_log(str(value), max_string_length=240)

    return summarize_payload_json_for_log(payload, max_string_length=240)


def _looks_like_base64_blob(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 120 or len(compact) % 4:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact):
        return False
    try:
        base64.b64decode(compact, validate=True)
        return True
    except Exception:
        return False


def _image_data_url_summary(value: str) -> str:
    header = value.split(",", 1)[0]
    return f"<image_data_url header={header} chars={len(value)}>"


def _redact_text_fragments_for_log(text: str) -> str:
    text = DATA_IMAGE_URL_RE.sub(lambda match: _image_data_url_summary(match.group(0)), text)
    text = BEARER_TOKEN_RE.sub(r"\1<redacted>", text)
    text = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return OPENAI_STYLE_KEY_RE.sub("<redacted>", text)


def summarize_text_for_log(value: str, max_string_length: int = 160, key_hint: str = "") -> str:
    text = str(value or "")
    stripped = text.strip()
    lowered = stripped.lower()
    key_lowered = key_hint.lower()
    if lowered.startswith("data:image"):
        return _image_data_url_summary(stripped)
    if any(marker in key_lowered for marker in IMAGE_LOG_KEY_MARKERS) and _looks_like_base64_blob(stripped):
        return f"<image_base64 chars={len(stripped)}>"
    redacted_text = _redact_text_fragments_for_log(text)
    if len(redacted_text) <= max_string_length:
        return redacted_text
    return f"{redacted_text[:max_string_length]}...<truncated chars={len(redacted_text)}>"


def summarize_payload_for_log(payload: Any, max_string_length: int = 160, key_hint: str = "") -> Any:
    """Build a compact, secret-safe payload summary for logs."""
    if isinstance(payload, dict):
        summary: Dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in SENSITIVE_LOG_KEY_MARKERS):
                summary[key_text] = "<redacted>"
            else:
                summary[key_text] = summarize_payload_for_log(value, max_string_length, key_text)
        return summary
    if isinstance(payload, list):
        return [summarize_payload_for_log(item, max_string_length, key_hint) for item in payload]
    if isinstance(payload, tuple):
        return [summarize_payload_for_log(item, max_string_length, key_hint) for item in payload]
    if not isinstance(payload, str):
        return payload
    return summarize_text_for_log(payload, max_string_length, key_hint)


def summarize_payload_json_for_log(payload: Any, max_string_length: int = 160) -> str:
    return json.dumps(
        summarize_payload_for_log(payload, max_string_length=max_string_length),
        ensure_ascii=False,
        default=str,
    )


def summarize_response_text_for_log(value: str, max_string_length: int = 500) -> str:
    try:
        return summarize_payload_json_for_log(json.loads(value), max_string_length=max_string_length)
    except Exception:
        return summarize_text_for_log(value, max_string_length=max_string_length)


def extract_image_url_from_response(payload: Any, base_url: str) -> str:
    """Extract an image URL or data URL from common image/chat/responses shapes."""

    def likely_base64(value: str) -> bool:
        text = value.strip()
        if len(text) < 80:
            return False
        if not re.fullmatch(r"[A-Za-z0-9+/=\s]+", text):
            return False
        try:
            base64.b64decode(text, validate=False)
            return True
        except Exception:
            return False

    def from_text(text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if text.startswith("data:image"):
            return text
        if text.startswith("{") or text.startswith("["):
            try:
                nested = json.loads(text)
            except Exception:
                nested = None
            if nested is not None:
                nested_image = walk(nested)
                if nested_image:
                    return nested_image
        markdown_match = re.search(r"!\[[^\]]*\]\((data:image[^)]+|https?://[^)\s]+)\)", text)
        if markdown_match:
            return resolve_response_url(markdown_match.group(1), base_url)
        url_match = re.search(r"(https?://[^\s\]\)\"']+)", text)
        if url_match:
            return resolve_response_url(url_match.group(1), base_url)
        return ""

    def coerce_image_value(value: Any, assume_base64: bool = False) -> str:
        if isinstance(value, str):
            text = value.strip()
            extracted = from_text(text)
            if extracted:
                return extracted
            if assume_base64 and likely_base64(text):
                return "data:image/png;base64," + re.sub(r"\s+", "", text)
            return ""
        return walk(value)

    def walk(value: Any) -> str:
        if isinstance(value, str):
            return from_text(value)
        if isinstance(value, list):
            for item in value:
                image = walk(item)
                if image:
                    return image
            return ""
        if not isinstance(value, dict):
            return ""

        if value.get("type") == "image_generation_call" and value.get("result"):
            image = coerce_image_value(value.get("result"), assume_base64=True)
            if image:
                return image

        for key in ("b64_json", "base64", "image_base64", "image_data", "binary_data_base64"):
            if key in value:
                image = coerce_image_value(value.get(key), assume_base64=True)
                if image:
                    return image

        if "image" in value:
            image_value = value.get("image")
            image = coerce_image_value(image_value, assume_base64=True)
            if image:
                return image
            if isinstance(image_value, str):
                text = image_value.strip()
                if re.search(r"\.(?:png|jpe?g|webp|gif|bmp|avif)(?:[?#].*)?$", text, re.IGNORECASE):
                    return resolve_response_url(text, base_url)

        for key in ("url", "image_url", "uri"):
            if key in value:
                nested = value.get(key)
                if isinstance(nested, dict) and "url" in nested:
                    nested = nested.get("url")
                if isinstance(nested, str) and nested.strip():
                    image = from_text(nested)
                    return image or resolve_response_url(nested, base_url)
                image = coerce_image_value(nested)
                if image:
                    return image

        for key in (
            "data",
            "images",
            "image",
            "output",
            "output_text",
            "result",
            "results",
            "choices",
            "message",
            "content",
            "text",
            "artifacts",
            "generations",
        ):
            if key in value:
                image = walk(value.get(key))
                if image:
                    return image

        return ""

    return walk(payload)


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig, session: aiohttp.ClientSession):
        self.config = config
        self.session = session
        self._api_keys = [str(key).strip() for key in self.config.api_keys if str(key).strip()]

    def get_current_key(self) -> str:
        return next_api_key(self.config.id, self._api_keys)

    def encode_local_image_to_base64(self, image_path: str) -> Optional[str]:
        """将本地图片文件转为 API 兼容的 Base64 字符串"""
        if not image_path or not os.path.exists(image_path):
            return None

        logger.info(f"[{self.config.id}] 正在将本地参考图转为 Base64: {image_path}")
        try:
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                mime_type = guess_image_content_type(image_path)
                return f"data:{mime_type};base64,{encoded_string}"
        except Exception as e:
            logger.error(f"❌ 读取本地图片失败: {e}")
            return None

    def get_reference_images(self, **kwargs: Any) -> List[str]:
        refs: List[str] = []
        for key in ("user_refs", "persona_refs"):
            value = kwargs.get(key)
            if isinstance(value, (list, tuple)):
                refs.extend(str(item) for item in value if item)

        for key in ("user_ref", "persona_ref"):
            value = kwargs.get(key)
            if value:
                refs.append(str(value))

        seen = set()
        return [ref for ref in refs if not (ref in seen or seen.add(ref))]

    @abstractmethod
    async def generate_image(self, prompt: str, **kwargs: Any) -> str:
        pass
