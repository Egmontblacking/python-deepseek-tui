"""会话持久化 — JSON 保存/恢复"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class Session:
    """会话状态"""

    def __init__(self, session_id: Optional[str] = None):
        self.id = session_id or str(uuid.uuid4())[:8]
        self.messages: list[dict] = []
        self.system_prompt: Optional[str] = None
        self.total_tokens = 0
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": self.messages,
            "system_prompt": self.system_prompt,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        s = cls(session_id=data["id"])
        s.messages = data.get("messages", [])
        s.system_prompt = data.get("system_prompt")
        s.total_tokens = data.get("total_tokens", 0)
        s.created_at = data.get("created_at", "")
        s.updated_at = data.get("updated_at", "")
        return s

    def touch(self):
        self.updated_at = datetime.now().isoformat()


class SessionStore:
    """会话持久化管理"""

    def __init__(self, storage_dir: Optional[str] = None):
        self.dir = Path(storage_dir) if storage_dir else Path.home() / ".deepseek-tui" / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: Session):
        session.touch()
        path = self.dir / f"{session.id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self, session_id: str) -> Optional[Session]:
        path = self.dir / f"{session_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        sessions = []
        for p in sorted(self.dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data["id"],
                    "msg_count": len(data.get("messages", [])),
                    "updated_at": data.get("updated_at", ""),
                })
            except Exception:
                pass
            if len(sessions) >= limit:
                break
        return sessions
