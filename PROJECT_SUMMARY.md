# Project Summary: Human-Sounding WhatsApp Agent

## 1. Executive Overview
This project is an advanced, highly realistic WhatsApp conversational agent. Unlike standard customer-service chatbots, this agent is designed to mimic the exact texting behavior, cadence, and memory of a real human friend. It utilizes a state-machine architecture powered by LangGraph and the Mistral LLM, equipped with persistent memory, proactive messaging capabilities, and dynamic tool calling.

## 2. Core Architecture
The system is built on **FastAPI** for webhook handling, **SQLite** for state and memory persistence, and **LangGraph** to orchestrate the AI's cognitive loop. 

The cognitive loop executes in a defined flow for every incoming message:
1. **Memory Load**: Retrieves the user's profile, relationship tier, and past conversation context from the database.
2. **Reasoning & Tool Execution**: The LLM analyzes the message, decides if it needs to use an external tool (like searching the web), and drafts a response.
3. **Vibe Check (Reflection)**: A secondary LLM pass evaluates the draft for "robotic" language. If the response sounds like an AI, it rewrites it to be casual and human-like.
4. **Memory Extraction**: The system silently extracts new facts about the user and updates their profile and rolling summary.
5. **Schedule Follow-up**: The system evaluates if the conversation warrants a proactive follow-up later (e.g., checking in after an interview).

## 3. Key Capabilities & Features

### A. Ultra-Realistic Persona ("The Vibe Check")
The agent is strictly prompted to communicate like a human: lowercase only, short sentences, no corporate jargon ("I apologize", "As an AI", "Certainly"), and no emojis. A dedicated reflection node ensures the output never breaks character, providing a seamless human experience.

### B. Persistent Memory & Relationship Modeling
The database maintains a dedicated profile for each user phone number.
- **Fact Extraction**: Automatically learns and stores personal details, interests, and life events.
- **Relationship Tiers**: Transitions users from "new" to "frequent" to "long_term" based on message count. The agent's behavior dynamically shifts to become warmer and more direct as it "gets to know" the user.

### C. Proactive Engagement
Traditional bots only speak when spoken to. This agent uses an asynchronous scheduler (`APScheduler`) to send proactive messages. If the user mentions an upcoming event, the agent can autonomously schedule a message to check in on them hours or days later.

### D. Dynamic Tool Calling
The agent has access to a suite of tools that it can use at its own discretion:
- **Web Search (Tavily)**: Fetches real-time information, news, or weather if the user asks a factual question.
- **Reminders**: Schedules future WhatsApp messages based on user requests (e.g., "Remind me in 30 minutes to...").
- **Notes System**: Saves and retrieves persistent information the user wants to keep track of (e.g., "Save my wifi password").

### E. Smart Typing Delays
To simulate actual human texting speeds, the Twilio client implements a dynamic, length-proportional delay algorithm. Short messages (e.g., "ok") are sent almost instantly, while longer sentences incorporate a natural typing pause, complete with slight random variations to prevent it from feeling robotic.

## 4. Technical Stack
- **Backend Framework**: Python / FastAPI
- **LLM Orchestration**: LangChain / LangGraph
- **Models Used**: Mistral AI
- **Messaging API**: Twilio WhatsApp API
- **Database**: SQLite (via `aiosqlite` for high-performance async operations)
- **Background Jobs**: APScheduler
- **External APIs**: Tavily (for live web search)
