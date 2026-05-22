"""子智能体运行器 — 独立 ChatEngine 实例执行只读任务"""

import asyncio
from pathlib import Path
from deepseek_tui.api.client import DeepSeekClient
from deepseek_tui.tools.registry import ToolRegistry
from deepseek_tui.db.session import Session


class SubagentRunner:
    """子智能体运行器 — 在受限工具集上执行单次对话

    与主 ChatEngine 的区别:
      - 工具集受限（通常只读：read_file, list_dir, grep_files, file_search, web_search）
      - 无审批（自动执行）
      - 单次 prompt + max_steps 限制
      - 结果以结构化 dict 返回
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        workspace: str = ".",
        allowed_tools: list[str] | None = None,
        max_steps: int = 5,
    ):
        self.client = DeepSeekClient(api_key, base_url, model)
        self.workspace = Path(workspace).resolve()

        # 创建工具注册表（全量工具）
        full_registry = ToolRegistry(str(self.workspace), allow_shell=False)

        # 过滤工具
        if allowed_tools:
            self.tools = self._filter_tools(full_registry, allowed_tools)
        else:
            # 默认只读工具集
            self.tools = self._filter_tools(
                full_registry,
                ["read_file", "list_dir", "grep_files", "file_search", "web_search",
                 "git_status", "git_diff", "git_log", "git_show"],
            )

        self.session = Session()
        self.max_steps = max_steps

    def _filter_tools(self, registry: ToolRegistry, names: list[str]) -> ToolRegistry:
        """创建只包含指定工具的新注册表"""
        filtered = ToolRegistry(str(self.workspace), allow_shell=False)
        filtered._handlers = {
            name: registry._handlers[name]
            for name in names
            if name in registry._handlers
        }
        # 重建别名
        filtered._aliases = {}
        import re
        for name in filtered._handlers:
            filtered._aliases[name.lower().replace("-", "_").replace(" ", "_")] = name
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            filtered._aliases[snake] = name
        return filtered

    async def run(self, prompt: str = "") -> dict:
        """执行子智能体任务

        Args:
            prompt: 任务描述（如已通过 session.messages 设置则可为空）

        Returns:
            {"summary": "...", "messages": [...], "success": bool}
        """
        if prompt:
            self.session.messages.append({"role": "user", "content": prompt})

        system = (
            "You are a read-only exploration sub-agent. "
            "You can read files, search code, list directories, and check git status. "
            "You CANNOT modify files or run shell commands. "
            "Analyze the task, explore the relevant files, and return a concise summary "
            "of your findings with specific file paths and line numbers where relevant."
        )

        assistant_text = ""
        step = 0

        while step < self.max_steps:
            step += 1
            messages = list(self.session.messages)
            tools = self.tools.get_definitions()

            tool_calls_found: list[dict] = []
            current_tools: dict[int, dict] = {}

            async for event in self.client.chat_stream(messages, tools, system=system):
                etype = event["type"]

                if etype == "text_delta":
                    assistant_text += event.get("content", "")
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
                elif etype == "error":
                    return {"summary": f"API error: {event['message']}", "messages": self.session.messages, "success": False}

            if not tool_calls_found:
                if assistant_text:
                    self.session.messages.append({"role": "assistant", "content": assistant_text})
                break

            # 构建 assistant 消息
            api_tool_calls = []
            import json
            for tc in tool_calls_found:
                api_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                    },
                })

            assistant_msg = {"role": "assistant", "content": assistant_text or None}
            assistant_msg["tool_calls"] = api_tool_calls
            self.session.messages.append(assistant_msg)
            assistant_text = ""

            # 执行工具
            for tc in tool_calls_found:
                result = await self.tools.execute(tc["name"], tc["arguments"])
                self.session.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result["content"],
                })

        return {
            "summary": assistant_text or "(no summary)",
            "messages": self.session.messages,
            "success": True,
        }

    async def close(self):
        await self.client.close()
