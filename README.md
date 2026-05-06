# 🧠 BlogAgent

> **An end-to-end AI-powered blog writing system** built with LangGraph, GPT-4.1-mini, Tavily, and Gemini — featuring autonomous research, self-critique loops, human-in-the-loop plan review, SQLite checkpointing, and a Streamlit frontend with streaming output.

---

## 📌 Table of Contents

- [Why BlogAgent?](#-why-blogagent)
- [How It Works — Architecture Overview](#-how-it-works--architecture-overview)
- [Workflow Graph](#-workflow-graph)
- [Feature Deep Dives](#-feature-deep-dives)
  - [1. Smart Router — Research or Not?](#1-smart-router--research-or-not)
  - [2. Web Research (Tavily)](#2-web-research-tavily)
  - [3. Orchestrator — Structured Blog Planning](#3-orchestrator--structured-blog-planning)
  - [4. Human-in-the-Loop Plan Review](#4-human-in-the-loop-plan-review)
  - [5. Parallel Worker Fanout](#5-parallel-worker-fanout)
  - [6. Self-Critique + Rewrite Loop](#6-self-critique--rewrite-loop)
  - [7. AI Image Generation](#7-ai-image-generation)
  - [8. Style Profiles / Personas](#8-style-profiles--personas)
  - [9. SQLite Checkpointing](#9-sqlite-checkpointing)
  - [10. Streamlit Frontend with Streaming](#10-streamlit-frontend-with-streaming)
- [Project Structure](#-project-structure)
- [Setup & Installation](#-setup--installation)
- [Usage](#-usage)
  - [CLI](#cli)
  - [Streamlit UI](#streamlit-ui)
  - [Python API](#python-api)
- [Environment Variables](#-environment-variables)
- [The Learning Notebooks](#-the-learning-notebooks)
- [Tech Stack](#-tech-stack)

---

## 💡 Why BlogAgent?

Writing quality technical blog posts is tedious. You need to:

- Research current events and tools
- Structure content for the audience
- Write multiple polished sections
- Self-review and improve drafts
- Add relevant diagrams or images
- Maintain a consistent voice and style

**BlogAgent automates all of this** — end-to-end, with human control at the points that matter. It's not just a "prompt GPT and get text" wrapper. It's a proper multi-agent pipeline with research, planning, parallel writing, critique, rewriting, and image generation — all wired together in a stateful LangGraph graph with full checkpointing.

Whether you're a developer writing about LLMs, a team producing weekly tech roundups, or a solo creator building a content engine — BlogAgent handles the heavy lifting while keeping you in the loop.

---

## 🏗 How It Works — Architecture Overview

BlogAgent is built as a **LangGraph state machine** where specialized nodes handle each stage of the writing process. The graph is deterministic in structure but adaptive in behavior — routing decisions, critique thresholds, and rewrite loops all respond to the actual content at runtime.

```
START
  │
  ▼
[Router]  ──── closed_book ────► [Orchestrator]
  │                                    │
  └──── hybrid / open_book ──► [Research] ──► [Orchestrator]
                                             │
                                             ▼
                                   [Human Plan Review]  ◄── INTERRUPT
                                             │
                              ┌──────────────┘
                              │  (parallel Send per section)
                              ▼
                          [Worker × N]
                              │
                              ▼
                         [Reducer Subgraph]
                         ┌───────────────────────────────────┐
                         │  merge_content                    │
                         │       │                           │
                         │  critic_node                      │
                         │       │                           │
                         │  overall_pass?                    │
                         │   ├── NO + under cap ──► EXIT ──► [rewrite_prep] ──► [Worker × N again]
                         │   └── YES ──► decide_images       │
                         │                   │               │
                         │       generate_and_place_images   │
                         └───────────────────────────────────┘
                                             │
                                            END
```

---

## 🔀 Workflow Graph

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BLOGAGENT PIPELINE                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐    needs_research?    ┌────────────┐                  │
│  │  ROUTER  │──────── YES ─────────►  RESEARCH  │                  │
│  └──────────┘                      └─────┬──────┘                  │
│       │                                  │                          │
│       └──────────── NO ──────────────────┤                          │
│                                          ▼                          │
│                                  ┌──────────────┐                   │
│                                  │ ORCHESTRATOR │ (structured plan) │
│                                  └──────┬───────┘                   │
│                                         │                           │
│                                         ▼                           │
│                                ┌─────────────────┐                  │
│                                │ HUMAN PLAN      │ ◄── INTERRUPT    │
│                                │ REVIEW (HITL)   │     approve/edit │
│                                └────────┬────────┘                  │
│                                         │                           │
│                    ┌────────────────────┼──────────────────┐        │
│                    ▼                    ▼                   ▼        │
│              ┌──────────┐        ┌──────────┐        ┌──────────┐   │
│              │ WORKER 1 │        │ WORKER 2 │  . . . │ WORKER N │   │
│              └────┬─────┘        └────┬─────┘        └────┬─────┘   │
│                   └────────────────┬──┘────────────────────┘        │
│                                    ▼                                │
│                         ┌─────────────────────┐                     │
│                         │   REDUCER SUBGRAPH  │                     │
│                         │  ┌───────────────┐  │                     │
│                         │  │ merge_content │  │                     │
│                         │  └──────┬────────┘  │                     │
│                         │         ▼            │                     │
│                         │  ┌─────────────┐    │                     │
│                         │  │ critic_node │    │                     │
│                         │  └──────┬──────┘    │                     │
│                         │    pass?│            │                     │
│                         │   YES   │    NO      │                     │
│                         │    ▼    │────────────┼──► rewrite_prep    │
│                         │  decide │            │         │           │
│                         │  images │            │         ▼           │
│                         │    ▼    │            │    workers again   │
│                         │  gen +  │            │    (max 2 loops)   │
│                         │  place  │            │                     │
│                         └────┬────┘            │                     │
│                              └─────────────────┘                    │
│                                    │                                │
│                                   END                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔬 Feature Deep Dives

### 1. Smart Router — Research or Not?

**What it does:** Before doing anything else, the Router decides whether the topic requires live web research.

It classifies every topic into one of three modes:

| Mode | When | Recency Window |
|------|------|----------------|
| `closed_book` | Evergreen concepts (e.g. "How Transformers work") | 10 years |
| `hybrid` | Mix of concepts + current tools/models | 45 days |
| `open_book` | News, weekly roundups, latest releases, pricing | 7 days |

For `hybrid` and `open_book` modes, the router also generates 3–10 high-signal search queries to pass to the research node.

**Why it matters:** It avoids wasting Tavily API calls for evergreen topics, while ensuring news roundups are grounded in real sources.

---

### 2. Web Research (Tavily)

**What it does:** Fires the router-generated queries against Tavily's search API, collects raw results, then uses an LLM to clean, deduplicate, and validate them into structured `EvidenceItem` objects.

For `open_book` mode, evidence is filtered by recency — articles outside the 7-day window are dropped so stale sources can't contaminate a news roundup.

Each evidence item carries: `title`, `url`, `published_at`, and `snippet`. Workers can later cite these URLs inline in their markdown sections.

---

### 3. Orchestrator — Structured Blog Planning

**What it does:** Takes the topic, mode, and evidence and produces a full `Plan` — a Pydantic-validated schema that includes:

- `blog_title`, `audience`, `tone`, `blog_kind`
- A list of 5–9 `Task` objects, each with:
  - `goal` (one-sentence outcome for the reader)
  - `bullets` (3–6 covering points)
  - `target_words` (120–550)
  - `requires_research`, `requires_citations`, `requires_code` flags

The orchestrator is mode-aware. For `open_book`, it forces `blog_kind = "news_roundup"` and avoids tutorial content. For `hybrid`, it marks tasks that need citation.

---

### 4. Human-in-the-Loop Plan Review

**What it does:** After the plan is generated, the graph **pauses** using LangGraph's `interrupt()` mechanism and surfaces the plan to the operator for review before any writing happens.

The host app (CLI or Streamlit) receives a `GraphInterrupt`, inspects the plan, and resumes with one of three responses:

```python
# Approve as-is
app.invoke(Command(resume={"approved": True}), config=config)

# Edit the plan before proceeding
app.invoke(Command(resume={
    "approved": False,
    "edited_plan": { ...your modified Plan dict... }
}), config=config)

# Abort the run entirely
app.invoke(Command(resume={"approved": False, "abort": True}), config=config)
```

**Why it matters:** The LLM's plan might not match your editorial direction. HITL gives you a checkpoint to reorder sections, change the tone, remove irrelevant tasks, or increase target word counts — before wasting time generating content you'll discard.

> **Note:** This requires SQLite checkpointing (Feature 9) to work — the suspended graph snapshot must be persisted somewhere.

---

### 5. Parallel Worker Fanout

**What it does:** Once the plan is approved, every section task is dispatched **in parallel** using LangGraph's `Send` API. Each worker receives:

- Its specific `Task` (goal, bullets, word target, flags)
- The full `Plan` context (title, audience, tone, blog kind)
- Filtered `Evidence` items for citation
- The `StyleProfile` (Feature 8)
- Any `rewrite_instructions` from the critic (Feature 6)

Workers write exactly one markdown section, starting with `## Section Title`, respecting the mode's grounding rules and citation requirements.

**Why it matters:** Parallel fanout means a 7-section blog doesn't take 7× as long. All sections are generated concurrently and collected via LangGraph's `operator.add` reducer.

---

### 6. Self-Critique + Rewrite Loop

**What it does:** After all sections are merged, a `critic_node` evaluates the complete draft on 5 dimensions:

| Dimension | Weight | What it checks |
|-----------|--------|----------------|
| `depth` | 30% | Concepts explained fully, no hand-waving |
| `citation_density` | 20% | External claims backed by URLs |
| `flow` | 20% | Coherent section-to-section narrative |
| `seo` | 15% | Compelling title, natural keyword use |
| `tone_match` | 15% | Voice matches stated audience/tone |

The critic computes a weighted `overall_score` (1–10). If it's below **7.0**, it generates concrete `rewrite_instructions` (up to 6 numbered items referencing specific section titles) and routes back to fanout for a full rewrite pass.

The loop is **capped at 2 rewrites** to prevent infinite cycles. On each rewrite, `sections` state is cleared before re-fanout so stale sections don't accumulate.

**Example critic output:**
```
[CRITIC] Overall score: 6.2/10 | Pass: False
  depth               5/10 — Section 3 glosses over attention math
  citation_density    7/10 — Most claims cited correctly
  flow                6/10 — Abrupt jump between sections 4 and 5
  → Rewrite #1 triggered
  Instructions:
    1. In "Attention Mechanisms": add the QKV formula with dimensions
    2. Add a transition sentence at the end of "Positional Encoding"
    ...
```

---

### 7. AI Image Generation

**What it does:** After a passing draft, a `decide_images` node reads the full markdown and decides whether images would materially improve understanding (max 3 images). It inserts `[[IMAGE_1]]` style placeholders and generates prompts for each.

Then `generate_and_place_images` calls **Gemini 2.5 Flash** to generate each image and replaces the placeholders with proper markdown image syntax:

```markdown
![alt text](images/qkv_flow.png)
*Caption: Query-Key-Value attention flow*
```

If generation fails (safety filter, quota, etc.), it gracefully replaces the placeholder with a descriptive callout block instead of crashing.

---

### 8. Style Profiles / Personas

**What it does:** A `StyleProfile` object lets you fully control the writing voice without touching prompts:

```python
StyleProfile(
    voice="dev-advocate",          # casual | academic | dev-advocate | storytelling
    preferred_length="standard",   # concise | standard | detailed
    avoid_phrases=["leverage", "robust", "seamless"],
    output_format="markdown",      # markdown | html | notion
    custom_instructions="Always include a TL;DR at the top of each section."
)
```

The profile is serialised into the graph state and injected into every worker's system prompt as a compact block:

```
=== STYLE PROFILE ===
Voice: dev-advocate
Length preference: standard
Output format: markdown
NEVER use these words/phrases: leverage, robust, seamless
=====================
```

**Why it matters:** Style consistency across 7 parallel workers is hard. Without this, you get tone drift between sections. The profile anchors every worker to the same voice contract.

---

### 9. SQLite Checkpointing

**What it does:** Every node execution is checkpointed to a local SQLite database (`blog_checkpoints.db` by default, configurable via `CHECKPOINT_DB` env var).

```python
checkpointer = SqliteSaver.from_conn_string(DB_PATH)
app = g.compile(checkpointer=checkpointer)

# Each run gets a unique thread_id
config = {"configurable": {"thread_id": "my-blog-run-001"}}
result = app.invoke(initial_state, config=config)
```

Benefits:
- **Resume crashed runs** from the exact node that failed — no re-running research or re-generating passing sections
- **Inspect intermediate state** at any point for debugging
- **Required for HITL** — the `interrupt()` call can only work if the suspended snapshot is persisted
- **Zero infrastructure** — it's just a local `.db` file, no Redis, no external service

---

### 10. Streamlit Frontend with Streaming

**What it does:** A polished Streamlit UI wraps the entire backend with:

- Topic input and style configuration controls
- Real-time **streaming output** — sections appear as they're generated
- Plan display with approve/edit/reject controls (HITL)
- Critic score visualisation
- Final markdown rendered inline with download option

---

## 📁 Project Structure

```
BlogAgent/
├── src/
│   ├── backend/
│   │   └── blog_agent.py        # Main LangGraph graph (this file)
│   └── frontend/
│       └── app.py               # Streamlit UI
├── learn/
│   └── *.ipynb                  # Exploratory notebooks (LangGraph basics,
│                                #   Tavily experiments, critic prompting, etc.)
├── pyproject.toml               # uv project config
├── uv.lock                      # Locked dependency tree
├── requirements.txt             # pip-compatible requirements
├── .python-version              # Python version pin
├── .gitignore
└── README.md
```

---

## ⚙️ Setup & Installation

This project uses **[uv](https://github.com/astral-sh/uv)** — a fast Python package manager. If you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 1. Clone the repo

```bash
git clone https://github.com/SambhavSurthi/BlogAgent.git
cd BlogAgent
```

### 2. Install dependencies

```bash
uv sync
```

This reads `pyproject.toml` and `uv.lock` to create a virtual environment and install all dependencies in one step.

### 3. Set up environment variables

Create a `.env` file in the project root:

```env
# Required
OPENAI_API_KEY=sk-...

# Required for research features (hybrid / open_book modes)
TAVILY_API_KEY=tvly-...

# Required for AI image generation
GOOGLE_API_KEY=AIza...

# Optional: custom checkpoint database path
CHECKPOINT_DB=blog_checkpoints.db
```

---

## 🚀 Usage

### CLI

```bash
uv run python src/backend/blog_agent.py "How Transformer Attention Mechanisms work"
```

The CLI will:
1. Route the topic (closed_book for this one — no research needed)
2. Generate a plan and **pause for your approval**
3. Print the plan sections and ask `Approve plan? [y/n]:`
4. On approval, run workers in parallel, critique, and generate the final blog
5. Save the output as `<blog-title-slug>.md` in the current directory

To pass a custom JSON plan edit at the CLI prompt:
```
Approve plan? [y/n]: n
Enter edits as JSON (or press Enter to abort):
{"blog_title": "...", "audience": "...", ...}
```

### Streamlit UI

```bash
uv run streamlit run src/frontend/app.py
```

Opens at `http://localhost:8501`. Enter your topic, configure style options, and watch the blog build in real time.

### Python API

```python
from src.backend.blog_agent import run_blog, StyleProfile

# Simple usage
markdown = run_blog("The State of Open-Source LLMs in 2025")

# With style customisation
style = StyleProfile(
    voice="storytelling",
    preferred_length="detailed",
    avoid_phrases=["leverage", "game-changer"],
    custom_instructions="Use analogies from cooking to explain technical concepts."
)

markdown = run_blog(
    topic="Vector Databases Explained",
    thread_id="vec-db-post-1",   # reuse this ID to resume if it crashes
    style=style
)

print(markdown)
```

**Handling the HITL interrupt in code:**

```python
from langgraph.types import Command
from src.backend.blog_agent import app

config = {"configurable": {"thread_id": "my-run-001"}}

# First call — will raise GraphInterrupt at plan review
try:
    result = app.invoke(initial_state, config=config)
except Exception as e:
    print("Graph paused for plan review")

# Inspect the plan
snapshot = app.get_state(config)
plan = snapshot.values["plan"]

# Resume with approval
result = app.invoke(Command(resume={"approved": True}), config=config)
print(result["final"])
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ Yes | Powers the LLM (GPT-4.1-mini) for all reasoning nodes |
| `TAVILY_API_KEY` | ⚡ For research | Web search for hybrid/open_book topics |
| `GOOGLE_API_KEY` | 🖼 For images | Gemini 2.5 Flash image generation |
| `CHECKPOINT_DB` | ❌ Optional | Path to SQLite checkpoint file (default: `blog_checkpoints.db`) |

> Closed-book mode only needs `OPENAI_API_KEY`. Research and image features activate automatically when the other keys are present.

---

## 📓 The Learning Notebooks

The `learn/` directory contains the exploratory notebooks used to build and test each component before wiring them into the final graph. These are useful if you want to understand any individual piece:

| Notebook | What it explores |
|----------|-----------------|
| LangGraph basics | State machines, `Send`, conditional edges, subgraphs |
| Tavily experiments | Query design, recency filtering, evidence structuring |
| Critic prompting | Scoring dimensions, rewrite instruction formats |
| Structured output | Pydantic schemas with `with_structured_output` |
| SQLite checkpointing | `SqliteSaver`, thread IDs, resuming interrupted runs |

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Orchestration** | [LangGraph](https://github.com/langchain-ai/langgraph) — stateful multi-agent graph |
| **LLM** | OpenAI GPT-4.1-mini via `langchain-openai` |
| **Web Search** | [Tavily](https://tavily.com/) via `langchain-community` |
| **Image Generation** | Google Gemini 2.5 Flash (`google-genai`) |
| **Frontend** | [Streamlit](https://streamlit.io/) with streaming |
| **Checkpointing** | `langgraph-checkpoint-sqlite` (SQLite) |
| **Validation** | [Pydantic](https://docs.pydantic.dev/) v2 |
| **Package Manager** | [uv](https://github.com/astral-sh/uv) |
| **Environment** | `python-dotenv` |

---

## 🗺 Roadmap

- [ ] Add support for Anthropic Claude as an alternative LLM backend
- [ ] Export to HTML and Notion formats (style profile `output_format` already wired)
- [ ] Multi-author mode — different style profiles per section
- [ ] LangSmith tracing integration for production observability
- [ ] REST API wrapper (FastAPI) for headless deployment
- [ ] Batch mode — generate multiple posts from a topic list

---

## 📄 License

MIT — do whatever you want with it, just don't remove the attribution.

---

<div align="center">
  Built with LangGraph · GPT-4.1-mini · Tavily · Gemini · Streamlit
</div>