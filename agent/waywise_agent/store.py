"""数据存储（软件文档第五章）。demo 阶段用 SQLite，一个文件，挂在容器卷上。

每张表都有 region 字段（执行文档 1.1 第 2 条：用户数据按区域可分离）。
"""

import json
import secrets
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
    blocked INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS follow_up (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL REFERENCES child(id),
    session_id TEXT NOT NULL REFERENCES session(id),
    text TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL,
    used_at REAL
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
CREATE TABLE IF NOT EXISTS safety_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL REFERENCES child(id),
    session_id TEXT NOT NULL REFERENCES session(id),
    category TEXT NOT NULL,
    text TEXT NOT NULL,
    notify_parent INTEGER NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS parent (
    openid TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES family(id),
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_theme (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES family(id),
    data TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_code (
    code TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES family(id),
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS family_profile (
    family_id TEXT PRIMARY KEY REFERENCES family(id),
    data TEXT NOT NULL,
    region TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS child_question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id TEXT NOT NULL REFERENCES child(id),
    session_id TEXT NOT NULL REFERENCES session(id),
    text TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS advisor_message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id TEXT NOT NULL REFERENCES family(id),
    child_id TEXT,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_advisor_family ON advisor_message(family_id, child_id, id);
CREATE TABLE IF NOT EXISTS weekly_report (
    child_id TEXT NOT NULL REFERENCES child(id),
    week_start TEXT NOT NULL,
    data TEXT NOT NULL,
    region TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (child_id, week_start)
);
CREATE INDEX IF NOT EXISTS idx_question_child ON child_question(child_id, id);
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
        self._migrate()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """给已有数据库补新加的列（CREATE TABLE IF NOT EXISTS 不会改旧表）。"""
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(message)")}
        if "blocked" not in cols:
            self._db.execute("ALTER TABLE message ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")

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

    def add_message(self, session_id: str, role: str, text: str, words: list[str], blocked: bool = False) -> None:
        now = time.time()
        self._q(
            "INSERT INTO message (session_id, role, text, words, blocked, region, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, text, json.dumps(words, ensure_ascii=False), int(blocked), self.region, now),
        )
        self._q("UPDATE session SET last_at = ? WHERE id = ?", (now, session_id))

    def session_messages(self, session_id: str, include_blocked: bool = True) -> list[dict]:
        """include_blocked=False：去掉安全层拦下的那一问一答（不进记忆，例如孩子说出的住址）。"""
        sql = "SELECT role, text FROM message WHERE session_id = ?" + ("" if include_blocked else " AND blocked = 0")
        rows = self._q(sql + " ORDER BY id", (session_id,))
        return [{"role": r["role"], "text": r["text"]} for r in rows]

    def session_extracted(self, session_id: str) -> bool:
        rows = self._q("SELECT extracted FROM session WHERE id = ?", (session_id,))
        return bool(rows and rows[0]["extracted"])

    def add_safety_event(self, child_id: str, session_id: str, category: str, text: str, notify_parent: bool) -> None:
        self._q(
            "INSERT INTO safety_event (child_id, session_id, category, text, notify_parent, region, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (child_id, session_id, category, text, int(notify_parent), self.region, time.time()),
        )

    # ---- 家庭教育理念 & 孩子的问题（家长教育 Agent 方案第四、五章）----

    def family_profile(self, family_id: str) -> dict:
        rows = self._q("SELECT data FROM family_profile WHERE family_id = ?", (family_id,))
        return json.loads(rows[0]["data"]) if rows else {}

    def family_profile_for_child(self, child_id: str) -> dict:
        rows = self._q(
            "SELECT p.data FROM family_profile p JOIN child c ON c.family_id = p.family_id WHERE c.id = ?", (child_id,)
        )
        return json.loads(rows[0]["data"]) if rows else {}

    def save_family_profile(self, family_id: str, data: dict) -> None:
        self._q(
            "INSERT INTO family_profile VALUES (?, ?, ?, ?) "
            "ON CONFLICT(family_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (family_id, json.dumps(data, ensure_ascii=False), self.region, time.time()),
        )

    def add_question(self, child_id: str, session_id: str, text: str) -> None:
        self._q(
            "INSERT INTO child_question (child_id, session_id, text, region, created_at) VALUES (?, ?, ?, ?, ?)",
            (child_id, session_id, text, self.region, time.time()),
        )

    def recent_questions(self, child_id: str, limit: int = 30) -> list[dict]:
        rows = self._q(
            "SELECT text, created_at FROM child_question WHERE child_id = ? ORDER BY id DESC LIMIT ?", (child_id, limit)
        )
        return [dict(r) for r in rows]

    def questions_between(self, child_id: str, start: float, end: float) -> list[dict]:
        rows = self._q(
            "SELECT text, created_at FROM child_question WHERE child_id = ? AND created_at >= ? AND created_at < ? ORDER BY id",
            (child_id, start, end),
        )
        return [dict(r) for r in rows]

    def last_activity(self, child_id: str) -> float:
        rows = self._q("SELECT MAX(last_at) t FROM session WHERE child_id = ?", (child_id,))
        return rows[0]["t"] or 0.0

    # ---- 顾问对话 & 周报缓存（家长教育 Agent 第二步）----

    def add_advisor_message(self, family_id: str, child_id: str | None, role: str, text: str) -> None:
        self._q(
            "INSERT INTO advisor_message (family_id, child_id, role, text, region, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (family_id, child_id, role, text, self.region, time.time()),
        )

    def advisor_history(self, family_id: str, child_id: str | None, limit: int = 40) -> list[dict]:
        rows = self._q(
            "SELECT role, text, created_at FROM advisor_message WHERE family_id = ? AND child_id IS ? ORDER BY id DESC LIMIT ?",
            (family_id, child_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def clear_advisor_history(self, family_id: str, child_id: str | None) -> None:
        self._q("DELETE FROM advisor_message WHERE family_id = ? AND child_id IS ?", (family_id, child_id))

    def weekly_report(self, child_id: str, week_start: str) -> tuple[dict, float] | None:
        rows = self._q("SELECT data, created_at FROM weekly_report WHERE child_id = ? AND week_start = ?", (child_id, week_start))
        return (json.loads(rows[0]["data"]), rows[0]["created_at"]) if rows else None

    def save_weekly_report(self, child_id: str, week_start: str, data: dict) -> None:
        self._q(
            "INSERT INTO weekly_report VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(child_id, week_start) DO UPDATE SET data = excluded.data, created_at = excluded.created_at",
            (child_id, week_start, json.dumps(data, ensure_ascii=False), self.region, time.time()),
        )

    def custom_theme(self, theme_id: str) -> dict | None:
        rows = self._q("SELECT data FROM custom_theme WHERE id = ?", (theme_id,))
        return json.loads(rows[0]["data"]) if rows else None

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

    # ---- 未完待续：孩子说过还没做完的一件事，下一场开头问起一次 ----

    def save_follow_up(self, child_id: str, session_id: str, text: str | None) -> None:
        """一场会话最多一件（同一场可能被抽取多次，以最后一次为准）。"""
        with self._lock:
            self._db.execute("DELETE FROM follow_up WHERE session_id = ? AND used_at IS NULL", (session_id,))
            if text:
                self._db.execute(
                    "INSERT INTO follow_up (child_id, session_id, text, region, created_at) VALUES (?, ?, ?, ?, ?)",
                    (child_id, session_id, text, self.region, time.time()),
                )

    def take_follow_up(self, child_id: str, exclude_session: str, max_age_s: float = 3 * 86400) -> str | None:
        """取最近一件还没问过、不太旧的事，并标记为已问起（只问一次）。"""
        rows = self._q(
            "SELECT id, text FROM follow_up WHERE child_id = ? AND session_id IS NOT ? AND used_at IS NULL "
            "AND created_at > ? ORDER BY id DESC LIMIT 1",
            (child_id, exclude_session, time.time() - max_age_s),
        )
        if not rows:
            return None
        self._q("UPDATE follow_up SET used_at = ? WHERE id = ?", (time.time(), rows[0]["id"]))
        return rows[0]["text"]

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


class BindError(Exception):
    pass


class ParentStore:
    """家长端（小程序）用到的查询。所有查询都以 family_id 为边界，家长只能看到自己家的数据。"""

    def __init__(self, store: Store):
        self.s = store

    def family_for_openid(self, openid: str) -> str:
        rows = self.s._q("SELECT family_id FROM parent WHERE openid = ?", (openid,))
        if rows:
            return rows[0]["family_id"]
        now, family_id = time.time(), _id("fam")
        self.s._q("INSERT INTO family VALUES (?, ?, ?, ?)", (family_id, None, self.s.region, now))
        self.s._q("INSERT INTO parent VALUES (?, ?, ?, ?)", (openid, family_id, self.s.region, now))
        return family_id

    def bind_device(self, family_id: str, device_id: str, default_name: str, default_age: int, default_theme: str) -> str:
        """把设备绑到这个家庭，返回孩子 id。

        设备已经聊过天（自动建了孩子）：连孩子和聊天记录一起搬过来。
        设备已属于另一个有家长的家庭：拒绝。
        """
        rows = self.s._q(
            "SELECT d.family_id, d.child_id, EXISTS(SELECT 1 FROM parent p WHERE p.family_id = d.family_id) owned "
            "FROM device d WHERE d.id = ?",
            (device_id,),
        )
        if not rows:
            now, child_id = time.time(), _id("child")
            self.s._q(
                "INSERT INTO child VALUES (?, ?, ?, ?, ?, ?, ?)",
                (child_id, family_id, default_name, default_age, default_theme, self.s.region, now),
            )
            self.s._q("INSERT INTO device VALUES (?, ?, ?, ?, ?)", (device_id, family_id, child_id, self.s.region, now))
            return child_id
        r = rows[0]
        if r["family_id"] == family_id:
            return r["child_id"]
        if r["owned"]:
            raise BindError("这台设备已经被别的家长绑定了")
        self.s._q("UPDATE device SET family_id = ?, bound_at = ? WHERE id = ?", (family_id, time.time(), device_id))
        self.s._q("UPDATE child SET family_id = ? WHERE id = ?", (family_id, r["child_id"]))
        return r["child_id"]

    def children(self, family_id: str) -> list[dict]:
        rows = self.s._q(
            "SELECT c.id, c.name, c.age, c.theme_id, group_concat(d.id) devices FROM child c "
            "LEFT JOIN device d ON d.child_id = c.id WHERE c.family_id = ? GROUP BY c.id ORDER BY c.created_at",
            (family_id,),
        )
        return [
            {"id": r["id"], "name": r["name"], "age": r["age"], "theme_id": r["theme_id"],
             "devices": r["devices"].split(",") if r["devices"] else []}
            for r in rows
        ]

    def owns_child(self, family_id: str, child_id: str) -> bool:
        return bool(self.s._q("SELECT 1 FROM child WHERE id = ? AND family_id = ?", (child_id, family_id)))

    def update_child(self, child_id: str, name: str | None, age: int | None, theme_id: str | None) -> None:
        for col, val in (("name", name), ("age", age), ("theme_id", theme_id)):
            if val is not None:
                self.s._q(f"UPDATE child SET {col} = ? WHERE id = ?", (val, child_id))

    def sessions_between(self, child_id: str, start: float, end: float) -> list[dict]:
        rows = self.s._q(
            "SELECT id, started_at, last_at FROM session WHERE child_id = ? AND started_at >= ? AND started_at < ? "
            "ORDER BY started_at",
            (child_id, start, end),
        )
        return [dict(r) for r in rows]

    def messages_of(self, session_ids: list[str]) -> list[dict]:
        if not session_ids:
            return []
        marks = ",".join("?" * len(session_ids))
        rows = self.s._q(
            f"SELECT session_id, role, text, words, blocked, created_at FROM message WHERE session_id IN ({marks}) ORDER BY id",
            tuple(session_ids),
        )
        return [{**dict(r), "words": json.loads(r["words"]), "blocked": bool(r["blocked"])} for r in rows]

    def word_progress(self, child_id: str) -> list[dict]:
        rows = self.s._q(
            "SELECT word, heard, said, updated_at FROM word_progress WHERE child_id = ? ORDER BY updated_at DESC",
            (child_id,),
        )
        return [{**dict(r), "status": word_status(r["heard"], r["said"])} for r in rows]

    def alerts(self, child_id: str, since: float) -> list[dict]:
        rows = self.s._q(
            "SELECT category, text, created_at FROM safety_event WHERE child_id = ? AND notify_parent = 1 AND created_at >= ? "
            "ORDER BY id DESC",
            (child_id, since),
        )
        return [dict(r) for r in rows]


class StudioStore:
    """开发者平台：家长自建主题 + 电脑登录码。"""

    CODE_TTL_S = 300

    def __init__(self, store: Store):
        self.s = store

    def themes(self, family_id: str) -> list[dict]:
        rows = self.s._q(
            "SELECT data, updated_at FROM custom_theme WHERE family_id = ? ORDER BY updated_at DESC", (family_id,)
        )
        return [{**json.loads(r["data"]), "updated_at": r["updated_at"]} for r in rows]

    def owns_theme(self, family_id: str, theme_id: str) -> bool:
        return bool(self.s._q("SELECT 1 FROM custom_theme WHERE id = ? AND family_id = ?", (theme_id, family_id)))

    def create_theme(self, family_id: str, theme: dict) -> dict:
        now, theme_id = time.time(), _id("c").lower()
        theme = {**theme, "theme_id": theme_id}
        self.s._q(
            "INSERT INTO custom_theme VALUES (?, ?, ?, ?, ?, ?)",
            (theme_id, family_id, json.dumps(theme, ensure_ascii=False), self.s.region, now, now),
        )
        return theme

    def update_theme(self, theme_id: str, theme: dict) -> dict:
        theme = {**theme, "theme_id": theme_id}
        self.s._q(
            "UPDATE custom_theme SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(theme, ensure_ascii=False), time.time(), theme_id),
        )
        return theme

    def delete_theme(self, theme_id: str) -> None:
        self.s._q("UPDATE child SET theme_id = NULL WHERE theme_id = ?", (theme_id,))
        self.s._q("DELETE FROM custom_theme WHERE id = ?", (theme_id,))

    def create_login_code(self, family_id: str) -> str:
        now = time.time()
        self.s._q("DELETE FROM login_code WHERE expires_at < ? OR family_id = ?", (now, family_id))
        for _ in range(20):
            code = f"{secrets.randbelow(10**6):06d}"
            if not self.s._q("SELECT 1 FROM login_code WHERE code = ?", (code,)):
                self.s._q("INSERT INTO login_code VALUES (?, ?, ?)", (code, family_id, now + self.CODE_TTL_S))
                return code
        raise RuntimeError("could not allocate login code")

    def consume_login_code(self, code: str) -> str | None:
        """一次性：用过即删。"""
        rows = self.s._q("SELECT family_id, expires_at FROM login_code WHERE code = ?", (code,))
        if not rows:
            return None
        self.s._q("DELETE FROM login_code WHERE code = ?", (code,))
        return rows[0]["family_id"] if rows[0]["expires_at"] >= time.time() else None


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
