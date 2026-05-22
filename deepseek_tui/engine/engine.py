"""对话引擎 — ReAct turn loop、工具审批、LoopGuard 防漂移"""

import json
import asyncio
import logging
from enum import Enum
from typing import Any, AsyncIterator, Optional

from deepseek_tui.api.client import DeepSeekClient
from deepseek_tui.tools.registry import ToolRegistry, BLOCKED_IN_PLAN, NEED_APPROVAL
from deepseek_tui.db.session import Session, SessionStore
from deepseek_tui.subagents.manager import SubagentManager
from deepseek_tui.context.compact import Compactor
from deepseek_tui.context.token_counter import estimate_session_tokens, format_tokens

logger = logging.getLogger(__name__)


class AppMode(Enum):
    PLAN = "plan"
    AGENT = "agent"
    YOLO = "yolo"


SYSTEM_PROMPTS = {
    AppMode.PLAN: (
        "You are a helpful coding assistant in Plan mode (read-only).\n"
        "You can explore code, read files, and analyze architecture.\n"
        "You CANNOT modify files or run shell commands.\n"
        "When you need to make changes, describe your plan clearly."
    ),
    AppMode.AGENT: (
        "You are a helpful coding assistant.\n"
        "You have access to tools for reading/writing files, running shell commands,"
        " searching code, managing git, and tracking plans/checklists.\n"
        "Before making changes, check existing code to avoid mistakes.\n"
        "Be concise and precise. Write operations require user approval."
    ),
    AppMode.YOLO: (
        "You are a helpful coding assistant in YOLO mode.\n"
        "All tool calls are auto-approved. Be careful with destructive operations.\n"
        "Be concise and precise."
    ),
}


class LoopGuard:
    """防止任务漂移与死循环

    两个维度:
      1. 相同工具+相同参数连续调用 ≥ 3 次 → Block
      2. 同一工具连续失败 ≥ 8 次 → Halt
      一次成功即重置计数器。
    """

    def __init__(self):
        self._identical_calls: dict[str, int] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._blocked_reason: Optional[str] = None
        self._halted_reason: Optional[str] = None

    def _hash_args(self, args: dict) -> str:
        items = sorted(args.items(), key=lambda x: x[0])
        return json.dumps(items, sort_keys=True, ensure_ascii=False)

    def should_block(self, tool_name: str, args: dict) -> bool:
        key = f"{tool_name}:{self._hash_args(args)}"
        count = self._identical_calls.get(key, 0) + 1
        self._identical_calls[key] = count
        if count >= 3:
            self._blocked_reason = (
                f"Blocked: '{tool_name}' with these arguments has already run "
                f"{count} times this turn."
            )
            return True
        return False

    def record_outcome(self, tool_name: str, success: bool) -> Optional[str]:
        if success:
            if tool_name in self._consecutive_failures:
                self._consecutive_failures[tool_name] = 0
            return None
        count = self._consecutive_failures.get(tool_name, 0) + 1
        self._consecutive_failures[tool_name] = count
        if count >= 3:
            logger.warning("Tool '%s' failed %d consecutive times", tool_name, count)
        if count >= 8:
            self._halted_reason = (
                f"Halted: '{tool_name}' has failed {count} consecutive times. "
                f"Please reconsider your approach."
            )
            return self._halted_reason
        return None

    def reset(self):
        self._identical_calls.clear()
        self._consecutive_failures.clear()
        self._blocked_reason = None
        self._halted_reason = None


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
        self.loop_guard = LoopGuard()
        self.subagents = SubagentManager()
        # 连接工具注册表到子智能体管理器
        self.tools.set_subagent_manager(self.subagents)
        self.subagents._engine_api_key = api_key
        self.subagents._engine_base_url = base_url
        self.subagents._engine_model = model
        self.compactor = Compactor()
        self._working_set: set[str] = set()
        self._approval_callback: Optional[callable] = None

    def set_approval_callback(self, cb: callable):
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
        self.loop_guard.reset()
        self.session.messages.append({"role": "user", "content": user_input})
        yield {"type": "user", "content": user_input}

        step = 0
        while step < self.max_tool_steps:
            step += 1
            if self.loop_guard._halted_reason:
                yield {"type": "error", "message": self.loop_guard._halted_reason}
                break

            # ── 自动压缩检查 ──
            estimated = estimate_session_tokens(self.session.messages)
            if self.compactor.should_compact(self.session.messages, estimated):
                result = await self._do_compact()
                if result.success:
                    yield {"type": "compact", "before": result.tokens_before,
                           "after": result.tokens_after, "summary": result.summary}

            messages = list(self.session.messages)
            system = SYSTEM_PROMPTS[self.mode]
            tools = self.tools.get_definitions()

            assistant_content = ""
            reasoning_content = ""
            tool_calls_found: list[dict] = []
            current_tools: dict[int, dict] = {}

            async for event in self.client.chat_stream(messages, tools, system=system):
                etype = event["type"]
                if etype in ("thinking_start", "thinking_delta", "thinking_stop"):
                    if etype == "thinking_delta":
                        reasoning_content += event.get("content", "")
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
                    current_tools[idx] = {"id": event["id"], "name": event["name"], "arguments": {}}
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

            # ── 注入子智能体完成事件 ──
            subagent_msgs = self.subagents.inject_completions()
            for msg in subagent_msgs:
                self.session.messages.append(msg)
                yield {"type": "user", "content": f"[Sub-agent: {msg['_meta']['agent_id']}] {msg['content'][:100]}..."}

            if not tool_calls_found:
                if assistant_content:
                    msg = {"role": "assistant", "content": assistant_content}
                    if reasoning_content:
                        msg["reasoning_content"] = reasoning_content
                    self.session.messages.append(msg)

                # 还有子智能体在跑？等一个
                if self.subagents.running_count > 0:
                    completion = await self.subagents.wait_any(timeout=15.0)
                    if completion:
                        inject_msg = {
                            "role": "user",
                            "content": f"<deepseek:subagent.done agent_id=\"{completion.agent_id}\" status=\"{completion.status}\">{completion.summary}</deepseek:subagent.done>",
                            "_meta": {"type": "subagent_completion", "agent_id": completion.agent_id},
                        }
                        self.session.messages.append(inject_msg)
                        continue  # 继续下一轮让 LLM 处理结果

                self.save_session()
                yield {"type": "turn_complete", "usage": {}}
                return

            assistant_msg: dict = {"role": "assistant", "content": assistant_content or None}
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            api_tool_calls = []
            for tc in tool_calls_found:
                api_tool_calls.append({
                    "id": tc["id"], "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                })
            assistant_msg["tool_calls"] = api_tool_calls
            self.session.messages.append(assistant_msg)

            for tc in tool_calls_found:
                name = tc["name"]
                args = tc["arguments"]

                if self.mode == AppMode.PLAN and name in BLOCKED_IN_PLAN:
                    result = {"success": False, "content": f"Tool '{name}' blocked in Plan mode"}
                    yield {"type": "tool_call", "name": name, "args": args, "result": result}
                    self.session.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result["content"]})
                    continue

                if self.loop_guard.should_block(name, args):
                    result = {"success": False, "content": self.loop_guard._blocked_reason}
                    yield {"type": "tool_call", "name": name, "args": args, "result": result}
                    self.session.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result["content"]})
                    continue

                is_readonly = name not in NEED_APPROVAL
                if self.mode == AppMode.AGENT and not is_readonly and self._approval_callback:
                    approved = await self._approval_callback(name, args)
                    if not approved:
                        result = {"success": False, "content": "User denied tool execution"}
                        yield {"type": "tool_call", "name": name, "args": args, "result": result}
                        self.session.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result["content"]})
                        continue

                result = await self.tools.execute(name, args)
                self.loop_guard.record_outcome(name, result.get("success", False))
                yield {"type": "tool_call", "name": name, "args": args, "result": result}
                self.session.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result["content"]})

        yield {"type": "turn_complete", "usage": {}}

    # ── 上下文管理 ─────────────────────────────────────

    def track_file(self, path: str):
        """追踪活跃文件"""
        self._working_set.add(path)

    @property
    def token_usage(self) -> dict:
        """当前 token 用量"""
        estimated = estimate_session_tokens(self.session.messages)
        pct = (estimated / self.compactor.token_budget) * 100
        return {
            "estimated": estimated,
            "budget": self.compactor.token_budget,
            "pct": pct,
            "formatted": f"{format_tokens(estimated)} / {format_tokens(self.compactor.token_budget)} ({pct:.0f}%)",
        }

    async def compact(self) -> dict:
        """手动触发压缩（/compact 命令）"""
        result = await self._do_compact()
        return {
            "success": result.success,
            "before": result.tokens_before,
            "after": result.tokens_after,
            "reduction": result.reduction_pct,
        }

    async def _do_compact(self):
        """执行压缩：将旧消息替换为摘要"""
        async def _summarize(messages_to_summarize):
            # 使用 Flash 模型生成摘要
            compact_client = DeepSeekClient(
                self.client.api_key,
                self.client.base_url,
                "deepseek-chat",  # 可用 deepseek-flash 降低成本
                max_retries=1,
            )
            summary_prompt = (
                "Summarize the following conversation history concisely. "
                "Include: (1) key decisions made, (2) files modified/read, "
                "(3) tools used, (4) tasks in progress. Be brief.\n\n"
            )
            for msg in messages_to_summarize:
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))[:500]
                summary_prompt += f"[{role}] {content}\n"

            summary_text = ""
            async for event in compact_client.chat_stream(
                [{"role": "user", "content": summary_prompt}],
                max_tokens=512,
            ):
                if event["type"] == "text_delta":
                    summary_text += event.get("content", "")
            await compact_client.close()
            return summary_text.strip()

        result = self.compactor.compact(
            self.session.messages,
            working_set_paths=self._working_set,
            summary_callback=_summarize,
        )
        if result.success:
            self.session.messages = result.messages
            self._working_set.clear()
        return result

    async def close(self):
        await self.client.close()
