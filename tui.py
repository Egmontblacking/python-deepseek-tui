"""终端界面 — Rich 渲染、多行输入、模式切换、审批弹窗

使用 Rich 进行终端渲染，支持:
  - 流式显示思考过程 (灰色斜体)
  - Markdown 渲染回复
  - 工具调用面板 (彩色边框)
  - 模式切换 (Plan/Agent/YOLO)
  - 工具审批 (y/n 交互)
"""

import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import box

from engine import ChatEngine, AppMode
from session import SessionStore


# 模式颜色和图标
MODE_STYLE = {
    AppMode.PLAN:  ("cyan", "[P] Plan"),
    AppMode.AGENT: ("green", "[A] Agent"),
    AppMode.YOLO:  ("yellow", "[Y] YOLO"),
}


class TerminalUI:
    """终端交互界面"""

    def __init__(self, engine: ChatEngine):
        self.engine = engine
        self.console = Console()
        self.mode = engine.mode
        self.store = SessionStore()

        # 设置审批回调
        engine.set_approval_callback(self._ask_approval)

    def run(self):
        """主循环"""
        self._print_welcome()
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self.console.print("\n[dim]Goodbye! Session saved.[/dim]")
            self.engine.save_session()
        except EOFError:
            pass

    def _print_welcome(self):
        self.console.print()
        self.console.print(Panel.fit(
            "[bold]DeepSeek TUI[/bold] — Python Edition\n\n"
            "Type your message and press Enter.\n"
            "[dim]Ctrl+D or /quit to exit | /plan /agent /yolo to switch mode[/dim]",
            border_style="blue",
        ))
        self._print_mode()

    def _print_mode(self):
        color, label = MODE_STYLE[self.mode]
        self.console.print(f"  Mode: [{color}]{label}[/{color}]")
        self.console.print()

    async def _repl(self):
        """交互式 REPL 循环"""
        while True:
            try:
                user_input = Prompt.ask("[bold green]>>[/bold green]").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue

            # 命令处理
            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            # 正常对话
            try:
                async for event in self.engine.chat(user_input):
                    self._handle_event(event)
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")

            # 分隔线
            self.console.print("[dim]─" * 60 + "[/dim]")

    def _handle_command(self, cmd: str):
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/quit", "/exit", "/q"):
            self.engine.save_session()
            raise EOFError()
        elif command == "/plan":
            self.mode = AppMode.PLAN
            self.engine.set_mode(self.mode)
            self._print_mode()
        elif command == "/agent":
            self.mode = AppMode.AGENT
            self.engine.set_mode(self.mode)
            self._print_mode()
        elif command == "/yolo":
            self.mode = AppMode.YOLO
            self.engine.set_mode(self.mode)
            self._print_mode()
        elif command == "/save":
            self.engine.save_session()
            self.console.print(f"[dim]Session saved: {self.engine.session.id}[/dim]")
        elif command == "/resume" and arg:
            s = self.store.load(arg)
            if s:
                self.engine.session = s
                self.console.print(f"[dim]Resumed session: {arg} ({len(s.messages)} messages)[/dim]")
            else:
                self.console.print(f"[red]Session not found: {arg}[/red]")
        elif command == "/list":
            sessions = self.store.list_sessions()
            if sessions:
                table = Table(title="Saved Sessions", box=box.SIMPLE)
                table.add_column("ID", style="cyan")
                table.add_column("Messages", justify="right")
                table.add_column("Updated", style="dim")
                for s in sessions:
                    table.add_row(s["id"], str(s["msg_count"]), s["updated_at"][:19])
                self.console.print(table)
            else:
                self.console.print("[dim]No saved sessions[/dim]")
        elif command == "/clear":
            self.engine.session.messages.clear()
            self.console.print("[dim]Session cleared[/dim]")
        elif command == "/help":
            self.console.print(Panel.fit(
                "[bold]Commands:[/bold]\n"
                "  /plan       — Plan mode (read-only)\n"
                "  /agent      — Agent mode (with approval)\n"
                "  /yolo       — YOLO mode (auto-approve)\n"
                "  /save       — Save session\n"
                "  /resume ID  — Resume session\n"
                "  /list       — List saved sessions\n"
                "  /clear      — Clear current session\n"
                "  /quit       — Exit",
                border_style="dim",
            ))
        else:
            self.console.print(f"[dim]Unknown command: {command} (use /help)[/dim]")

    def _handle_event(self, event: dict):
        """处理引擎事件并渲染到终端"""
        etype = event["type"]

        if etype == "user":
            self.console.print(f"\n[bold]You:[/bold] {event['content']}")

        elif etype == "thinking_start":
            pass  # 不打印，等 delta

        elif etype == "thinking_delta":
            self.console.print(f"[dim italic]{event['content']}[/dim italic]", end="")

        elif etype == "thinking_stop":
            pass  # flush 由 text_start 处理

        elif etype == "text_start":
            self.console.print("\n[bold]Assistant:[/bold]")
            self.console.print("[bold]────────────────[/bold]")

        elif etype == "text_delta":
            # 直接用 Rich 渲染 — 处理 markdown
            content = event.get("content", "")
            # 简单情况直接 print
            self.console.print(content, end="")

        elif etype == "text_stop":
            self.console.print()  # 换行

        elif etype == "tool_call":
            name = event["name"]
            args = event.get("args", {})
            result = event.get("result", {})
            success = result.get("success", False)
            color = "green" if success else "red"
            icon = "[OK]" if success else "[FAIL]"

            # 截断参数和结果以便显示
            args_str = str(args)
            if len(args_str) > 100:
                args_str = args_str[:100] + "..."

            result_str = result.get("content", "")
            if len(result_str) > 300:
                result_str = result_str[:300] + "...[truncated]"

            panel = Panel(
                f"{result_str}",
                title=f"[{color}]{icon} {name}[/{color}]",
                subtitle=f"[dim]{args_str}[/dim]",
                border_style=color,
                box=box.ROUNDED,
            )
            self.console.print(panel)

        elif etype == "turn_complete":
            usage = event.get("usage", {})
            if usage:
                self.console.print(
                    f"[dim]Tokens: {usage.get('total_tokens', '?')} | "
                    f"Session: {self.engine.session.id}[/dim]"
                )

        elif etype == "error":
            self.console.print(f"\n[red]X {event['message']}[/red]")

    async def _ask_approval(self, tool_name: str, args: dict) -> bool:
        """工具审批 — 询问用户"""
        args_preview = str(args)
        if len(args_preview) > 80:
            args_preview = args_preview[:80] + "..."

        self.console.print()
        approved = Confirm.ask(
            f"[yellow]Approve [bold]{tool_name}[/bold]?[/yellow]\n"
            f"  [dim]{args_preview}[/dim]\n"
            f"  [dim]Approve?[/dim]",
            default=False,
        )
        return approved


# ── 独立运行 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    from pathlib import Path

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("Set DEEPSEEK_API_KEY environment variable")
        sys.exit(1)

    engine = ChatEngine(
        api_key=api_key,
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        workspace=str(Path.cwd()),
    )
    ui = TerminalUI(engine)
    ui.run()
