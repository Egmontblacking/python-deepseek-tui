"""CLI 参数解析与配置加载

优先级: CLI 参数 > 环境变量 > config.toml > 默认值
"""

import argparse
import os
from pathlib import Path


def build_argparser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器"""
    p = argparse.ArgumentParser(
        description="DeepSeek TUI — 企业级终端 AI 编程智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  deepseek-tui                              # 交互模式
  deepseek-tui -p "解释 client.py"           # 单次对话
  deepseek-tui -r abc123                     # 恢复会话
  deepseek-tui -w D:/my-project              # 指定工作区
  deepseek-tui --mode plan                   # 只读模式启动
  deepseek-tui --no-shell                    # 禁用 shell 工具
        """,
    )
    p.add_argument("--prompt", "-p", help="单次对话（非交互模式）")
    p.add_argument("--resume", "-r", help="恢复指定会话 ID")
    p.add_argument("--model", "-m", help="模型名称（覆盖 config.toml / 环境变量）")
    p.add_argument("--base-url", help="API 地址（覆盖 config.toml / 环境变量）")
    p.add_argument("--workspace", "-w", help="工作区目录")
    p.add_argument("--no-shell", action="store_true", help="禁用 shell 执行工具")
    p.add_argument("--rich", action="store_true", help="使用 Rich TUI（默认 Textual）")
    p.add_argument(
        "--mode",
        choices=["plan", "agent", "yolo"],
        help="启动模式（默认 agent）",
    )
    return p


def parse_args():
    return build_argparser().parse_args()
