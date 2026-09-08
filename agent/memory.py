import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "memory.db"


class Memory:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS session_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,          -- 'user' | 'assistant' | 'tool'
                content TEXT,
                created_at TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS project_memory (
                project_id TEXT,        -- путь к проекту, разделяет память между проектами
                key TEXT,
                value TEXT,
                updated_at TEXT,
                PRIMARY KEY (project_id, key)
            )
        """)
        self.conn.commit()

    # --- короткая память (в рамках сессии) ---
    def log_turn(self, session_id: str, role: str, content: str):
        self.conn.execute(
            "INSERT INTO session_log (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_session_history(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM session_log WHERE session_id=? ORDER BY id",
            (session_id,)
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    # --- долгая память (между запусками) ---
    def set_fact(self, project_id: str, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO project_memory (project_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (project_id, key, value, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_fact(self, project_id: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM project_memory WHERE project_id=? AND key=?", (project_id, key)
        ).fetchone()
        return row[0] if row else None

    def get_all_facts(self, project_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT key, value FROM project_memory WHERE project_id=?", (project_id,)
        ).fetchall()
        return dict(rows)