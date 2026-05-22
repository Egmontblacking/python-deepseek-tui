"""Token 计数器 — tiktoken 估算 + 消息级计数"""

import tiktoken
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 缓存编码器（懒加载）
_encoder: Optional[tiktoken.Encoding] = None
_TOOLS_OVERHEAD = 2000  # 工具定义约 2000 tokens
_SYSTEM_OVERHEAD = 500   # 系统提示约 500 tokens


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # 降级：简单字符估算 (1 token ≈ 4 chars)
            logger.warning("tiktoken encoder not available, using char-based estimation")
            _encoder = None
    return _encoder


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is None:
        return len(text) // 4  # 粗略估算
    try:
        return len(enc.encode(text))
    except Exception:
        return len(text) // 4


def estimate_message_tokens(msg: dict) -> int:
    """估算单条消息的 token 数"""
    tokens = 4  # 消息格式开销

    role = msg.get("role", "user")
    tokens += estimate_tokens(role)

    content = msg.get("content", "")
    if content:
        tokens += estimate_tokens(str(content))

    # tool_calls
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        tokens += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))

    # tool_call_id + name (tool 消息)
    if msg.get("tool_call_id"):
        tokens += estimate_tokens(msg["tool_call_id"])
    if msg.get("name"):
        tokens += estimate_tokens(msg["name"])

    # reasoning_content
    reasoning = msg.get("reasoning_content", "")
    if reasoning:
        tokens += estimate_tokens(reasoning)

    return tokens


def estimate_session_tokens(messages: list[dict]) -> int:
    """估算会话总 token 数"""
    total = _SYSTEM_OVERHEAD + _TOOLS_OVERHEAD
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


def estimate_input_tokens(
    messages: list[dict],
    system_prompt: str = "",
    tool_definitions: list[dict] | None = None,
) -> int:
    """估算 API 请求的 input token 数

    Args:
        messages: 消息列表
        system_prompt: 系统提示
        tool_definitions: 工具定义列表

    Returns:
        预估 token 数
    """
    total = _SYSTEM_OVERHEAD

    if system_prompt:
        total += estimate_tokens(system_prompt)

    if tool_definitions:
        total += estimate_tokens(json.dumps(tool_definitions, ensure_ascii=False))

    for msg in messages:
        total += estimate_message_tokens(msg)

    return total


def format_tokens(n: int) -> str:
    """格式化 token 数为可读字符串"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)
