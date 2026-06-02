"""Agnes Image 2.1 Flash provider."""

import json
from typing import Any, Dict, Iterable, List

import aiohttp
from astrbot.api import logger

from ..constants import DEFAULT_AGNES_IMAGE_SIZE
from .base import (
    BaseProvider,
    PROVIDER_INTERNAL_KWARG_KEYS,
    build_agnes_image_endpoint,
    extract_error_message,
    extract_image_url_from_response,
    summarize_payload_json_for_log,
    summarize_response_text_for_log,
)


class AgnesImageProvider(BaseProvider):
    """Agnes Image 专用 JSON 通道，支持文生图和 URL 图生图。"""

    def _as_list(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]

    def _valid_url_refs(self, refs: Iterable[str]) -> List[str]:
        seen = set()
        urls = []
        for ref in refs:
            ref_text = str(ref or "").strip()
            if not ref_text.startswith("http"):
                continue
            if ref_text in seen:
                continue
            seen.add(ref_text)
            urls.append(ref_text)
        return urls

    def _reference_urls(self, kwargs: Dict[str, Any]) -> List[str]:
        source_urls = []
        for key in ("_omnidraw_user_ref_urls", "_omnidraw_persona_ref_urls"):
            source_urls.extend(self._as_list(kwargs.get(key)))
        if source_urls:
            return self._valid_url_refs(source_urls)

        refs = self.get_reference_images(**kwargs)
        url_refs = self._valid_url_refs(refs)
        local_refs = [ref for ref in refs if ref and not str(ref).startswith("http")]
        if local_refs:
            raise RuntimeError(
                "Agnes Image 图生图只支持可公网访问的图片 URL；"
                "当前参考图已被保存为本地路径，无法直接提交给 Agnes。"
            )
        return url_refs

    def _parse_extra_body(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except Exception:
                logger.warning("⚠️ [Agnes Image] extra_body 不是合法 JSON 对象，已忽略。")
                return {}
            if isinstance(parsed, dict):
                return parsed
            logger.warning("⚠️ [Agnes Image] extra_body JSON 不是对象，已忽略。")
        return {}

    async def _parse_response(self, response: aiohttp.ClientResponse, endpoint: str) -> str:
        text = await response.text()
        if response.status >= 400:
            logger.error("💥 Agnes Image API 返回错误摘要: " + summarize_response_text_for_log(text, max_string_length=500))
            raise RuntimeError(f"HTTP {response.status}: {extract_error_message(text)}")

        try:
            payload = json.loads(text)
        except Exception:
            payload = text

        image_url = extract_image_url_from_response(payload, endpoint)
        if image_url:
            return image_url
        raise ValueError(
            "Agnes Image 返回结构异常，未找到图片 URL: "
            + summarize_payload_json_for_log(payload, max_string_length=500)
        )

    async def generate_image(self, prompt: str, **kwargs: Any) -> str:
        current_key = self.get_current_key()
        if not current_key:
            raise ValueError("节点未配置 API Key！")
        if not self.config.model:
            raise ValueError("Agnes Image 节点未配置模型名！")

        endpoint = build_agnes_image_endpoint(self.config.base_url)
        if not endpoint:
            raise ValueError("Agnes Image 节点未配置接口地址！")

        api_kwargs = {key: value for key, value in kwargs.items() if key not in PROVIDER_INTERNAL_KWARG_KEYS}
        extra_body = self._parse_extra_body(api_kwargs.pop("extra_body", {}))
        response_format = api_kwargs.pop("response_format", None)
        extra_body.setdefault("response_format", str(response_format or "url"))

        image_arg = api_kwargs.pop("image", None)
        ref_urls = self._reference_urls(kwargs)
        ref_urls.extend(self._valid_url_refs(self._as_list(image_arg)))
        ref_urls = self._valid_url_refs(ref_urls)
        if ref_urls:
            extra_body["image"] = ref_urls

        payload: Dict[str, Any] = {
            "model": self.config.model,
            "prompt": prompt,
            "size": str(api_kwargs.pop("size", DEFAULT_AGNES_IMAGE_SIZE) or DEFAULT_AGNES_IMAGE_SIZE),
        }
        if extra_body:
            payload["extra_body"] = extra_body
        payload.update(api_kwargs)

        logger.info(f"📝 [Agnes Image] 最终发送给 API 的核心提示词:\n{prompt}")
        logger.info(f"📤 [Agnes Image] 请求体摘要: {summarize_payload_json_for_log(payload)}")

        headers = {
            "Authorization": "Bearer " + current_key,
            "Content-Type": "application/json",
        }
        timeout_obj = aiohttp.ClientTimeout(total=self.config.timeout)
        async with self.session.post(endpoint, json=payload, headers=headers, timeout=timeout_obj) as response:
            return await self._parse_response(response, endpoint)
