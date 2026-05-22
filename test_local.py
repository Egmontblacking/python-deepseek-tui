"""集成测试 — 无需 API Key 的本地验证 (v0.2)"""

import sys
import asyncio
from pathlib import Path

# 确保 src 在 path 中
sys.path.insert(0, str(Path(__file__).parent))

# ═══════════════════════════════════════════════════════════════
# 1. 工具系统
# ═══════════════════════════════════════════════════════════════

print("=== 测试工具系统 ===")
from deepseek_tui.tools.registry import ToolRegistry

tools = ToolRegistry(workspace=str(Path(__file__).parent), allow_shell=True)

# 工具定义
defs = tools.get_definitions()
print(f"工具定义: {len(defs)} 个")
for d in defs:
    print(f"  - {d['function']['name']}")

# 幻觉解析
print("\n幻觉名称解析:")
for name in ["read_file", "ReadFile", "read-file", "READ FILE", "read_file_tool", "checklist_write", "ChecklistWrite"]:
    resolved = tools.resolve(name)
    print(f"  '{name}' → {resolved}")

# 文件读写
print("\n文件操作:")
result = asyncio.run(tools.execute("read_file", {"path": "README.md"}))
print(f"  read_file README.md: ok={result['success']}, len={len(result['content'])}")

result = asyncio.run(tools.execute("list_dir", {"path": "."}))
print(f"  list_dir .: ok={result['success']}")

# Plan/Checklist 工具
print("\n规划工具:")
result = asyncio.run(tools.execute("update_plan", {"plan": [
    {"step": "Phase 1: Setup", "status": "completed"},
    {"step": "Phase 2: Implement", "status": "in_progress"},
]}))
print(f"  update_plan: {result['content']}")

result = asyncio.run(tools.execute("checklist_write", {"todos": [
    {"content": "Create config.py", "status": "completed"},
    {"content": "Migrate tools", "status": "in_progress"},
]}))
print(f"  checklist_write: {result['content']}")

# Git 工具
print("\nGit 工具:")
result = asyncio.run(tools.execute("git_status", {}))
if result.get("success"):
    lines = result["content"].splitlines()
    print(f"  git_status: {len(lines)} changed files")
else:
    print(f"  git_status: {result['content'][:60]}")

# edit_file (写一个测试文件)
print("\n编辑文件:")
test_file = Path(__file__).parent / "_test_edit.txt"
test_file.write_text("line1\nline2\nline3\n")
result = asyncio.run(tools.execute("edit_file", {
    "path": "_test_edit.txt",
    "old_string": "line2",
    "new_string": "LINE_TWO",
}))
print(f"  edit_file: ok={result['success']}")
content = test_file.read_text().strip()
print(f"  content: {content}")
assert "LINE_TWO" in content, f"Edit failed! Content: {content}"
test_file.unlink()

# ═══════════════════════════════════════════════════════════════
# 2. 会话持久化 (SQLite)
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试会话持久化 (SQLite) ===")
from deepseek_tui.db.session import Session, SessionStore

# 使用测试数据库
test_db = Path(__file__).parent / "_test_sessions.db"
store = SessionStore(db_path=str(test_db))

# 保存
s = Session()
s.messages.append({"role": "user", "content": "hello"})
s.messages.append({"role": "assistant", "content": "hi!", "reasoning_content": "Let me think..."})
s.total_input_tokens = 100
store.save(s)

# 恢复
s2 = store.load(s.id)
assert s2 is not None, "Failed to load session"
assert len(s2.messages) == 2, f"Expected 2 messages, got {len(s2.messages)}"
assert s2.messages[1].get("reasoning_content") == "Let me think..."
print(f"  SQLite 保存/恢复: OK (id={s.id}, messages={len(s2.messages)}, reasoning preserved)")

# 列表
sessions = store.list_sessions()
print(f"  SQLite 列表查询: OK ({len(sessions)} sessions)")

# 删除
store.delete(s.id)
print(f"  SQLite 删除: OK")

# 清理
test_db.unlink(missing_ok=True)

# ═══════════════════════════════════════════════════════════════
# 3. 配置系统
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试配置系统 ===")
from deepseek_tui.config import Config, load_toml_config

c = Config(api_key="test-key")
assert c.api_key == "test-key"
assert c.base_url == "https://api.deepseek.com"
assert c.db_path.name == "deepseek.db"
print(f"  Config 默认值: OK")
print(f"  db_path: {c.db_path}")
print(f"  sessions_dir: {c.sessions_dir}")

# ═══════════════════════════════════════════════════════════════
# 4. CLI
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试 CLI ===")
from deepseek_tui.cli import build_argparser

parser = build_argparser()
args = parser.parse_args(["-p", "test", "-w", "D:/test", "--mode", "plan"])
assert args.prompt == "test"
assert args.workspace == "D:/test"
assert args.mode == "plan"
print(f"  CLI 解析: OK (prompt={args.prompt}, workspace={args.workspace}, mode={args.mode})")

# ═══════════════════════════════════════════════════════════════
# 5. LoopGuard
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试 LoopGuard ===")
from deepseek_tui.engine.engine import LoopGuard

lg = LoopGuard()
# 相同调用 3 次
assert not lg.should_block("read_file", {"path": "test.txt"})
assert not lg.should_block("read_file", {"path": "test.txt"})
assert lg.should_block("read_file", {"path": "test.txt"})
print(f"  LoopGuard 重复调用阻止: OK")

# 不同参数不阻止
lg.reset()
assert not lg.should_block("read_file", {"path": "a.txt"})
assert not lg.should_block("read_file", {"path": "b.txt"})
assert not lg.should_block("read_file", {"path": "c.txt"})
print(f"  LoopGuard 不同参数: OK")

# 失败计数 → 成功重置
lg.reset()
lg.record_outcome("exec_shell", False)
lg.record_outcome("exec_shell", False)
lg.record_outcome("exec_shell", True)  # 成功重置
lg.record_outcome("exec_shell", False)
assert lg.record_outcome("exec_shell", False) is None  # 只失败 2 次
print(f"  LoopGuard 成功重置: OK")

# ═══════════════════════════════════════════════════════════════
# 6. Token 计数器
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试 Token 计数器 ===")
from deepseek_tui.context.token_counter import estimate_tokens, estimate_message_tokens, estimate_session_tokens, format_tokens

assert estimate_tokens("hello world") > 0
assert estimate_tokens("") == 0
msg = {"role": "user", "content": "hello world"}
assert estimate_message_tokens(msg) > 0
tokens = estimate_session_tokens([msg])
assert tokens > 100  # 至少包含 overhead
assert "K" in format_tokens(5000) or format_tokens(5000) == "5K"
print(f"  Token 估算: OK (hello world ≈ {estimate_tokens('hello world')} tokens)")
print(f"  消息 token: {estimate_message_tokens(msg)}")
print(f"  格式化: {format_tokens(5000)}, {format_tokens(1500000)}")

# ═══════════════════════════════════════════════════════════════
# 7. 上下文压缩器
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试上下文压缩器 ===")
from deepseek_tui.context.compact import Compactor

compactor = Compactor(keep_recent=2, min_summarize=3, token_budget=1_000_000)

# should_compact: 太少消息 → False
few = [{"role": "user", "content": "hi"}] * 5
assert not compactor.should_compact(few, 10_000)
print(f"  should_compact (few): OK (False)")

# should_compact: 超过阈值 → True
many = [{"role": "user", "content": "x" * 1000}] * 20
# 粗略估算达到阈值
assert compactor.should_compact(many, 900_000)
print(f"  should_compact (threshold): OK (True)")

# compact: 消息少 → 不摘要
result = compactor.compact(few)
assert result.success
assert len(result.messages) <= len(few)
print(f"  compact (few): OK ({len(few)} → {len(result.messages)})")

# compact: 无 summary_callback → 裁剪但无摘要
result2 = compactor.compact(many)
assert result2.success
print(f"  compact (many, no summary): OK ({len(many)} → {len(result2.messages)})")

# ═══════════════════════════════════════════════════════════════
# 8. 子智能体管理器
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试子智能体管理器 ===")
from deepseek_tui.subagents.manager import SubagentManager, SubagentCompletion

mgr = SubagentManager(max_concurrent=2)
assert mgr.running_count == 0
assert mgr.list_running() == []
print(f"  SubagentManager 初始化: OK (running={mgr.running_count})")

# 完成事件
completion = SubagentCompletion(agent_id="test-1", status="completed", summary="Done")
mgr._results["test-1"] = completion
result = mgr.get_result("test-1")
assert result is not None
assert result.status == "completed"
print(f"  get_result: OK ({result.agent_id}: {result.status})")

# inject_completions
mgr._completions.put_nowait(completion)
msgs = mgr.inject_completions()
assert len(msgs) == 1
assert "subagent.done" in msgs[0]["content"]
print(f"  inject_completions: OK ({len(msgs)} msg)")

# ═══════════════════════════════════════════════════════════════
# 9. 子智能体工具定义
# ═══════════════════════════════════════════════════════════════

print("\n=== 测试子智能体工具定义 ===")
agent_tools = ["agent_spawn", "agent_wait", "agent_result", "agent_cancel", "agent_list"]
for name in agent_tools:
    resolved = tools.resolve(name)
    assert resolved == name, f"Expected {name}, got {resolved}"
print(f"  子智能体工具: OK ({', '.join(agent_tools)})")

# 总数检查
all_defs = tools.get_definitions()
total = len(all_defs)
assert total >= 20, f"Expected >=20 tools, got {total}"
print(f"  工具总数: {total}")

print("\n=== 全部 9 项测试通过 ===")
