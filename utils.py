"""通用工具函数。"""

import asyncio
import functools
from typing import Callable, Any, AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from .constants import MessageEmoji

def handle_errors(func: Callable) -> Callable:
    """统一错误处理装饰器"""
    @functools.wraps(func)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs) -> AsyncGenerator[Any, None]:
        try:
            async for result in func(self, event, *args, **kwargs):
                yield result
        except asyncio.TimeoutError:
            logger.error(f"[{func.__name__}] 操作超时", exc_info=True)
            custom_builder = getattr(self, "_build_command_error_message", None)
            if callable(custom_builder):
                custom_message = custom_builder(func.__name__, asyncio.TimeoutError(), error_kind="timeout")
                if custom_message:
                    yield event.plain_result(custom_message)
                    return
            yield event.plain_result(f"{MessageEmoji.ERROR} 操作超时，请稍后重试")
        except ValueError as e:
            logger.warning(f"[{func.__name__}] 参数错误: {e}")
            yield event.plain_result(f"{MessageEmoji.ERROR} 参数错误: {str(e)}")
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"[{func.__name__}] 执行失败 [{error_type}]: {e}", exc_info=True)
            custom_builder = getattr(self, "_build_command_error_message", None)
            if callable(custom_builder):
                custom_message = custom_builder(func.__name__, e, error_kind="exception")
                if custom_message:
                    yield event.plain_result(custom_message)
                    return
            yield event.plain_result(f"{MessageEmoji.ERROR} 操作失败，请联系管理员")
    return wrapper
