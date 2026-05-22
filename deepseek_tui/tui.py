"""终端界面 — Rich 渲染、模式切换、审批弹窗"""

import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import box

from deepseek_tui.engine.engine import ChatEngine, AppMode
from deepseek_tui.db.session import SessionStore


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
        self.store = engine.store
        engine.set_approval_callback(self._ask_approval)

    def run(self):
        self._print_welcome()
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self.console.print("\n[dim]Goodbye![/dim]")
            self.engine.save_session()
        except EOFError:
            pass

    def _print_welcome(self):
        self.console.print()
        self.console.print(Panel.fit(
            "[bold]DeepSeek TUI[/bold] — Enterprise Edition\n\n"
            "15+ tools, SQLite persistence, LoopGuard\n"
            "/plan /agent /yolo to switch mode, /help for commands",
            border_style="blue",
        ))
        self._print_mode()

    def _print_mode(self):
        color, label = MODE_STYLE.get(self.mode, ("green", "[A] Agent"))
        self.console.print(f"  Mode: [{color}]{label}[/{color}]")
        self.console.print()

    async def _repl(self):
        while True:
            try:
                user_input = Prompt.ask("[bold green]>>[/bold green]").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue
            try:
                async for event in self.engine.chat(user_input):
                    self._handle_event(event)
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")
            self.console.print("[dim]" + chr(0x2500) * 60 + "[/dim]")

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
            self.console.print(f"[dim]Saved: {self.engine.session.id}[/dim]")
        elif command == "/resume" and arg:
            s = self.store.load(arg)
            if s:
                self.engine.session = s
                self.console.print(f"[dim]Resumed: {arg} ({len(s.messages)} msgs)[/dim]")
            else:
                self.console.print(f"[red]Not found: {arg}[/red]")
        elif command == "/list":
            sessions = self.store.list_sessions()
            if sessions:
                table = Table(title="Saved Sessions", box=box.SIMPLE)
                table.add_column("ID", style="cyan")
                table.add_column("Msgs", justify="right")
                table.add_column("Updated", style="dim")
                for s in sessions:
                    table.add_row(s["id"], str(s["msg_count"]), s["updated_at"][:19])
                self.console.print(table)
            else:
                self.console.print("[dim]No saved sessions[/dim]")
        elif command == "/clear":
            self.engine.session.messages.clear()
            self.console.print("[dim]Cleared[/dim]")
        elif command == "/help":
            self.console.print(Panel.fit(
                "/plan | /agent | /yolo — switch mode\n"
                "/save | /resume ID | /list | /clear\n"
                "/quit — exit",
                border_style="dim",
            ))
        else:
            self.console.print(f"[dim]Unknown: {command}[/dim]")

    def _handle_event(self, event: dict):
        etype = event["type"]
        if etype == "user":
            self.console.print(f"\n[bold]You:[/bold] {event['content']}")
        elif etype == "thinking_delta":
            self.console.print(f"[dim italic]{event['content']}[/dim italic]", end="")
        elif etype == "text_start":
            self.console.print("\n[bold]Assistant:[/bold]")
        elif etype == "text_delta":
            self.console.print(event.get("content", ""), end="")
        elif etype == "text_stop":
            self.console.print()
        elif etype == "tool_call":
            name = event["name"]
            result = event.get("result", {})
            success = result.get("success", False)
            color = "green" if success else "red"
            icon = "[OK]" if success else "[FAIL]"
            content = result.get("content", "")[:300]
            args_str = str(event.get("args", {}))[:100]
            panel = Panel(content, title=f"[{color}]{icon} {name}[/{color}]",
                          subtitle=f"[dim]{args_str}[/dim]", border_style=color, box=box.ROUNDED)
            self.console.print(panel)
        elif etype == "error":
            self.console.print(f"\n[red]X {event['message']}[/red]")

    async def _ask_approval(self, tool_name: str, args: dict) -> bool:
        preview = str(args)[:80]
        self.console.print()
        return Confirm.ask(
            f"[yellow]Approve [bold]{tool_name}[/bold]?[/yellow]\n"
            f"  [dim]{preview}[/dim]\n  [dim]Approve?[/dim]",
            default=False,
        )
