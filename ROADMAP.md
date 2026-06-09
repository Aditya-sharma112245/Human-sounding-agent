# Project Roadmap: Human-Sounding WhatsApp Agent

This document outlines the priority-based features and development roadmap for the agent, tracking what has been completed and what is planned for future sessions.

---

## 🟢 Completed Priorities

### Priority 1: Core Requirements
- [x] **Natural Human-like Conversational Style** (lowercase only, no emojis, strict length limits, casual openings).
- [x] **Strong System Prompt & Personality Design** (rules, good/bad phrasing banks, dynamic build).
- [x] **User Profile Memory** (SQLite-backed persistent fields).
- [x] **Conversation Memory** (chronological logging of recent history).
- [x] **Relationship-aware Interactions** (tier modifiers for new, frequent, and long-term friends).
- [x] **Proactive Follow-ups** (LLM scheduling logic + background scheduler).
- [x] **LangGraph Workflow Architecture** (structured node progression).
- [x] **SQLite Persistence Layer** (async `aiosqlite` integration).
- [x] **Mistral API Integration** (low-latency LLM provider).

### Priority 2: High Impact Features
- [x] **Reflection / Vibe Check Node** (automatic rewrite layer for formal or AI-like drafts).
- [x] **Automatic Memory Extraction** (JSON facts extractor from dialogue history).
- [x] **Relationship State Tracking** (buckets users into tiers based on message frequency).
- [x] **Memory Deduplication** (checks semantic overlaps and prevents duplicates in database).

---

## 🟡 Future Priorities (To Be Implemented)

### Priority 3: Utility & Assistant Features (If Time Allows)
- [ ] **Tool Calling** (extend LangGraph to support tool selection for utility tasks).
- [ ] **Reminders** (allow users to say "remind me to..." and receive a WhatsApp message later).
- [ ] **Notes** (allow the agent to save specific notes for the user).
- [ ] **Task Management** (manage simple interactive TODOs/tasks via message).
- [ ] **Web Search** (integrate a search API so the agent can look up current details).

### Priority 4: Stretch Goals & Advanced Features
- [ ] **Voice Note Support** (transcribe voice notes using a Speech-to-Text API like Whisper, process them, and potentially respond).
- [ ] **Advanced Semantic Memory** (enhanced extraction of complex topics).
- [ ] **Embeddings & Vector Search** (integrate vector retrieval if memories scale to a size where simple SQLite text match is insufficient).
