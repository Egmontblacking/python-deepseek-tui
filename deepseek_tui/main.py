"""DeepSeek TUI 入口 — 企业级 Python 实现

用法:
    python main.py                          # 交互模式
    python main.py -p "解释 client.py"      # 单次对话
    python main.py -r abc123                # 恢复会话
    python main.py -w D:/my-project         # 指定工作区
    python main.py --mode plan              # 只读模式启动
"""

import sys
import asyncio
from pathlib import Path

# 确保项目根在 path 中（直接运行此文件时也需要）
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from deepseek_tui.cli import parse_args
from deepseek_tui.config import build_config
from deepseek_tui.engine.engine import ChatEngine
try:
    from deepseek_tui.ui.app import run_textual
    _has_textual = True
except ImportError:
    _has_textual = False
    run_textual = None

from deepseek_tui.tui import TerminalUI  # Rich 回退


def main():
    args = parse_args()
    config = build_config(args)

    if not config.api_key:
        print("Error: DEEPSEEK_API_KEY is not set.")
        print("  Set it via environment variable:")
        print("    export DEEPSEEK_API_KEY=sk-your-key-here")
        print("  Or set 'api.key' in config.toml")
        sys.exit(1)

    engine = ChatEngine(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        workspace=config.workspace,
        allow_shell=config.allow_shell,
        max_tool_steps=config.max_tool_steps,
    )

    if hasattr(args, "mode") and args.mode:
        from deepseek_tui.engine.engine import AppMode
        engine.set_mode(AppMode(args.mode))

    if args.resume:
        engine.load_session(args.resume)
        print(f"Resumed session: {args.resume}")

    if args.prompt:
        async def _oneshot():
            async for event in engine.chat(args.prompt):
                etype = event["type"]
                if etype == "thinking_delta":
                    print(f"\x1b[90m{event['content']}\x1b[0m", end="", flush=True)
                elif etype == "text_delta":
                    print(event.get("content", ""), end="", flush=True)
                elif etype == "text_stop":
                    print()
                elif etype == "tool_call" and event.get("result"):
                    name = event["name"]
                    ok = event["result"].get("success", False)
                    icon = "✓" if ok else "✗"
                    content = event["result"].get("content", "")[:200]
                    print(f"\n\x1b[90m[{icon} {name}]\x1b[0m {content}")
                elif etype == "error":
                    print(f"\n\x1b[91mError: {event['message']}\x1b[0m")
        asyncio.run(_oneshot())
        print()
    else:
        if getattr(args, "rich", False) or not _has_textual:
            if not _has_textual:
                print("Textual not installed. Using Rich TUI.")
                print("Install with: pip install textual")
            ui = TerminalUI(engine)
            ui.run()
        else:
            run_textual(
                api_key=config.api_key,
                model=config.model,
                base_url=config.base_url,
                workspace=config.workspace,
                allow_shell=config.allow_shell,
                max_tool_steps=config.max_tool_steps,
            )


if __name__ == "__main__":
    main()
