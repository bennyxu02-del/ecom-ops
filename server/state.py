"""运行时状态（SQLite）：问题卡状态、动作记录、报告、重点池调整、AI 缓存。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("STATE_DB", Path(__file__).resolve().parents[1] / "state" / "state.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (ds TEXT, card_id TEXT, status TEXT, reason TEXT, note TEXT, updated_at REAL,
  PRIMARY KEY (ds, card_id));
CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY AUTOINCREMENT, ds TEXT, card_id TEXT, product_id TEXT,
  product_name TEXT, action_id TEXT, name TEXT, cause TEXT, cause_name TEXT, target TEXT, plan_json TEXT, exec_type TEXT,
  owner_role TEXT, status TEXT, reject_reason TEXT, transfer_role TEXT, track_metric TEXT, variant TEXT,
  adopted_date TEXT, exec_date TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, ds TEXT, type TEXT, title TEXT, period TEXT,
  content TEXT, status TEXT, source TEXT, created_at REAL, published_at REAL);
CREATE TABLE IF NOT EXISTS focus_override (ds TEXT, product_id TEXT, focus INTEGER, PRIMARY KEY (ds, product_id));
CREATE TABLE IF NOT EXISTS ai_cache (key TEXT PRIMARY KEY, value TEXT, created_at REAL);
"""


def conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


_C = None


def db() -> sqlite3.Connection:
    global _C
    if _C is None:
        _C = conn()
        _C.executescript(SCHEMA)
        _C.commit()
    return _C


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


def x(sql, args=()):
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur.lastrowid


# ---------------- 问题卡 ----------------
def card_states(ds: str) -> dict:
    return {r["card_id"]: r for r in q("SELECT * FROM alert_state WHERE ds=?", (ds,))}


def set_card(ds, card_id, status, reason=None, note=None):
    x("INSERT INTO alert_state(ds,card_id,status,reason,note,updated_at) VALUES(?,?,?,?,?,?) "
      "ON CONFLICT(ds,card_id) DO UPDATE SET status=excluded.status, reason=COALESCE(excluded.reason, alert_state.reason),"
      " note=COALESCE(excluded.note, alert_state.note), updated_at=excluded.updated_at",
      (ds, card_id, status, reason, note, time.time()))


# ---------------- 动作 ----------------
def add_action(**kw) -> int:
    kw.setdefault("created_at", time.time())
    cols = ",".join(kw)
    return x(f"INSERT INTO actions({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))


def update_action(aid: int, **kw):
    sets = ",".join(f"{k}=?" for k in kw)
    x(f"UPDATE actions SET {sets} WHERE id=?", (*kw.values(), aid))


def list_actions(ds: str, product_id: str | None = None) -> list[dict]:
    if product_id:
        return q("SELECT * FROM actions WHERE ds=? AND product_id=? ORDER BY id DESC", (ds, product_id))
    return q("SELECT * FROM actions WHERE ds=? ORDER BY id DESC", (ds,))


def get_action(aid: int):
    r = q("SELECT * FROM actions WHERE id=?", (aid,))
    return r[0] if r else None


# ---------------- 报告 ----------------
def add_report(**kw) -> int:
    kw.setdefault("created_at", time.time())
    cols = ",".join(kw)
    return x(f"INSERT INTO reports({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))


def list_reports(ds: str):
    return q("SELECT id,ds,type,title,period,status,source,created_at,published_at FROM reports WHERE ds=? ORDER BY id DESC", (ds,))


def get_report(rid: int):
    r = q("SELECT * FROM reports WHERE id=?", (rid,))
    return r[0] if r else None


def update_report(rid: int, **kw):
    sets = ",".join(f"{k}=?" for k in kw)
    x(f"UPDATE reports SET {sets} WHERE id=?", (*kw.values(), rid))


# ---------------- 重点池 ----------------
def focus_overrides(ds: str) -> dict:
    return {r["product_id"]: bool(r["focus"]) for r in q("SELECT * FROM focus_override WHERE ds=?", (ds,))}


def set_focus(ds, pid, focus: bool):
    x("INSERT INTO focus_override(ds,product_id,focus) VALUES(?,?,?) ON CONFLICT(ds,product_id) DO UPDATE SET focus=excluded.focus",
      (ds, pid, int(focus)))


# ---------------- AI 缓存 ----------------
CACHE_DIR = Path(__file__).resolve().parent / "cache"


def cache_get(key: str):
    r = q("SELECT value FROM ai_cache WHERE key=?", (key,))
    if r:
        return json.loads(r[0]["value"])
    f = CACHE_DIR / (key.replace("/", "_") + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def cache_put(key: str, value, to_file: bool = False):
    s = json.dumps(value, ensure_ascii=False, default=str)
    x("INSERT INTO ai_cache(key,value,created_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
      (key, s, time.time()))
    if to_file:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / (key.replace("/", "_") + ".json")).write_text(s, encoding="utf-8")


def cache_del(key: str):
    x("DELETE FROM ai_cache WHERE key=?", (key,))


# ---------------- 重置 ----------------
def reset(seed_fn=None):
    with _lock:
        for t in ("alert_state", "actions", "reports", "focus_override"):
            db().execute(f"DELETE FROM {t}")
        db().commit()
    if seed_fn:
        seed_fn()
