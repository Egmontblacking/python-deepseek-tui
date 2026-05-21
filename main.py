"""DeepSeek TUI - Python 实现
终端对话智能体，支持流式推理、工具调用、会话持久化。

用法:
    python main.py                          # 交互模式
    python main.py --prompt "hello"         # 单次对话
    python main.py --resume <session_id>    # 恢复会话
"""

import argparse
import os
import sys
from pathlib import Path

# 确保项目根在 path 中
sys.path.insert(0, str(Path(__file__).parent))

from cli import parse_args, load_config
from engine import ChatEngine
from tui import TerminalUI


def main():
    args = parse_args()
    config = load_config(args)

    if not config.get("api_key"):
        print("Error: DEEPSEEK_API_KEY is not set.")
        print("Set it via environment variable:")
        print("  set DEEPSEEK_API_KEY=sk-your-key-here")
        print("Or create a .env file in the project directory with:")
        print("  DEEPSEEK_API_KEY=sk-your-key-here")
        sys.exit(1)

    engine = ChatEngine(
        api_key=config["api_key"],
        base_url=config.get("base_url", "https://api.deepseek.com"),
        model=config.get("model", "deepseek-chat"),
        workspace=config.get("workspace", str(Path.cwd())),
        allow_shell=config.get("allow_shell", True),
    )

    if args.resume:
        engine.load_session(args.resume)

    if args.prompt:
        # 单次对话模式
        run_one_shot(engine, args.prompt)
    else:
        # 交互模式
        ui = TerminalUI(engine)
        ui.run()


def run_one_shot(engine: ChatEngine, prompt: str):
    """单次对话，直接输出到 stdout"""
    for chunk in engine.chat(prompt, stream=True):
        if chunk["type"] == "thinking":
            print(f"\x1b[90m{chunk['content']}\x1b[0m", end="", flush=True)
        elif chunk["type"] == "text":
            print(chunk["content"], end="", flush=True)
    print()


if __name__ == "__main__":
    main()
