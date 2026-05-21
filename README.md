# python-deepseek-tui 使用指南与源码解析

> Python 实现的终端对话智能体 — 流式推理、工具调用、会话持久化  
> 对应原版 DeepSeek TUI (Rust) 的核心功能子集

---

## 目录

1. [快速开始](#1-快速开始)
2. [使用指南](#2-使用指南)
3. [架构总览](#3-架构总览)
4. [入口与 CLI](#4-入口与-cli)
5. [API 客户端](#5-api-客户端)
6. [工具系统](#6-工具系统)
7. [对话引擎](#7-对话引擎)
8. [会话持久化](#8-会话持久化)
9. [终端界面](#9-终端界面)
10. [完整数据流](#10-完整数据流)

---

## 1. 快速开始

### 安装

```bash
cd python-deepseek-tui
pip install -r requirements.txt
```

依赖:

```
httpx>=0.28.0          # 异步 HTTP/2 客户端
rich>=13.0.0           # 终端渲染 (颜色/面板/Markdown)
pydantic>=2.0.0        # 数据校验 (预留)
python-dotenv>=1.0.0   # .env 环境变量加载
```

### 配置

方式一 — 环境变量:

```bash
set DEEPSEEK_API_KEY=sk-your-key-here
set DEEPSEEK_MODEL=deepseek-chat        # 可选
set DEEPSEEK_BASE_URL=https://api.deepseek.com  # 可选
```

方式二 — `.env` 文件 (放在项目根目录):

```
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_MODEL=deepseek-chat
```

### 启动

```bash
# 交互模式 (默认)
python main.py

# 单次对话
python main.py --prompt "explain the code in client.py"

# 恢复历史会话
python main.py --resume <session_id>

# 指定工作区
python main.py --workspace D:/my-project

# 禁用 shell 工具
python main.py --no-shell
```

### 运行时命令

```
/plan      🔍 只读探索模式 (阻止写文件/执行命令)
/agent     🤖 智能体模式 (写入需要审批)
/yolo      ⚡ 自动批准模式
/save      保存当前会话
/resume ID 恢复指定会话
/list      列出已保存的会话
/clear     清空当前会话
/quit      退出 (自动保存)
```

### 交互示例

```
▸ read the file main.py and explain its structure

Assistant:
────────────────
Let me start by reading the file.
────────────────
┌─ ✓ read_file ───────────────────────────────────────┐
│ 1 | """DeepSeek TUI - Python...                    │
│ 2 | ...                                            │
│ ...                                                │
└─ path='main.py' ───────────────────────────────────┘

The file is structured as follows:
1. Shebang and docstring
2. argparse and standard library imports
3. ...
```

---

## 2. 使用指南

### 2.1 三种运行模式

| 模式 | 命令 | 读文件 | 写文件 | Shell | 审批 |
|---|---|---|---|---|---|
| Plan 🔍 | `/plan` | ✓ | ✗ | ✗ | 无需 |
| Agent 🤖 | `/agent` | ✓ | ✓ | ✓ | 需确认 |
| YOLO ⚡ | `/yolo` | ✓ | ✓ | ✓ | 自动通过 |

### 2.2 工具调用审批

在 Agent 模式下，当模型尝试执行写操作或 shell 命令时：

```
⚠ Approve write_file?
  {'path': 'output.txt', 'content': 'hello world'}
  Approve? [y/N]
```

- 输入 `y` — 允许执行
- 输入 `n` 或回车 — 拒绝，模型收到错误后重新规划

### 2.3 会话管理

会话自动保存到 `~/.deepseek-tui/sessions/<id>.json`:

```
/list

  Saved Sessions
 ┌──────────┬──────────┬─────────────────────┐
 │ ID       │ Messages │ Updated             │
 ├──────────┼──────────┼─────────────────────┤
 │ a1b2c3d4 │    12    │ 2026-05-09T10:30:00 │
 │ e5f6g7h8 │     3    │ 2026-05-09T09:15:00 │
 └──────────┴──────────┴─────────────────────┘

/resume a1b2c3d4   # 恢复这个会话
```

### 2.4 单次对话模式

适合脚本集成和管道:

```bash
# 直接输出到 stdout
python main.py -p "what's in tools.py?"

# 结合管道
python main.py -p "list all function names in client.py" | grep "def "
```

---

## 3. 架构总览

### 3.1 模块关系图

```
┌──────────────┐
│   main.py    │  入口 + 参数路由
└──────┬───────┘
       │
       ├── cli.py      参数解析 + 配置加载
       │
       ├── engine.py   对话引擎 (核心)
       │   ├── client.py   DeepSeek API 客户端
       │   ├── tools.py    工具注册表 + 6 个工具
       │   └── session.py  会话持久化
       │
       └── tui.py      终端界面 (Rich)
           └── engine.py  (通过事件流交互)
```

### 3.2 数据流全景

```
用户输入 (stdin)
    │
    ▼
┌─────────────────────────────────────────┐
│  tui.py — TerminalUI                    │
│  ├─ Prompt.ask() 获取输入               │
│  ├─ /命令 解析                          │
│  └─ engine.chat(user_input) 调用       │
└────────────────┬────────────────────────┘
                 │ async generator
                 ▼
┌─────────────────────────────────────────┐
│  engine.py — ChatEngine                 │
│  ┌───────────────────────────────────┐  │
│  │  while step < max_tool_steps:     │  │
│  │    ① 构建消息 + 工具定义          │  │
│  │    ② client.chat_stream()  SSE   │  │
│  │    ③ 解析 thinking/text/tool     │  │
│  │    ④ Plan 模式阻止检查           │  │
│  │    ⑤ 审批回调 (Agent 模式)       │  │
│  │    ⑥ tools.execute()            │  │
│  │    ⑦ 结果注入消息历史            │  │
│  │    ⑧ tool_calls 为空 → break    │  │
│  └───────────────────────────────────┘  │
└────────────────┬────────────────────────┘
                 │ yield events
                 ▼
┌─────────────────────────────────────────┐
│  DeepSeek API                           │
│  POST /v1/chat/completions              │
│  ← SSE stream (text/thinking/tool_use)  │
└─────────────────────────────────────────┘
```

---

## 4. 入口与 CLI

### 4.1 启动流程

```
main()
  │
  ├─ parse_args()  — argparse 解析
  │   ├─ --prompt / -p
  │   ├─ --resume / -r
  │   ├─ --model / -m
  │   ├─ --workspace / -w
  │   ├─ --no-shell
  │   └─ --mode {plan|agent|yolo}
  │
  ├─ load_config()  — 合并 CLI + 环境变量 + .env
  │   ├─ os.getenv("DEEPSEEK_API_KEY")
  │   ├─ os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
  │   └─ os.getenv("DEEPSEEK_WORKSPACE", str(cwd))
  │
  ├─ ChatEngine(api_key, base_url, model, workspace)
  │
  ├─ args.resume?
  │   └─ engine.load_session(id)
  │
  └─ args.prompt?
      ├─ YES → run_one_shot(engine, prompt)
      └─ NO  → TerminalUI(engine).run()
```

### 4.2 源码实现

**文件**: `cli.py`

```python
def load_config(args) -> dict:
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": args.model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "workspace": args.workspace or os.getenv("DEEPSEEK_WORKSPACE", str(Path.cwd())),
        "allow_shell": not args.no_shell
            and os.getenv("DEEPSEEK_DISABLE_SHELL", "").lower() != "true",
        "max_tool_steps": int(os.getenv("DEEPSEEK_MAX_TOOL_STEPS", "10")),
        "mode": args.mode,
    }
```

优先级: **CLI 参数 > 环境变量 > 默认值**

---

## 5. API 客户端

### 5.1 模块结构

**文件**: `client.py`

```
DeepSeekClient
├── TokenBucket       — 速率限制 (令牌桶算法)
├── ConnectionHealth  — 连接健康状态机
├── chat_stream()     — 主入口 (带重试)
└── _do_stream()      — SSE 逐行解析
```

### 5.2 SSE 流式请求流程

```
chat_stream(messages, tools, system)
  │
  └─ for attempt in 0..max_retries:
       │
       ├─ TokenBucket.acquire() — 等待速率令牌
       │
       ├─ POST /v1/chat/completions
       │   Headers: Authorization, Content-Type, Accept: text/event-stream
       │   Body: { model, messages, stream: true, tools, max_tokens }
       │
       ├─ HTTP 2xx?
       │   └─ YES → _do_stream(body)
       │       │
       │       └─ 逐行读取 response body:
       │            │
       │            ├─ "data: {...}" → JSON 解析
       │            │   ├─ reasoning_content → thinking_start/delta/stop
       │            │   ├─ tool_calls        → tool_call_start/delta/stop
       │            │   └─ content           → text_start/delta/stop
       │            │
       │            └─ "data: [DONE]" → finish
       │
       ├─ HTTP 429/5xx → 指数退避重试
       │   delay = 2^attempt + jitter
       │
       └─ 网络错误 → 重试或返回 error
```

### 5.3 TokenBucket 速率限制

```python
@dataclass
class TokenBucket:
    capacity: float = 10.0          # 最大突发
    refill_per_sec: float = 5.0     # 每秒补充 5 个令牌
    _tokens: float = 10.0           # 当前令牌数

    async def acquire(self, tokens=1.0):
        while True:
            self._refill()                     # 按时间补充
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self.refill_per_sec
            await asyncio.sleep(wait)          # 等待直到有足够令牌
```

### 5.4 连接健康状态机

```
     连续成功
  ┌──────────────┐
  │   HEALTHY    │◄─────────── 恢复成功 ──────┐
  └──────┬───────┘                            │
         │ 连续失败 >= 2                       │
         ▼                                    │
  ┌──────────────┐    冷却期满(15s)   ┌───────┴──────┐
  │  DEGRADED    │─────────────────▶│ RECOVERING   │
  └──────────────┘                  └──────┬───────┘
                                          │ 探测失败
                                          └──▶ 回到 DEGRADED
```

### 5.5 SSE 事件类型

客户端将原始 SSE 流转换为统一事件格式:

| 事件 | 含义 | 示例载荷 |
|---|---|---|
| `thinking_start` | 开始思考块 | `{"index": 0}` |
| `thinking_delta` | 思考内容 | `{"index": 0, "content": "Let me analyze..."}` |
| `thinking_stop` | 思考结束 | `{"index": 0}` |
| `text_start` | 开始文本块 | `{"index": 1}` |
| `text_delta` | 文本内容 | `{"index": 1, "content": "Hello!"}` |
| `text_stop` | 文本结束 | `{"index": 1}` |
| `tool_call_start` | 工具调用开始 | `{"index": 2, "id": "call_1", "name": "read_file"}` |
| `tool_call_stop` | 工具调用结束 | `{"index": 2, "id": "call_1", "name": "read_file", "input": {...}}` |
| `finish` | 流结束 | `{"usage": {"total_tokens": 150}}` |
| `error` | 错误 | `{"message": "HTTP 429"}` |

---

## 6. 工具系统

### 6.1 工具定义

**文件**: `tools.py`

内置 6 个工具:

| 工具 | 功能 | 只读 |
|---|---|---|
| `read_file` | 读取文件 (带行号) | ✓ |
| `write_file` | 写入文件 | ✗ |
| `list_dir` | 列出目录 | ✓ |
| `exec_shell` | 执行 shell 命令 | ✗ |
| `grep_files` | 正则搜索文件 | ✓ |
| `web_search` | Web 搜索 (stub) | ✓ |

### 6.2 ToolRegistry — 工具注册表

```
ToolRegistry
├── _handlers: dict[str, callable]
│   ├── "read_file"  → _read_file()
│   ├── "write_file" → _write_file()
│   ├── "list_dir"   → _list_dir()
│   ├── "exec_shell" → _exec_shell()
│   ├── "grep_files" → _grep_files()
│   └── "web_search" → _web_search()
│
├── _aliases: dict[str, str]
│   ├── "readfile"     → "read_file"
│   ├── "read-file"    → "read_file"
│   ├── "read_file_tool" → "read_file"
│   └── ...
│
├── resolve(name) → str | None   ← 幻觉工具名解析
├── get_definitions() → list     ← API 工具定义
└── execute(name, args) → dict   ← 执行工具
```

### 6.3 幻觉工具名解析 (5 级)

```python
def resolve(self, name: str) -> Optional[str]:
    # 1. 精确匹配
    if name in self._handlers: return name

    # 2. 规范化匹配 (小写 + 连字符/空格 → 下划线)
    norm = name.lower().replace("-", "_").replace(" ", "_")
    if norm in self._aliases: return self._aliases[norm]

    # 3. CamelCase → snake_case (ReadFile → read_file)
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if snake in self._aliases: return self._aliases[snake]

    # 4. 去 _tool 后缀 (read_file_tool → read_file)
    if norm.endswith("_tool"):
        short = norm[:-5]
        if short in self._aliases: return self._aliases[short]

    return None  # 5. 无法解析
```

### 6.4 工具执行流程

```
tools.execute(name, args)
  │
  ├─ resolve(name) → canonical_name
  │   └─ None → return {success: false, "Tool not found"}
  │
  ├─ handler = self._handlers[canonical]
  │
  ├─ await handler(args)
  │   │
  │   ├─ _read_file:
  │   │   ├─ _resolve_path() — 安全检查路径
  │   │   ├─ path.read_text(utf-8)
  │   │   └─ 返回带行号的内容
  │   │
  │   ├─ _write_file:
  │   │   ├─ parent.mkdir(parents=True)
  │   │   └─ path.write_text(content)
  │   │
  │   ├─ _exec_shell:
  │   │   ├─ allow_shell 检查
  │   │   ├─ subprocess.run(cmd, shell=True, timeout=60)
  │   │   └─ 返回 stdout + stderr
  │   │
  │   └─ _grep_files:
  │       ├─ re.compile(pattern)
  │       ├─ rglob("*") 遍历文件
  │       └─ 返回匹配行 (最多 50 个)
  │
  └─ return {success: bool, content: str}
```

### 6.5 安全特性

- **路径沙箱**: 所有相对路径解析到 workspace 根目录
- **超时保护**: shell 命令 60 秒超时
- **结果截断**: grep 限制 100 个文件、50 条结果
- **Plan 模式阻止**: `exec_shell` + `write_file` 在只读模式被阻止

---

## 7. 对话引擎

### 7.1 ChatEngine 结构

**文件**: `engine.py`

```python
class ChatEngine:
    client: DeepSeekClient       # API 客户端
    tools: ToolRegistry           # 工具系统
    session: Session              # 会话状态
    mode: AppMode                 # Plan/Agent/YOLO
    max_tool_steps: int           # 最大工具步数 (默认 10)
    _approval_callback: callable  # 审批回调 (TUI 注入)
```

### 7.2 Turn Loop 完整流程

```
engine.chat(user_input)
  │
  ├─ ① session.messages.append({role: "user", content})
  │
  └─ while step < max_tool_steps:
       │
       ├─ ② 构建 API 请求
       │   messages = list(session.messages)
       │   system = SYSTEM_PROMPTS[mode]
       │   tools = self.tools.get_definitions()
       │
       ├─ ③ 流式调用 LLM
       │   async for event in client.chat_stream(messages, tools, system):
       │       ├─ thinking_* → yield (让 TUI 渲染)
       │       ├─ text_*     → yield + 累加 assistant_content
       │       └─ tool_call_* → 收集到 tool_calls_found[]
       │
       ├─ ④ tool_calls_found 为空?
       │   └─ YES → 保存 assistant msg → break (结束)
       │
       ├─ ⑤ 构建 assistant 消息 (含 tool_calls)
       │   session.messages.append({role: "assistant", tool_calls: [...]})
       │
       └─ ⑥ 执行每个工具:
            │
            ├─ Plan 模式阻止检查
            │   └─ BLOCKED_IN_PLAN → deny
            │
            ├─ 审批检查 (Agent 模式 + 写操作)
            │   ├─ _approval_callback(name, args)
            │   ├─ approved → 继续
            │   └─ denied → 错误结果
            │
            ├─ tools.execute(name, args)
            │   └─ yield {"type": "tool_call", ...}
            │
            └─ session.messages.append({role: "tool", content: result})
```

### 7.3 三种模式的系统提示

| 模式 | 系统提示核心 |
|---|---|
| Plan | "You CANNOT modify files or run shell commands. Describe your plan clearly." |
| Agent | "You have tools for reading/writing. Before changes, check existing code. Be concise." |
| YOLO | "All tool calls are auto-approved. Be careful with destructive operations." |

### 7.4 审批回调注入

TUI 通过回调函数注入审批逻辑:

```python
# tui.py
engine.set_approval_callback(self._ask_approval)

async def _ask_approval(self, tool_name, args):
    return Confirm.ask(
        f"⚠ Approve {tool_name}?\n  {args}\n  Approve?",
        default=False,
    )

# engine.py — 在工具执行前调用
if self._approval_callback:
    approved = await self._approval_callback(name, args)
    if not approved:
        result = {"success": False, "content": "User denied"}
```

### 7.5 事件生成器模式

引擎不持有 TUI 引用，而是通过 `async generator` 向 TUI 推送事件，实现双向解耦:

```
engine.chat()  →  yield events  →  tui._handle_event()
    (生成器)        (事件流)          (消费者)
```

---

## 8. 会话持久化

### 8.1 存储结构

**文件**: `session.py`

```
~/.deepseek-tui/sessions/
├── a1b2c3d4.json
├── e5f6g7h8.json
└── ...
```

### 8.2 JSON 格式

```json
{
  "id": "a1b2c3d4",
  "messages": [
    {"role": "user", "content": "read main.py"},
    {"role": "assistant", "content": null, "tool_calls": [
      {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"main.py\"}"}}
    ]},
    {"role": "tool", "tool_call_id": "call_1", "content": "1 | ..."}
  ],
  "system_prompt": null,
  "total_tokens": 0,
  "created_at": "2026-05-09T10:00:00",
  "updated_at": "2026-05-09T10:30:00"
}
```

### 8.3 操作流程

```
保存:
  SessionStore.save(session)
    ├─ session.touch() — 更新 updated_at
    ├─ json.dumps(session.to_dict())
    └─ 写入 ~/.deepseek-tui/sessions/<id>.json

恢复:
  SessionStore.load(id)
    ├─ 读取 <id>.json
    ├─ json.loads()
    └─ Session.from_dict(data)

列表:
  SessionStore.list_sessions(limit=20)
    ├─ glob("*.json") → 按 mtime 排序
    └─ 返回 [{id, msg_count, updated_at}, ...]
```

---

## 9. 终端界面

### 9.1 TerminalUI 结构

**文件**: `tui.py`

```
TerminalUI
├── engine: ChatEngine
├── console: rich.Console
├── mode: AppMode
│
├── run()           — 主入口
│   └─ asyncio.run(_repl())
│
├── _repl()         — REPL 循环
│   ├─ Prompt.ask("▸ ") 获取输入
│   ├─ /命令 → _handle_command()
│   └─ 对话 → engine.chat() → _handle_event()
│
├── _handle_command() — 命令分发
│   ├─ /plan, /agent, /yolo
│   ├─ /save, /resume, /list, /clear
│   └─ /quit, /help
│
├── _handle_event() — 事件渲染
│   ├─ thinking_delta → 灰色斜体
│   ├─ text_delta     → 直接输出
│   ├─ tool_call      → Panel (绿色/红色边框)
│   └─ turn_complete  → Token 统计
│
└── _ask_approval() — 审批弹窗
    └─ Confirm.ask("⚠ Approve {tool}?")
```

### 9.2 事件渲染细节

```
thinking_delta:
  "[dim italic]Let me analyze the code...[/dim italic]"
  (灰色斜体，流式追加)

text_delta:
  "The file contains..."
  (直接 print，支持 ANSI/Markdown)

tool_call (成功):
  ┌─ ✓ read_file ──────────────────────────┐
  │ 1 | """DeepSeek TUI...                  │
  │ 2 | import argparse                     │
  │ ...                                     │
  └─ path='main.py' ────────────────────────┘
  (绿色边框 Panel)

tool_call (失败):
  ┌─ ✗ exec_shell ─────────────────────────┐
  │ Tool 'exec_shell' blocked in Plan mode  │
  └─ command='rm -rf /' ────────────────────┘
  (红色边框 Panel)

turn_complete:
  "Tokens: 150 | Session: a1b2c3d4"
  (灰色统计行)
```

### 9.3 命令面板 (`/help`)

```
┌──────────────────────────────────────────┐
│ Commands:                                │
│   /plan       — Plan mode (read-only)    │
│   /agent      — Agent mode (with approval)│
│   /yolo       — YOLO mode (auto-approve) │
│   /save       — Save session             │
│   /resume ID  — Resume session           │
│   /list       — List saved sessions      │
│   /clear      — Clear current session    │
│   /quit       — Exit                     │
└──────────────────────────────────────────┘
```

---

## 10. 完整数据流

### 一次完整的 "读文件 + 解释" 交互

```
时间线                     终端显示
───────                    ────────

t=0  用户输入 ▸ read main.py and explain
                          You: read main.py and explain

t=1  engine 添加 user msg
     → client POST /v1/chat/completions

t=2  SSE: text_delta       Assistant:
                            ────────────
                            Let me start by reading the file.

t=3  SSE: tool_call_start  (内部处理)
t=4  SSE: tool_call_stop   ┌─ ✓ read_file ─────────────────┐
     → tools.execute()     │ 1 | """DeepSeek TUI...        │
                           │ 2 | import argparse           │
                           └─ path='main.py' ──────────────┘

t=5  engine 添加 tool result
     → client POST /v1/chat/completions (含工具结果)

t=6  SSE: text_delta       The file's structure is:
                            - Module docstring
                            - Standard library imports

t=7  SSE: [DONE]           Tokens: 320 | Session: a1b2c3d4
                            ──────────────────────────────

t=8  等待下一条输入          ▸
```

### 关键设计决策

| 决策 | 理由 |
|---|---|
| async generator 事件流 | 引擎不持有 TUI 引用，解耦清晰 |
| 审批回调注入 | TUI 控制交互方式，引擎只负责逻辑 |
| ToolRegistry.resolve() | 处理 LLM 幻觉，5 级 fallback |
| JSON 会话持久化 | 简单可靠，人工可读 |
| TokenBucket 客户端限速 | 避免 API 429 错误 |
| 滚动消息历史 (不截断) | 保持上下文完整性 |
| path.resolve() 沙箱 | 防止路径遍历攻击 |

### 与原版 Rust 实现的对比

| 特性 | Rust 版 | Python 版 |
|---|---|---|
| 子智能体 spawn | ✓ (14 个工具) | ✗ (简化) |
| RLM 递归语言模型 | ✓ | ✗ |
| LSP 诊断集成 | ✓ (5 语言) | ✗ |
| 分层上下文 Seam | ✓ | ✗ |
| Side-git 快照 | ✓ | ✗ |
| MCP 协议扩展 | ✓ | ✗ |
| 审批缓存 (approval_key) | ✓ | ✗ |
| 多行 TUI 布局 | ✓ (ratatui) | ✗ (单行 REPL) |
| **核心 turn loop** | ✓ | ✓ |
| **SSE 流式解析** | ✓ | ✓ |
| **TokenBucket 限速** | ✓ | ✓ |
| **工具幻觉解析** | ✓ | ✓ |
| **Plan/Agent/YOLO 模式** | ✓ | ✓ |
| **会话持久化** | ✓ | ✓ |
| **工具审批** | ✓ | ✓ |
