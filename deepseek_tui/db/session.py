"""会话持久化 — SQLite + 崩溃恢复 checkpoint

双轨存储:
  - SQLite: 消息级持久化 (sessions + messages 表)
  - JSON checkpoint: 崩溃恢复 (~/.deepseek-tui/checkpoints/latest.json)
"""

import json
import uuid
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Session:
    """会话状态"""

    def __init__(self, session_id: Optional[str] = None):
        self.id = session_id or str(uuid.uuid4())[:8]
        self.messages: list[dict] = []
        self.system_prompt: Optional[str] = None
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.created_at = datetime.now().isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "messages": self.messages,
            "system_prompt": self.system_prompt,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        s = cls(session_id=data["id"])
        s.messages = data.get("messages", [])
        s.system_prompt = data.get("system_prompt")
        s.total_input_tokens = data.get("total_input_tokens", 0)
        s.total_output_tokens = data.get("total_output_tokens", 0)
        s.created_at = data.get("created_at", "")
        s.updated_at = data.get("updated_at", "")
        return s

    def touch(self):
        self.updated_at = datetime.now().isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL DEFAULT 'deepseek-chat',
    workspace TEXT NOT NULL DEFAULT '.',
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    reasoning_content TEXT,
    token_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
"""


class SessionStore:
    """会话持久化管理 — SQLite 主存储 + JSON 崩溃恢复"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = Path.home() / ".deepseek-tui" / "deepseek.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir = self.db_path.parent / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.executescript(SCHEMA)
                conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to init database: %s", e)

    def save(self, session: Session) -> str:
        session.touch()
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(
                    """INSERT OR REPLACE INTO sessions 
                       (id, workspace, total_input_tokens, total_output_tokens, created_at, updated_at)
                       VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM sessions WHERE id=?), ?), ?)""",
                    (session.id, str(Path.cwd()), session.total_input_tokens, session.total_output_tokens,
                     session.id, session.created_at, now),
                )
                conn.execute("DELETE FROM messages WHERE session_id=?", (session.id,))
                for seq, msg in enumerate(session.messages):
                    conn.execute(
                        """INSERT INTO messages (session_id, seq, role, content, tool_calls, 
                           tool_call_id, name, reasoning_content, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (session.id, seq, msg.get("role", ""), msg.get("content"),
                         json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                         msg.get("tool_call_id"), msg.get("name"), msg.get("reasoning_content"), now),
                    )
                conn.commit()
        except sqlite3.Error as e:
            logger.error("Failed to save session %s: %s", session.id, e)
        self._clear_checkpoint()
        return session.id

    def load(self, session_id: str) -> Optional[Session]:
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
                if row is None:
                    return None
                session = Session(session_id=row["id"])
                session.total_input_tokens = row["total_input_tokens"] or 0
                session.total_output_tokens = row["total_output_tokens"] or 0
                session.created_at = row["created_at"]
                session.updated_at = row["updated_at"]
                msg_rows = conn.execute("SELECT * FROM messages WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
                for m in msg_rows:
                    msg = {"role": m["role"], "content": m["content"]}
                    if m["tool_calls"]:
                        msg["tool_calls"] = json.loads(m["tool_calls"])
                    if m["tool_call_id"]:
                        msg["tool_call_id"] = m["tool_call_id"]
                    if m["name"]:
                        msg["name"] = m["name"]
                    if m["reasoning_content"]:
                        msg["reasoning_content"] = m["reasoning_content"]
                    session.messages.append(msg)
                return session
        except sqlite3.Error as e:
            logger.error("Failed to load session %s: %s", session_id, e)
        return self._load_checkpoint()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT s.id, s.updated_at,
                              (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) as msg_count
                       FROM sessions s ORDER BY s.updated_at DESC LIMIT ?""", (limit,)
                ).fetchall()
                return [{"id": r["id"], "msg_count": r["msg_count"], "updated_at": r["updated_at"]} for r in rows]
        except sqlite3.Error:
            return []

    def delete(self, session_id: str) -> bool:
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("Failed to delete session %s: %s", session_id, e)
            return False

    def save_checkpoint(self, session: Session) -> None:
        checkpoint_path = self.checkpoint_dir / "latest.json"
        try:
            data = {"session": session.to_dict(), "timestamp": datetime.now().isoformat()}
            checkpoint_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to save checkpoint: %s", e)

    def _load_checkpoint(self) -> Optional[Session]:
        checkpoint_path = self.checkpoint_dir / "latest.json"
        if not checkpoint_path.exists():
            return None
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            return Session.from_dict(data["session"])
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Failed to load checkpoint: %s", e)
            return None

    def _clear_checkpoint(self) -> None:
        (self.checkpoint_dir / "latest.json").unlink(missing_ok=True)

    def has_checkpoint(self) -> bool:
        return (self.checkpoint_dir / "latest.json").exists()
