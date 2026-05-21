"""集成测试 — 无需 API Key 的本地验证"""

import asyncio
from pathlib import Path

# 测试工具系统
print("=== 测试工具系统 ===")
from tools import ToolRegistry

tools = ToolRegistry(workspace=str(Path(__file__).parent), allow_shell=True)

# 1. 工具定义
defs = tools.get_definitions()
print(f"工具定义: {len(defs)} 个")
for d in defs:
    print(f"  - {d['function']['name']}")

# 2. 幻觉解析
print("\n幻觉解析:")
for name in ["read_file", "ReadFile", "read-file", "READ FILE", "read_file_tool"]:
    resolved = tools.resolve(name)
    print(f"  '{name}' → {resolved}")

# 3. 文件读写
print("\n文件读写:")
result = asyncio.run(tools.execute("read_file", {"path": "main.py"}))
print(f"  read main.py: success={result['success']}, lines={len(result['content'].splitlines())}")

result = asyncio.run(tools.execute("list_dir", {"path": "."}))
print(f"  list_dir: success={result['success']}, entries={len(result['content'].splitlines())}")

# 4. 会话持久化
print("\n=== 测试会话持久化 ===")
from session import Session, SessionStore

store = SessionStore(storage_dir=str(Path(__file__).parent / "test_sessions"))
s = Session()
s.messages.append({"role": "user", "content": "hello"})
s.messages.append({"role": "assistant", "content": "hi!"})
store.save(s)

s2 = store.load(s.id)
assert s2 is not None
assert len(s2.messages) == 2
print(f"  保存/恢复: OK (id={s.id}, messages={len(s2.messages)})")

# 清理测试数据
for p in Path("test_sessions").glob("*.json"):
    p.unlink()
Path("test_sessions").rmdir()

print("\n=== 全部测试通过 ===")
