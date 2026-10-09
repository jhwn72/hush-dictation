"""SQLite storage for dictations, dictionary, snippets, transforms and notes."""
import datetime as dt
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  raw TEXT NOT NULL,
  text TEXT NOT NULL,
  app TEXT,
  category TEXT,
  duration REAL DEFAULT 0,
  words INTEGER DEFAULT 0,
  fixes INTEGER DEFAULT 0,
  dict_fixes INTEGER DEFAULT 0,
  audio TEXT,
  flagged INTEGER DEFAULT 0,
  transform TEXT
);
CREATE INDEX IF NOT EXISTS idx_dictations_ts ON dictations(ts);
CREATE TABLE IF NOT EXISTS dictionary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phrase TEXT NOT NULL,
  replacement TEXT,
  starred INTEGER DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS snippets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger TEXT NOT NULL,
  expansion TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transforms (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  prompt TEXT NOT NULL,
  slot INTEGER,
  builtin INTEGER DEFAULT 0,
  sort INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app TEXT NOT NULL,          -- matched against the app name, process, or (in browsers) the site in the tab title
  instruction TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  title TEXT,
  duration REAL DEFAULT 0,
  transcript TEXT,
  summary TEXT,
  status TEXT DEFAULT 'done',
  audio TEXT
);
"""

BUILTIN_TRANSFORMS = [
    {
        "id": "polish",
        "name": "Polish",
        "description": "Improve clarity and conciseness",
        "prompt": (
            "Rewrite the text to be clearer and more concise. Fix grammar and awkward phrasing, "
            "cut redundancy, and keep the author's meaning, facts and voice. Keep roughly the same format."
        ),
        "slot": 1,
    },
    {
        "id": "prompt_engineer",
        "name": "Prompt Engineer",
        "description": "Constructs optimal prompts",
        "prompt": (
            "Rewrite the text into a clear, well-structured prompt for an AI assistant. State the goal, "
            "the relevant context, every requirement and constraint from the original, and the desired "
            "output format. Use short sections or bullet points where they help. Do not invent requirements."
        ),
        "slot": 2,
    },
]

CATEGORIES = ["ai", "other", "documents", "email", "personal", "work"]


def _dict_rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class DB:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(dictionary)")}
            if "auto" not in cols:  # added with learning from corrections: 1 = learned automatically
                self._conn.execute("ALTER TABLE dictionary ADD COLUMN auto INTEGER DEFAULT 0")
            if not self._conn.execute("SELECT 1 FROM transforms LIMIT 1").fetchone():
                self._seed_transforms()
            self._conn.commit()

    def _seed_transforms(self):
        for i, t in enumerate(BUILTIN_TRANSFORMS):
            self._conn.execute(
                "INSERT OR REPLACE INTO transforms(id,name,description,prompt,slot,builtin,sort) VALUES(?,?,?,?,?,1,?)",
                (t["id"], t["name"], t["description"], t["prompt"], t["slot"], i),
            )

    def _q(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            return _dict_rows(cur) if cur.description else []

    def _x(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    # ---- dictations ----
    def add_dictation(self, **row) -> int:
        keys = ",".join(row)
        marks = ",".join("?" * len(row))
        return self._x(f"INSERT INTO dictations({keys}) VALUES({marks})", tuple(row.values()))

    def get_dictation(self, did):
        rows = self._q("SELECT * FROM dictations WHERE id=?", (did,))
        return rows[0] if rows else None

    def list_dictations(self, query="", offset=0, limit=60):
        if query:
            like = f"%{query}%"
            return self._q(
                "SELECT * FROM dictations WHERE text LIKE ? OR raw LIKE ? ORDER BY ts DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            )
        return self._q("SELECT * FROM dictations ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset))

    def recent_in_app(self, app, within=600, limit=2):
        """Latest dictations into the same app in the last `within` seconds (newest first)."""
        if not app:
            return []
        return self._q("SELECT text FROM dictations WHERE app=? AND ts>? ORDER BY ts DESC LIMIT ?",
                       (app, time.time() - within, limit))

    def delete_dictation(self, did):
        row = self.get_dictation(did)
        self._x("DELETE FROM dictations WHERE id=?", (did,))
        return row

    def toggle_flag(self, did):
        self._x("UPDATE dictations SET flagged = 1 - flagged WHERE id=?", (did,))

    def stats(self) -> dict:
        tot = self._q(
            "SELECT COUNT(*) n, COALESCE(SUM(words),0) words, COALESCE(SUM(duration),0) dur, "
            "COALESCE(SUM(fixes),0) fixes, COALESCE(SUM(dict_fixes),0) dict_fixes FROM dictations"
        )[0]
        wpm = round(tot["words"] / (tot["dur"] / 60)) if tot["dur"] > 5 else 0
        cats = {c: 0 for c in CATEGORIES}
        for r in self._q("SELECT category, COUNT(*) n FROM dictations GROUP BY category"):
            cats[r["category"] if r["category"] in cats else "other"] += r["n"]

        # Per-day words, local time
        days = {}
        for r in self._q("SELECT ts, words FROM dictations"):
            d = dt.date.fromtimestamp(r["ts"]).isoformat()
            days[d] = days.get(d, 0) + r["words"]

        today = dt.date.today()
        streak = 0
        cur = today if today.isoformat() in days else today - dt.timedelta(days=1)
        while cur.isoformat() in days:
            streak += 1
            cur -= dt.timedelta(days=1)
        longest, run, prev = 0, 0, None
        for d in sorted(days):
            day = dt.date.fromisoformat(d)
            run = run + 1 if prev and (day - prev).days == 1 else 1
            longest = max(longest, run)
            prev = day

        month_start = today.replace(day=1)
        last_month_start = (month_start - dt.timedelta(days=1)).replace(day=1)
        this_m = sum(w for d, w in days.items() if d >= month_start.isoformat())
        last_m = sum(w for d, w in days.items() if last_month_start.isoformat() <= d < month_start.isoformat())
        growth = round((this_m - last_m) / last_m * 100) if last_m else None

        return {
            "count": tot["n"],
            "words": tot["words"],
            "wpm": wpm,
            "fixes": tot["fixes"] + tot["dict_fixes"],
            "word_fixes": tot["fixes"],
            "dict_fixes": tot["dict_fixes"],
            "categories": cats,
            "days": days,
            "streak": streak,
            "longest_streak": longest,
            "month_growth": growth,
        }

    def weekly_recap(self, weeks_ago: int = 0) -> dict:
        """Recap of one calendar week (Monday to Sunday, local time). "This week" is compared with the same
        stretch of last week (Monday to now-minus-7-days), so a Tuesday isn't measured against a full week."""
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=weeks_ago)
        start = dt.datetime.combine(monday, dt.time()).timestamp()
        end = min(start + 7 * 86400, time.time()) if weeks_ago == 0 else start + 7 * 86400
        prev_start, prev_end = start - 7 * 86400, end - 7 * 86400

        def span(a, b):
            return self._q(
                "SELECT COUNT(*) n, COALESCE(SUM(words),0) words, COALESCE(SUM(duration),0) dur, "
                "COALESCE(SUM(fixes + dict_fixes),0) fixes, COALESCE(SUM(transform='command'),0) commands "
                "FROM dictations WHERE ts >= ? AND ts < ?", (a, b))[0]

        cur, prev = span(start, end), span(prev_start, prev_end)
        apps = self._q("SELECT app, COUNT(*) n, SUM(words) words FROM dictations WHERE ts >= ? AND ts < ? "
                       "AND app IS NOT NULL AND app != '' GROUP BY app ORDER BY words DESC LIMIT 3", (start, end))
        by_day = [0] * 7
        for r in self._q("SELECT ts, words FROM dictations WHERE ts >= ? AND ts < ?", (start, end)):
            by_day[dt.date.fromtimestamp(r["ts"]).weekday()] += r["words"]
        busiest = max(range(7), key=lambda i: by_day[i]) if any(by_day) else None
        learned = self._q("SELECT phrase FROM dictionary WHERE auto=1 AND created >= ? AND created < ? ORDER BY created",
                          (start, end))
        typing_min = cur["words"] / 40  # average typing speed
        speaking_min = cur["dur"] / 60
        change = round((cur["words"] - prev["words"]) / prev["words"] * 100) if prev["words"] else None
        return {
            "start": monday.isoformat(),
            "end": (monday + dt.timedelta(days=6)).isoformat(),
            "this_week": weeks_ago == 0,
            "words": cur["words"],
            "dictations": cur["n"],
            "commands": cur["commands"],
            "fixes": cur["fixes"],
            "speaking_min": round(speaking_min, 1),
            "saved_min": max(0, round(typing_min - speaking_min)),
            "change_pct": change,
            "prev_words": prev["words"],
            "top_apps": apps,
            "by_day": by_day,
            "busiest_day": busiest,
            "learned": [r["phrase"] for r in learned],
        }

    # ---- dictionary ----
    def list_dictionary(self):
        return self._q("SELECT * FROM dictionary ORDER BY starred DESC, created DESC")

    def add_dictionary(self, phrase, replacement=None, auto=False):
        return self._x(
            "INSERT INTO dictionary(phrase,replacement,created,auto) VALUES(?,?,?,?)",
            (phrase, replacement or None, time.time(), 1 if auto else 0),
        )

    def update_dictionary(self, did, phrase, replacement=None):
        self._x("UPDATE dictionary SET phrase=?, replacement=? WHERE id=?", (phrase, replacement or None, did))

    def star_dictionary(self, did):
        self._x("UPDATE dictionary SET starred = 1 - starred WHERE id=?", (did,))

    def delete_dictionary(self, did):
        self._x("DELETE FROM dictionary WHERE id=?", (did,))

    # ---- snippets ----
    def list_snippets(self):
        return self._q("SELECT * FROM snippets ORDER BY created DESC")

    def add_snippet(self, trigger, expansion):
        return self._x("INSERT INTO snippets(trigger,expansion,created) VALUES(?,?,?)", (trigger, expansion, time.time()))

    def update_snippet(self, sid, trigger, expansion):
        self._x("UPDATE snippets SET trigger=?, expansion=? WHERE id=?", (trigger, expansion, sid))

    def delete_snippet(self, sid):
        self._x("DELETE FROM snippets WHERE id=?", (sid,))

    # ---- transforms ----
    def list_transforms(self):
        return self._q("SELECT * FROM transforms ORDER BY sort, rowid")

    def get_transform(self, tid):
        rows = self._q("SELECT * FROM transforms WHERE id=?", (tid,))
        return rows[0] if rows else None

    def transform_for_slot(self, slot):
        rows = self._q("SELECT * FROM transforms WHERE slot=?", (slot,))
        return rows[0] if rows else None

    def save_transform(self, tid, name, description, prompt):
        if self.get_transform(tid):
            self._x("UPDATE transforms SET name=?, description=?, prompt=? WHERE id=?", (name, description, prompt, tid))
        else:
            used = {t["slot"] for t in self.list_transforms()}
            slot = next((s for s in range(1, 10) if s not in used), None)
            sort = len(self.list_transforms())
            self._x(
                "INSERT INTO transforms(id,name,description,prompt,slot,builtin,sort) VALUES(?,?,?,?,?,0,?)",
                (tid, name, description, prompt, slot, sort),
            )

    def delete_transform(self, tid):
        self._x("DELETE FROM transforms WHERE id=? AND builtin=0", (tid,))

    def reset_transforms(self):
        with self._lock:
            self._conn.execute("DELETE FROM transforms")
            self._seed_transforms()
            self._conn.commit()

    # ---- per-app rules ----
    def list_rules(self):
        return self._q("SELECT * FROM app_rules ORDER BY lower(app), created")

    def add_rule(self, app, instruction):
        return self._x("INSERT INTO app_rules(app,instruction,created) VALUES(?,?,?)", (app, instruction, time.time()))

    def update_rule(self, rid, app, instruction, enabled=1):
        self._x("UPDATE app_rules SET app=?, instruction=?, enabled=? WHERE id=?", (app, instruction, 1 if enabled else 0, rid))

    def delete_rule(self, rid):
        self._x("DELETE FROM app_rules WHERE id=?", (rid,))

    def recent_apps(self, limit=12):
        """Apps the user has dictated into most, for the rule picker."""
        return [r["app"] for r in self._q(
            "SELECT app, COUNT(*) n FROM dictations WHERE app IS NOT NULL AND app != '' GROUP BY app ORDER BY n DESC LIMIT ?",
            (limit,))]

    # ---- notes ----
    def add_note(self, **row):
        keys = ",".join(row)
        marks = ",".join("?" * len(row))
        return self._x(f"INSERT INTO notes({keys}) VALUES({marks})", tuple(row.values()))

    def update_note(self, nid, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), nid))

    def list_notes(self):
        return self._q("SELECT id, ts, title, duration, status, substr(summary,1,400) summary FROM notes ORDER BY ts DESC")

    def get_note(self, nid):
        rows = self._q("SELECT * FROM notes WHERE id=?", (nid,))
        return rows[0] if rows else None

    def delete_note(self, nid):
        row = self.get_note(nid)
        self._x("DELETE FROM notes WHERE id=?", (nid,))
        return row
