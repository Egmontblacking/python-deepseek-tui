"""对话引擎 — turn loop、消息管理、工具审批"""

import json
import asyncio
from enum import Enum
from typing import Any, AsyncIterator, Optional

from client import DeepSeekClient
from tools import ToolRegistry, BLOCKED_IN_PLAN
from session import Session, SessionStore


class AppMode(Enum):
    PLAN = "plan"       # 只读，阻止写工具
    AGENT = "agent"     # 全部工具，需要审批
    YOLO = "yolo"       # 全部工具，自动批准


SYSTEM_PROMPTS = {
    AppMode.PLAN: """You are a helpful coding assistant in Plan mode (read-only).
You can explore code, read files, and analyze architecture. 
You CANNOT modify files or run shell commands.
When you need to make changes, describe your plan clearly.""",

    AppMode.AGENT: """You are a helpful coding assistant.
You have access to tools for reading/writing files, running shell commands, and searching code.
Before making changes, check existing code to avoid mistakes.
Be concise and precise.""",

    AppMode.YOLO: """You are a helpful coding assistant in YOLO mode.
All tool calls are auto-approved. Be careful with destructive operations."""
}


class ChatEngine:
    """对话引擎 — 消息管理、turn loop、工具执行"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        workspace: str = ".",
        allow_shell: bool = True,
        max_tool_steps: int = 10,
    ):
        self.client = DeepSeekClient(api_key, base_url, model)
        self.tools = ToolRegistry(workspace, allow_shell)
        self.session = Session()
        self.mode = AppMode.AGENT
        self.max_tool_steps = max_tool_steps
        self.store = SessionStore()

        # 审批回调 (由 TUI 设置)
        self._approval_callback: Optional[callable] = None

    def set_approval_callback(self, cb: callable):
        """设置审批回调: async def cb(tool_name, args) -> bool"""
        self._approval_callback = cb

    def set_mode(self, mode: AppMode):
        self.mode = mode

    def load_session(self, session_id: str):
        s = self.store.load(session_id)
        if s:
            self.session = s

    def save_session(self):
        self.store.save(self.session)

    async def chat(self, user_input: str, stream: bool = True) -> AsyncIterator[dict]:
        """处理用户输入，返回事件流
        
        事件类型:
            {"type": "user", "content": "..."}
            {"type": "thinking_start"|"thinking_delta"|"thinking_stop", ...}
            {"type": "text_start"|"text_delta"|"text_stop", ...}
            {"type": "tool_call", "name": "...", "args": {...}, "result": {...}}
            {"type": "turn_complete", "usage": {...}}
            {"type": "error", "message": "..."}
        """

        # 添加用户消息
        self.session.messages.append({"role": "user", "content": user_input})
        yield {"type": "user", "content": user_input}

        # 工具循环
        step = 0
        while step < self.max_tool_steps:
            step += 1

            # 构建消息列表
            messages = list(self.session.messages)
            system = SYSTEM_PROMPTS[self.mode]
            tools = self.tools.get_definitions()

            # 流式调用 LLM
            assistant_content = ""
            tool_calls_found: list[dict] = []
            current_tools: dict[int, dict] = {}

            async for event in self.client.chat_stream(messages, tools, system=system):
                etype = event["type"]

                if etype in ("thinking_start", "thinking_delta", "thinking_stop"):
                    yield event
                elif etype == "text_delta":
                    assistant_content += event.get("content", "")
                    yield event
                elif etype == "text_start":
                    yield event
                elif etype == "text_stop":
                    yield event
                elif etype == "tool_call_start":
                    idx = event["index"]
                    current_tools[idx] = {
                        "id": event["id"],
                        "name": event["name"],
                        "arguments": {},
                    }
                elif etype == "tool_call_stop":
                    idx = event["index"]
                    if idx in current_tools:
                        current_tools[idx]["arguments"] = event.get("input", {})
                        tool_calls_found.append(current_tools.pop(idx))
                elif etype == "finish":
                    yield event
                elif etype == "error":
                    yield event
                    return

            # 没有工具调用 → 结束
            if not tool_calls_found:
                if assistant_content:
                    self.session.messages.append({"role": "assistant", "content": assistant_content})
                self.save_session()
                yield {"type": "turn_complete", "usage": {}}
                return

            # 构建 assistant 消息（含 tool_calls）
            assistant_msg = {"role": "assistant", "content": assistant_content or None}
            api_tool_calls = []
            for tc in tool_calls_found:
                api_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                    },
                })
            assistant_msg["tool_calls"] = api_tool_calls
            self.session.messages.append(assistant_msg)

            # ── 执行工具 ──
            for tc in tool_calls_found:
                name = tc["name"]
                args = tc["arguments"]

                # Plan 模式阻止
                if self.mode == AppMode.PLAN and name in BLOCKED_IN_PLAN:
                    result = {"success": False, "content": f"Tool '{name}' blocked in Plan mode"}
                else:
                    # 审批 (非 YOLO 模式)
                    if self.mode != AppMode.YOLO and not self.tools.resolve(name) in {"read_file", "list_dir", "grep_files", "web_search"}:
                        if self._approval_callback:
                            approved = await self._approval_callback(name, args)
                            if not approved:
                                result = {"success": False, "content": "User denied tool execution"}
                                yield {"type": "tool_call", "name": name, "args": args, "result": result}
                                # 添加工具结果
                                self.session.messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": result["content"],
                                })
                                continue

                    result = await self.tools.execute(name, args)

                # 通知 UI
                yield {"type": "tool_call", "name": name, "args": args, "result": result}

                # 添加工具结果消息
                self.session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result["content"],
                })

        # 达到最大步数
        yield {"type": "turn_complete", "usage": {}}

    async def close(self):
        await self.client.close()
