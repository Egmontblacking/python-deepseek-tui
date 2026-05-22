"""DeepSeek API 客户端 — SSE 流式请求、TokenBucket 限速、指数退避重试"""

import json
import time
import asyncio
import logging
from typing import AsyncIterator, Optional
from dataclasses import dataclass, field
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

# ── Token Bucket ──────────────────────────────────────────────

@dataclass
class TokenBucket:
    capacity: float = 10.0          # 最大令牌数
    refill_per_sec: float = 5.0     # 每秒补充速率
    _tokens: float = field(default=10.0, init=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0):
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self.refill_per_sec
            await asyncio.sleep(wait)


# ── 连接健康 ──────────────────────────────────────────────────

class HealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERING = "recovering"


@dataclass
class ConnectionHealth:
    state: HealthState = HealthState.HEALTHY
    failures: int = 0
    last_failure: float = 0.0
    last_probe: float = 0.0

    FAILURE_THRESHOLD = 2
    PROBE_COOLDOWN = 15.0

    def mark_success(self):
        if self.state != HealthState.HEALTHY:
            logger.info("Connection recovered → healthy")
        self.state = HealthState.HEALTHY
        self.failures = 0

    def mark_failure(self):
        self.failures += 1
        self.last_failure = time.monotonic()
        if self.failures >= self.FAILURE_THRESHOLD:
            self.state = HealthState.DEGRADED
            logger.warning("Connection degraded (failures=%d)", self.failures)

    def should_probe(self) -> bool:
        if self.state != HealthState.DEGRADED:
            return False
        if time.monotonic() - self.last_failure < self.PROBE_COOLDOWN:
            return False
        self.state = HealthState.RECOVERING
        self.last_probe = time.monotonic()
        return True


# ── DeepSeek 客户端 ───────────────────────────────────────────

class DeepSeekClient:
    """DeepSeek API 客户端，支持流式 SSE、自动重试、速率限制"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_retries = max_retries
        self.bucket = TokenBucket()
        self.health = ConnectionHealth()

        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            http2=True,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )

    async def close(self):
        await self._http.aclose()

    def _build_messages(self, messages: list[dict]) -> list[dict]:
        """清理消息以确保 API 兼容性"""
        cleaned = []
        for msg in messages:
            m = {"role": msg["role"], "content": msg.get("content")}
            if "tool_calls" in msg:
                m["tool_calls"] = msg["tool_calls"]
            if "tool_call_id" in msg:
                m["tool_call_id"] = msg["tool_call_id"]
            if "name" in msg:
                m["name"] = msg["name"]
            if "reasoning_content" in msg:
                m["reasoning_content"] = msg["reasoning_content"]
            cleaned.append(m)
        return cleaned

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[dict]:
        """流式聊天，返回 SSE 事件字典
        
        事件类型:
            {"type": "thinking_start", "index": 0}
            {"type": "thinking_delta", "index": 0, "content": "..."}
            {"type": "thinking_stop", "index": 0}
            {"type": "text_start", "index": 0}
            {"type": "text_delta", "index": 0, "content": "..."}
            {"type": "text_stop", "index": 0}
            {"type": "tool_call_start", "index": 0, "id": "...", "name": "...", "input": {}}
            {"type": "tool_call_delta", "index": 0, "partial_json": "..."}
            {"type": "tool_call_stop", "index": 0}
            {"type": "finish", "usage": {...}}
            {"type": "error", "message": "..."}
        """

        body = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "stream": True,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if system:
            body["messages"] = [{"role": "system", "content": system}] + body["messages"]
        if temperature is not None:
            body["temperature"] = temperature

        for attempt in range(self.max_retries + 1):
            try:
                await self.bucket.acquire()
                async for event in self._do_stream(body):
                    self.health.mark_success()
                    yield event
                return
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                ERROR_HINTS = {
                    401: "Invalid or missing API key",
                    403: "Access denied",
                    429: "Rate limited — retrying...",
                    500: "Server error",
                    502: "Bad gateway",
                    503: "Service unavailable",
                }
                hint = ERROR_HINTS.get(status_code, "")
                error_msg = f"HTTP {status_code}" + (f": {hint}" if hint else "")
                if status_code in (429, 500, 502, 503) and attempt < self.max_retries:
                    delay = 2 ** attempt + (0.1 * attempt)
                    logger.warning("Retry %d/%d after %.1fs", attempt + 1, self.max_retries, delay)
                    await asyncio.sleep(delay)
                    self.health.mark_failure()
                    continue
                logger.error("%s", error_msg)
                yield {"type": "error", "message": error_msg}
                return
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                if attempt < self.max_retries:
                    delay = 2 ** attempt
                    logger.warning("Network error, retry %d/%d", attempt + 1, self.max_retries)
                    await asyncio.sleep(delay)
                    self.health.mark_failure()
                    continue
                yield {"type": "error", "message": str(e)}
                return

    async def _do_stream(self, body: dict) -> AsyncIterator[dict]:
        url = f"{self.base_url}/v1/chat/completions"
        async with self._http.stream("POST", url, json=body) as response:
            response.raise_for_status()
            current_block_type = None
            current_index = -1
            tool_calls_state: dict[int, dict] = {}
            usage = {}

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    if current_block_type == "thinking":
                        yield {"type": "thinking_stop", "index": current_index}
                    elif current_block_type == "text":
                        yield {"type": "text_stop", "index": current_index}
                    elif current_block_type == "tool_call":
                        for idx in sorted(tool_calls_state.keys()):
                            state = tool_calls_state[idx]
                            raw = "".join(state["input_parts"])
                            tool_input = json.loads(raw) if raw.strip() else {}
                            yield {"type": "tool_call_stop", "index": idx, "id": state["id"], "name": state["name"], "input": tool_input}
                    yield {"type": "finish", "usage": usage}
                    return
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices", [])
                if not choices:
                    continue
                usage = obj.get("usage", usage)
                delta = choices[0].get("delta", {})

                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    if current_block_type == "thinking":
                        yield {"type": "thinking_delta", "index": current_index, "content": reasoning}
                    else:
                        if current_block_type == "text":
                            yield {"type": "text_stop", "index": current_index}
                        elif current_block_type == "tool_call":
                            for idx in sorted(tool_calls_state.keys()):
                                s = tool_calls_state[idx]
                                raw = "".join(s["input_parts"])
                                yield {"type": "tool_call_stop", "index": idx, "id": s["id"], "name": s["name"], "input": json.loads(raw) if raw.strip() else {}}
                            tool_calls_state.clear()
                        current_block_type = "thinking"
                        current_index += 1
                        yield {"type": "thinking_start", "index": current_index}
                        yield {"type": "thinking_delta", "index": current_index, "content": reasoning}
                    continue

                tool_calls = delta.get("tool_calls", [])
                if tool_calls:
                    if current_block_type != "tool_call":
                        if current_block_type == "thinking":
                            yield {"type": "thinking_stop", "index": current_index}
                        elif current_block_type == "text":
                            yield {"type": "text_stop", "index": current_index}
                        current_block_type = "tool_call"
                    for tc in tool_calls:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_state:
                            tool_calls_state[idx] = {"id": tc.get("id", ""), "name": tc.get("function", {}).get("name", ""), "input_parts": []}
                            yield {"type": "tool_call_start", "index": idx, "id": tool_calls_state[idx]["id"], "name": tool_calls_state[idx]["name"]}
                        func = tc.get("function", {})
                        if "arguments" in func:
                            tool_calls_state[idx]["input_parts"].append(func["arguments"])
                    continue

                content = delta.get("content", "")
                if content:
                    if current_block_type != "text":
                        if current_block_type == "thinking":
                            yield {"type": "thinking_stop", "index": current_index}
                        elif current_block_type == "tool_call":
                            for idx in sorted(tool_calls_state.keys()):
                                s = tool_calls_state[idx]
                                raw = "".join(s["input_parts"])
                                yield {"type": "tool_call_stop", "index": idx, "id": s["id"], "name": s["name"], "input": json.loads(raw) if raw.strip() else {}}
                            tool_calls_state.clear()
                        current_block_type = "text"
                        current_index += 1
                        yield {"type": "text_start", "index": current_index}
                    yield {"type": "text_delta", "index": current_index, "content": content}

        if current_block_type == "tool_call":
            for idx in sorted(tool_calls_state.keys()):
                s = tool_calls_state[idx]
                raw = "".join(s["input_parts"])
                yield {"type": "tool_call_stop", "index": idx, "id": s["id"], "name": s["name"], "input": json.loads(raw) if raw.strip() else {}}
        yield {"type": "finish", "usage": usage}
