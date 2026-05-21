"""CLI 参数解析与配置加载"""

import argparse
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def parse_args():
    parser = argparse.ArgumentParser(
        description="DeepSeek TUI - 终端对话智能体"
    )
    parser.add_argument("--prompt", "-p", help="单次对话 (非交互模式)")
    parser.add_argument("--resume", "-r", help="恢复会话 ID")
    parser.add_argument("--model", "-m", default="deepseek-chat", help="模型名称")
    parser.add_argument("--workspace", "-w", help="工作区路径")
    parser.add_argument("--no-shell", action="store_true", help="禁用 shell 工具")
    parser.add_argument(
        "--mode",
        choices=["plan", "agent", "yolo"],
        default="agent",
        help="运行模式",
    )
    return parser.parse_args()


def load_config(args) -> dict:
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": args.model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "workspace": args.workspace or os.getenv("DEEPSEEK_WORKSPACE", str(Path.cwd())),
        "allow_shell": not args.no_shell and os.getenv("DEEPSEEK_DISABLE_SHELL", "").lower() != "true",
        "max_tool_steps": int(os.getenv("DEEPSEEK_MAX_TOOL_STEPS", "10")),
        "mode": args.mode,
    }
