"""
Blog Agent — Streamlit Frontend (Fixed)
========================================
Run:  streamlit run app.py

Fixes applied:
  1. HITL: GraphInterrupt is now caught specifically and posted as ("hitl", plan_dict)
     to the queue. The UI watches for this and shows the approve/edit panel.
  2. Live nodes: node_start events are posted immediately inside the streaming loop,
     before any output processing. The auto-refresh loop now uses st.empty() placeholders
     so the active node badge and pipeline sidebar update every 1.5s without full rerender jank.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

import streamlit as st

st.set_page_config(
    page_title="BlogMind AI",
    page_icon="✍️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

:root {
    --bg:        #0d0f14;
    --surface:   #141720;
    --surface2:  #1c2030;
    --border:    #2a2f42;
    --accent:    #e8c547;
    --accent2:   #4fc3f7;
    --text:      #e2e4ec;
    --muted:     #6b7280;
    --success:   #34d399;
    --warning:   #f59e0b;
    --error:     #f87171;
    --font-head: 'Playfair Display', Georgia, serif;
    --font-body: 'DM Sans', sans-serif;
    --font-mono: 'IBM Plex Mono', monospace;
}

html, body, [class*="css"] {
    font-family: var(--font-body) !important;
    background-color: var(--bg) !important;
    color: var(--text) !important;
}

[data-testid="stSidebar"] {
    background-color: var(--surface) !important;
    border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] * { color: var(--text) !important; }

.main .block-container { padding: 1.5rem 2rem 3rem; max-width: 1400px; }

.masthead {
    font-family: var(--font-head);
    font-size: 2.8rem;
    font-weight: 900;
    letter-spacing: -0.03em;
    color: var(--accent);
    margin: 0;
    line-height: 1;
}
.masthead-sub {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--muted);
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-top: 4px;
}

.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
}
.card-accent { border-left: 3px solid var(--accent); }
.card-blue   { border-left: 3px solid var(--accent2); }
.card-green  { border-left: 3px solid var(--success); }
.card-red    { border-left: 3px solid var(--error); }

/* ACTIVE NODE BADGE - the glowing indicator */
.node-badge {
    display: inline-block;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    padding: 2px 8px;
    border-radius: 12px;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--accent2);
    letter-spacing: 0.05em;
}
.node-badge.active {
    background: rgba(232,197,71,0.12);
    border-color: var(--accent);
    color: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(232,197,71,0.4); }
    50%       { opacity: 0.7; box-shadow: 0 0 0 4px rgba(232,197,71,0); }
}

/* LIVE NODE TICKER — large animated indicator */
.live-node-ticker {
    background: linear-gradient(135deg, rgba(232,197,71,0.08), rgba(79,195,247,0.05));
    border: 1px solid rgba(232,197,71,0.3);
    border-radius: 10px;
    padding: 1rem 1.4rem;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 14px;
}
.live-node-ticker .spinner {
    width: 18px; height: 18px;
    border: 2px solid rgba(232,197,71,0.2);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }
.live-node-ticker .node-name {
    font-family: var(--font-mono);
    font-size: 0.9rem;
    font-weight: 500;
    color: var(--accent);
    letter-spacing: 0.05em;
}
.live-node-ticker .node-desc {
    font-size: 0.8rem;
    color: var(--muted);
    margin-top: 2px;
}

.log-line {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    color: var(--muted);
    padding: 2px 0;
    border-bottom: 1px solid rgba(42,47,66,0.4);
}
.log-line.info    { color: var(--accent2); }
.log-line.success { color: var(--success); }
.log-line.warn    { color: var(--warning); }
.log-line.error   { color: var(--error); }

.prog-bar {
    height: 3px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    margin: 8px 0;
}
.prog-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    border-radius: 2px;
    transition: width 0.4s ease;
}

.section-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.7rem;
    font-family: var(--font-mono);
    margin: 2px;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--muted);
}
.section-pill.done {
    background: rgba(52,211,153,0.1);
    border-color: var(--success);
    color: var(--success);
}

.citation {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 0.82rem;
}
.citation a { color: var(--accent2) !important; text-decoration: none; }
.citation a:hover { text-decoration: underline; }
.citation-date {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    color: var(--muted);
}

.blog-body {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 2rem 2.5rem;
    line-height: 1.8;
    max-width: 860px;
    margin: 0 auto;
}
.blog-body h1 {
    font-family: var(--font-head);
    font-size: 2rem;
    font-weight: 900;
    color: var(--accent);
    margin-bottom: 0.4rem;
}
.blog-body h2 {
    font-family: var(--font-head);
    font-size: 1.35rem;
    font-weight: 700;
    color: var(--text);
    margin-top: 2rem;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
}
.blog-body h3 { color: var(--accent2); font-weight: 600; }
.blog-body code {
    font-family: var(--font-mono);
    background: var(--surface2);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 0.85em;
    color: var(--accent);
}
.blog-body pre {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem;
    overflow-x: auto;
}
.blog-body blockquote {
    border-left: 3px solid var(--accent);
    margin-left: 0;
    padding-left: 1rem;
    color: var(--muted);
    font-style: italic;
}
.blog-body a { color: var(--accent2); }

/* HITL PANEL */
.hitl-panel {
    background: linear-gradient(135deg, rgba(232,197,71,0.06), rgba(79,195,247,0.04));
    border: 2px solid var(--accent);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.5rem;
}
.hitl-header {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    color: var(--accent);
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.hitl-pulse {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    animation: pulse 1s infinite;
    display: inline-block;
}

.chat-item {
    padding: 8px 10px;
    border-radius: 6px;
    cursor: pointer;
    margin: 3px 0;
    border: 1px solid transparent;
    font-size: 0.82rem;
}
.chat-item:hover { background: var(--surface2); border-color: var(--border); }
.chat-item.active { background: rgba(232,197,71,0.08); border-color: var(--accent); }

.stTextInput input, .stTextArea textarea, .stSelectbox select {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
    border-radius: 6px !important;
    font-family: var(--font-body) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px rgba(232,197,71,0.15) !important;
}

.stButton button {
    background: var(--accent) !important;
    color: #0d0f14 !important;
    font-weight: 600 !important;
    border: none !important;
    border-radius: 6px !important;
    font-family: var(--font-body) !important;
    transition: opacity 0.15s !important;
}
.stButton button:hover { opacity: 0.85 !important; }
.stButton button[kind="secondary"] {
    background: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
}

.stTabs [data-baseweb="tab-list"] {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    color: var(--muted) !important;
    font-family: var(--font-mono) !important;
    font-size: 0.78rem !important;
    letter-spacing: 0.05em;
    padding: 8px 16px !important;
    border-radius: 4px 4px 0 0 !important;
}
.stTabs [aria-selected="true"] {
    color: var(--accent) !important;
    background: rgba(232,197,71,0.06) !important;
    border-bottom: 2px solid var(--accent) !important;
}

.streamlit-expanderHeader {
    background: var(--surface2) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    font-family: var(--font-mono) !important;
    font-size: 0.8rem !important;
    color: var(--accent2) !important;
}

[data-testid="metric-container"] {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 14px;
}
[data-testid="metric-container"] label { color: var(--muted) !important; font-size: 0.72rem !important; }
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: var(--accent) !important;
    font-family: var(--font-mono) !important;
    font-weight: 700 !important;
}

hr { border-color: var(--border) !important; margin: 1rem 0 !important; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }

.stream-box {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 14px;
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--text);
    max-height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
}

.plan-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.plan-table th {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    text-align: left;
}
.plan-table td {
    padding: 8px 10px;
    border-bottom: 1px solid rgba(42,47,66,0.4);
    vertical-align: top;
}
.plan-table tr:hover td { background: var(--surface2); }

/* Pipeline sidebar nodes */
.pipeline-node {
    font-family: var(--font-mono);
    font-size: 0.68rem;
    padding: 4px 8px;
    border-radius: 4px;
    margin: 2px 0;
    transition: all 0.2s;
}
.pipeline-node.active {
    background: rgba(232,197,71,0.12);
    color: var(--accent);
    border-left: 2px solid var(--accent);
    padding-left: 6px;
}
.pipeline-node.done {
    color: var(--success);
}
.pipeline-node.pending {
    color: var(--muted);
}

#MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Session State Bootstrap
# ══════════════════════════════════════════════════════════════════════════════

def _init_state():
    defaults = {
        "sessions": {},
        "active_session": None,
        "run_status": "idle",        # idle | running | awaiting_hitl | done | error
        "current_node": None,
        "completed_nodes": [],       # list of node names that finished
        "node_log": [],
        "node_streams": {},
        "queue": None,

        "final_md": None,
        "plan_data": None,
        "evidence_data": [],
        "image_specs": [],
        "sections_done": [],
        "critique_data": None,

        # HITL — stores the full plan dict surfaced by interrupt()
        "hitl_plan": None,

        "openai_key": "",
        "gemini_key": "",
        "tavily_key": "",
        
        # active thread_id for the current session
        "current_thread_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _log(message: str, level: str = "info", node: str = ""):
    st.session_state.node_log.append({
        "node": node or st.session_state.current_node or "system",
        "message": message,
        "level": level,
        "ts": _ts(),
    })

def _new_session_id() -> str:
    return str(uuid.uuid4())[:8]

def _create_session(topic: str) -> str:
    sid = _new_session_id()
    st.session_state.sessions[sid] = {
        "id": sid,
        "title": topic[:45] + ("…" if len(topic) > 45 else ""),
        "created": datetime.now().strftime("%d %b %Y, %H:%M"),
        "topic": topic,
        "result": None,
        "logs": [],
        "status": "running",
    }
    return sid

def _save_session_result(sid: str):
    if sid and sid in st.session_state.sessions:
        st.session_state.sessions[sid].update({
            "result": st.session_state.final_md,
            "plan": st.session_state.plan_data,
            "evidence": st.session_state.evidence_data,
            "critique": st.session_state.critique_data,
            "logs": list(st.session_state.node_log),
            "status": st.session_state.run_status,
        })

def _load_session(sid: str):
    s = st.session_state.sessions.get(sid, {})
    st.session_state.final_md      = s.get("result")
    st.session_state.plan_data     = s.get("plan")
    st.session_state.evidence_data = s.get("evidence", [])
    st.session_state.critique_data = s.get("critique")
    st.session_state.node_log      = list(s.get("logs", []))
    st.session_state.run_status    = s.get("status", "done")

def _b64_md(text: str) -> str:
    return base64.b64encode(text.encode()).decode()

def _download_button(label: str, data: str, filename: str, mime: str = "text/markdown"):
    b64 = _b64_md(data)
    st.markdown(
        f'<a href="data:{mime};base64,{b64}" download="{filename}" '
        f'style="display:inline-block;padding:6px 16px;background:#e8c547;color:#0d0f14;'
        f'font-weight:600;border-radius:6px;font-size:0.8rem;text-decoration:none;'
        f'font-family:\'DM Sans\',sans-serif;">{label}</a>',
        unsafe_allow_html=True,
    )


def _plan_from_snapshot(app: Any, config: dict) -> dict:
    """Best-effort extraction of plan dict from current checkpoint state."""
    try:
        snapshot = app.get_state(config)
        plan_obj = snapshot.values.get("plan") if snapshot and snapshot.values else None
        if plan_obj is None:
            return {}
        return plan_obj.model_dump() if hasattr(plan_obj, "model_dump") else dict(plan_obj)
    except Exception:
        return {}


def _extract_interrupt_plan(stream_exc: Exception, app: Any, config: dict) -> dict:
    """Extract interrupt payload plan across langgraph versions; fallback to checkpoint."""
    interrupt_value = None
    try:
        args = stream_exc.args
        if args and hasattr(args[0], "__iter__"):
            for item in args[0]:
                if hasattr(item, "value"):
                    interrupt_value = item.value
                    break
        elif args:
            interrupt_value = args[0]
    except Exception:
        interrupt_value = None

    if isinstance(interrupt_value, dict):
        plan = interrupt_value.get("plan")
        if isinstance(plan, dict):
            return plan

    return _plan_from_snapshot(app, config)

NODE_DESCRIPTIONS = {
    "router":            "Analysing topic to decide research mode (closed_book / hybrid / open_book).",
    "research":          "Querying Tavily web search and synthesising evidence items from live results.",
    "orchestrator":      "Building the blog plan — sections, goals, bullets, and word targets.",
    "human_plan_review": "Waiting for your approval of the generated plan before writing begins.",
    "worker":            "Writing blog sections in parallel. Each worker covers one task.",
    "merge_content":     "Merging all parallel sections into a single ordered markdown document.",
    "critic_node":       "Scoring the draft on 5 dimensions: depth, citations, flow, SEO, tone.",
    "rewrite_prep":      "Critic found issues — resetting sections for a targeted rewrite pass.",
    "decide_images":     "Deciding where diagrams would improve understanding; writing prompts.",
    "generate_and_place_images": "Generating images via Gemini and embedding them into markdown.",
    "reducer":           "Running reducer subgraph (merge → critic → images).",
}

PIPELINE_ORDER = [
    "router", "research", "orchestrator", "human_plan_review",
    "worker", "merge_content", "critic_node",
    "rewrite_prep", "decide_images", "generate_and_place_images",
]


# ══════════════════════════════════════════════════════════════════════════════
# Background runner thread
# ══════════════════════════════════════════════════════════════════════════════

def _run_agent(topic: str, thread_id: str, q: Queue, openai_key: str, gemini_key: str, tavily_key: str):
    """
    Runs the LangGraph app in a background thread, posting events to queue q.

    KEY FIX: GraphInterrupt (from HITL interrupt()) is caught specifically and
    posted as ("hitl", plan_dict) so the UI can show the approve panel.
    All other exceptions are posted as ("error", traceback).

    Event types:
      ("node_start", node_name)
      ("node_end", node_name)
      ("plan_ready", plan_dict)
      ("hitl", plan_dict)        ← NEW: graph paused at human_plan_review
      ("evidence", [ev_dicts])
      ("section_done", task_id, preview)
      ("critique", critique_dict)
      ("image_specs", [spec_dicts])
      ("final", final_md)
      ("error", traceback_str)
      ("done",)
    """
    os.environ["OPENAI_API_KEY"] = openai_key
    os.environ["GOOGLE_API_KEY"] = gemini_key
    if tavily_key:
        os.environ["TAVILY_API_KEY"] = tavily_key

    try:
        from backend_updated import app, State, StyleProfile, reset_llm  # type: ignore
        reset_llm()  # force fresh LLM with new key

        style = StyleProfile(
            voice="dev-advocate",
            preferred_length="standard",
            avoid_phrases=["leverage", "robust", "seamless", "innovative"],
            output_format="markdown",
        )

        initial_state: State = {
            "topic": topic,
            "as_of": date.today().isoformat(),
            "mode": "closed_book",
            "needs_research": False,
            "queries": [],
            "evidence": [],
            "plan": None,
            "style_profile": style.model_dump(),
            "sections": [],
            "critique": None,
            "rewrite_count": 0,
            "merged_md": "",
            "md_with_placeholders": "",
            "image_specs": [],
            "final": "",
        }

        config = {"configurable": {"thread_id": thread_id}}

        # ── stream node-by-node ──────────────────────────────────────────
        # GraphInterrupt is raised by interrupt() inside human_plan_review.
        # We catch it here and extract the plan from the interrupt value.
        try:
            for event in app.stream(initial_state, config=config, stream_mode="updates"):
                if "__interrupt__" in event:
                    q.put(("hitl", _plan_from_snapshot(app, config)))
                    return

                for node_name, output in event.items():
                    if node_name == "__interrupt__":
                        q.put(("hitl", _plan_from_snapshot(app, config)))
                        return

                    # Post node_start FIRST so UI updates immediately
                    q.put(("node_start", node_name))

                    if isinstance(output, dict):
                        if "plan" in output and output["plan"] is not None:
                            plan = output["plan"]
                            plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan
                            q.put(("plan_ready", plan_dict))

                        if "evidence" in output and output["evidence"]:
                            evs = output["evidence"]
                            ev_list = [e.model_dump() if hasattr(e, "model_dump") else e for e in evs]
                            q.put(("evidence", ev_list))

                        if "sections" in output and output["sections"]:
                            for task_id, section_md in output["sections"]:
                                q.put(("section_done", task_id, section_md[:80]))

                        if "critique" in output and output["critique"]:
                            q.put(("critique", output["critique"]))

                        if "final" in output and output["final"]:
                            q.put(("final", output["final"]))

                        if "image_specs" in output and output["image_specs"]:
                            q.put(("image_specs", output["image_specs"]))

                    q.put(("node_end", node_name))

            q.put(("done",))

        except Exception as stream_exc:
            # ── CRITICAL FIX: detect GraphInterrupt from HITL ──────────
            # LangGraph raises langgraph.errors.GraphInterrupt when interrupt() is called.
            # We detect it by class name to avoid import issues across versions.
            exc_type_name = type(stream_exc).__name__
            if exc_type_name == "GraphInterrupt" or "Interrupt" in exc_type_name:
                q.put(("hitl", _extract_interrupt_plan(stream_exc, app, config)))
            else:
                import traceback
                q.put(("error", traceback.format_exc()))

    except Exception as outer_exc:
        import traceback
        q.put(("error", traceback.format_exc()))


def _run_agent_hitl_resume(
    thread_id: str,
    q: Queue,
    resume_payload: dict,
    openai_key: str,
    gemini_key: str,
    tavily_key: str,
):
    """Resume the graph after HITL approval/edit."""
    os.environ["OPENAI_API_KEY"] = openai_key
    os.environ["GOOGLE_API_KEY"] = gemini_key
    if tavily_key:
        os.environ["TAVILY_API_KEY"] = tavily_key

    try:
        from backend_updated import app  # type: ignore
        from langgraph.types import Command

        config = {"configurable": {"thread_id": thread_id}}

        try:
            for event in app.stream(Command(resume=resume_payload), config=config, stream_mode="updates"):
                if "__interrupt__" in event:
                    q.put(("hitl", _plan_from_snapshot(app, config)))
                    return

                for node_name, output in event.items():
                    if node_name == "__interrupt__":
                        q.put(("hitl", _plan_from_snapshot(app, config)))
                        return

                    q.put(("node_start", node_name))

                    if isinstance(output, dict):
                        if "plan" in output and output["plan"] is not None:
                            plan = output["plan"]
                            plan_dict = plan.model_dump() if hasattr(plan, "model_dump") else plan
                            q.put(("plan_ready", plan_dict))

                        if "evidence" in output and output["evidence"]:
                            evs = output["evidence"]
                            ev_list = [e.model_dump() if hasattr(e, "model_dump") else e for e in evs]
                            q.put(("evidence", ev_list))

                        if "sections" in output and output["sections"]:
                            for task_id, section_md in output["sections"]:
                                q.put(("section_done", task_id, section_md[:80]))

                        if "critique" in output and output["critique"]:
                            q.put(("critique", output["critique"]))

                        if "final" in output and output["final"]:
                            q.put(("final", output["final"]))

                        if "image_specs" in output and output["image_specs"]:
                            q.put(("image_specs", output["image_specs"]))

                    q.put(("node_end", node_name))

            q.put(("done",))

        except Exception as stream_exc:
            exc_type_name = type(stream_exc).__name__
            if exc_type_name == "GraphInterrupt" or "Interrupt" in exc_type_name:
                q.put(("hitl", _extract_interrupt_plan(stream_exc, app, config)))
            else:
                import traceback
                tb = traceback.format_exc()
                if "Run aborted by human reviewer" in tb:
                    q.put(("aborted",))
                else:
                    q.put(("error", tb))

    except Exception as outer_exc:
        import traceback
        q.put(("error", traceback.format_exc()))


def _drain_queue() -> bool:
    """
    Drain all pending events from the queue and update session state.
    Returns True if any events were processed (triggers a rerun).
    """
    q: Optional[Queue] = st.session_state.queue
    if q is None:
        return False

    changed = False
    while True:
        try:
            event = q.get_nowait()
        except Empty:
            break

        changed = True
        kind = event[0]

        if kind == "node_start":
            node = event[1]
            st.session_state.current_node = node
            _log(f"▶ {node} started", "info", node)

        elif kind == "node_end":
            node = event[1]
            if node not in st.session_state.completed_nodes:
                st.session_state.completed_nodes.append(node)
            _log(f"✓ {node} complete", "success", node)

        elif kind == "plan_ready":
            st.session_state.plan_data = event[1]
            _log("Plan generated", "success", "orchestrator")

        elif kind == "hitl":
            # ← CRITICAL FIX: HITL plan arrives here now
            st.session_state.hitl_plan = event[1]
            st.session_state.run_status = "awaiting_hitl"
            st.session_state.current_node = "human_plan_review"
            _log("⏸ Awaiting plan approval", "warn", "human_plan_review")

        elif kind == "evidence":
            st.session_state.evidence_data = event[1]
            _log(f"{len(event[1])} evidence items collected", "info", "research")

        elif kind == "section_done":
            task_id, preview = event[1], event[2]
            if task_id not in st.session_state.sections_done:
                st.session_state.sections_done.append(task_id)
            _log(f"Section #{task_id} written: {preview}…", "success", "worker")

        elif kind == "critique":
            st.session_state.critique_data = event[1]
            score = event[1].get("overall_score", "?")
            passed = event[1].get("overall_pass", False)
            _log(
                f"Critic score: {score}/10 — {'PASS ✓' if passed else 'REWRITE ↻'}",
                "success" if passed else "warn",
                "critic_node",
            )

        elif kind == "image_specs":
            st.session_state.image_specs = event[1]

        elif kind == "final":
            st.session_state.final_md = event[1]
            st.session_state.run_status = "done"
            st.session_state.current_node = None
            _log("🎉 Blog generation complete!", "success", "system")
            _save_session_result(st.session_state.active_session)

        elif kind == "error":
            st.session_state.run_status = "error"
            st.session_state.current_node = None
            _log(f"ERROR: {event[1]}", "error", "system")
            _save_session_result(st.session_state.active_session)

        elif kind == "aborted":
            st.session_state.run_status = "idle"
            st.session_state.current_node = None
            st.session_state.hitl_plan = None
            _log("Run aborted from plan review.", "warn", "human_plan_review")
            _save_session_result(st.session_state.active_session)

        elif kind == "done":
            if st.session_state.run_status == "running":
                st.session_state.run_status = "done"
                st.session_state.current_node = None
            _save_session_result(st.session_state.active_session)

    return changed


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown('<div class="masthead">BlogMind</div>', unsafe_allow_html=True)
    st.markdown('<div class="masthead-sub">AI Multi-Agent Writer</div>', unsafe_allow_html=True)
    st.markdown("---")

    with st.expander("🔑 API Keys", expanded=not st.session_state.openai_key):
        oai = st.text_input(
            "OpenAI API Key",
            type="password",
            value=st.session_state.openai_key,
            placeholder="sk-...",
            key="oai_input",
        )
        gem = st.text_input(
            "Google Gemini API Key",
            type="password",
            value=st.session_state.gemini_key,
            placeholder="AIza...",
            key="gem_input",
        )
        tav = st.text_input(
            "Tavily API Key (optional — for web research)",
            type="password",
            value=st.session_state.tavily_key,
            placeholder="tvly-...",
            key="tav_input",
        )
        if st.button("Save Keys", key="save_keys"):
            st.session_state.openai_key = oai
            st.session_state.gemini_key = gem
            st.session_state.tavily_key = tav
            st.success("Keys saved.")

    st.markdown("---")

    # ── Pipeline live status ─────────────────────────────────────────────────
    st.markdown(
        '<div style="font-family:var(--font-mono);font-size:0.65rem;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:6px;">Pipeline</div>',
        unsafe_allow_html=True,
    )

    cur = st.session_state.current_node
    completed = set(st.session_state.completed_nodes)

    for nd in PIPELINE_ORDER:
        if nd == cur:
            cls = "active"
            icon = "▶"
        elif nd in completed:
            cls = "done"
            icon = "✓"
        else:
            cls = "pending"
            icon = "·"
        st.markdown(
            f'<div class="pipeline-node {cls}">{icon} {nd}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown(
        '<div style="font-family:var(--font-mono);font-size:0.65rem;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:8px;">Chat History</div>',
        unsafe_allow_html=True,
    )

    if st.button("＋  New Blog", key="new_chat", use_container_width=True):
        st.session_state.active_session   = None
        st.session_state.run_status       = "idle"
        st.session_state.final_md         = None
        st.session_state.plan_data        = None
        st.session_state.evidence_data    = []
        st.session_state.critique_data    = None
        st.session_state.node_log         = []
        st.session_state.sections_done    = []
        st.session_state.image_specs      = []
        st.session_state.current_node     = None
        st.session_state.completed_nodes  = []
        st.session_state.node_streams     = {}
        st.session_state.hitl_plan        = None
        st.session_state.current_thread_id= None
        st.rerun()

    sessions = list(reversed(st.session_state.sessions.values()))
    for s in sessions:
        sid = s["id"]
        is_active = sid == st.session_state.active_session
        status_icon = {"done": "✓", "running": "⟳", "error": "✗", "awaiting_hitl": "⏸"}.get(s["status"], "·")
        col_a, col_b = st.columns([5, 1])
        with col_a:
            if st.button(
                f"{status_icon}  {s['title']}",
                key=f"sess_{sid}",
                use_container_width=True,
                type="secondary" if not is_active else "primary",
            ):
                if sid != st.session_state.active_session:
                    st.session_state.active_session = sid
                    _load_session(sid)
                    st.rerun()
        with col_b:
            if st.button("✕", key=f"del_{sid}", help="Delete session"):
                del st.session_state.sessions[sid]
                if st.session_state.active_session == sid:
                    st.session_state.active_session = None
                    st.session_state.run_status = "idle"
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

# Drain queue on every render — this updates session_state from background thread
_drain_queue()

status = st.session_state.run_status

status_color = {
    "idle": "#6b7280", "running": "#e8c547", "awaiting_hitl": "#f59e0b",
    "done": "#34d399", "error": "#f87171"
}.get(status, "#6b7280")
status_label = {
    "idle": "Ready", "running": "Generating…", "awaiting_hitl": "⏸ Awaiting Approval",
    "done": "Complete", "error": "Error"
}.get(status, status)

st.markdown(
    f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem;">'
    f'<div><span style="font-family:var(--font-head);font-size:1.6rem;font-weight:900;color:#e8c547;">✍ BlogMind AI</span>'
    f'<span style="font-family:var(--font-mono);font-size:0.7rem;color:#6b7280;margin-left:12px;">multi-agent blog writer</span></div>'
    f'<div style="display:flex;align-items:center;gap:8px;font-family:var(--font-mono);font-size:0.75rem;">'
    f'<div style="width:8px;height:8px;border-radius:50%;background:{status_color};'
    f'{"animation:pulse 1s infinite;" if status in ("running","awaiting_hitl") else ""}"></div>'
    f'<span style="color:{status_color}">{status_label}</span></div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════════════════════
# IDLE — Input form
# ══════════════════════════════════════════════════════════════════════════════

if status == "idle":
    st.markdown('<div class="card card-accent">', unsafe_allow_html=True)
    topic = st.text_area(
        "What should the blog be about?",
        placeholder=(
            "e.g. 'How Transformer attention mechanisms work under the hood'\n"
            "'Latest developments in LLM reasoning — May 2025'\n"
            "'Building a RAG pipeline with LangChain and Chroma'"
        ),
        height=110,
        key="topic_input",
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        voice = st.selectbox("Voice", ["dev-advocate", "casual", "academic", "storytelling"], key="voice_sel")
    with c2:
        length = st.selectbox("Length", ["standard", "concise", "detailed"], key="len_sel")
    with c3:
        avoid = st.text_input("Avoid phrases (comma-sep)", placeholder="leverage, robust, seamless", key="avoid_inp")

    st.markdown("</div>", unsafe_allow_html=True)

    if st.button("Generate Blog ✦", key="generate_btn", use_container_width=True):
        if not topic.strip():
            st.error("Please enter a topic.")
        elif not st.session_state.openai_key and not st.session_state.get("oai_input"):
            st.error("OpenAI API key required — add it in the sidebar.")
        elif not st.session_state.gemini_key and not st.session_state.get("gem_input"):
            st.error("Gemini API key required for image generation — add it in the sidebar.")
        else:
            oai_key = st.session_state.get("oai_input") or st.session_state.openai_key
            gem_key = st.session_state.get("gem_input") or st.session_state.gemini_key
            tav_key = st.session_state.get("tav_input") or st.session_state.tavily_key

            # Persist keys
            st.session_state.openai_key = oai_key
            st.session_state.gemini_key = gem_key
            st.session_state.tavily_key = tav_key

            sid = _create_session(topic.strip())
            st.session_state.active_session = sid

            thread_id = f"blog-{sid}"
            st.session_state.current_thread_id = thread_id

            # Reset runtime state
            st.session_state.run_status     = "running"
            st.session_state.final_md       = None
            st.session_state.plan_data      = None
            st.session_state.evidence_data  = []
            st.session_state.critique_data  = None
            st.session_state.node_log       = []
            st.session_state.sections_done  = []
            st.session_state.image_specs    = []
            st.session_state.current_node   = None
            st.session_state.completed_nodes= []
            st.session_state.node_streams   = {}
            st.session_state.hitl_plan      = None

            q = Queue()
            st.session_state.queue = q

            t = threading.Thread(
                target=_run_agent,
                args=(topic.strip(), thread_id, q, oai_key, gem_key, tav_key),
                daemon=True,
            )
            t.start()
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE — Running / HITL / Done / Error
# ══════════════════════════════════════════════════════════════════════════════

elif status in ("running", "awaiting_hitl", "done", "error"):

    # ── Auto-refresh while running ────────────────────────────────────────────
    if status == "running":
        # Small sleep then rerun so the queue drain above picks up new events
        time.sleep(1.2)
        st.rerun()

    # ── Live node ticker (only while running) ─────────────────────────────────
    if status == "running":
        cur_node = st.session_state.current_node or "starting…"
        desc = NODE_DESCRIPTIONS.get(cur_node, "Processing…")
        st.markdown(
            f'<div class="live-node-ticker">'
            f'<div class="spinner"></div>'
            f'<div>'
            f'<div class="node-name">▶ {cur_node}</div>'
            f'<div class="node-desc">{desc}</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Progress bar
        completed_count = len(st.session_state.completed_nodes)
        pct = min(int(completed_count / len(PIPELINE_ORDER) * 100), 92)
        st.markdown(
            f'<div class="prog-bar"><div class="prog-fill" style="width:{pct}%"></div></div>',
            unsafe_allow_html=True,
        )

        # Section pills — show which sections are done
        if st.session_state.plan_data:
            tasks = st.session_state.plan_data.get("tasks", [])
            if tasks:
                pills = "".join([
                    f'<span class="section-pill{"  done" if t.get("id") in st.session_state.sections_done else ""}">'
                    f'{t.get("title","§")[:28]}</span>'
                    for t in tasks
                ])
                st.markdown(f'<div style="margin-bottom:1rem;">{pills}</div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # ⏸ HITL PLAN APPROVAL PANEL
    # ══════════════════════════════════════════════════════════════════════════
    if status == "awaiting_hitl":
        plan = st.session_state.hitl_plan or {}

        st.markdown(
            '<div class="hitl-panel">'
            '<div class="hitl-header">'
            '<span class="hitl-pulse"></span>'
            'Plan Review Required — Approve to start writing'
            '</div>',
            unsafe_allow_html=True,
        )

        blog_title = plan.get("blog_title", "Untitled")
        audience   = plan.get("audience", "")
        tone       = plan.get("tone", "")
        blog_kind  = plan.get("blog_kind", "")

        st.markdown(
            f'<div style="font-family:var(--font-head);font-size:1.4rem;font-weight:700;'
            f'color:#e8c547;margin-bottom:6px;">{blog_title}</div>'
            f'<div style="font-size:0.8rem;color:#6b7280;margin-bottom:16px;">'
            f'Audience: <b style="color:#e2e4ec">{audience}</b> · '
            f'Tone: <b style="color:#e2e4ec">{tone}</b> · '
            f'Kind: <b style="color:#e2e4ec">{blog_kind}</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        tasks = plan.get("tasks", [])
        if tasks:
            table_rows = "".join([
                f'<tr>'
                f'<td style="color:#6b7280;font-family:var(--font-mono);font-size:0.78rem;">#{t.get("id","")}</td>'
                f'<td style="font-weight:500;padding-right:12px;">{t.get("title","")}</td>'
                f'<td style="color:#6b7280;font-size:0.78rem;">{t.get("goal","")}</td>'
                f'<td style="font-family:var(--font-mono);color:#e8c547;white-space:nowrap;">~{t.get("target_words","")}w</td>'
                f'<td style="font-size:0.72rem;color:#6b7280;">'
                f'{"💻" if t.get("requires_code") else ""}{"📚" if t.get("requires_citations") else ""}{"🔍" if t.get("requires_research") else ""}'
                f'</td>'
                f'</tr>'
                for t in tasks
            ])
            st.markdown(
                f'<table class="plan-table"><thead><tr>'
                f'<th>#</th><th>Section</th><th>Goal</th><th>Words</th><th>Flags</th>'
                f'</tr></thead><tbody>{table_rows}</tbody></table>',
                unsafe_allow_html=True,
            )

        st.markdown('</div>', unsafe_allow_html=True)

        # Bullets preview per section
        with st.expander("📋 View full section bullets", expanded=False):
            for t in tasks:
                st.markdown(f"**{t.get('title','')}**")
                for b in t.get("bullets", []):
                    st.markdown(f"  - {b}")

        st.markdown("<br>", unsafe_allow_html=True)

        col_ap, col_ab, col_ed = st.columns([2, 1, 2])

        with col_ap:
            if st.button("✓ Approve — Start Writing", key="hitl_approve", use_container_width=True):
                sid = st.session_state.active_session
                thread_id = st.session_state.current_thread_id or f"blog-{sid}"
                q = Queue()
                st.session_state.queue = q
                st.session_state.run_status = "running"
                st.session_state.hitl_plan = None
                # Don't reset completed_nodes — router/orchestrator already done
                t = threading.Thread(
                    target=_run_agent_hitl_resume,
                    args=(
                        thread_id, q, {"approved": True},
                        st.session_state.openai_key,
                        st.session_state.gemini_key,
                        st.session_state.tavily_key,
                    ),
                    daemon=True,
                )
                t.start()
                st.rerun()

        with col_ab:
            if st.button("✕ Reject & Abort", key="hitl_abort", type="secondary", use_container_width=True):
                sid = st.session_state.active_session
                thread_id = st.session_state.current_thread_id or f"blog-{sid}"
                q = Queue()
                st.session_state.queue = q
                st.session_state.run_status = "running"
                st.session_state.hitl_plan = None
                t = threading.Thread(
                    target=_run_agent_hitl_resume,
                    args=(
                        thread_id, q, {"approved": False, "abort": True},
                        st.session_state.openai_key,
                        st.session_state.gemini_key,
                        st.session_state.tavily_key,
                    ),
                    daemon=True,
                )
                t.start()
                st.rerun()

        with col_ed:
            with st.expander("✎ Edit plan JSON"):
                plan_json = st.text_area(
                    "Edit plan JSON then submit",
                    value=json.dumps(plan, indent=2),
                    height=260,
                    key="plan_edit_json",
                )
                if st.button("Submit Edits →", key="hitl_edit_submit"):
                    try:
                        edited = json.loads(plan_json)
                        sid = st.session_state.active_session
                        thread_id = st.session_state.current_thread_id or f"blog-{sid}"
                        q = Queue()
                        st.session_state.queue = q
                        st.session_state.run_status = "running"
                        st.session_state.hitl_plan = None
                        t = threading.Thread(
                            target=_run_agent_hitl_resume,
                            args=(
                                thread_id, q,
                                {"approved": False, "edited_plan": edited},
                                st.session_state.openai_key,
                                st.session_state.gemini_key,
                                st.session_state.tavily_key,
                            ),
                            daemon=True,
                        )
                        t.start()
                        st.rerun()
                    except json.JSONDecodeError as e:
                        st.error(f"Invalid JSON: {e}")

        st.markdown("---")

    # ══════════════════════════════════════════════════════════════════════════
    # ERROR banner
    # ══════════════════════════════════════════════════════════════════════════
    if status == "error":
        err_entries = [e for e in st.session_state.node_log if e["level"] == "error"]
        if err_entries:
            st.markdown(
                f'<div class="card card-red" style="margin-bottom:1rem;">'
                f'<div style="font-family:var(--font-mono);font-size:0.75rem;color:#f87171;">'
                f'⚠ Error at node: {err_entries[-1]["node"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            with st.expander("🔴 Error Detail"):
                st.code(err_entries[-1]["message"], language="python")

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN TABS — shown in all non-idle states
    # ══════════════════════════════════════════════════════════════════════════
    tab_blog, tab_plan, tab_nodes, tab_evidence, tab_images, tab_critic, tab_logs = st.tabs([
        "📄 Blog", "🗺 Plan", "⚡ Live Nodes", "🔗 Citations", "🖼 Images", "🧠 Critique", "📋 Logs"
    ])

    # ── TAB: BLOG ────────────────────────────────────────────────────────────
    with tab_blog:
        if st.session_state.final_md:
            _col_dl, _col_sp = st.columns([1, 4])
            with _col_dl:
                _download_button("⬇ Download .md", st.session_state.final_md, "blog.md")
            st.markdown('<div class="blog-body">', unsafe_allow_html=True)
            st.markdown(st.session_state.final_md)
            st.markdown("</div>", unsafe_allow_html=True)
        elif status in ("running", "awaiting_hitl"):
            st.markdown(
                '<div class="card" style="text-align:center;padding:3rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">'
                'Blog is being written… check the Live Nodes tab for progress.'
                '</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:3rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">No blog generated yet.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── TAB: PLAN ────────────────────────────────────────────────────────────
    with tab_plan:
        plan = st.session_state.plan_data
        if plan:
            st.markdown(
                f'<div class="card card-accent">'
                f'<div style="font-family:var(--font-head);font-size:1.4rem;font-weight:700;color:#e8c547;">'
                f'{plan.get("blog_title","")}</div>'
                f'<div style="font-size:0.8rem;color:#6b7280;margin-top:4px;">'
                f'Audience: <b style="color:#e2e4ec">{plan.get("audience","")}</b> · '
                f'Tone: <b style="color:#e2e4ec">{plan.get("tone","")}</b> · '
                f'Kind: <b style="color:#e2e4ec">{plan.get("blog_kind","")}</b>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            tasks = plan.get("tasks", [])
            sections_done = st.session_state.sections_done

            for t in tasks:
                tid = t.get("id", 0)
                is_done = tid in sections_done
                icon = "✓" if is_done else ("▶" if status == "running" else "○")
                flags = " ".join(filter(None, [
                    "🔍" if t.get("requires_research") else "",
                    "📚" if t.get("requires_citations") else "",
                    "💻" if t.get("requires_code") else "",
                ]))

                with st.expander(f"{icon}  {t.get('title','Section')}  {flags}", expanded=False):
                    st.markdown(
                        f'<div style="font-size:0.82rem;color:#6b7280;margin-bottom:8px;">'
                        f'Goal: {t.get("goal","")}</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"**Target words:** `{t.get('target_words','?')}`")
                    bullets = t.get("bullets", [])
                    if bullets:
                        st.markdown("**Bullets:**")
                        for b in bullets:
                            st.markdown(f"- {b}")
                    tags = t.get("tags", [])
                    if tags:
                        st.markdown(
                            " ".join(
                                f'<span class="section-pill{"  done" if is_done else ""}">{tg}</span>'
                                for tg in tags
                            ),
                            unsafe_allow_html=True,
                        )

            constraints = plan.get("constraints", [])
            if constraints:
                st.markdown("**Constraints:**")
                for c in constraints:
                    st.markdown(f"- `{c}`")
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:2rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">Plan will appear here once the orchestrator runs.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── TAB: LIVE NODES ──────────────────────────────────────────────────────
    with tab_nodes:
        cur = st.session_state.current_node
        completed_set = set(st.session_state.completed_nodes)

        # Visual pipeline flow
        st.markdown("**Pipeline Progress**")
        cols = st.columns(len(PIPELINE_ORDER))
        for i, nd in enumerate(PIPELINE_ORDER):
            with cols[i]:
                if nd == cur:
                    color = "#e8c547"
                    icon = "▶"
                    bg = "rgba(232,197,71,0.12)"
                    border = "1px solid #e8c547"
                elif nd in completed_set:
                    color = "#34d399"
                    icon = "✓"
                    bg = "rgba(52,211,153,0.08)"
                    border = "1px solid #34d399"
                else:
                    color = "#6b7280"
                    icon = "·"
                    bg = "transparent"
                    border = "1px solid #2a2f42"

                st.markdown(
                    f'<div style="background:{bg};border:{border};border-radius:6px;'
                    f'padding:6px 4px;text-align:center;margin-bottom:4px;">'
                    f'<div style="font-size:0.9rem;color:{color};">{icon}</div>'
                    f'<div style="font-family:var(--font-mono);font-size:0.6rem;color:{color};'
                    f'word-break:break-all;">{nd.replace("_","_​")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("---")

        # Node detail inspector
        selected_node = st.selectbox(
            "Inspect a node",
            options=PIPELINE_ORDER,
            index=PIPELINE_ORDER.index(cur) if cur and cur in PIPELINE_ORDER else 0,
            key="node_select",
            format_func=lambda n: (
                f"▶ {n} (active)" if n == cur else
                f"✓ {n} (done)" if n in completed_set else
                f"· {n}"
            ),
        )

        desc = NODE_DESCRIPTIONS.get(selected_node, "")
        is_cur = selected_node == cur
        st.markdown(
            f'<div class="card {"card-accent" if is_cur else ""}">'
            f'<div class="node-badge {"active" if is_cur else ""}">'
            f'{"▶ " if is_cur else ""}{selected_node}</div>'
            f'<div style="margin-top:8px;font-size:0.85rem;color:#e2e4ec;">{desc}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        node_entries = [e for e in st.session_state.node_log if e["node"] == selected_node]
        if node_entries:
            st.markdown("**Activity log for this node:**")
            for e in node_entries:
                st.markdown(
                    f'<div class="log-line {e["level"]}">[{e["ts"]}] {e["message"]}</div>',
                    unsafe_allow_html=True,
                )

    # ── TAB: CITATIONS ───────────────────────────────────────────────────────
    with tab_evidence:
        evidence = st.session_state.evidence_data
        if evidence:
            st.markdown(
                f'<div style="font-family:var(--font-mono);font-size:0.7rem;color:#6b7280;margin-bottom:10px;">'
                f'{len(evidence)} sources collected</div>',
                unsafe_allow_html=True,
            )
            for i, ev in enumerate(evidence, 1):
                title   = ev.get("title", "Untitled")
                url     = ev.get("url", "#")
                snippet = ev.get("snippet", "")
                pub     = ev.get("published_at") or ""
                source  = ev.get("source") or ""
                st.markdown(
                    f'<div class="citation">'
                    f'<div style="font-weight:500;margin-bottom:4px;">'
                    f'<span style="color:#6b7280;font-family:var(--font-mono);font-size:0.7rem;">[{i}]</span> '
                    f'<a href="{url}" target="_blank">{title}</a></div>'
                    f'{"<div class=citation-date>" + pub + ("  ·  " + source if source else "") + "</div>" if pub or source else ""}'
                    f'{"<div style=color:#6b7280;font-size:0.78rem;margin-top:4px;>" + snippet[:200] + ("…" if len(snippet)>200 else "") + "</div>" if snippet else ""}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:2rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">'
                'Evidence appears here when web research mode is active (hybrid or open_book topics).'
                '</div></div>',
                unsafe_allow_html=True,
            )

    # ── TAB: IMAGES ──────────────────────────────────────────────────────────
    with tab_images:
        image_specs = st.session_state.image_specs
        images_dir = Path("images")

        if image_specs:
            st.markdown(
                f'<div style="font-family:var(--font-mono);font-size:0.7rem;color:#6b7280;margin-bottom:10px;">'
                f'{len(image_specs)} images planned</div>',
                unsafe_allow_html=True,
            )
            for spec in image_specs:
                fname       = spec.get("filename", "")
                img_path    = images_dir / fname
                alt         = spec.get("alt", "")
                caption     = spec.get("caption", "")
                placeholder = spec.get("placeholder", "")

                st.markdown(
                    f'<div class="card card-blue">'
                    f'<div style="font-family:var(--font-mono);font-size:0.72rem;color:#4fc3f7;">{placeholder}</div>'
                    f'<div style="font-weight:500;margin:4px 0;">{alt}</div>'
                    f'<div style="font-size:0.78rem;color:#6b7280;font-style:italic;">{caption}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if img_path.exists():
                    st.image(str(img_path), caption=caption, use_column_width=True)
                else:
                    st.markdown(
                        f'<div style="background:var(--surface2);border:1px dashed var(--border);'
                        f'border-radius:6px;padding:2rem;text-align:center;'
                        f'font-family:var(--font-mono);font-size:0.75rem;color:#6b7280;">'
                        f'Generating… {fname}</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:2rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">Image specs appear after the decide_images node runs.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── TAB: CRITIQUE ────────────────────────────────────────────────────────
    with tab_critic:
        critique = st.session_state.critique_data
        if critique:
            overall = critique.get("overall_score", 0)
            passed  = critique.get("overall_pass", False)
            rewrite_count = st.session_state.sessions.get(
                st.session_state.active_session, {}
            ).get("plan", {}) and len([e for e in st.session_state.node_log if "REWRITE" in e.get("message","")])

            col_s, col_p, col_r = st.columns(3)
            col_s.metric("Overall Score", f"{overall:.1f}/10")
            col_p.metric("Verdict", "PASS ✓" if passed else "REWRITE ↻")
            col_r.metric("Rewrites triggered", len([
                e for e in st.session_state.node_log if "REWRITE" in e.get("message", "")
            ]))

            scores = critique.get("scores", [])
            if scores:
                st.markdown("---")
                for sc in scores:
                    dim      = sc.get("dimension", "")
                    score    = sc.get("score", 0)
                    rationale= sc.get("rationale", "")
                    fix      = sc.get("fix", "")
                    pct      = score / 10 * 100
                    color    = "#34d399" if score >= 7 else "#f59e0b" if score >= 5 else "#f87171"
                    st.markdown(
                        f'<div class="card" style="margin-bottom:8px;">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<div style="font-family:var(--font-mono);font-size:0.8rem;font-weight:600;">{dim}</div>'
                        f'<div style="font-family:var(--font-mono);color:{color};font-weight:700;">{score}/10</div>'
                        f'</div>'
                        f'<div class="prog-bar" style="margin:6px 0;">'
                        f'<div class="prog-fill" style="width:{pct}%;background:{color};"></div></div>'
                        f'<div style="font-size:0.78rem;color:#6b7280;">{rationale}</div>'
                        f'{"<div style=font-size:0.75rem;color:#f59e0b;margin-top:4px;>💡 " + fix + "</div>" if fix and not passed else ""}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            rewrite_instructions = critique.get("rewrite_instructions", "")
            if rewrite_instructions and rewrite_instructions != "PASS":
                st.markdown("**Rewrite Instructions sent to workers:**")
                st.markdown(
                    f'<div class="stream-box" style="color:#f59e0b;">{rewrite_instructions}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:2rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">Critique scores appear after the critic_node runs.</div>'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── TAB: LOGS ────────────────────────────────────────────────────────────
    with tab_logs:
        logs = st.session_state.node_log

        col_f, col_cl = st.columns([4, 1])
        with col_f:
            filter_level = st.selectbox(
                "Filter by level",
                ["all", "info", "success", "warn", "error"],
                key="log_filter",
            )
        with col_cl:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Clear", key="clear_logs"):
                st.session_state.node_log = []
                st.rerun()

        filtered = logs if filter_level == "all" else [e for e in logs if e["level"] == filter_level]

        if filtered:
            log_html = "".join([
                f'<div class="log-line {e["level"]}">'
                f'[{e["ts"]}] <span style="opacity:0.5">[{e["node"]}]</span> {e["message"]}'
                f'</div>'
                for e in reversed(filtered)
            ])
            st.markdown(
                f'<div style="background:var(--surface2);border:1px solid var(--border);'
                f'border-radius:6px;padding:12px 14px;max-height:500px;overflow-y:auto;">'
                f'{log_html}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="card" style="text-align:center;padding:1.5rem;">'
                '<div style="font-family:var(--font-mono);color:#6b7280;">No logs yet.</div>'
                '</div>',
                unsafe_allow_html=True,
            )