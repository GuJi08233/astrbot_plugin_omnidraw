"""
AstrBot 万象画卷插件 v3.1
功能：支持 Gemini / gptimage2 高阶参数动态透传。
优化：完美的多模态图片解析，规避所有数组冲突。内置开发者详细汇报模式。完整保留所有指令与高级透传参数。
"""
import os
import base64
import uuid
import time
import aiohttp
import asyncio
import re
import json
from typing import AsyncGenerator, Any

from quart import jsonify, request

try:
    from astrbot.api.star import Context, Star, register, StarTools 
    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api.message_components import Image, Plain, Video
    from astrbot.api import logger, llm_tool 
except ImportError:
    from astrbot.api.star import Context, Star, register
    from astrbot.api.star.tools import StarTools
    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api.event.components import Image, Plain, Video
    from astrbot.api.utils import logger
    from astrbot.api import llm_tool

try:
    from astrbot.api.event import EventMessageType
except ImportError:
    from astrbot.api.event.filter import EventMessageType

from .models import PluginConfig
from .constants import MessageEmoji
from .utils import handle_errors
from .core.chain_manager import ChainManager
from .core.parser import CommandParser
from .core.persona_manager import PersonaManager
from .core.video_manager import VideoManager
from .core.prompt_optimizer import PromptOptimizer

@register("astrbot_plugin_omnidraw", "your_name", "万象画卷 v3.1 - 终极版", "3.1.0")
class OmniDrawPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        
        base_dir = os.getcwd()
        self.data_dir = os.path.join(base_dir, "data", "plugin_data", "astrbot_plugin_omnidraw")
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.config_path = os.path.join(self.data_dir, "omnidraw_persist_config.json")
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.raw_config = json.load(f)
            except Exception as e:
                logger.error(f"[OmniDraw] 读取本地持久化配置失败: {e}")
                self.raw_config = config or {}
        else:
            self.raw_config = config or {}
            
        self.plugin_config = PluginConfig.from_dict(self.raw_config, self.data_dir)
        self.cmd_parser = CommandParser()
        self.persona_manager = PersonaManager(self.plugin_config)
        self.video_manager = VideoManager(self.plugin_config)
        self.prompt_optimizer = PromptOptimizer(self.plugin_config) 

        self.context.register_web_api("/astrbot_plugin_omnidraw/get_config", self.get_config_handler, ["GET"], "获取万象画卷配置")
        self.context.register_web_api("/astrbot_plugin_omnidraw/save_config", self.save_config_handler, ["POST"], "保存万象画卷配置")

    async def get_config_handler(self):
        return jsonify(self.raw_config)

    async def save_config_handler(self):
        new_config = await request.get_json()
        self.raw_config = new_config
        self.plugin_config = PluginConfig.from_dict(new_config, self.data_dir)
        self.persona_manager = PersonaManager(self.plugin_config)
        self.video_manager = VideoManager(self.plugin_config)
        self.prompt_optimizer = PromptOptimizer(self.plugin_config)
        
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.raw_config, f, ensure_ascii=False, indent=4)
            logger.info(f"[OmniDraw] 配置已成功持久化落盘至: {self.config_path}")
        except Exception as e:
            logger.error(f"[OmniDraw] 配置文件持久化写入失败: {e}")
            return jsonify({"success": False, "message": f"硬盘写入失败: {e}"})
        
        if hasattr(self.context, 'update_config'):
            try:
                self.context.update_config(new_config)
            except Exception:
                pass
                
        return jsonify({"success": True, "message": "配置已落盘，热重载生效"})

    def _get_event_images(self, event: AstrMessageEvent) -> list:
        images = []
        visited = set()
        
        def _search(obj):
            if obj is None or id(obj) in visited: return
            visited.add(id(obj))
            obj_type = type(obj).__name__
            
            if obj_type == "Image":
                path = getattr(obj, "path", getattr(obj, "file", getattr(obj, "file_path", None)))
                url = getattr(obj, "url", None)
                ref = path if (path and not str(path).startswith("http")) else url
                if ref: images.append(str(ref))
            elif obj_type == "Plain":
                text = getattr(obj, "text", "")
                if text and text.startswith("data:image"): images.append(text)
            elif isinstance(obj, (list, tuple)):
                for item in obj: _search(item)
            else:
                attrs = []
                if hasattr(obj, "__dict__"): attrs.extend(vars(obj).keys())
                if hasattr(obj, "__slots__"): attrs.extend(obj.__slots__)
                for key in set(attrs):
                    if key not in ["context", "star", "bot", "provider", "session", "config", "plugin_config", "cmd_parser", "video_manager"]:
                        try: _search(getattr(obj, key))
                        except Exception: pass

        _search(event.message_obj)
        quote_obj = getattr(event.message_obj, "quote", None)
        if quote_obj: _search(quote_obj)
        
        seen = set()
        return [x for x in images if not (x in seen or seen.add(x))]

    async def _process_and_save_images(self, raw_images: list) -> list:
        processed_paths = []
        if not raw_images: return processed_paths
        
        save_dir = os.path.join(self.data_dir, "user_refs")
        os.makedirs(save_dir, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        
        async with aiohttp.ClientSession() as session:
            for img_ref in raw_images:
                if not img_ref: continue
                
                if str(img_ref).startswith("data:image"):
                    try:
                        b64_data = img_ref.split(",", 1)[1]
                        file_path = os.path.join(save_dir, f"ref_{uuid.uuid4().hex[:8]}.png")
                        with open(file_path, "wb") as f: f.write(base64.b64decode(b64_data))
                        processed_paths.append(file_path)
                    except Exception as e:
                        logger.error(f"Base64 解码失败: {e}")
                    continue

                if not str(img_ref).startswith("http"):
                    abs_path = os.path.abspath(img_ref)
                    if os.path.exists(abs_path): processed_paths.append(abs_path)
                    continue

                for attempt in range(3):
                    try:
                        async with session.get(img_ref, headers=headers, timeout=15) as resp:
                            if resp.status == 200:
                                img_data = await resp.read()
                                file_path = os.path.join(save_dir, f"ref_{uuid.uuid4().hex[:8]}.png")
                                with open(file_path, "wb") as f: f.write(img_data)
                                processed_paths.append(file_path) 
                                break
                    except: await asyncio.sleep(1)
                        
        return processed_paths

    def _normalize_count(self, count: Any) -> int:
        try: return int(str(count).strip())
        except: return 1

    def _has_permission(self, event: AstrMessageEvent) -> bool:
        allowed = self.plugin_config.allowed_users
        if not allowed: return True
        sender_id = str(event.get_sender_id())
        if sender_id in allowed: return True
        return False

    def _get_event_text(self, event: AstrMessageEvent) -> str:
        text = getattr(event, "message_str", "") or getattr(event.message_obj, "message_str", "")
        if text:
            return str(text).strip()
        message = getattr(event.message_obj, "message", []) or []
        plain_text = "".join(getattr(comp, "text", "") for comp in message if isinstance(comp, Plain)).strip()
        if plain_text:
            return plain_text
        return str(getattr(event, "message_obj", "") or "").strip()

    def _extract_command_message(self, event: AstrMessageEvent, command: str, fallback: str = "") -> str:
        text = self._get_event_text(event)
        if not text:
            return fallback.strip()
        pattern = rf'^\s*[/!！.]?{re.escape(command)}(?:\s+(.*))?$'
        match = re.match(pattern, text, flags=re.S)
        if match:
            return (match.group(1) or "").strip()
        return fallback.strip()

    def _create_image_component(self, image_url: str) -> Image:
        if image_url.startswith("data:image"):
            b64_data = image_url.split(",", 1)[1]
            save_dir = os.path.join(self.data_dir, "temp_images")
            os.makedirs(save_dir, exist_ok=True)
            file_path = os.path.join(save_dir, f"img_{uuid.uuid4().hex[:8]}.png")
            with open(file_path, "wb") as f: f.write(base64.b64decode(b64_data))
            return Image.fromFileSystem(file_path)
        return Image.fromURL(image_url)

    def _get_active_provider(self, chain_type: str = "text2img"):
        chain = self.plugin_config.chains.get(chain_type, [])
        if chain_type == "video":
            if chain: 
                prov = self.plugin_config.get_video_provider(chain[0])
                if prov: return prov
            return self.plugin_config.video_providers[0] if self.plugin_config.video_providers else None
        else:
            if chain: 
                prov = self.plugin_config.get_provider(chain[0])
                if prov: return prov
            return self.plugin_config.providers[0] if self.plugin_config.providers else None

    # ==========================================
    # 🎨 指令与控制区 
    # ==========================================
    @filter.command("万象帮助")
    @handle_errors
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        msg = "📖 万象画卷 v3.1\n/画 [提示词]\n/自拍 [动作]\n/视频 [提示词]\n/切换链路 [画图/自拍/视频] [节点ID]\n/切换模型 [画图/自拍/视频] [序号]\n\n"
        if self.plugin_config.presets:
            msg += "✨ 极速宏:\n" + "\n".join([f"/{p}" for p in self.plugin_config.presets.keys()])
        yield event.plain_result(msg)

    @filter.command("切换链路")
    @handle_errors
    async def cmd_switch_chain(self, event: AstrMessageEvent, target: str = "", node_id: str = "") -> AsyncGenerator[Any, None]:
        if not self._has_permission(event): return
        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video", "副脑": "optimizer"}
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频/副脑")
            return
        
        if not node_id:
            yield event.plain_result(f"{MessageEmoji.WARNING} 缺少节点ID参数！用法: /切换链路 [目标] [节点ID]")
            return

        chain_key = target_map[target]
        if chain_key == "video": prov = self.plugin_config.get_video_provider(node_id)
        else: prov = self.plugin_config.get_provider(node_id)
            
        if not prov:
            yield event.plain_result(f"{MessageEmoji.WARNING} 找不到节点 ID: {node_id}")
            return
            
        self.plugin_config.chains[chain_key] = [node_id]
        self.raw_config.setdefault("router_config", {})[f"chain_{chain_key}"] = node_id
        
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.raw_config, f, ensure_ascii=False, indent=4)
        except Exception: pass
            
        if hasattr(self.context, 'update_config'):
            self.context.update_config(self.raw_config)
        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已将 {target} 链路切换至节点: {node_id}")

    @filter.command("切换模型")
    @handle_errors
    async def cmd_switch_model(self, event: AstrMessageEvent, target: str = "", model_idx: str = "") -> AsyncGenerator[Any, None]:
        if not self._has_permission(event): return
        target_map = {"画图": "text2img", "自拍": "selfie", "视频": "video"}
        if target not in target_map:
            yield event.plain_result(f"{MessageEmoji.WARNING} 未知目标！支持: 画图/自拍/视频")
            return
            
        chain_key = target_map[target]
        prov = self._get_active_provider(chain_key)
        if not prov:
            yield event.plain_result(f"{MessageEmoji.WARNING} 当前 {target} 链路没有可用的节点配置")
            return
            
        models = prov.available_models
        if not models:
            yield event.plain_result(f"{MessageEmoji.WARNING} 当前节点 ({prov.id}) 未配置可选模型")
            return
            
        if not model_idx:
            msg = f"🎛️ 节点 {prov.id} 的可用模型:\n"
            for i, m in enumerate(models):
                marker = "👉" if m == prov.model else "  "
                msg += f"{marker} [{i}] {m}\n"
            msg += "\n回复 /切换模型 [目标] [序号] 进行选择"
            yield event.plain_result(msg)
            return
            
        try:
            idx = int(model_idx)
            if idx < 0 or idx >= len(models): raise ValueError
        except:
            yield event.plain_result(f"{MessageEmoji.WARNING} 序号无效")
            return
            
        selected_model = models[idx]
        prov.model = selected_model
        
        prov_list_key = "video_providers" if chain_key == "video" else "providers"
        for p_dict in self.raw_config.get(prov_list_key, []):
            p_id = p_dict.get("id") or p_dict.get("节点ID")
            if p_id == prov.id:
                p_dict["model"] = selected_model
                break
                
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.raw_config, f, ensure_ascii=False, indent=4)
        except Exception: pass
                
        if hasattr(self.context, 'update_config'):
            self.context.update_config(self.raw_config)
        yield event.plain_result(f"{MessageEmoji.SUCCESS} 已将 {target} 节点 ({prov.id}) 默认模型切换为: {selected_model}")

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message_preset(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        if not self.plugin_config.presets: return
        text = "".join([comp.text for comp in event.message_obj.message if isinstance(comp, Plain)]).strip()
        if not text: return
        match = re.match(r'^([^\w\u4e00-\u9fa5]+)(.*)$', text)
        if not match: return 
        cmd_name = match.group(2).strip()
        if cmd_name not in self.plugin_config.presets: return 
        if not self._has_permission(event): return

        raw_refs = self._get_event_images(event)
        preset_prompt = self.plugin_config.presets[cmd_name]
        safe_refs = await self._process_and_save_images(raw_refs)
        
        msg = f"{MessageEmoji.PAINTING} 收到灵感，正在绘制..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 宏对应提示词: {preset_prompt}\n🖼️ 实际参考图：{len(safe_refs) if safe_refs else 0} 张"
        yield event.plain_result(msg)
        
        try:
            async with aiohttp.ClientSession() as session:
                chain_manager = ChainManager(self.plugin_config, session)
                image_url = await chain_manager.run_chain("text2img", preset_prompt, user_refs=safe_refs)
            yield event.chain_result([self._create_image_component(image_url)])
        except Exception as e:
            yield event.plain_result(f"💥 绘制失败: {e}")

    @filter.command("画")
    @handle_errors
    async def cmd_draw(self, event: AstrMessageEvent, p1: str="", p2: str="", p3: str="", p4: str="", p5: str="", p6: str="", p7: str="", p8: str="", p9: str="", p10: str="") -> AsyncGenerator[Any, None]:
        if not self._has_permission(event): return
        fallback = " ".join(str(x) for x in [p1,p2,p3,p4,p5,p6,p7,p8,p9,p10] if x).strip()
        message = self._extract_command_message(event, "画", fallback)
        raw_refs = self._get_event_images(event)
        if not message and not raw_refs:
            yield event.plain_result(f"{MessageEmoji.WARNING} 请输入提示词或附带参考图！")
            return
            
        safe_refs = await self._process_and_save_images(raw_refs)
        prompt, kwargs = self.cmd_parser.parse(message)
        param_count = len(kwargs)
        if safe_refs: kwargs["user_refs"] = safe_refs
            
        msg = f"{MessageEmoji.PAINTING} 收到灵感，正在绘制..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 最终提示词: {prompt}\n⚙️ 附加参数：{param_count} 个\n🖼️ 实际参考图：{len(safe_refs) if safe_refs else 0} 张"
        yield event.plain_result(msg)
        
        async with aiohttp.ClientSession() as session:
            chain_manager = ChainManager(self.plugin_config, session)
            image_url = await chain_manager.run_chain("text2img", prompt, **kwargs)
        yield event.chain_result([self._create_image_component(image_url)])

    @filter.command("自拍")
    @handle_errors
    async def cmd_selfie(self, event: AstrMessageEvent, p1: str="", p2: str="", p3: str="", p4: str="", p5: str="", p6: str="", p7: str="", p8: str="", p9: str="", p10: str="") -> AsyncGenerator[Any, None]:
        if not self._has_permission(event): return
        fallback = " ".join(str(x) for x in [p1,p2,p3,p4,p5,p6,p7,p8,p9,p10] if x).strip()
        message = self._extract_command_message(event, "自拍", fallback)
        user_input, kwargs = self.cmd_parser.parse(message)
        if not user_input: user_input = "看着镜头微笑"
        
        opt_actions = await self.prompt_optimizer.optimize(user_input, count=1)
        final_prompt, extra_kwargs = self.persona_manager.build_persona_prompt(opt_actions[0] if opt_actions else user_input)
        extra_kwargs.update(kwargs)
        param_count = len(kwargs)
        
        # 🔴 调用本脚本专用列表字段，底层依然可以拿单数爽
        persona_ref = self.plugin_config.persona_ref_images
        raw_refs = self._get_event_images(event)
        target_refs = raw_refs if raw_refs else persona_ref
        
        safe_refs = await self._process_and_save_images(target_refs)
        if safe_refs:
            extra_kwargs["user_refs"] = safe_refs
            if not raw_refs: extra_kwargs.pop("persona_ref", None)
        else: extra_kwargs.pop("user_refs", None)
            
        msg = f"{MessageEmoji.INFO} 正在为「{self.plugin_config.persona_name}」生成自拍，请稍候..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 构建提示词: {final_prompt}\n⚙️ 附加参数：{param_count} 个\n🖼️ 实际参考图：{len(safe_refs) if safe_refs else 0} 张"
        yield event.plain_result(msg)
        
        chain_to_use = "selfie" if "selfie" in self.plugin_config.chains else "text2img"
        async with aiohttp.ClientSession() as session:
            chain_manager = ChainManager(self.plugin_config, session)
            image_url = await chain_manager.run_chain(chain_to_use, final_prompt, **extra_kwargs)
        yield event.chain_result([self._create_image_component(image_url)])

    @filter.command("视频")
    @handle_errors
    async def cmd_video(self, event: AstrMessageEvent, p1: str="", p2: str="", p3: str="", p4: str="", p5: str="", p6: str="", p7: str="", p8: str="", p9: str="", p10: str="") -> AsyncGenerator[Any, None]:
        if not self._has_permission(event): return
        fallback = " ".join(str(x) for x in [p1,p2,p3,p4,p5,p6,p7,p8,p9,p10] if x).strip()
        message = self._extract_command_message(event, "视频", fallback)
        raw_refs = self._get_event_images(event)
        if not message and not raw_refs: return
        prompt, _ = self.cmd_parser.parse(message)
        safe_refs = await self._process_and_save_images(raw_refs)
        
        msg = f"{MessageEmoji.INFO} 视频任务已提交后台渲染..."
        if self.plugin_config.verbose_report:
            msg += f"\n📝 渲染提示词: {prompt}\n⚙️ 附加参数：0 个\n🖼️ 参考图/首尾帧：{len(safe_refs) if safe_refs else 0} 张"
        yield event.plain_result(msg)
        
        asyncio.create_task(self.video_manager.background_task_runner(event, prompt, safe_refs))

    # ==========================================
    # 🤖 LLM 工具区 (参数一字不落，全部保留)
    # ==========================================
    @llm_tool(name="generate_selfie")
    async def tool_generate_selfie(self, event: AstrMessageEvent, action: str, count: int = 1, aspect_ratio: str = "", size: str = "", extra_params: str = "") -> str:
        """
        以此 AI 助理的固定人设拍摄自拍。
        Args:
            action (string): 动作和场景描述。
            count (int): 需要生成的图片数量。默认为1。
            aspect_ratio (string): 宽高比例。
            size (string): 分辨率。
            extra_params (string): 附加模型参数透传。
        """
        if not self._has_permission(event): return "无权限调用。"
        try:
            count = min(max(1, self._normalize_count(count)), self.plugin_config.max_batch_count or 10)
            optimized_actions = await self.prompt_optimizer.optimize(action, count)
            
            # 🔴 调用本脚本专用的列表字段
            persona_ref = self.plugin_config.persona_ref_images
            
            raw_refs = self._get_event_images(event)
            target_refs = raw_refs if raw_refs else persona_ref
            safe_refs = await self._process_and_save_images(target_refs)

            chain_to_use = "selfie" if "selfie" in self.plugin_config.chains else "text2img"
            tasks = []
            async with aiohttp.ClientSession() as session:
                for opt_action in optimized_actions:
                    final_prompt, extra_kwargs = self.persona_manager.build_persona_prompt(opt_action)
                    if safe_refs:
                        extra_kwargs["user_refs"] = safe_refs
                        if not raw_refs: extra_kwargs.pop("persona_ref", None)
                    
                    if aspect_ratio: extra_kwargs["aspect_ratio"] = aspect_ratio
                    if size: extra_kwargs["size"] = size
                    if extra_params:
                        _, ep_kwargs = self.cmd_parser.parse(extra_params)
                        extra_kwargs.update(ep_kwargs)
                            
                    chain_manager = ChainManager(self.plugin_config, session)
                    tasks.append(chain_manager.run_chain(chain_to_use, final_prompt, **extra_kwargs))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
            
            valid_urls = [u for u in results if isinstance(u, str) and u]
            if not valid_urls: raise Exception("所有绘图节点请求失败")
            for url in valid_urls:
                await event.send(event.chain_result([self._create_image_component(url)]))
                await asyncio.sleep(0.5) 
            return f"系统提示：已成功生成并下发了 {len(valid_urls)} 张图。"
        except Exception as e:
            return f"系统提示：画图失败 ({str(e)})。"

    @llm_tool(name="generate_image")
    async def tool_generate_image(self, event: AstrMessageEvent, prompt: str, count: int = 1, aspect_ratio: str = "", size: str = "", extra_params: str = "") -> str:
        """
        AI 画图工具。当用户提出明确的画面要求你画出来时调用此工具。
        Args:
            prompt (string): 提示词。
            count (int): 图片数量。默认为1。
            aspect_ratio (string): 宽高比例。
            size (string): 分辨率。
            extra_params (string): 其他参数。
        """
        if not self._has_permission(event): return "无权限调用。"
        try:
            count = min(max(1, self._normalize_count(count)), self.plugin_config.max_batch_count or 10)
            optimized_actions = await self.prompt_optimizer.optimize(prompt, count)
            safe_refs = await self._process_and_save_images(self._get_event_images(event))
            
            kwargs = {"user_refs": safe_refs} if safe_refs else {}
            if aspect_ratio: kwargs["aspect_ratio"] = aspect_ratio
            if size: kwargs["size"] = size
            if extra_params:
                _, ep_kwargs = self.cmd_parser.parse(extra_params)
                kwargs.update(ep_kwargs)

            tasks = []
            async with aiohttp.ClientSession() as session:
                for opt_action in optimized_actions:
                    tasks.append(ChainManager(self.plugin_config, session).run_chain("text2img", opt_action, **kwargs))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
            valid_urls = [u for u in results if isinstance(u, str) and u]
            if not valid_urls: raise Exception("所有绘图节点请求失败")
            for url in valid_urls:
                await event.send(event.chain_result([self._create_image_component(url)]))
                await asyncio.sleep(0.5) 
            return f"系统提示：已成功下发 {len(valid_urls)} 张图。"
        except Exception as e:
            return f"系统提示：画图失败 ({str(e)})。"

    @llm_tool(name="generate_video")
    async def tool_generate_video(self, event: AstrMessageEvent, prompt: str, count: int = 1, aspect_ratio: str = "", size: str = "", extra_params: str = "") -> str:
        """
        AI 视频生成工具。当用户要求生成一段视频时调用。
        Args:
            prompt (string): 视频提示词。
            count (int): 视频数量，默认为 1。
            aspect_ratio (string): 宽高比例。
            size (string): 分辨率。
            extra_params (string): 附加参数，透传至底层引擎。
        """
        if not self._has_permission(event): return "无权限调用。"
        try:
            count = min(max(1, self._normalize_count(count)), self.plugin_config.max_batch_count or 10)
            safe_refs = await self._process_and_save_images(self._get_event_images(event))
            
            full_prompt = prompt
            if aspect_ratio: full_prompt += f" --ar {aspect_ratio}"
            if size: full_prompt += f" --size {size}"
            if extra_params: full_prompt += f" {extra_params}"

            for _ in range(count):
                asyncio.create_task(self.video_manager.background_task_runner(event, full_prompt, safe_refs))
            return f"系统提示：已在后台独立提交了 {count} 个视频渲染任务。请告诉用户正在渲染中。"
        except Exception as e:
            return f"系统提示：失败 ({str(e)})。"
