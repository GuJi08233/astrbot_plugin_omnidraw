"""兜底链路调度器。"""

import aiohttp
from typing import Any
from astrbot.api import logger
from ..models import PluginConfig
from ..providers import create_provider

class ChainManager:
    def __init__(self, config: PluginConfig, session: aiohttp.ClientSession):
        self.config = config
        self.session = session

    async def run_chain(self, chain_name: str, prompt: str, **kwargs: Any) -> str:
        raw_chain = self.config.chains.get(chain_name)
        chain = []
        seen = set()
        for provider_id in raw_chain or []:
            provider_id = str(provider_id).strip()
            if provider_id and provider_id not in seen:
                seen.add(provider_id)
                chain.append(provider_id)
        if not chain:
            raise ValueError(f"未找到链路配置: {chain_name}")

        last_error = None
        skipped_errors = []

        for provider_id in chain:
            provider_config = self.config.get_provider(provider_id)
            if not provider_config:
                skipped_errors.append(f"{provider_id}: 节点不存在")
                logger.warning(f"⚠️ 链路 [{chain_name}] 中的节点 [{provider_id}] 不存在。")
                continue
            if not provider_config.base_url or not provider_config.model:
                skipped_errors.append(f"{provider_id}: 缺少接口地址或模型")
                logger.warning(f"⚠️ 链路 [{chain_name}] 中的节点 [{provider_id}] 缺少接口地址或模型。")
                continue
            if not provider_config.has_api_key:
                skipped_errors.append(f"{provider_id}: 未配置 API Key")
                logger.warning(f"⚠️ 链路 [{chain_name}] 中的节点 [{provider_id}] 未配置 API Key。")
                continue

            logger.info(f"🚀 [Chain] 正在将任务交由节点 [{provider_id}] 处理...")
            try:
                provider = create_provider(provider_config, self.session)
                result = await provider.generate_image(prompt, **kwargs)
                logger.info(f"✅ [Chain] 节点 [{provider_id}] 创作成功！")
                return result

            except Exception as e:
                # 增强日志捕获
                error_detail = repr(e)
                last_error = error_detail
                logger.error(f"❌ [Chain] 节点 [{provider_id}] 发生异常: {error_detail}", exc_info=True)
                logger.warning(f"🔄 正在尝试切换到下一个备用节点...")
                continue

        failure_detail = last_error or "；".join(skipped_errors) or "没有可尝试的有效节点"
        raise RuntimeError(f"所有节点均已失败！最后一次报错内容: {failure_detail}")
