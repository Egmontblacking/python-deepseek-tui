"""配置系统 — TOML + .env + 环境变量 + CLI 四级合并

优先级: CLI 参数 > 环境变量 > config.toml > 默认值
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class Config:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    workspace: str = ""
    allow_shell: bool = True
    max_tool_steps: int = 10
    shell_timeout_secs: int = 60
    theme: str = "dark"
    mode: str = "agent"
    _home: Path = field(default_factory=lambda: Path.home() / ".deepseek-tui")

    @property
    def sessions_dir(self) -> Path:
        return self._home / "sessions"

    @property
    def db_path(self) -> Path:
        return self._home / "deepseek.db"

    @property
    def checkpoints_dir(self) -> Path:
        return self._home / "checkpoints"


def _find_config_file() -> Optional[Path]:
    for p in [Path.cwd() / "config.toml", Path.home() / ".deepseek-tui" / "config.toml"]:
        if p.exists():
            return p
    return None


def load_toml_config() -> dict:
    path = _find_config_file()
    if path is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as e:
        print(f"Warning: Failed to parse {path}: {e}", file=sys.stderr)
        return {}


def build_config(cli_args=None) -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    toml = load_toml_config()
    Path.home().joinpath(".deepseek-tui").mkdir(parents=True, exist_ok=True)

    def _v(section, key, env_key, default):
        env_val = os.getenv(env_key, "")
        return env_val if env_val else toml.get(section, {}).get(key, default)

    config = Config(
        api_key=_v("api", "key", "DEEPSEEK_API_KEY", ""),
        base_url=_v("api", "base_url", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=_v("api", "model", "DEEPSEEK_MODEL", "deepseek-chat"),
        workspace=os.getenv("DEEPSEEK_WORKSPACE", str(Path.cwd())),
        allow_shell=os.getenv("DEEPSEEK_DISABLE_SHELL", "").lower() != "true",
        max_tool_steps=int(toml.get("limits", {}).get("max_tool_steps", 10)),
        shell_timeout_secs=int(toml.get("limits", {}).get("shell_timeout_secs", 60)),
        theme=toml.get("ui", {}).get("theme", "dark"),
    )

    if cli_args:
        if cli_args.workspace:
            config.workspace = cli_args.workspace
        if cli_args.model:
            config.model = cli_args.model
        if getattr(cli_args, "no_shell", False):
            config.allow_shell = False
        if hasattr(cli_args, "mode"):
            config.mode = cli_args.mode

    return config
