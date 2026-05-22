# DeepSeek TUI — Python 企业级实现

> 终端 AI 编程智能体 · 20 工具 · 子智能体 · 上下文管理 · Textual 多面板 UI

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 设置 API Key
# 方式 A: .env 文件
echo DEEPSEEK_API_KEY=sk-xxx > .env

# 方式 B: 环境变量
set DEEPSEEK_API_KEY=sk-xxx

# 3. 启动
python main.py                    # Textual 多面板 UI
python main.py --rich             # Rich 终端 UI
python main.py -p "解释这个项目"   # 单次对话
```

## 功能概览

| 模块 | 功能 |
|------|------|
| **AI Agent 引擎** | ReAct 循环 + Plan/Agent/YOLO 三模式 |
| **20 个工具** | 文件 I/O、Shell、Git、Web 搜索、规划、子智能体 |
| **子智能体** | 并行只读探索，asyncio.Task 池 |
| **上下文管理** | tiktoken 估算 + 三层压缩（保留/裁剪/摘要） |
| **LoopGuard** | 相同调用 3 次阻止，连续失败 8 次终止 |
| **持久化** | SQLite WAL + JSON Checkpoint 崩溃恢复 |
| **配置** | TOML + .env + 环境变量 + CLI 四级合并 |

## 架构

```
deepseek_tui/
├── main.py              # 入口
├── cli.py               # CLI 参数
├── config.py            # 配置加载
├── tui.py               # Rich TUI
├── ui/app.py            # Textual TUI
├── api/client.py        # SSE 流式 + TokenBucket
├── engine/engine.py     # ReAct + LoopGuard
├── tools/registry.py    # 20 工具 + 幻觉解析
├── db/session.py        # SQLite + Checkpoint
├── subagents/           # 子智能体管理
│   ├── manager.py       # Task 池 + 事件管道
│   └── runner.py        # 子智能体引擎
└── context/             # 上下文管理
    ├── token_counter.py # tiktoken 估算
    └── compact.py       # 三层压缩
```

## 工具列表

| 类别 | 工具 |
|------|------|
| 文件 | `read_file` `write_file` `edit_file` `apply_patch` `list_dir` |
| Shell | `exec_shell` |
| 搜索 | `grep_files` `file_search` `web_search` |
| Git | `git_status` `git_diff` `git_log` `git_show` |
| 规划 | `update_plan` `checklist_write` |
| 子智能体 | `agent_spawn` `agent_wait` `agent_result` `agent_cancel` `agent_list` |

## 运行模式

```
/plan    只读探索，阻止写文件和 Shell
/agent   写入需审批（默认）
/yolo    自动批准全部操作
```

## 快捷键 (Textual UI)

| 键 | 功能 |
|----|------|
| `Ctrl+P` | Plan 模式 |
| `Ctrl+A` | Agent 模式 |
| `Ctrl+Y` | YOLO 模式 |
| `Ctrl+Shift+Q` | 退出 |

## 配置

优先级: `CLI 参数 > 环境变量 > config.toml > 默认值`

```toml
# config.toml
[api]
key = ""              # 或 DEEPSEEK_API_KEY
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[limits]
max_tool_steps = 10

[context]
max_tokens = 1000000
compact_threshold = 0.8
```
