"""工具系统 — 文件读写、shell 执行、搜索

工具定义格式:
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": { "type": "object", "properties": {...}, "required": [...] }
        }
    }
"""

import subprocess
import os
import re
import shlex
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
            "description": "Write content to a file.",
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
            "name": "list_dir",
            "description": "List directory contents.",
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
            "description": "Execute a shell command and return output.",
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
            "description": "Search for a pattern in files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "Directory or file"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via Baidu and return top results with titles and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
]

# Plan 模式下被阻止的工具
BLOCKED_IN_PLAN = {"exec_shell", "write_file"}


# ── 工具注册表 ────────────────────────────────────────────────

class ToolRegistry:
    """工具注册表，支持幻觉工具名解析"""

    def __init__(self, workspace: str, allow_shell: bool = True):
        self.workspace = Path(workspace).resolve()
        self.allow_shell = allow_shell
        self._handlers: dict[str, callable] = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "list_dir": self._list_dir,
            "exec_shell": self._exec_shell,
            "grep_files": self._grep_files,
            "web_search": self._web_search,
        }
        self._aliases: dict[str, str] = {}
        # 构建别名映射
        for name in list(self._handlers.keys()):
            self._aliases[name.lower().replace("-", "_").replace(" ", "_")] = name

    def resolve(self, name: str) -> Optional[str]:
        """解析工具名（处理 LLM 幻觉）"""
        # 1. 精确匹配
        if name in self._handlers:
            return name
        # 2. 规范化匹配
        norm = name.lower().replace("-", "_").replace(" ", "_")
        if norm in self._aliases:
            return self._aliases[norm]
        # 3. 驼峰转蛇形 (ReadFile → read_file)
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        if snake in self._aliases:
            return self._aliases[snake]
        # 4. 去 _tool 后缀
        if norm.endswith("_tool"):
            short = norm[:-5]
            if short in self._aliases:
                return self._aliases[short]
        return None

    def get_definitions(self) -> list[dict]:
        """返回工具定义列表"""
        tools = []
        for name in self._handlers:
            if name == "exec_shell" and not self.allow_shell:
                continue
            for td in TOOL_DEFINITIONS:
                if td["function"]["name"] == name:
                    tools.append(td)
                    break
        return tools

    async def execute(self, name: str, args: dict) -> dict:
        """执行工具调用，返回 {success, content}"""
        canonical = self.resolve(name)
        if canonical is None:
            return {"success": False, "content": f"Tool not found: {name}"}

        # 校验必填参数
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
        """检查必填参数是否缺失，返回错误信息或 None"""
        for td in TOOL_DEFINITIONS:
            if td["function"]["name"] == name:
                required = td["function"]["parameters"].get("required", [])
                missing = [p for p in required if p not in args]
                if missing:
                    params_list = ", ".join(required)
                    return (
                        f"Missing required parameter(s): {', '.join(missing)}. "
                        f"'{name}' requires: {params_list}"
                    )
                break
        return None

    # ── 工具实现 ─────────────────────────────────────────

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

    async def _list_dir(self, args: dict) -> dict:
        path = self._resolve_path(args.get("path", "."))
        if not path.is_dir():
            return {"success": False, "content": f"Not a directory: {path}"}
        entries = []
        for p in sorted(path.iterdir()):
            label = f"{'[D]' if p.is_dir() else '[F]'} {p.name}"
            entries.append(label)
        return {"success": True, "content": "\n".join(entries) if entries else "(empty)"}

    async def _exec_shell(self, args: dict) -> dict:
        if not self.allow_shell:
            return {"success": False, "content": "Shell execution disabled"}
        cmd = args["command"]
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            return {
                "success": result.returncode == 0,
                "content": output.strip() or f"(exit code {result.returncode})",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "content": "Command timed out (60s)"}

    async def _grep_files(self, args: dict) -> dict:
        pattern = args["pattern"]
        search_path = self._resolve_path(args.get("path", "."))
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {"success": False, "content": f"Invalid regex: {e}"}

        results = []
        paths = list(search_path.rglob("*")) if search_path.is_dir() else [search_path]
        for path in paths[:100]:  # 限制
            if not path.is_file():
                continue
            if path.suffix in {".pyc", ".exe", ".dll", ".png", ".jpg"}:
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

    async def _web_search(self, args: dict) -> dict:
        """通过百度搜索并解析结果"""
        query = args["query"]
        try:
            import httpx
            from urllib.parse import quote

            # 构造百度搜索 URL
            url = f"https://www.baidu.com/s?wd={quote(query)}&ie=utf-8"

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                html = response.text

            # 解析百度搜索结果
            results = self._parse_baidu_results(html)

            if not results:
                return {
                    "success": True,
                    "content": f"Search for '{query}' returned no results.",
                }

            lines = [f"🔍 百度搜索结果: {query}", ""]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['snippet']}")
                lines.append(f"   {r['url']}")
                lines.append("")

            return {"success": True, "content": "\n".join(lines).strip()}

        except httpx.HTTPStatusError as e:
            return {"success": False, "content": f"Search failed (HTTP {e.response.status_code})"}
        except httpx.TimeoutException:
            return {"success": False, "content": "Search timed out"}
        except Exception as e:
            return {"success": False, "content": f"Search error: {e}"}

    def _parse_baidu_results(self, html: str) -> list[dict]:
        """解析百度搜索结果页面 HTML"""
        results = []

        # 百度搜索结果在 <div class="result"> 或 <div class="c-container"> 中
        # 方法1: 匹配标题链接 <a class="c-title" 或 <h3 class="t"> 内的 <a>
        # 方法2: 匹配摘要 <span class="content-right_..."> 或 <div class="c-abstract">

        # 提取结果块: 按 <div class="result-op" 或 <div class="c-container" 分割
        blocks = re.split(r'<div\s+class="(?:result-op|c-container|result)[^"]*"', html)

        for block in blocks[1:]:  # 跳过第一个（分割前的内容）
            title = ""
            url = ""
            snippet = ""

            # 提取标题
            title_match = re.search(
                r'<a[^>]*?>(.*?)</a>',
                block
            )
            # 更精确: 找 h3 里的 a 标签
            h3_match = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if h3_match:
                url = h3_match.group(1)
                title = re.sub(r'<[^>]+>', '', h3_match.group(2)).strip()
            else:
                a_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if a_match:
                    url = a_match.group(1)
                    title = re.sub(r'<[^>]+>', '', a_match.group(2)).strip()

            # 提取摘要
            # 百度常用: <span class="content-right_..."> 或 <div class="c-abstract">
            abstract_match = re.search(
                r'<div\s+class="c-abstract[^"]*"[^>]*>(.*?)</div>',
                block, re.DOTALL
            )
            if not abstract_match:
                abstract_match = re.search(
                    r'<span\s+class="content-right_[^"]*"[^>]*>(.*?)</span>',
                    block, re.DOTALL
                )
            if not abstract_match:
                # fallback: 找任意包含文本的 div
                abstract_match = re.search(
                    r'<div\s+class="[^"]*abstract[^"]*"[^>]*>(.*?)</div>',
                    block, re.DOTALL
                )
            if abstract_match:
                snippet = re.sub(r'<[^>]+>', '', abstract_match.group(1)).strip()

            if title:
                # 百度 URL 可能是 /link?url=... 需要提取真实 URL
                if url.startswith("/"):
                    url = "https://www.baidu.com" + url
                results.append({
                    "title": title,
                    "url": url,
                    "snippet": snippet or "(no description)",
                })

            if len(results) >= 8:
                break

        return results

    def _resolve_path(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        return p.resolve()
