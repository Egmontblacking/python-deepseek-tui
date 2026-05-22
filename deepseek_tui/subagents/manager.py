"""子智能体管理器 — asyncio.Task 池 + 完成事件管道"""

import asyncio
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SubagentCompletion:
    """子智能体完成事件"""
    agent_id: str
    status: str  # "completed" | "failed" | "cancelled"
    summary: str = ""
    error: str = ""
    result_messages: list[dict] = field(default_factory=list)
    finished_at: str = ""


class SubagentManager:
    """管理子智能体的生命周期: spawn / wait / cancel / list

    架构:
        _tasks: agent_id → asyncio.Task  (运行中的子智能体)
        _completions: asyncio.Queue       (完成事件管道)
        _results: agent_id → SubagentCompletion (已完成的子智能体结果缓存)
    """

    def __init__(self, max_concurrent: int = 10):
        self._tasks: dict[str, asyncio.Task] = {}
        self._completions: asyncio.Queue[SubagentCompletion] = asyncio.Queue()
        self._results: dict[str, SubagentCompletion] = {}
        self.max_concurrent = max_concurrent

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    def list_running(self) -> list[str]:
        """列出当前运行中的 agent_id"""
        return list(self._tasks.keys())

    def get_result(self, agent_id: str) -> Optional[SubagentCompletion]:
        """获取已完成的子智能体结果"""
        return self._results.get(agent_id)

    async def spawn(
        self,
        runner_factory,  # callable: () -> SubagentRunner
        agent_name: str = "",
    ) -> str:
        """启动一个子智能体，返回 agent_id

        Args:
            runner_factory: 工厂函数，返回已配置好的 SubagentRunner
            agent_name: 名称前缀

        Returns:
            agent_id: 8 位随机 ID
        """
        agent_id = agent_name + uuid.uuid4().hex[:6]

        # 并发限制
        while self.running_count >= self.max_concurrent:
            await self.wait_any(timeout=30.0)

        runner = runner_factory()
        task = asyncio.create_task(self._run_agent(agent_id, runner))
        self._tasks[agent_id] = task
        logger.info("Subagent spawned: %s (running=%d)", agent_id, self.running_count)
        return agent_id

    async def _run_agent(self, agent_id: str, runner):
        """运行子智能体并收集结果"""
        try:
            result = await runner.run()
            completion = SubagentCompletion(
                agent_id=agent_id,
                status="completed",
                summary=result.get("summary", ""),
                result_messages=result.get("messages", []),
                finished_at=datetime.now().isoformat(),
            )
        except asyncio.CancelledError:
            completion = SubagentCompletion(
                agent_id=agent_id,
                status="cancelled",
                finished_at=datetime.now().isoformat(),
            )
        except Exception as e:
            logger.error("Subagent %s failed: %s", agent_id, e)
            completion = SubagentCompletion(
                agent_id=agent_id,
                status="failed",
                error=str(e),
                finished_at=datetime.now().isoformat(),
            )

        self._tasks.pop(agent_id, None)
        self._results[agent_id] = completion
        await self._completions.put(completion)
        logger.info("Subagent %s: %s", agent_id, completion.status)

    async def wait_any(self, timeout: float = 30.0) -> Optional[SubagentCompletion]:
        """等待任意一个子智能体完成

        Returns:
            SubagentCompletion 或 None (超时)
        """
        if not self._tasks:
            return None
        try:
            return await asyncio.wait_for(self._completions.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def wait_all(self, timeout: float = 120.0) -> list[SubagentCompletion]:
        """等待所有子智能体完成"""
        results = []
        deadline = asyncio.get_event_loop().time() + timeout
        while self._tasks:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            completion = await self.wait_any(timeout=min(remaining, 10.0))
            if completion:
                results.append(completion)
        return results

    def cancel(self, agent_id: str) -> bool:
        """取消指定子智能体"""
        task = self._tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def cancel_all(self):
        """取消所有子智能体"""
        for agent_id in list(self._tasks.keys()):
            self.cancel(agent_id)

    def inject_completions(self) -> list[dict]:
        """将完成事件转换为可注入会话的系统消息

        Returns:
            list[dict]: 格式为 {"role": "system", "content": "<subagent.done>...</subagent.done>"}
        """
        messages = []
        while not self._completions.empty():
            try:
                completion = self._completions.get_nowait()
            except asyncio.QueueEmpty:
                break

            content = (
                f"<deepseek:subagent.done agent_id=\"{completion.agent_id}\" "
                f"status=\"{completion.status}\">\n"
                f"{completion.summary}\n"
            )
            if completion.error:
                content += f"Error: {completion.error}\n"
            content += "</deepseek:subagent.done>"

            messages.append({
                "role": "user",
                "content": content,
                "_meta": {"type": "subagent_completion", "agent_id": completion.agent_id},
            })

        return messages
