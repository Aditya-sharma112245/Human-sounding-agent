"""
app/agent/memory.py
───────────────────
SQLite-backed memory layer.

Tables
──────
  messages           – per-user conversation history
  user_profiles      – extracted facts + relationship state
  important_memories – notable life events the agent remembers
  scheduled_followups – proactive messages to send later

All DB operations are async via aiosqlite.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Optional

import aiosqlite

from app.core.logger import get_logger

logger = get_logger(__name__)

DB_PATH = "agent.db"

# ──────────────────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────────────────

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_phone  TEXT NOT NULL,
    role        TEXT NOT NULL,           -- 'user' | 'assistant'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

_CREATE_USER_PROFILES = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_phone          TEXT PRIMARY KEY,
    name                TEXT,
    facts_json          TEXT DEFAULT '{}',  -- arbitrary extracted facts
    relationship_state  TEXT DEFAULT 'new', -- 'new' | 'frequent' | 'long_term'
    message_count       INTEGER DEFAULT 0,
    summary             TEXT,               -- rolling conversation summary
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""

_CREATE_IMPORTANT_MEMORIES = """
CREATE TABLE IF NOT EXISTS important_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_phone  TEXT NOT NULL,
    memory      TEXT NOT NULL,              -- e.g. "started internship at Google"
    created_at  TEXT NOT NULL
);
"""

_CREATE_SCHEDULED_FOLLOWUPS = """
CREATE TABLE IF NOT EXISTS scheduled_followups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_phone  TEXT NOT NULL,
    message     TEXT NOT NULL,
    send_at     TEXT NOT NULL,             -- ISO datetime string
    sent        INTEGER DEFAULT 0,          -- 0 = pending, 1 = sent
    created_at  TEXT NOT NULL
);
"""

_CREATE_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_phone  TEXT NOT NULL,
    title       TEXT NOT NULL,             -- short identifier / key
    content     TEXT NOT NULL,             -- actual note body
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


async def init_db() -> None:
    """Create all tables if they do not exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_MESSAGES)
        await db.execute(_CREATE_USER_PROFILES)
        await db.execute(_CREATE_IMPORTANT_MEMORIES)
        await db.execute(_CREATE_SCHEDULED_FOLLOWUPS)
        await db.execute(_CREATE_NOTES)
        await db.commit()
    logger.info("Database initialised at %s", DB_PATH)
    await cleanup_duplicate_memories()


# ──────────────────────────────────────────────────────────────────────────────
# Messages
# ──────────────────────────────────────────────────────────────────────────────

async def add_message(user_phone: str, role: str, content: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_phone, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_phone, role, content, _now()),
        )
        await db.commit()


async def get_recent_messages(user_phone: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT role, content, created_at FROM messages
            WHERE user_phone = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_phone, limit),
        )
        rows = await cursor.fetchall()
    # Return in chronological order
    messages = [dict(r) for r in reversed(rows)]
    return messages


async def count_messages(user_phone: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_phone = ?", (user_phone,)
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


# ──────────────────────────────────────────────────────────────────────────────
# User Profiles
# ──────────────────────────────────────────────────────────────────────────────

async def get_or_create_profile(user_phone: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_profiles WHERE user_phone = ?", (user_phone,)
        )
        row = await cursor.fetchone()
        if row:
            profile = dict(row)
            profile["facts"] = json.loads(profile.pop("facts_json", "{}"))
            return profile

        # New user
        now = _now()
        await db.execute(
            """
            INSERT INTO user_profiles
              (user_phone, facts_json, relationship_state, message_count, created_at, updated_at)
            VALUES (?, '{}', 'new', 0, ?, ?)
            """,
            (user_phone, now, now),
        )
        await db.commit()
    return {
        "user_phone": user_phone,
        "name": None,
        "facts": {},
        "relationship_state": "new",
        "message_count": 0,
        "summary": None,
        "created_at": now,
        "updated_at": now,
    }


async def update_profile(user_phone: str, updates: dict) -> None:
    """
    updates may include:
        name, facts (dict), relationship_state, message_count, summary
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        if "facts" in updates:
            # Merge with existing facts
            cursor = await db.execute(
                "SELECT facts_json FROM user_profiles WHERE user_phone = ?", (user_phone,)
            )
            row = await cursor.fetchone()
            existing = json.loads(row[0]) if row and row[0] else {}
            existing.update(updates.pop("facts"))
            updates["facts_json"] = json.dumps(existing)

        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [now, user_phone]
        await db.execute(
            f"UPDATE user_profiles SET {set_clauses}, updated_at = ? WHERE user_phone = ?",
            values,
        )
        await db.commit()


def compute_relationship_state(message_count: int) -> str:
    """Bucket a user into a relationship tier based on how many messages they've sent."""
    if message_count < 10:
        return "new"
    elif message_count < 50:
        return "frequent"
    else:
        return "long_term"


# ──────────────────────────────────────────────────────────────────────────────
# Important Memories
# ──────────────────────────────────────────────────────────────────────────────

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "been", "be",
    "user", "user's", "he", "she", "they", "i", "my", "your",
    "has", "have", "had", "and", "to", "of", "in", "on", "at",
    "for", "with", "about", "like", "likes", "favorite",
    "thing", "things", "wants", "want", "wanted",
    "is", "was", "earlier", "already", "currently", "do", "does", "did"
}

def get_clean_words(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"'s\b", "", text)
    words = re.findall(r"\b[a-z0-9]+\b", text)
    return {w for w in words if w not in STOP_WORDS and (len(w) > 1 or w.isdigit())}

def word_match(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    if len(w1) >= 4 and len(w2) >= 4:
        if w1.startswith(w2) or w2.startswith(w1):
            return True
    return False

def check_near_duplicate(m1: str, m2: str) -> bool:
    s1 = get_clean_words(m1)
    s2 = get_clean_words(m2)
    if not s1 or not s2:
        return False
    
    s_small, s_large = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    
    matches = 0
    for w_s in s_small:
        if any(word_match(w_s, w_l) for w_l in s_large):
            matches += 1
            
    overlap = matches / len(s_small)
    return overlap >= 0.7

async def add_memory(user_phone: str, memory: str) -> None:
    """Save a memory, skipping if a duplicate or near-duplicate already exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Fetch all existing memories for this user
        cursor = await db.execute(
            "SELECT memory FROM important_memories WHERE user_phone = ?",
            (user_phone,),
        )
        rows = await cursor.fetchall()
        existing_memories = [r[0] for r in rows]
        
        # Check if the new memory is a near-duplicate of any existing one
        for existing in existing_memories:
            if check_near_duplicate(existing, memory):
                logger.debug("Near-duplicate memory skipped: %s (matches existing: %s)", memory, existing)
                return

        await db.execute(
            "INSERT INTO important_memories (user_phone, memory, created_at) VALUES (?, ?, ?)",
            (user_phone, memory, _now()),
        )
        await db.commit()
    logger.debug("Memory saved for %s: %s", user_phone, memory)

async def cleanup_duplicate_memories() -> None:
    """Scan important_memories and delete duplicates or near-duplicates."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, user_phone, memory FROM important_memories ORDER BY id ASC")
        rows = await cursor.fetchall()
        
        # Group by user_phone
        user_memories = {}
        for row in rows:
            phone = row["user_phone"]
            user_memories.setdefault(phone, []).append((row["id"], row["memory"]))
            
        to_delete = []
        for phone, mems in user_memories.items():
            keep = []
            for row_id, memory in mems:
                is_dup = False
                for keep_id, keep_mem in keep:
                    if check_near_duplicate(keep_mem, memory):
                        is_dup = True
                        to_delete.append(row_id)
                        break
                if not is_dup:
                    keep.append((row_id, memory))
                    
        if to_delete:
            logger.info("Cleaning up %d duplicate memories from database", len(to_delete))
            for row_id in to_delete:
                await db.execute("DELETE FROM important_memories WHERE id = ?", (row_id,))
            await db.commit()


async def get_memories(user_phone: str, limit: int = 15) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT memory FROM important_memories
            WHERE user_phone = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_phone, limit),
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Scheduled Follow-ups
# ──────────────────────────────────────────────────────────────────────────────

async def schedule_followup(user_phone: str, message: str, send_at: datetime) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        # Deduplication guard: skip if an unsent reminder with the same message
        # and a send_at within 5 minutes already exists for this user.
        cursor = await db.execute(
            """
            SELECT id, send_at FROM scheduled_followups
            WHERE user_phone = ? AND sent = 0 AND message = ?
            """,
            (user_phone, message),
        )
        existing = await cursor.fetchone()
        if existing:
            existing_id, existing_send_at_str = existing
            try:
                existing_send_at = datetime.fromisoformat(existing_send_at_str)
                diff_minutes = abs((send_at - existing_send_at).total_seconds()) / 60
                if diff_minutes < 5:
                    logger.warning(
                        "Duplicate followup skipped for %s — identical message already scheduled at %s (id=%d)",
                        user_phone, existing_send_at_str[:25], existing_id,
                    )
                    return existing_id
            except Exception:
                pass  # If we can't parse the date, allow the insert

        cursor = await db.execute(
            """
            INSERT INTO scheduled_followups (user_phone, message, send_at, sent, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (user_phone, message, send_at.isoformat(), _now()),
        )
        await db.commit()
        followup_id = cursor.lastrowid
    logger.info("Follow-up #%d scheduled for %s at %s", followup_id, user_phone, send_at)
    return followup_id


async def get_pending_followups() -> list[dict]:
    now_str = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, user_phone, message, send_at FROM scheduled_followups
            WHERE sent = 0 AND send_at <= ?
            ORDER BY send_at ASC
            """,
            (now_str,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_followup_sent(followup_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_followups SET sent = 1 WHERE id = ?", (followup_id,)
        )
        await db.commit()


async def get_all_followups(user_phone: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM scheduled_followups WHERE user_phone = ? ORDER BY send_at ASC",
            (user_phone,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────────────────────

async def save_note(user_phone: str, title: str, content: str) -> None:
    """
    Insert a new note or replace an existing one with the same title (case-insensitive).
    """
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if a note with this title already exists
        cursor = await db.execute(
            "SELECT id FROM notes WHERE user_phone = ? AND LOWER(title) = LOWER(?)",
            (user_phone, title),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE notes SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, row[0]),
            )
        else:
            await db.execute(
                "INSERT INTO notes (user_phone, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_phone, title, content, now, now),
            )
        await db.commit()
    logger.debug("Note saved/updated for %s: %s", user_phone, title)


async def get_notes(user_phone: str) -> list[dict]:
    """Return all notes for a user, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title, content, updated_at FROM notes WHERE user_phone = ? ORDER BY updated_at DESC",
            (user_phone,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_note_by_title(user_phone: str, title: str) -> Optional[dict]:
    """Return a single note by (case-insensitive) title match."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, title, content, updated_at FROM notes WHERE user_phone = ? AND LOWER(title) = LOWER(?)",
            (user_phone, title),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_note(user_phone: str, title: str) -> bool:
    """Delete a note by title. Returns True if a row was deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM notes WHERE user_phone = ? AND LOWER(title) = LOWER(?)",
            (user_phone, title),
        )
        await db.commit()
        deleted = cursor.rowcount > 0
    return deleted


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(UTC).isoformat()
