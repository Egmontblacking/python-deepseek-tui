"""上下文压缩 — 三层策略：保留 → 裁剪 → 摘要"""

import logging
from typing import Optional
from deepseek_tui.context.token_counter import estimate_message_tokens, estimate_session_tokens, format_tokens

logger = logging.getLogger(__name__)

# 默认配置
KEEP_RECENT = 8          # 最近 N 条消息始终保留
MIN_SUMMARIZE = 6        # 至少 6 条才值得摘要
AUTO_THRESHOLD = 0.8     # 80% 用量触发自动压缩
TOKEN_BUDGET = 1_000_000  # V4 上下文窗口


class CompactionResult:
    """压缩结果"""

    def __init__(self):
        self.messages: list[dict] = []
        self.summary: str = ""
        self.tokens_before: int = 0
        self.tokens_after: int = 0
        self.retries: int = 0
        self.success: bool = False

    @property
    def reduction_pct(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return (1.0 - self.tokens_after / self.tokens_before) * 100


class Compactor:
    """上下文压缩器"""

    def __init__(
        self,
        keep_recent: int = KEEP_RECENT,
        min_summarize: int = MIN_SUMMARIZE,
        auto_threshold: float = AUTO_THRESHOLD,
        token_budget: int = TOKEN_BUDGET,
    ):
        self.keep_recent = keep_recent
        self.min_summarize = min_summarize
        self.auto_threshold = auto_threshold
        self.token_budget = token_budget

    def should_compact(self, messages: list[dict], estimated_tokens: int | None = None) -> bool:
        """判断是否需要压缩"""
        if len(messages) < self.keep_recent + self.min_summarize:
            return False

        if estimated_tokens is None:
            estimated_tokens = estimate_session_tokens(messages)

        threshold_tokens = int(self.token_budget * self.auto_threshold)
        return estimated_tokens >= threshold_tokens

    def compact(
        self,
        messages: list[dict],
        working_set_paths: set[str] | None = None,
        summary_callback=None,
    ) -> CompactionResult:
        """执行压缩

        Args:
            messages: 完整消息列表
            working_set_paths: 活跃文件路径集合（这些路径相关的消息优先保留）
            summary_callback: async def (messages_to_summarize) -> str

        Returns:
            CompactionResult
        """
        result = CompactionResult()
        result.tokens_before = estimate_session_tokens(messages)

        if len(messages) <= self.keep_recent:
            result.messages = list(messages)
            result.tokens_after = result.tokens_before
            result.success = True
            return result

        # 分层策略
        pinned, to_summarize = self._plan_compaction(messages, working_set_paths)

        # 如果可摘要的消息太少，只保留保留的
        if len(to_summarize) < self.min_summarize:
            result.messages = pinned
            result.tokens_after = estimate_session_tokens(pinned)
            result.success = True
            return result

        # 生成摘要
        if summary_callback:
            try:
                result.summary = summary_callback(to_summarize)
            except Exception as e:
                logger.error("Summary generation failed: %s", e)
                result.messages = pinned
                result.tokens_after = estimate_session_tokens(pinned)
                result.success = True
                return result

        # 构建压缩后的消息
        compacted = list(pinned)  # 保留的消息
        if result.summary:
            compacted.append({
                "role": "user",
                "content": f"[Context compaction summary]\n{result.summary}\n[/Context compaction summary]",
                "_meta": {"type": "compaction_summary"},
            })

        result.messages = compacted
        result.tokens_after = estimate_session_tokens(compacted)
        result.success = True
        return result

    def _plan_compaction(
        self,
        messages: list[dict],
        working_set_paths: set[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """规划压缩分区：pinned（保留）vs to_summarize（摘要）

        Returns:
            (pinned_messages, messages_to_summarize)
        """
        total = len(messages)

        # 最近 N 条保留
        recent_start = max(0, total - self.keep_recent)
        recent_msgs = messages[recent_start:]

        older_msgs = messages[:recent_start]

        pinned = []
        to_summarize = []

        # 老消息中：涉及工作集文件的优先保留
        if working_set_paths:
            for msg in older_msgs:
                content = str(msg.get("content", ""))
                if any(path in content for path in working_set_paths):
                    pinned.append(msg)
                else:
                    to_summarize.append(msg)
        else:
            to_summarize = list(older_msgs)

        # 错误消息保留
        error_msgs = []
        for i, msg in enumerate(to_summarize):
            content = str(msg.get("content", ""))
            if "error" in content.lower() or "Error" in content or "failed" in content.lower():
                error_msgs.append(msg)
        for msg in error_msgs:
            if msg in to_summarize:
                to_summarize.remove(msg)
                pinned.append(msg)

        # 最近的消息保留
        pinned.extend(recent_msgs)

        return pinned, to_summarize

    def trim_oldest(self, messages: list[dict], max_messages: int) -> list[dict]:
        """直接裁剪最老的消息到指定数量"""
        if len(messages) <= max_messages:
            return list(messages)
        return messages[-max_messages:]
