"""数据存储（软件文档第五章）。demo 阶段用 SQLite，一个文件，挂在容器卷上。

每张表都有 region 字段（执行文档 1.1 第 2 条：用户数据按区域可分离）。
"""

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS family (
    id TEXT PRIMARY KEY,
    name TEXT,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS child (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES family(id),
    name TEXT NOT NULL,
    age INTEGER NOT NULL,
    theme_id TEXT,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS device (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES family(id),
    child_id TEXT NOT NULL REFERENCES child(id),
    region TEXT NOT NULL,
    bound_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    child_id TEXT NOT NULL REFERENCES child(id),
    started_at REAL NOT NULL,
    last_at REAL NOT NULL,
    extracted INTEGER NOT NULL DEFAULT 0,
    region TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES session(id),
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    words TEXT NOT NULL DEFAULT '[]',
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_fact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL REFERENCES child(id),
    session_id TEXT NOT NULL REFERENCES session(id),
    fact TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS word_progress (
    child_id TEXT NOT NULL REFERENCES child(id),
    word TEXT NOT NULL,
    heard INTEGER NOT NULL DEFAULT 0,
    said INTEGER NOT NULL DEFAULT 0,
    region TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (child_id, word)
);
CREATE INDEX IF NOT EXISTS idx_session_device ON session(device_id, last_at);
CREATE INDEX IF NOT EXISTS idx_message_session ON message(session_id, id);
CREATE INDEX IF NOT EXISTS idx_fact_child ON memory_fact(child_id, id);
"""

# 掌握度三档（软件文档 4.4）：没见过 → 听过（出现过 ≥1 次）→ 会说（孩子主动说出过 ≥2 次）
NEW, HEARD, SAID = "new", "heard", "said"


def word_status(heard: int, said: int) -> str:
    if said >= 2:
        return SAID
    if heard + said >= 1:
        return HEARD
    return NEW


@dataclass
class Child:
    id: str
    name: str
    age: int
    theme_id: str | None


@dataclass
class SessionRef:
    id: str
    is_new: bool
    previous_id: str | None  # 同一设备的上一场，新会话开始时用来补抽记忆


class Store:
    def __init__(self, path: str | Path, region: str = "cn"):
        self.region = region
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---- 设备与孩子 ----

    def child_for_device(self, device_id: str, default_name: str, default_age: int, default_theme: str) -> Child:
        """未绑定的设备自动建一个家庭和孩子（第 6 天有了配对流程后，由小程序改名字和年龄）。"""
        rows = self._q(
            "SELECT c.id, c.name, c.age, c.theme_id FROM device d JOIN child c ON c.id = d.child_id WHERE d.id = ?",
            (device_id,),
        )
        if rows:
            r = rows[0]
            return Child(r["id"], r["name"], r["age"], r["theme_id"])
        now = time.time()
        family_id, child_id = _id("fam"), _id("child")
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute("INSERT INTO family VALUES (?, ?, ?, ?)", (family_id, None, self.region, now))
                self._db.execute(
                    "INSERT INTO child VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (child_id, family_id, default_name, default_age, default_theme, self.region, now),
                )
                self._db.execute(
                    "INSERT INTO device VALUES (?, ?, ?, ?, ?)", (device_id, family_id, child_id, self.region, now)
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return Child(child_id, default_name, default_age, default_theme)

    # ---- 会话 ----

    def resolve_session(self, device_id: str, child_id: str, first_turn: bool, idle_s: float) -> SessionRef:
        """同一设备：设备层新连接（first_turn）或空闲超过 idle_s 就开新会话，否则续上一场。"""
        now = time.time()
        rows = self._q("SELECT id, last_at FROM session WHERE device_id = ? ORDER BY last_at DESC LIMIT 1", (device_id,))
        last = rows[0] if rows else None
        if last and not first_turn and now - last["last_at"] < idle_s:
            self._q("UPDATE session SET last_at = ? WHERE id = ?", (now, last["id"]))
            return SessionRef(last["id"], is_new=False, previous_id=None)
        session_id = _id("sess")
        self._q(
            "INSERT INTO session (id, device_id, child_id, started_at, last_at, region) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, device_id, child_id, now, now, self.region),
        )
        return SessionRef(session_id, is_new=True, previous_id=last["id"] if last else None)

    def add_message(self, session_id: str, role: str, text: str, words: list[str]) -> None:
        now = time.time()
        self._q(
            "INSERT INTO message (session_id, role, text, words, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, text, json.dumps(words, ensure_ascii=False), self.region, now),
        )
        self._q("UPDATE session SET last_at = ? WHERE id = ?", (now, session_id))

    def session_messages(self, session_id: str) -> list[dict]:
        rows = self._q("SELECT role, text FROM message WHERE session_id = ? ORDER BY id", (session_id,))
        return [{"role": r["role"], "text": r["text"]} for r in rows]

    def session_extracted(self, session_id: str) -> bool:
        rows = self._q("SELECT extracted FROM session WHERE id = ?", (session_id,))
        return bool(rows and rows[0]["extracted"])

    # ---- 跨会话记忆 ----

    def save_facts(self, child_id: str, session_id: str, facts: list[str]) -> None:
        """一场会话的事实整体替换（同一场可能被抽取多次，以最后一次为准）。"""
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute("DELETE FROM memory_fact WHERE session_id = ?", (session_id,))
                self._db.executemany(
                    "INSERT INTO memory_fact (child_id, session_id, fact, region, created_at) VALUES (?, ?, ?, ?, ?)",
                    [(child_id, session_id, f, self.region, now) for f in facts],
                )
                self._db.execute("UPDATE session SET extracted = 1 WHERE id = ?", (session_id,))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def recent_facts(self, child_id: str, exclude_session: str | None = None, limit: int = 8) -> list[str]:
        rows = self._q(
            "SELECT fact FROM memory_fact WHERE child_id = ? AND session_id IS NOT ? ORDER BY id DESC LIMIT ?",
            (child_id, exclude_session, limit),
        )
        return [r["fact"] for r in reversed(rows)]

    # ---- 掌握度 ----

    def record_words(self, child_id: str, heard: list[str], said: list[str]) -> None:
        """heard：Mumi 说出的目标词；said：孩子说出的目标词。"""
        now = time.time()
        for word, col in [(w, "heard") for w in heard] + [(w, "said") for w in said]:
            self._q(
                f"INSERT INTO word_progress (child_id, word, {col}, region, updated_at) VALUES (?, ?, 1, ?, ?) "
                f"ON CONFLICT(child_id, word) DO UPDATE SET {col} = {col} + 1, updated_at = excluded.updated_at",
                (child_id, word, self.region, now),
            )

    def word_statuses(self, child_id: str) -> dict[str, str]:
        rows = self._q("SELECT word, heard, said FROM word_progress WHERE child_id = ?", (child_id,))
        return {r["word"]: word_status(r["heard"], r["said"]) for r in rows}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
