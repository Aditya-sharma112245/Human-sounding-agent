"""
tests/test_memory.py
─────────────────────
Tests for the SQLite memory layer.
Uses a temp DB so it doesn't pollute the real DB.
"""

import os
import pytest

# ── Override DB path before importing memory ──────────────────────────────────
TEST_DB = "./test_agent.db"

import app.agent.memory as mem
mem.DB_PATH = TEST_DB


@pytest.fixture(autouse=True)
async def setup_and_teardown_db():
    """Create tables before each test, delete DB after."""
    await mem.init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


async def test_create_profile():
    profile = await mem.get_or_create_profile("+919999999999")
    assert profile["relationship_state"] == "new"
    assert profile["message_count"] == 0


async def test_add_and_get_messages():
    phone = "+919999999998"
    await mem.add_message(phone, "user", "hey")
    await mem.add_message(phone, "assistant", "hey back")
    messages = await mem.get_recent_messages(phone)
    assert len(messages) == 2
    assert messages[0]["content"] == "hey"
    assert messages[1]["content"] == "hey back"


def test_relationship_state_thresholds():
    assert mem.compute_relationship_state(0) == "new"
    assert mem.compute_relationship_state(9) == "new"
    assert mem.compute_relationship_state(10) == "frequent"
    assert mem.compute_relationship_state(49) == "frequent"
    assert mem.compute_relationship_state(50) == "long_term"


async def test_memories():
    phone = "+919999999997"
    await mem.add_memory(phone, "started internship at google")
    memories = await mem.get_memories(phone)
    assert "started internship at google" in memories


async def test_profile_update():
    phone = "+919999999996"
    await mem.get_or_create_profile(phone)
    await mem.update_profile(phone, {"name": "aditya"})
    profile = await mem.get_or_create_profile(phone)
    assert profile["name"] == "aditya"


async def test_near_duplicate_deduplication():
    phone = "+919999999995"
    # Add initial memory
    await mem.add_memory(phone, "name is aditya")
    memories = await mem.get_memories(phone)
    assert len(memories) == 1
    assert "name is aditya" in memories

    # Add exact duplicate (different case)
    await mem.add_memory(phone, "Name is Aditya")
    # Add near duplicate with extra stop words and spacing
    await mem.add_memory(phone, "user's name is aditya")
    # Add near duplicate with different prefix/suffix match (play vs playing)
    await mem.add_memory(phone, "interested in playing ps5 games")
    await mem.add_memory(phone, "user's favorite thing to do is play PS5 games")

    memories = await mem.get_memories(phone)
    # Should only contain "name is aditya" and "interested in playing ps5 games"
    assert len(memories) == 2
    assert "name is aditya" in memories
    assert "interested in playing ps5 games" in memories


async def test_startup_cleanup_duplicates():
    phone = "+919999999994"
    
    # Directly insert duplicates into the test database to simulate legacy duplicates
    import aiosqlite
    async with aiosqlite.connect(mem.DB_PATH) as db:
        await db.execute(
            "INSERT INTO important_memories (user_phone, memory, created_at) VALUES (?, ?, ?)",
            (phone, "is 23 years old", "2026-06-08T00:00:00"),
        )
        await db.execute(
            "INSERT INTO important_memories (user_phone, memory, created_at) VALUES (?, ?, ?)",
            (phone, "user is 23 years old", "2026-06-08T00:01:00"),
        )
        await db.execute(
            "INSERT INTO important_memories (user_phone, memory, created_at) VALUES (?, ?, ?)",
            (phone, "23 years old", "2026-06-08T00:02:00"),
        )
        await db.commit()
        
    # Verify duplicates are initially present
    memories_before = await mem.get_memories(phone)
    assert len(memories_before) == 3
    
    # Run cleanup
    await mem.cleanup_duplicate_memories()
    
    # Verify only one remains (the earliest one inserted)
    memories_after = await mem.get_memories(phone)
    assert len(memories_after) == 1
    assert memories_after[0] == "is 23 years old"
