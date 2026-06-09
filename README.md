# human-sounding whatsapp agent

a whatsapp AI agent that feels like texting a real person, not a chatbot. built with fastapi, langgraph, and mistral.

---

## what it does

- **sounds human** — lowercase, short messages, no corporate tone, no emojis, no apologies
- **remembers things** — tracks who you are, what you've said, what you care about
- **relationship-aware** — behaves differently with new users vs people it knows well
- **proactive follow-ups** — remembers "i have an interview tomorrow" and follows up the next day
- **vibe check** — an internal node that re-evaluates every response before sending. if it sounds like chatgpt, it rewrites it

---

## tech stack

| component | tool |
|---|---|
| api | fastapi + uvicorn |
| agent | langgraph |
| llm | mistral (`mistral-small-2506`) |
| db | sqlite (aiosqlite) |
| whatsapp | twilio |
| scheduler | apscheduler |

---

## project structure

```
human-sounding-agent/
├── app/
│   ├── api/
│   │   └── endpoints.py       # webhook + debug endpoints
│   ├── core/
│   │   ├── config.py          # env var settings
│   │   └── logger.py          # logging setup
│   ├── agent/
│   │   ├── graph.py           # langgraph wiring
│   │   ├── nodes.py           # all 5 graph nodes
│   │   ├── prompts.py         # system prompts + vibe check + extraction prompts
│   │   └── memory.py          # sqlite crud for all 4 tables
│   └── services/
│       ├── twilio_client.py   # send whatsapp with human delay
│       └── scheduler.py       # apscheduler for proactive follow-ups
├── tests/
│   ├── test_vibe_check.py
│   └── test_memory.py
├── main.py
├── requirements.txt
├── .env.example
└── .env                       # (not committed)
```

---

## agent graph

```
incoming message
       ↓
  load_memory          # fetch profile, memories, last 20 messages
       ↓
   reasoning           # mistral call with full context
       ↓
  vibe_check           # flag and rewrite if it sounds like a bot
       ↓
 memory_update         # extract facts, update profile, update summary
       ↓
schedule_followup      # register follow-up for significant events only
       ↓
  send response        # human delay + twilio
```

---

## database tables

| table | purpose |
|---|---|
| `messages` | full conversation history per user |
| `user_profiles` | extracted facts, relationship state, summary |
| `important_memories` | notable life events and facts |
| `scheduled_followups` | proactive messages to send later |

---

## setup

### 1. clone and install

```bash
cd "human sounding agent"
python -m venv venv
venv\Scripts\activate          # windows
pip install -r requirements.txt
```

### 2. configure environment

```bash
copy .env.example .env
# edit .env and fill in your Twilio credentials
```

your `.env` needs:
- `MISTRAL_API_KEY` — already set
- `TWILIO_ACCOUNT_SID` — from twilio console
- `TWILIO_AUTH_TOKEN` — from twilio console
- `TWILIO_WHATSAPP_FROM` — your twilio sandbox number

### 3. run the server

```bash
python main.py
```

or with uvicorn directly:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## twilio whatsapp sandbox setup

1. go to [twilio console](https://console.twilio.com/)
2. navigate to: **messaging → try it out → send a whatsapp message**
3. follow the sandbox join instructions (send "join <word>" from your phone)
4. set the **when a message comes in** webhook url to:
   ```
   https://your-ngrok-url.ngrok.io/webhook/whatsapp
   ```
5. set method to `HTTP POST`

### expose locally with ngrok

```bash
# install ngrok: https://ngrok.com/download
ngrok http 8000
```

copy the `https://xxxx.ngrok.io` url into the twilio webhook field.

---

## api endpoints

| method | path | description |
|---|---|---|
| `POST` | `/webhook/whatsapp` | twilio incoming message webhook |
| `GET` | `/memory/{phone}` | view user profile + memories |
| `GET` | `/history/{phone}` | view conversation history |
| `GET` | `/followups/{phone}` | view scheduled follow-ups |
| `GET` | `/health` | health check |

example:
```bash
curl http://localhost:8000/memory/+919876543210
curl http://localhost:8000/history/+919876543210
```

---

## run tests

```bash
pytest tests/ -v
```

the vibe_check tests are pure unit tests (no llm calls, very fast). memory tests use a temp sqlite db that gets cleaned up automatically.

---

## example conversations

**new user:**
```
user:  hey
agent: hey. what's up?

user:  i have an interview tomorrow at google
agent: google's process is usually pretty long. what role is it for?

[next day, automatically sent]
agent: how'd the interview go?
```

**frequent user:**
```
user:  been grinding leetcode all week
agent: what's the weakest area right now

user:  dynamic programming
agent: yeah dp is rough. have you done the classic ones yet — knapsack, longest common subsequence?
```

**long-term user:**
```
user:  got the offer
agent: the google one?

user:  yeah
agent: nice. when do you start?
```

---

## key design decisions

- **no follow-up on everything** — the llm decides if an event is worth following up on. casual messages don't trigger follow-ups.
- **relationship tiers** — at <10 messages (new), 10-50 (frequent), 50+ (long-term). the prompt adapts to each.
- **human delay** — every response waits a random 1-3.5 seconds before sending.
- **vibe check is strict** — catches: uppercase, emojis, banned phrases, responses over 320 characters.
- **async everywhere** — all db operations, llm calls, and twilio sends are async for clean concurrency.

---

## model choice: `mistral-small-2506`

`mistral-small-2506` is a strong choice for this use case:
- fast inference (important for messaging)
- good instruction-following (critical for enforcing the lowercase/style rules)
- cost-effective

if you ever want to upgrade to a heavier model for better memory extraction quality, `mistral-large-latest` is the next step up.
