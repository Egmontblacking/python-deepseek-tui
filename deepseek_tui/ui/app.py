"""DeepSeek TUI — Textual 多面板终端界面"""
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Input, ListView, ListItem, Label, Button
from textual.containers import Horizontal, VerticalScroll, Container
from textual.screen import ModalScreen
from textual.message import Message
from textual.worker import Worker
from textual import work
from rich.text import Text
from rich.panel import Panel
from rich.markdown import Markdown
from rich import box
import asyncio
import json
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Messages
# ═══════════════════════════════════════════════════════════════

class EngineEvent(Message):
    """Engine → UI 事件"""
    def __init__(self, event: dict):
        self.event = event
        super().__init__()

class ApprovalRequest(Message):
    """工具审批请求"""
    def __init__(self, tool_name: str, args: dict, reply: asyncio.Queue):
        self.tool_name = tool_name
        self.args = args
        self.reply = reply
        super().__init__()

class PlanUpdated(Message):
    """Plan 更新"""
    def __init__(self, steps: list, explanation: str = ""):
        self.steps = steps
        self.explanation = explanation
        super().__init__()

class ChecklistUpdated(Message):
    """Checklist 更新"""
    def __init__(self, items: list):
        self.items = items
        super().__init__()

UserInputSubmitted = None  # handled by Input.Submitted directly


# ═══════════════════════════════════════════════════════════════
# Approval Modal
# ═══════════════════════════════════════════════════════════════

class ApprovalModal(ModalScreen[bool]):
    """工具审批弹窗"""

    def __init__(self, tool_name: str, args: dict):
        super().__init__()
        self.tool_name = tool_name
        self.args = args

    def compose(self) -> ComposeResult:
        args_preview = str(self.args)
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "..."
        yield Static(
            f"[bold yellow]Approve tool execution?[/bold yellow]\n\n"
            f"[bold]{self.tool_name}[/bold]\n"
            f"[dim]{args_preview}[/dim]",
            id="approval-text",
        )
        with Horizontal(id="approval-buttons"):
            yield Button("Approve", variant="success", id="btn-approve")
            yield Button("Deny", variant="error", id="btn-deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-approve")


# ═══════════════════════════════════════════════════════════════
# Chat View
# ═══════════════════════════════════════════════════════════════

class ChatView(VerticalScroll, can_focus=False):
    """可滚动的聊天消息列表"""

    def __init__(self):
        super().__init__(id="chat-view")
        self._current_assistant_block: list[str] = []
        self._thinking_text = ""

    def add_user_message(self, content: str):
        self.mount(Static(f"[bold green]You:[/bold green] {content}", classes="msg-user"))

    def begin_assistant(self):
        self.mount(Static("[bold]Assistant:[/bold]", classes="msg-assistant-header"))
        self._assistant_content = ""
        self._assistant_widget = Static("", classes="msg-assistant")
        self.mount(self._assistant_widget)

    def append_text(self, content: str):
        if hasattr(self, '_assistant_widget'):
            self._assistant_content += content
            self._assistant_widget.update(self._assistant_content)

    def show_thinking(self, content: str):
        """增量渲染 thinking 内容"""
        self._thinking_text += content
        if not hasattr(self, '_thinking_widget') or self._thinking_widget not in self.children:
            self._thinking_widget = Static("", classes="msg-thinking")
            self.mount(self._thinking_widget)
        self._thinking_widget.update(f"[dim italic]{self._thinking_text}[/dim italic]")

    def clear_thinking(self):
        self._thinking_text = ""
        if hasattr(self, '_thinking_widget') and self._thinking_widget in self.children:
            self._thinking_widget.remove()

    def add_tool_call(self, name: str, args: dict, result: dict):
        success = result.get("success", False)
        icon = "✓" if success else "✗"
        color = "green" if success else "red"

        args_str = str(args)
        if len(args_str) > 100:
            args_str = args_str[:100] + "..."

        content = result.get("content", "")[:400]

        tool_text = (
            f"[{color}][{icon} {name}][/{color}]\n"
            f"[dim]{content}[/dim]\n"
            f"[dim italic]{args_str}[/dim italic]"
        )
        self.mount(Static(tool_text, classes="msg-tool"))

    def scroll_to_bottom(self):
        if hasattr(self, 'scroll_end'):
            self.scroll_end(animate=False)


# ═══════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════

class Sidebar(VerticalScroll, can_focus=False):
    """右侧边栏：Plan + Todos"""

    def __init__(self):
        super().__init__(id="sidebar")
        self._plan_steps: list = []
        self._todo_items: list = []

    def compose(self) -> ComposeResult:
        yield Static("[bold]Plan[/bold]", classes="sidebar-title")
        yield Static("(no plan)", id="plan-content", classes="sidebar-section")
        yield Static("", classes="sidebar-sep")
        yield Static("[bold]Todos[/bold]", classes="sidebar-title")
        yield Static("(no todos)", id="todo-content", classes="sidebar-section")
        yield Static("", classes="sidebar-sep")
        yield Static("[bold]Agents[/bold]", classes="sidebar-title")
        yield Static("(none)", id="agent-content", classes="sidebar-section")

    def update_plan(self, steps: list, explanation: str = ""):
        self._plan_steps = steps
        if not steps:
            self.query_one("#plan-content").update("(no plan)")
            return
        lines = []
        for s in steps:
            status = s.get("status", "pending")
            step = s.get("step", "")
            icon = {"completed": "✓", "in_progress": "→", "pending": "○"}.get(status, "○")
            color = {"completed": "green", "in_progress": "yellow", "pending": "dim"}.get(status, "dim")
            lines.append(f"[{color}]{icon} {step}[/{color}]")
        self.query_one("#plan-content").update("\n".join(lines))

    def update_agents(self, running: list[str]):
        if not running:
            self.query_one("#agent-content").update("(none)")
            return
        lines = [f"[yellow]→ {a}[/yellow]" for a in running]
        self.query_one("#agent-content").update("\n".join(lines))

    def update_todos(self, items: list):
        self._todo_items = items
        if not items:
            self.query_one("#todo-content").update("(no todos)")
            return
        lines = []
        for item in items:
            status = item.get("status", "pending")
            content = item.get("content", "")[:60]
            icon = {"completed": "✓", "in_progress": "→", "pending": "○"}.get(status, "○")
            color = {"completed": "green", "in_progress": "yellow", "pending": "dim"}.get(status, "dim")
            lines.append(f"[{color}]{icon} {content}[/{color}]")
        self.query_one("#todo-content").update("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# Input Area
# ═══════════════════════════════════════════════════════════════

class InputArea(Container):
    """底部输入区域 — Input.Submitted 自动冒泡到 App"""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Type a message... (/help for commands)", id="chat-input")


# ═══════════════════════════════════════════════════════════════
# Main App
# ═══════════════════════════════════════════════════════════════

class DeepSeekTUI(App):
    """Textual DeepSeek TUI 主应用"""

    CSS = """
    Horizontal {
        height: 1fr;
    }
    #sidebar {
        width: 28;
    }
    #chat-view {
        width: 1fr;
    }
    InputArea {
        height: 3;
    }
    """
    BINDINGS = [
        ("ctrl+shift+q", "quit", "Quit"),
        ("ctrl+p", "mode_plan", "Plan"),
        ("ctrl+a", "mode_agent", "Agent"),
        ("ctrl+y", "mode_yolo", "YOLO"),
    ]

    def __init__(self, api_key: str = "", model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com", workspace: str = "",
                 allow_shell: bool = True, max_tool_steps: int = 10):
        super().__init__()
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._workspace = workspace or str(Path.cwd())
        self._allow_shell = allow_shell
        self._max_tool_steps = max_tool_steps
        self._engine = None
        self._mode = "agent"
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield ChatView()
            yield Sidebar()
        yield InputArea()
        yield Footer()

    def on_mount(self) -> None:
        from deepseek_tui.engine.engine import ChatEngine, AppMode
        from deepseek_tui.tools.registry import BLOCKED_IN_PLAN, NEED_APPROVAL

        self._engine = ChatEngine(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            workspace=self._workspace,
            allow_shell=self._allow_shell,
            max_tool_steps=self._max_tool_steps,
        )
        self._engine.set_approval_callback(self._ask_approval)
        self._BLOCKED_IN_PLAN = BLOCKED_IN_PLAN
        self._NEED_APPROVAL = NEED_APPROVAL
        self.title = "DeepSeek TUI"
        self.sub_title = f"Mode: {self._mode}"

    # ── 引擎事件流 ─────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理用户输入"""
        user_text = event.value.strip()
        if not user_text:
            return
        event.input.clear()

        if user_text.startswith("/"):
            await self._handle_command(user_text)
            return

        chat_view = self.query_one(ChatView)
        chat_view.add_user_message(user_text)
        chat_view.scroll_to_bottom()

        self._run_engine(user_text)

    @work(exclusive=True, thread=False)
    async def _run_engine(self, user_input: str) -> None:
        """后台 worker：跑 ChatEngine，投递事件到主线程"""
        from deepseek_tui.engine.engine import ChatEngine

        event_queue: asyncio.Queue = asyncio.Queue()

        async def _collect():
            async for event in self._engine.chat(user_input):
                await event_queue.put(event)
            await event_queue.put(None)  # sentinel

        # 并行跑 engine 和 UI 消费
        chat_view = self.query_one(ChatView)
        sidebar = self.query_one(Sidebar)

        engine_task = asyncio.create_task(_collect())
        thinking_active = False
        assistant_started = False

        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            if event is None:
                break

            etype = event["type"]

            if etype == "thinking_delta":
                thinking_active = True
                chat_view.show_thinking(event.get("content", ""))

            elif etype == "thinking_stop":
                if thinking_active:
                    chat_view.clear_thinking()
                    thinking_active = False

            elif etype == "text_delta":
                if not assistant_started:
                    chat_view.begin_assistant()
                    assistant_started = True
                chat_view.append_text(event.get("content", ""))

            elif etype == "tool_call":
                chat_view.add_tool_call(
                    event["name"],
                    event.get("args", {}),
                    event.get("result", {}),
                )
                # 检测 plan/checklist 更新
                if event["name"] == "update_plan" and event.get("result", {}).get("success"):
                    sidebar.update_plan(
                        self._engine.tools._plan_state.get("steps", []),
                        self._engine.tools._plan_state.get("explanation", ""),
                    )
                elif event["name"] == "checklist_write" and event.get("result", {}).get("success"):
                    sidebar.update_todos(
                        self._engine.tools._checklist_state.get("items", []),
                    )

            elif etype == "compact":
                before = event.get("before", 0)
                after = event.get("after", 0)
                pct = (1 - after / before) * 100 if before else 0
                chat_view.mount(Static(
                    f"[dim]Context compacted: {before} → {after} tokens ({pct:.0f}% reduced)[/dim]",
                    classes="msg-system",
                ))

            elif etype == "error":
                chat_view.mount(Static(f"[red]✗ {event['message']}[/red]", classes="msg-error"))

            # 更新 token 用量到 subtitle
            if self._engine:
                usage = self._engine.token_usage
                self.sub_title = f"Mode: {self._mode} | {usage['formatted']}"

            chat_view.scroll_to_bottom()

            # 更新 agent 侧边栏
            if self._engine and self._engine.subagents:
                sidebar.update_agents(self._engine.subagents.list_running())

        await engine_task

    # ── 审批 ───────────────────────────────────────────

    async def _ask_approval(self, tool_name: str, args: dict) -> bool:
        """引擎回调：弹出审批 Modal，阻塞等待结果"""
        reply_queue: asyncio.Queue = asyncio.Queue()
        self.post_message(ApprovalRequest(tool_name, args, reply_queue))
        result = await reply_queue.get()
        return result

    def on_approval_request(self, msg: ApprovalRequest) -> None:
        """显示审批弹窗"""

        async def _show_and_reply():
            modal = ApprovalModal(msg.tool_name, msg.args)
            approved = await self.push_screen(modal)
            await msg.reply.put(approved)

        asyncio.create_task(_show_and_reply())

    # ── 命令处理 ───────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        from deepseek_tui.engine.engine import AppMode

        command = cmd.lower().split()[0]

        if command in ("/quit", "/exit", "/q"):
            self._engine.save_session()
            self.exit()
        elif command == "/plan":
            self._mode = "plan"
            self._engine.set_mode(AppMode.PLAN)
            self.sub_title = "Mode: Plan"
        elif command == "/agent":
            self._mode = "agent"
            self._engine.set_mode(AppMode.AGENT)
            self.sub_title = "Mode: Agent"
        elif command == "/yolo":
            self._mode = "yolo"
            self._engine.set_mode(AppMode.YOLO)
            self.sub_title = "Mode: YOLO"
        elif command == "/compact":
            result = await self._engine.compact()
            if result["success"]:
                self.sub_title = f"Mode: {self._mode} | Compacted: {result['reduction']:.0f}%"
        elif command == "/save":
            self._engine.save_session()
        elif command == "/clear":
            self._engine.session.messages.clear()
            self.query_one(ChatView).remove_children()
        elif command == "/help":
            self.query_one(ChatView).mount(Static(
                "[dim]/plan | /agent | /yolo — switch mode[/dim]\n"
                "[dim]/save | /clear | /quit[/dim]",
                classes="msg-system",
            ))

    # ── 快捷键 ─────────────────────────────────────────

    def action_mode_plan(self):
        self._handle_command("/plan")

    def action_mode_agent(self):
        self._handle_command("/agent")

    def action_mode_yolo(self):
        self._handle_command("/yolo")

    def action_quit(self):
        self._engine.save_session() if self._engine else None
        self.exit()


# ═══════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════

def run_textual(api_key: str, model: str = "deepseek-chat",
                base_url: str = "https://api.deepseek.com",
                workspace: str = "", allow_shell: bool = True,
                max_tool_steps: int = 10):
    """启动 Textual TUI"""
    app = DeepSeekTUI(
        api_key=api_key,
        model=model,
        base_url=base_url,
        workspace=workspace,
        allow_shell=allow_shell,
        max_tool_steps=max_tool_steps,
    )
    app.run()
