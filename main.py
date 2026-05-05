"""
AstrBot 万象画卷插件。

负责命令入口、LLM 工具、配置页面 API、图片缓存与后台视频任务生命周期。
"""
import asyncio
import base64
import binascii
import copy
import json
import mimetypes
import os
import re
import uuid
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional

import aiohttp
from quart import jsonify, request

try:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.message_components import Image, Plain
    from astrbot.api import llm_tool, logger
except ImportError:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.event.components import Image, Plain
    from astrbot.api import llm_tool
    from astrbot.api.utils import logger

try:
    from astrbot.api.event import EventMessageType
except ImportError:
    from astrbot.api.event.filter import EventMessageType

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:
    def get_astrbot_data_path() -> str:
        return os.path.join(os.getcwd(), "data")

from .constants import DEFAULT_BATCH_LIMIT, MAX_IMAGE_BYTES, MessageEmoji
from .core.chain_manager import ChainManager
from .core.parser import CommandParser
from .core.persona_manager import PersonaManager
from .core.prompt_optimizer import PromptOptimizer
from .core.video_manager import VideoManager
from .models import PLUGIN_AUTHOR, PLUGIN_NAME, PLUGIN_VERSION, PluginConfig
from .utils import handle_errors


@register(PLUGIN_NAME, PLUGIN_AUTHOR, f"万象画卷 v{PLUGIN_VERSION}", PLUGIN_VERSION)
class OmniDrawPlugin(Star):
    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)

        self.data_dir = self._resolve_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.config_path = os.path.join(self.data_dir, "omnidraw_persist_config.json")
        self._background_tasks = set()

        self.cmd_parser = CommandParser()
        self._apply_runtime_config(self._load_initial_config(config or {}))

        self.context.register_web_api(
            f"/{PLUGIN_NAME}/get_config",
            self.get_config_handler,
            ["GET"],
            "获取万象画卷配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/save_config",
            self.save_config_handler,
            ["POST"],
            "保存万象画卷配置",
        )

    def _resolve_data_dir(self) -> str:
        base_data_dir = str(get_astrbot_data_path())
        return os.path.join(base_data_dir, "plugin_data", PLUGIN_NAME)

    def _load_initial_config(self, fallback_config: Dict[str, Any]) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    return data
                logger.warning("[OmniDraw] 本地配置不是 JSON 对象，已回退到 AstrBot 配置。")
            except Exception as exc:
                logger.error(f"[OmniDraw] 读取本地持久化配置失败: {exc}", exc_info=True)
        return copy.deepcopy(fallback_config) if isinstance(fallback_config, dict) else {}

    def _apply_runtime_config(self, raw_config: Dict[str, Any]) -> None:
        self.raw_config = raw_config if isinstance(raw_config, dict) else {}
        self.plugin_config = PluginConfig.from_dict(self.raw_config, self.data_dir)
        self.persona_manager = PersonaManager(self.plugin_config)
        self.video_manager = VideoManager(self.plugin_config)
        self.prompt_optimizer = PromptOptimizer(self.plugin_config)

    def _persist_config(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        tmp_path = f"{self.config_path}.{uuid.uuid4().hex}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(self.raw_config, file, ensure_ascii=False, indent=4)
        os.replace(tmp_path, self.config_path)

    def _safe_update_context_config(self) -> None:
        if not hasattr(self.context, "update_config"):
            return
        try:
            self.context.update_config(self.raw_config)
        except Exception as exc:
            logger.warning(f"[OmniDraw] AstrBot 主配置同步失败，已保留本地持久化配置: {exc}")

    async def get_config_handler(self):
        return jsonify(self._config_for_page())

    def _config_for_page(self) -> Dict[str, Any]:
        page_config = copy.deepcopy(self.raw_config)
        persona_config = page_config.get("persona_config")
        if not isinstance(persona_config, dict):
            return page_config

        raw_images = persona_config.get("persona_ref_image", [])
        if not isinstance(raw_images, list):
            raw_images = [raw_images] if raw_images else []
        persona_config["persona_ref_image"] = [self._image_ref_for_page(ref) for ref in raw_images if ref]
        return page_config

    def _image_ref_for_page(self, image_ref: str) -> str:
        image_ref = str(image_ref)
        if image_ref.startswith(("http", "data:image")):
            return image_ref
        if not os.path.exists(image_ref):
            return image_ref
        try:
            if os.path.getsize(image_ref) > MAX_IMAGE_BYTES:
                return image_ref
            mime_type = mimetypes.guess_type(image_ref)[0] or "image/png"
            with open(image_ref, "rb") as file:
                encoded = base64.b64encode(file.read()).decode("utf-8")
            return f"data:{mime_type};base64,{encoded}"
        except OSError:
            return image_ref

    async def save_config_handler(self):
        new_config = await request.get_json(silent=True)
        if not isinstance(new_config, dict):
            return jsonify({"success": False, "message": "配置格式错误：请求体必须是 JSON 对象。"}), 400

        try:
            self._apply_runtime_config(new_config)
            self._persist_config()
            self._safe_update_context_config()
        except Exception as exc:
            logger.error(f"[OmniDraw] 配置保存失败: {exc}", exc_info=True)
            return jsonify({"success": False, "message": f"配置保存失败: {exc}"}), 500

        logger.info(f"[OmniDraw] 配置已持久化并热重载: {self.config_path}")
        return jsonify({"success": True, "message": "配置已保存，热重载生效。"})

    def _create_background_task(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _cleanup(done_task: asyncio.Task) -> None:
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                return
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            if exc:
                logger.error(
                    f"[OmniDraw] 后台任务异常退出: {exc}",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_cleanup)
        return task

    async def terminate(self):
        if not self._background_tasks:
            return
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        logger.info("[OmniDraw] 已取消所有后台视频任务。")

    def _get_event_images(self, event: AstrMessageEvent) -> List[str]:
        images = []
        visited = set()

        def _search(obj: Any) -> None:
            if obj is None or id(obj) in visited:
                return
            visited.add(id(obj))
            obj_type = type(obj).__name__

            if obj_type == "Image":
                path = getattr(obj, "path", getattr(obj, "file", getattr(obj, "file_path", None)))
                url = getattr(obj, "url", None)
                ref = path if path and not str(path).startswith("http") else url
                if ref:
                    images.append(str(ref))
                return

            if obj_type == "Plain":
                text = getattr(obj, "text", "")
                if text and str(text).startswith("data:image"):
                    images.append(str(text))
                return

            if isinstance(obj, (list, tuple)):
                for item in obj:
                    _search(item)
                return

            attrs = []
            if hasattr(obj, "__dict__"):
                attrs.extend(vars(obj).keys())
            if hasattr(obj, "__slots__"):
                attrs.extend(obj.__slots__)
            blocked = {"context", "star", "bot", "provider", "session", "config", "plugin_config"}
            for key in set(attrs) - blocked:
                try:
                    _search(getattr(obj, key))
                except Exception:
                    continue

        _search(getattr(event, "message_obj", None))
        quote_obj = getattr(getattr(event, "message_obj", None), "quote", None)
        if quote_obj:
            _search(quote_obj)

        seen = set()
        return [item for item in images if not (item in seen or seen.add(item))]

    async def _read_response_limited(self, response: aiohttp.ClientResponse, limit: int = MAX_IMAGE_BYTES) -> bytes:
        chunks = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"图片超过大小限制 {limit // 1024 // 1024}MB")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _process_and_save_images(self, raw_images: Iterable[str]) -> List[str]:
        processed_paths = []
        if not raw_images:
            return processed_paths

        save_dir = os.path.join(self.data_dir, "user_refs")
        os.makedirs(save_dir, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        async with aiohttp.ClientSession() as session:
            for img_ref in raw_images:
                if not img_ref:
                    continue
                img_ref = str(img_ref)

                if img_ref.startswith("data:image"):
                    try:
                        b64_data = img_ref.split(",", 1)[1]
                        decoded = base64.b64decode(b64_data, validate=False)
                        if len(decoded) > MAX_IMAGE_BYTES:
                            raise ValueError("Base64 图片超过大小限制")
                        file_path = os.path.join(save_dir, f"ref_{uuid.uuid4().hex[:12]}.png")
                        with open(file_path, "wb") as file:
                            file.write(decoded)
                        processed_paths.append(file_path)
                    except (IndexError, ValueError, binascii.Error, OSError) as exc:
                        logger.warning(f"[OmniDraw] Base64 参考图处理失败: {exc}")
                    continue

                if not img_ref.startswith("http"):
                    abs_path = os.path.abspath(img_ref)
                    if os.path.exists(abs_path):
                        processed_paths.append(abs_path)
                    else:
                        logger.warning(f"[OmniDraw] 本地参考图不存在: {abs_path}")
                    continue

                for attempt in range(1, 4):
                    try:
                        async with session.get(img_ref, headers=headers, timeout=15) as response:
                            if response.status != 200:
                                logger.warning(f"[OmniDraw] 下载参考图失败，状态码 {response.status}: {img_ref}")
                                continue
                            img_data = await self._read_response_limited(response)
                            file_path = os.path.join(save_dir, f"ref_{uuid.uuid4().hex[:12]}.png")
                            with open(file_path, "wb") as file:
                                file.write(img_data)
                            processed_paths.append(file_path)
                            break
                    except Exception as exc:
                        if attempt == 3:
                            logger.warning(f"[OmniDraw] 参考图下载失败: {img_ref} ({exc})")
                        else:
                            await asyncio.sleep(1)

        return processed_paths

    def _normalize_count(self, count: Any) -> int:
        try:
            parsed_count = int(float(str(count).strip()))
        except Exception:
            parsed_count = 1
        limit = self.plugin_config.max_batch_count or DEFAULT_BATCH_LIMIT
        return min(max(1, parsed_count), max(1, limit))

    def _has_permission(self, event: AstrMessageEvent) -> bool:
        allowed = self.plugin_config.allowed_users
        if not allowed:
            return True
        return str(event.get_sender_id()) in allowed

    def _get_event_text(self, event: AstrMessageEvent) -> str:
        text = getattr(event, "message_str", "") or getattr(getattr(event, "message_obj", None), "message_str", "")
        if text:
            return str(text).strip()

        message = getattr(getattr(event, "message_obj", None), "message", []) or []
        plain_text = "".join(getattr(comp, "text", "") for comp in message if isinstance(comp, Plain)).strip()
        if plain_text:
            return plain_text
        return str(getattr(event, "message_obj", "") or "").strip()

    def _extract_command_message(self, event: AstrMessageEvent, command: str, fallback: str = "") -> str:
        text = self._get_event_text(event)
        if not text:
            return fallback.strip()
        pattern = rf"^\s*[/!！.]?{re.escape(command)}(?:\s+(.*))?$"
        match = re.match(pattern, text, flags=re.S)
        return (match.group(1) or "").strip() if match else fallback.strip()

    def _create_image_component(self, image_url: str) -> Image:
        if image_url.startswith("data:image"):
            b64_data = image_url.split(",", 1)[1]
            save_dir = os.path.join(self.data_dir, "temp_images")
            os.makedirs(save_dir, exist_ok=True)
            file_path = os.path.join(save_dir, f"img_{uuid.uuid4().hex[:12]}.png")
            with open(file_path, "wb") as file:
                file.write(base64.b64decode(b64_data, validate=False))
            return Image.fromFileSystem(file_path)
        if image_url.startswith("http"):
            return Image.fromURL(image_url)
        return Image.fromFileSystem(os.path.abspath(image_url))

    def _get_active_provider(self, chain_type: str = "text2img"):
        chain = self.plugin_config.chains.get(chain_type, [])
        if chain_type == "video":
            for provider_id in chain:
                provider = self.plugin_config.get_video_provider(provider_id)
                if provider:
                    return provider
            return self.plugin_config.video_providers[0] if self.plugin_config.video_providers else None

        for provider_id in chain:
            provider = self.plugin_config.get_provider(provider_id)
            if provider:
                return provider
        return self.plugin_config.providers[0] if self.plugin_config.providers else None

    def _set_chain_config(self, chain_key: str, provider_id: str) -> None:
        self.plugin_config.chains[chain_key] = [provider_id]
        if chain_key == "optimizer":
            self.raw_config.setdefault("optimizer_config", {})["chain_optimizer"] = provider_id
        else:
            config_key = "chain_text2img" if chain_key == "text2img" else f"chain_{chain_key}"
            self.raw_config.setdefault("router_config", {})[config_key] = provider_id

    def _set_provider_model(self, chain_key: str, provider_id: str, selected_model: str) -> None:
        provider_key = "video_providers" if chain_key == "video" else "providers"
        for provider in self.raw_config.get(provider_key, []):
            if isinstance(provider, dict) and str(provider.get("id", provider.get("节点ID", ""))) == provider_id:
                provider["model"] = selected_model
                if selected_model not in provider.get("available_models", []):
                    provider.setdefault("available_models", []).insert(0, selected_model)
                return

    def _parse_extra_params(self, extra_params: str) -> Dict[str, Any]:
        if not extra_params:
            return {}
        _, parsed = self.cmd_parser.parse(extra_params)
        return parsed

    async def _send_generated_images(self, event: AstrMessageEvent, urls: Iterable[str]) -> int:
        sent = 0
        for url in urls:
            if not isinstance(url, str) or not url:
                continue
            await event.send(event.chain_result([self._create_image_component(url)]))
            sent += 1
            await asyncio.sleep(0.5)
        return sent

    @filter.command("万象帮助")
    @handle_errors
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        msg = (
            f"📖 万象画卷 v{PLUGIN_VERSION}\n"
            "/画 [提示词] [--参数 值]\n"
            "/自拍 [动作] [--参数 值]\n"
            "/视频 [提示词] [--参数 值]\n"
            "/切换链路 [画图/自拍/视频/副脑] [节点ID]\n"
            "/切换模型 [画图/自拍/视频] [序号或名称]\n"
            "/万象帮助\n\n"
        )
        if self.plugin_config.presets:
            msg += "✨ 极速宏:\n" + "\n".join([f"/{preset}" for preset in self.plugin_config.presets.keys()])
        yield event.plain_result(msg)

    @filter.command("切换链路")
    @handle_errors
    async def cmd_switch_chain(
        self,
        event: AstrMessageEvent,
        target: str = "",
        node_id: str = "",
    ) -> AsyncGenerator[Any, None]:
        if not self._has_permission(event):
            return

        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video", "副脑": "optimizer"}
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频/副脑")
            return
        if not node_id:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少节点 ID。用法: /切换链路 [目标] [节点ID]")
            return

        chain_key = target_map[target]
        provider = self.plugin_config.get_video_provider(node_id) if chain_key == "video" else self.plugin_config.get_provider(node_id)
        if not provider:
            yield event.plain_result(f"{MessageEmoji.WARNING} 找不到节点 ID: {node_id}")
            return

        self._set_chain_config(chain_key, node_id)
        self._persist_config()
        self._safe_update_context_config()
        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已将 {target} 链路切换至节点: {node_id}")

    @filter.command("切换模型")
    @handle_errors
    async def cmd_switch_model(
        self,
        event: AstrMessageEvent,
        target: str = "",
        model_idx: str = "",
    ) -> AsyncGenerator[Any, None]:
        if not self._has_permission(event):
            return

        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video"}
        if target and target not in target_map and not model_idx:
            model_idx = target
            target = "画图"
        if not target:
            target = "画图"
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频")
            return

        chain_key = target_map[target]
        provider = self._get_active_provider(chain_key)
        if not provider:
            yield event.plain_result(f"{MessageEmoji.WARNING} 当前 {target} 链路没有可用节点。")
            return

        models = provider.available_models
        if not models:
            yield event.plain_result(f"{MessageEmoji.WARNING} 当前节点 ({provider.id}) 未配置可选模型。")
            return

        if not model_idx:
            msg = f"🎛️ 节点 {provider.id} 的可用模型:\n"
            for index, model_name in enumerate(models):
                marker = "👉" if model_name == provider.model else "  "
                msg += f"{marker} [{index}] {model_name}\n"
            msg += f"\n回复 /切换模型 {target} [序号或名称] 进行选择"
            yield event.plain_result(msg)
            return

        selected_model = ""
        try:
            index = int(model_idx)
            if 0 <= index < len(models):
                selected_model = models[index]
        except ValueError:
            selected_model = next((model_name for model_name in models if model_name == model_idx), "")

        if not selected_model:
            yield event.plain_result(f"{MessageEmoji.WARNING} 模型序号或名称无效。")
            return

        provider.model = selected_model
        self._set_provider_model(chain_key, provider.id, selected_model)
        self._persist_config()
        self._safe_update_context_config()
        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已将 {target} 节点 ({provider.id}) 默认模型切换为: {selected_model}")

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message_preset(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if not self.plugin_config.presets:
            return
        text = self._get_event_text(event)
        match = re.match(r"^\s*[/!！.]([^\s]+)", text)
        if not match:
            return
        cmd_name = match.group(1).strip()
        if cmd_name not in self.plugin_config.presets or not self._has_permission(event):
            return

        raw_refs = self._get_event_images(event)
        preset_prompt = self.plugin_config.presets[cmd_name]
        safe_refs = await self._process_and_save_images(raw_refs)

        msg = f"{MessageEmoji.PAINTING} 收到灵感，正在绘制..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 宏对应提示词: {preset_prompt}\n🖼️ 实际参考图：{len(safe_refs)} 张"
        yield event.plain_result(msg)

        try:
            async with aiohttp.ClientSession() as session:
                chain_manager = ChainManager(self.plugin_config, session)
                image_url = await chain_manager.run_chain("text2img", preset_prompt, user_refs=safe_refs)
            yield event.chain_result([self._create_image_component(image_url)])
        except Exception as exc:
            yield event.plain_result(f"💥 绘制失败: {exc}")

    @filter.command("画")
    @handle_errors
    async def cmd_draw(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
        p6: str = "",
        p7: str = "",
        p8: str = "",
        p9: str = "",
        p10: str = "",
    ) -> AsyncGenerator[Any, None]:
        if not self._has_permission(event):
            return

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        message = self._extract_command_message(event, "画", fallback)
        raw_refs = self._get_event_images(event)
        if not message and not raw_refs:
            yield event.plain_result(f"{MessageEmoji.WARNING} 请输入提示词或附带参考图。")
            return

        safe_refs = await self._process_and_save_images(raw_refs)
        prompt, kwargs = self.cmd_parser.parse(message)
        if not prompt and safe_refs:
            prompt = "根据参考图生成一张自然、清晰、符合原图语义的图片。"
        param_count = len(kwargs)
        if safe_refs:
            kwargs["user_refs"] = safe_refs

        msg = f"{MessageEmoji.PAINTING} 收到灵感，正在绘制..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 最终提示词: {prompt}\n⚙️ 附加参数：{param_count} 个\n🖼️ 实际参考图：{len(safe_refs)} 张"
        yield event.plain_result(msg)

        async with aiohttp.ClientSession() as session:
            chain_manager = ChainManager(self.plugin_config, session)
            image_url = await chain_manager.run_chain("text2img", prompt, **kwargs)
        yield event.chain_result([self._create_image_component(image_url)])

    @filter.command("自拍")
    @handle_errors
    async def cmd_selfie(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
        p6: str = "",
        p7: str = "",
        p8: str = "",
        p9: str = "",
        p10: str = "",
    ) -> AsyncGenerator[Any, None]:
        if not self._has_permission(event):
            return

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        message = self._extract_command_message(event, "自拍", fallback)
        user_input, kwargs = self.cmd_parser.parse(message)
        user_input = user_input or "看着镜头微笑"

        optimized_actions = await self.prompt_optimizer.optimize(user_input, count=1)
        final_prompt, extra_kwargs = self.persona_manager.build_persona_prompt(optimized_actions[0] if optimized_actions else user_input)
        extra_kwargs.update(kwargs)

        raw_refs = self._get_event_images(event)
        target_refs = raw_refs if raw_refs else self.plugin_config.persona_ref_images
        safe_refs = await self._process_and_save_images(target_refs)
        if safe_refs:
            extra_kwargs["user_refs"] = safe_refs
            if not raw_refs:
                extra_kwargs.pop("persona_ref", None)

        msg = f"{MessageEmoji.INFO} 正在为「{self.plugin_config.persona_name}」生成自拍，请稍候..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 构建提示词: {final_prompt}\n⚙️ 附加参数：{len(kwargs)} 个\n🖼️ 实际参考图：{len(safe_refs)} 张"
        yield event.plain_result(msg)

        chain_to_use = "selfie" if self.plugin_config.chains.get("selfie") else "text2img"
        async with aiohttp.ClientSession() as session:
            chain_manager = ChainManager(self.plugin_config, session)
            image_url = await chain_manager.run_chain(chain_to_use, final_prompt, **extra_kwargs)
        yield event.chain_result([self._create_image_component(image_url)])

    @filter.command("视频")
    @handle_errors
    async def cmd_video(
        self,
        event: AstrMessageEvent,
        p1: str = "",
        p2: str = "",
        p3: str = "",
        p4: str = "",
        p5: str = "",
        p6: str = "",
        p7: str = "",
        p8: str = "",
        p9: str = "",
        p10: str = "",
    ) -> AsyncGenerator[Any, None]:
        if not self._has_permission(event):
            return

        fallback = " ".join(str(item) for item in [p1, p2, p3, p4, p5, p6, p7, p8, p9, p10] if item).strip()
        message = self._extract_command_message(event, "视频", fallback)
        raw_refs = self._get_event_images(event)
        if not message and not raw_refs:
            yield event.plain_result(f"{MessageEmoji.WARNING} 请输入视频提示词或附带参考图。")
            return

        safe_refs = await self._process_and_save_images(raw_refs)
        prompt, kwargs = self.cmd_parser.parse(message)
        if not prompt and safe_refs:
            prompt = "根据参考图生成一段自然、流畅、清晰的视频。"

        msg = f"{MessageEmoji.INFO} 视频任务已提交后台渲染..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 渲染提示词: {prompt}\n⚙️ 附加参数：{len(kwargs)} 个\n🖼️ 参考图/首尾帧：{len(safe_refs)} 张"
        yield event.plain_result(msg)

        self._create_background_task(self.video_manager.background_task_runner(event, prompt, safe_refs, kwargs))

    @llm_tool(name="generate_selfie")
    async def tool_generate_selfie(
        self,
        event: AstrMessageEvent,
        action: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
    ) -> str:
        """
        以此 AI 助理的固定人设拍摄自拍。
        Args:
            action (string): 动作、姿态、服装、场景或画面描述。
            count (int): 需要生成的图片数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 1:1、3:4、9:16、16:9。
            size (string): 分辨率或尺寸参数，例如 1024x1024。
            extra_params (string): 附加模型参数透传，格式为 --key value，可同时传多个。
        """
        if not self._has_permission(event):
            return "无权限调用。"

        try:
            count = self._normalize_count(count)
            optimized_actions = await self.prompt_optimizer.optimize(action or "看着镜头微笑", count)
            raw_refs = self._get_event_images(event)
            target_refs = raw_refs if raw_refs else self.plugin_config.persona_ref_images
            safe_refs = await self._process_and_save_images(target_refs)
            extra_param_kwargs = self._parse_extra_params(extra_params)

            chain_to_use = "selfie" if self.plugin_config.chains.get("selfie") else "text2img"
            async with aiohttp.ClientSession() as session:
                chain_manager = ChainManager(self.plugin_config, session)
                tasks = []
                for optimized_action in optimized_actions:
                    final_prompt, kwargs = self.persona_manager.build_persona_prompt(optimized_action)
                    if safe_refs:
                        kwargs["user_refs"] = safe_refs
                        if not raw_refs:
                            kwargs.pop("persona_ref", None)
                    if aspect_ratio:
                        kwargs["aspect_ratio"] = aspect_ratio
                    if size:
                        kwargs["size"] = size
                    kwargs.update(extra_param_kwargs)
                    tasks.append(chain_manager.run_chain(chain_to_use, final_prompt, **kwargs))
                results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_urls = [result for result in results if isinstance(result, str) and result]
            if not valid_urls:
                raise RuntimeError("所有绘图节点请求失败")
            sent = await self._send_generated_images(event, valid_urls)
            return f"系统提示：已成功生成并下发了 {sent} 张图。"
        except Exception as exc:
            logger.error(f"[OmniDraw] LLM 自拍工具失败: {exc}", exc_info=True)
            return f"系统提示：画图失败 ({exc})。"

    @llm_tool(name="generate_image")
    async def tool_generate_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
    ) -> str:
        """
        AI 画图工具。当用户提出明确的画面要求你画出来时调用此工具。
        Args:
            prompt (string): 图片提示词，描述主体、风格、场景、构图和细节。
            count (int): 图片数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 1:1、3:4、9:16、16:9。
            size (string): 分辨率或尺寸参数，例如 1024x1024。
            extra_params (string): 其他模型参数透传，格式为 --key value，可同时传多个。
        """
        if not self._has_permission(event):
            return "无权限调用。"

        try:
            count = self._normalize_count(count)
            optimized_actions = await self.prompt_optimizer.optimize(prompt, count)
            safe_refs = await self._process_and_save_images(self._get_event_images(event))

            kwargs = {"user_refs": safe_refs} if safe_refs else {}
            if aspect_ratio:
                kwargs["aspect_ratio"] = aspect_ratio
            if size:
                kwargs["size"] = size
            kwargs.update(self._parse_extra_params(extra_params))

            async with aiohttp.ClientSession() as session:
                chain_manager = ChainManager(self.plugin_config, session)
                tasks = [chain_manager.run_chain("text2img", optimized_action, **kwargs) for optimized_action in optimized_actions]
                results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_urls = [result for result in results if isinstance(result, str) and result]
            if not valid_urls:
                raise RuntimeError("所有绘图节点请求失败")
            sent = await self._send_generated_images(event, valid_urls)
            return f"系统提示：已成功下发 {sent} 张图。"
        except Exception as exc:
            logger.error(f"[OmniDraw] LLM 画图工具失败: {exc}", exc_info=True)
            return f"系统提示：画图失败 ({exc})。"

    @llm_tool(name="generate_video")
    async def tool_generate_video(
        self,
        event: AstrMessageEvent,
        prompt: str,
        count: int = 1,
        aspect_ratio: str = "",
        size: str = "",
        extra_params: str = "",
    ) -> str:
        """
        AI 视频生成工具。当用户要求生成一段视频时调用。
        Args:
            prompt (string): 视频提示词，描述画面、动作、镜头运动、时长感和风格。
            count (int): 视频数量。默认为 1。
            aspect_ratio (string): 宽高比例，例如 9:16、16:9、1:1。
            size (string): 分辨率或尺寸参数，例如 1280x720、1920x1080。
            extra_params (string): 附加参数，透传至底层视频引擎，格式为 --key value。
        """
        if not self._has_permission(event):
            return "无权限调用。"

        try:
            count = self._normalize_count(count)
            safe_refs = await self._process_and_save_images(self._get_event_images(event))
            kwargs = self._parse_extra_params(extra_params)
            if aspect_ratio:
                kwargs["aspect_ratio"] = aspect_ratio
            if size:
                kwargs["size"] = size

            for _ in range(count):
                self._create_background_task(self.video_manager.background_task_runner(event, prompt, safe_refs, kwargs))
            return f"系统提示：已在后台独立提交了 {count} 个视频渲染任务。请告诉用户正在渲染中。"
        except Exception as exc:
            logger.error(f"[OmniDraw] LLM 视频工具失败: {exc}", exc_info=True)
            return f"系统提示：失败 ({exc})。"
