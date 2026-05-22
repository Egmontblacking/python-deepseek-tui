"""工具系统 — 插件化 ToolRegistry、幻觉名称解析、模式过滤"""

import subprocess
import re
from pathlib import Path
from typing import Any, Optional

# ── 工具定义 ──────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace. Returns file content with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a UTF-8 file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a single block of text in a file. Provide old_string and new_string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_string": {"type": "string", "description": "Text to replace"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a unified diff patch to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "patch": {"type": "string", "description": "Unified diff content"},
                },
                "required": ["path", "patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries in a directory relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: .)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "Execute a shell command and return stdout + stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "Search for a regex pattern in workspace files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Directory or file (default: .)"},
                    "include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns for files to include",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "Search for files by name using fuzzy matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "File name or path fragment"},
                    "path": {"type": "string", "description": "Base path to search (default: .)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via Baidu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show the working tree status (git status --porcelain).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show changes (git diff).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to diff (optional)"},
                    "staged": {"type": "boolean", "description": "Show staged changes"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show commit history (git log --oneline).",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "description": "Max commits to show (default: 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_show",
            "description": "Show a commit's content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "commit": {"type": "string", "description": "Commit hash or ref"},
                },
                "required": ["commit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Update the high-level implementation plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["step", "status"],
                        },
                    },
                    "explanation": {"type": "string", "description": "Optional high-level explanation"},
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checklist_write",
            "description": "Replace the active checklist with new todo items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_spawn",
            "description": "Spawn a read-only sub-agent to explore files or analyze code in parallel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Task description for the sub-agent"},
                    "type": {"type": "string", "description": "Agent type: explore (default)", "enum": ["explore"]},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_wait",
            "description": "Wait for sub-agents to complete. Use wait_mode='any' to get the first result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific agent IDs to wait for (omit for all)",
                    },
                    "wait_mode": {"type": "string", "enum": ["any", "all"], "description": "any: return first, all: wait for all"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_result",
            "description": "Get the result of a completed sub-agent by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID returned by agent_spawn"},
                },
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_cancel",
            "description": "Cancel a running sub-agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent ID to cancel"},
                },
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_list",
            "description": "List all currently running sub-agents.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

BLOCKED_IN_PLAN = {"exec_shell", "write_file", "edit_file", "apply_patch"}
NEED_APPROVAL = {"exec_shell", "write_file", "edit_file", "apply_patch"}


class ToolRegistry:
    """工具注册表，支持幻觉工具名解析、模式过滤、参数校验"""

    def __init__(self, workspace: str, allow_shell: bool = True):
        self.workspace = Path(workspace).resolve()
        self.allow_shell = allow_shell
        self._handlers: dict[str, callable] = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "apply_patch": self._apply_patch,
            "list_dir": self._list_dir,
            "exec_shell": self._exec_shell,
            "grep_files": self._grep_files,
            "file_search": self._file_search,
            "web_search": self._web_search,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "git_log": self._git_log,
            "git_show": self._git_show,
            "update_plan": self._update_plan,
            "checklist_write": self._checklist_write,
            "agent_spawn": self._agent_spawn,
            "agent_wait": self._agent_wait,
            "agent_result": self._agent_result,
            "agent_cancel": self._agent_cancel,
            "agent_list": self._agent_list,
        }
        self._subagent_manager = None  # 由 ChatEngine 注入
        self._aliases: dict[str, str] = {}
        for name in list(self._handlers.keys()):
            self._aliases[name.lower().replace("-", "_").replace(" ", "_")] = name
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            self._aliases[snake] = name
        self._plan_state: dict = {"explanation": None, "steps": []}
        self._checklist_state: dict = {"items": [], "next_id": 1}

    def resolve(self, name: str) -> Optional[str]:
        if name in self._handlers:
            return name
        norm = name.lower().replace("-", "_").replace(" ", "_")
        if norm in self._aliases:
            return self._aliases[norm]
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        if snake in self._aliases:
            return self._aliases[snake]
        if norm.endswith("_tool") and norm[:-5] in self._aliases:
            return self._aliases[norm[:-5]]
        return None

    def get_definitions(self) -> list[dict]:
        tools = []
        for name in sorted(self._handlers.keys()):
            if name == "exec_shell" and not self.allow_shell:
                continue
            for td in TOOL_DEFINITIONS:
                if td["function"]["name"] == name:
                    tools.append(td)
                    break
        return tools

    async def execute(self, name: str, args: dict) -> dict:
        canonical = self.resolve(name)
        if canonical is None:
            available = ", ".join(sorted(self._handlers.keys()))
            return {"success": False, "content": f"Tool not found: '{name}'. Available: {available}"}
        validation_error = self._validate_args(canonical, args)
        if validation_error:
            return {"success": False, "content": validation_error}
        handler = self._handlers[canonical]
        try:
            result = await handler(args)
            return result
        except Exception as e:
            return {"success": False, "content": f"Error: {e}"}

    def _validate_args(self, name: str, args: dict) -> Optional[str]:
        for td in TOOL_DEFINITIONS:
            if td["function"]["name"] == name:
                required = td["function"]["parameters"].get("required", [])
                missing = [p for p in required if p not in args]
                if missing:
                    params_list = ", ".join(required)
                    return f"Missing required parameter(s): {', '.join(missing)}. '{name}' requires: {params_list}"
                break
        return None

    # ── 文件工具 ─────────────────────────────────────────
    async def _read_file(self, args: dict) -> dict:
        path = self._resolve_path(args["path"])
        if not path.exists():
            return {"success": False, "content": f"File not found: {args['path']}"}
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4} | {line}" for i, line in enumerate(lines))
        return {"success": True, "content": f"{path}\n{numbered}"}

    async def _write_file(self, args: dict) -> dict:
        path = self._resolve_path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        return {"success": True, "content": f"Written: {path} ({len(args['content'])} bytes)"}

    async def _edit_file(self, args: dict) -> dict:
        path = self._resolve_path(args["path"])
        if not path.exists():
            return {"success": False, "content": f"File not found: {args['path']}"}
        old = args["old_string"]
        new = args.get("new_string", "")
        content = path.read_text(encoding="utf-8")
        if old not in content:
            return {"success": False, "content": "old_string not found in file"}
        if content.count(old) > 1:
            return {"success": False, "content": "old_string is not unique — found multiple matches"}
        content = content.replace(old, new, 1)
        path.write_text(content, encoding="utf-8")
        return {"success": True, "content": f"Edited: {path}"}

    async def _apply_patch(self, args: dict) -> dict:
        path = self._resolve_path(args["path"])
        if not path.exists():
            return {"success": False, "content": f"File not found: {args['path']}"}
        patch = args["patch"]
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False, encoding="utf-8") as f:
            f.write(patch)
            patch_path = f.name
        try:
            result = subprocess.run(["patch", "-u", str(path), patch_path], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return {"success": True, "content": f"Patch applied: {path}"}
            else:
                return {"success": False, "content": result.stderr or "patch failed"}
        finally:
            Path(patch_path).unlink(missing_ok=True)

    async def _list_dir(self, args: dict) -> dict:
        path = self._resolve_path(args.get("path", "."))
        if not path.is_dir():
            return {"success": False, "content": f"Not a directory: {path}"}
        entries = [f"{'[D]' if p.is_dir() else '[F]'} {p.name}" for p in sorted(path.iterdir())]
        return {"success": True, "content": "\n".join(entries) if entries else "(empty)"}

    # ── Shell ────────────────────────────────────────────
    async def _exec_shell(self, args: dict) -> dict:
        if not self.allow_shell:
            return {"success": False, "content": "Shell execution disabled"}
        cmd = args["command"]
        try:
            result = subprocess.run(cmd, shell=True, cwd=str(self.workspace), capture_output=True, text=True, timeout=60)
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            return {"success": result.returncode == 0, "content": output.strip() or f"(exit code {result.returncode})"}
        except subprocess.TimeoutExpired:
            return {"success": False, "content": "Command timed out (60s)"}

    # ── 搜索 ────────────────────────────────────────────
    async def _grep_files(self, args: dict) -> dict:
        pattern = args["pattern"]
        search_path = self._resolve_path(args.get("path", "."))
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"success": False, "content": f"Invalid regex: {e}"}
        results = []
        paths = list(search_path.rglob("*")) if search_path.is_dir() else [search_path]
        for path in paths[:100]:
            if not path.is_file() or path.suffix in {".pyc", ".exe", ".dll", ".png", ".jpg"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{path}:{i}: {line.strip()[:120]}")
                        if len(results) > 50:
                            break
            except Exception:
                pass
        return {"success": True, "content": "\n".join(results) if results else "No matches"}

    async def _file_search(self, args: dict) -> dict:
        query = args["query"].lower()
        search_path = self._resolve_path(args.get("path", "."))
        matches = [str(p.relative_to(self.workspace)) for p in search_path.rglob("*") if query in p.name.lower()][:50]
        return {"success": True, "content": "\n".join(matches) if matches else "No files found"}

    # ── Web ──────────────────────────────────────────────
    async def _web_search(self, args: dict) -> dict:
        query = args["query"]
        try:
            import httpx
            from urllib.parse import quote
            url = f"https://www.baidu.com/s?wd={quote(query)}&ie=utf-8"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                html = response.text
            results = self._parse_baidu(html)
            if not results:
                return {"success": True, "content": f"No results for '{query}'"}
            lines = [f"Search: {query}", ""]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   {r['url']}\n")
            return {"success": True, "content": "\n".join(lines)}
        except Exception as e:
            return {"success": False, "content": f"Search error: {e}"}

    def _parse_baidu(self, html: str) -> list[dict]:
        results = []
        blocks = re.split(r'<div\s+class="(?:result-op|c-container|result)[^"]*"', html)
        for block in blocks[1:]:
            h3 = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
            title, url = "", ""
            if h3:
                url, title = h3.group(1), re.sub(r'<[^>]+>', '', h3.group(2)).strip()
            else:
                a = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if a:
                    url, title = a.group(1), re.sub(r'<[^>]+>', '', a.group(2)).strip()
            abstract = re.search(r'<div\s+class="c-abstract[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
            snippet = re.sub(r'<[^>]+>', '', abstract.group(1)).strip() if abstract else ""
            if title:
                if url.startswith("/"):
                    url = "https://www.baidu.com" + url
                results.append({"title": title, "url": url, "snippet": snippet or "(no description)"})
            if len(results) >= 8:
                break
        return results

    # ── Git ──────────────────────────────────────────────
    async def _git_status(self, args: dict) -> dict:
        return await self._run_git(["status", "--porcelain"])

    async def _git_diff(self, args: dict) -> dict:
        cmd = ["diff"]
        if args.get("staged"):
            cmd.append("--staged")
        if args.get("path"):
            cmd.append(args["path"])
        return await self._run_git(cmd)

    async def _git_log(self, args: dict) -> dict:
        return await self._run_git(["log", "--oneline", "-n", str(args.get("max_count", 10))])

    async def _git_show(self, args: dict) -> dict:
        return await self._run_git(["show", args["commit"]])

    async def _run_git(self, args: list[str]) -> dict:
        try:
            result = subprocess.run(["git"] + args, cwd=str(self.workspace), capture_output=True, text=True, timeout=30)
            output = result.stdout.strip()
            if result.returncode != 0:
                output += f"\nError: {result.stderr.strip()}"
            return {"success": result.returncode == 0, "content": output or "(empty)"}
        except FileNotFoundError:
            return {"success": False, "content": "git not found"}
        except subprocess.TimeoutExpired:
            return {"success": False, "content": "git command timed out"}

    # ── 规划 ─────────────────────────────────────────────
    async def _update_plan(self, args: dict) -> dict:
        plan_steps = args.get("plan", [])
        explanation = args.get("explanation")
        self._plan_state = {"explanation": explanation, "steps": [{"step": s["step"], "status": s["status"]} for s in plan_steps]}
        statuses = [s["status"] for s in plan_steps]
        done, in_prog, pend = statuses.count("completed"), statuses.count("in_progress"), statuses.count("pending")
        return {"success": True, "content": f"Plan updated: {len(plan_steps)} steps ({done} done, {in_prog} in_progress, {pend} pending)"}

    async def _checklist_write(self, args: dict) -> dict:
        todos = args.get("todos", [])
        items = [{"id": i + 1, "content": t["content"], "status": t["status"]} for i, t in enumerate(todos)]
        self._checklist_state = {"items": items, "next_id": len(items) + 1}
        done = sum(1 for i in items if i["status"] == "completed")
        return {"success": True, "content": f"Checklist: {len(items)} items ({done} done)"}

    # ── 子智能体 ─────────────────────────────────────────

    def set_subagent_manager(self, mgr):
        """注入 SubagentManager 实例"""
        self._subagent_manager = mgr

    async def _agent_spawn(self, args: dict) -> dict:
        if self._subagent_manager is None:
            return {"success": False, "content": "Subagent manager not available"}
        from deepseek_tui.subagents.runner import SubagentRunner
        from deepseek_tui.api.client import DeepSeekClient

        # 复用当前 API 客户端的凭证和模型
        prompt = args["prompt"]
        # 工厂函数捕获 workspace
        ws = str(self.workspace)
        runner_factory = lambda: SubagentRunner(
            api_key=self._subagent_manager._engine_api_key,
            base_url=self._subagent_manager._engine_base_url,
            model=self._subagent_manager._engine_model,
            workspace=ws,
            max_steps=5,
        )
        agent_id = await self._subagent_manager.spawn(runner_factory, agent_name="explore-")
        return {"success": True, "content": f"Spawned sub-agent: {agent_id}"}

    async def _agent_wait(self, args: dict) -> dict:
        if self._subagent_manager is None:
            return {"success": False, "content": "Subagent manager not available"}
        wait_mode = args.get("wait_mode", "any")
        if wait_mode == "all":
            completions = await self._subagent_manager.wait_all(timeout=120.0)
            lines = [f"{len(completions)} sub-agent(s) completed:"]
            for c in completions:
                lines.append(f"  {c.agent_id}: {c.status} — {c.summary[:200]}")
            return {"success": True, "content": "\n".join(lines)}
        else:
            completion = await self._subagent_manager.wait_any(timeout=60.0)
            if completion:
                return {"success": True, "content": f"{completion.agent_id}: {completion.status}\n{completion.summary}"}
            return {"success": True, "content": "No sub-agents running"}

    async def _agent_result(self, args: dict) -> dict:
        if self._subagent_manager is None:
            return {"success": False, "content": "Subagent manager not available"}
        result = self._subagent_manager.get_result(args["agent_id"])
        if result:
            return {"success": True, "content": f"{result.agent_id}: {result.status}\n{result.summary}"}
        return {"success": False, "content": f"Agent {args['agent_id']} not found"}

    async def _agent_cancel(self, args: dict) -> dict:
        if self._subagent_manager is None:
            return {"success": False, "content": "Subagent manager not available"}
        ok = self._subagent_manager.cancel(args["agent_id"])
        return {"success": ok, "content": f"Cancelled: {args['agent_id']}" if ok else f"Agent {args['agent_id']} not running"}

    async def _agent_list(self, args: dict) -> dict:
        if self._subagent_manager is None:
            return {"success": False, "content": "Subagent manager not available"}
        running = self._subagent_manager.list_running()
        if running:
            return {"success": True, "content": f"{len(running)} running: " + ", ".join(running)}
        return {"success": True, "content": "No sub-agents running"}

    # ── 路径 ─────────────────────────────────────────────
    def _resolve_path(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        return p.resolve()