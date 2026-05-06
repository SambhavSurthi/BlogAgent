from __future__ import annotations

# ============================================================
# Blog Writer — Enhanced Backend
#
# Architecture:
#   Router → (Research?) → Orchestrator → [HITL: plan approval]
#   → Fanout Workers → Reducer Subgraph
#       (merge_content → critic_node → decide_images → generate_and_place_images)
#
# Features:
#   1. Self-Critique + Rewrite Loop  (critic_node, rewrite routing, iteration cap)
#   2. Human-in-the-Loop on Plan    (interrupt_before="human_plan_review")
#   3. SQLite Checkpointing         (SqliteSaver passed to app.compile())
#   4. Style Profile / Persona      (StyleProfile schema, injected into workers)
# ============================================================

import operator
import os
import re
import base64
import json
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict, List, Optional, Literal, Annotated

from pydantic import BaseModel, Field
from openai import OpenAI

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

load_dotenv(override=False)

# ============================================================
# SECTION 1: Schemas
# ============================================================

class Task(BaseModel):
    id: int
    title: str
    goal: str = Field(..., description="One sentence describing what the reader should do/understand.")
    bullets: List[str] = Field(..., min_length=3, max_length=6)
    target_words: int = Field(..., description="Target words (120–550).")
    tags: List[str] = Field(default_factory=list)
    requires_research: bool = False
    requires_citations: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str
    audience: str
    tone: str
    blog_kind: Literal["explainer", "tutorial", "news_roundup", "comparison", "system_design"] = "explainer"
    constraints: List[str] = Field(default_factory=list)
    tasks: List[Task]


class EvidenceItem(BaseModel):
    title: str
    url: str
    published_at: Optional[str] = None
    snippet: Optional[str] = None
    source: Optional[str] = None


class RouterDecision(BaseModel):
    needs_research: bool
    mode: Literal["closed_book", "hybrid", "open_book"]
    reason: str
    queries: List[str] = Field(default_factory=list)
    max_results_per_query: int = Field(5)


class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(default_factory=list)


class ImageSpec(BaseModel):
    placeholder: str = Field(..., description="e.g. [[IMAGE_1]]")
    filename: str = Field(..., description="Save under images/, e.g. qkv_flow.png")
    alt: str
    caption: str
    prompt: str = Field(..., description="Prompt to send to the image model.")
    size: Literal["1024x1024", "1024x1536", "1536x1024"] = "1024x1024"
    quality: Literal["low", "medium", "high"] = "low"


class GlobalImagePlan(BaseModel):
    md_with_placeholders: str
    images: List[ImageSpec] = Field(default_factory=list)


# ============================================================
# SECTION 2: Style Profile
# ============================================================

class StyleProfile(BaseModel):
    voice: Literal["casual", "academic", "dev-advocate", "storytelling"] = "dev-advocate"
    preferred_length: Literal["concise", "standard", "detailed"] = "standard"
    avoid_phrases: List[str] = Field(default_factory=list)
    output_format: Literal["markdown", "html", "notion"] = "markdown"
    custom_instructions: Optional[str] = None

    def as_prompt_block(self) -> str:
        lines = [
            "=== STYLE PROFILE ===",
            f"Voice: {self.voice}",
            f"Length preference: {self.preferred_length}",
            f"Output format: {self.output_format}",
        ]
        if self.avoid_phrases:
            lines.append(f"NEVER use these words/phrases: {', '.join(self.avoid_phrases)}")
        if self.custom_instructions:
            lines.append(f"Additional instructions: {self.custom_instructions}")
        lines.append("=====================")
        return "\n".join(lines)


# ============================================================
# SECTION 3: Critic Schemas
# ============================================================

class SectionScore(BaseModel):
    dimension: str
    score: int = Field(..., ge=1, le=10)
    rationale: str
    fix: str = Field(..., description="One concrete sentence on how to improve.")


class CritiqueReport(BaseModel):
    scores: List[SectionScore]
    overall_score: float = Field(..., description="Weighted average, 1–10.")
    overall_pass: bool = Field(..., description="True if overall_score >= threshold.")
    rewrite_instructions: str = Field(
        ...,
        description="Compact, actionable instructions for the workers on the rewrite pass."
    )


# ============================================================
# SECTION 4: Main Graph State
# ============================================================

class State(TypedDict):
    topic: str

    # routing / research
    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[EvidenceItem]
    plan: Optional[Plan]

    # recency
    as_of: str
    recency_days: int

    # Style profile
    style_profile: Optional[dict]

    # workers
    sections: Annotated[List[tuple[int, str]], operator.add]

    # critic loop
    critique: Optional[dict]
    rewrite_count: int

    # reducer / image
    merged_md: str
    md_with_placeholders: str
    image_specs: List[dict]

    final: str


# ============================================================
# SECTION 5: Constants & LLM
# ============================================================

MAX_REWRITES = 2
CRITIC_PASS_THRESHOLD = 7.0

_llm_instance = None

def _get_llm():
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = ChatOpenAI(model="gpt-4.1-mini")
    return _llm_instance

def reset_llm():
    global _llm_instance
    _llm_instance = None


# ============================================================
# SECTION 6: Router
# ============================================================

ROUTER_SYSTEM = """You are a routing module for a technical blog planner.

Decide whether web research is needed BEFORE planning.

Modes:
- closed_book (needs_research=false): evergreen concepts.
- hybrid (needs_research=true): evergreen + needs up-to-date examples/tools/models.
- open_book (needs_research=true): volatile weekly/news/"latest"/pricing/policy.

If needs_research=true:
- Output 3–10 high-signal, scoped queries.
- For open_book weekly roundup, include queries reflecting last 7 days.
"""

def router_node(state: State) -> dict:
    decider = _get_llm().with_structured_output(RouterDecision)
    decision = decider.invoke([
        SystemMessage(content=ROUTER_SYSTEM),
        HumanMessage(content=f"Topic: {state['topic']}\nAs-of date: {state['as_of']}"),
    ])

    recency_days = {"open_book": 7, "hybrid": 45}.get(decision.mode, 3650)

    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
        "recency_days": recency_days,
    }

def route_after_router(state: State) -> str:
    return "research" if state["needs_research"] else "orchestrator"


# ============================================================
# SECTION 7: Research (Tavily)
# ============================================================

def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
    if not os.getenv("TAVILY_API_KEY"):
        return []
    try:
        from langchain_community.tools.tavily_search import TavilySearchResults
        tool = TavilySearchResults(max_results=max_results)
        results = tool.invoke({"query": query})
        return [
            {
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "snippet": r.get("content") or r.get("snippet") or "",
                "published_at": r.get("published_date") or r.get("published_at"),
                "source": r.get("source"),
            }
            for r in (results or [])
        ]
    except Exception:
        return []

def _iso_to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

RESEARCH_SYSTEM = """You are a research synthesizer.

Given raw web search results, produce EvidenceItem objects.

Rules:
- Only include items with a non-empty url.
- Prefer relevant + authoritative sources.
- Normalize published_at to ISO YYYY-MM-DD if reliably inferable; else null (do NOT guess).
- Keep snippets short.
- Deduplicate by URL.
"""

def research_node(state: State) -> dict:
    queries = (state.get("queries") or [])[:10]
    raw: List[dict] = []
    for q in queries:
        raw.extend(_tavily_search(q, max_results=6))

    if not raw:
        return {"evidence": []}

    extractor = _get_llm().with_structured_output(EvidencePack)
    pack = extractor.invoke([
        SystemMessage(content=RESEARCH_SYSTEM),
        HumanMessage(content=(
            f"As-of date: {state['as_of']}\n"
            f"Recency days: {state['recency_days']}\n\n"
            f"Raw results:\n{raw}"
        )),
    ])

    dedup = {e.url: e for e in pack.evidence if e.url}
    evidence = list(dedup.values())

    if state.get("mode") == "open_book":
        as_of = date.fromisoformat(state["as_of"])
        cutoff = as_of - timedelta(days=int(state["recency_days"]))
        evidence = [e for e in evidence if (d := _iso_to_date(e.published_at)) and d >= cutoff]

    return {"evidence": evidence}


# ============================================================
# SECTION 8: Orchestrator
# ============================================================

ORCH_SYSTEM = """You are a senior technical writer and developer advocate.
Produce a highly actionable outline for a technical blog post.

Requirements:
- 5–9 tasks, each with goal + 3–6 bullets + target_words.
- Tags are flexible; do not force a fixed taxonomy.

Grounding:
- closed_book: evergreen, no evidence dependence.
- hybrid: use evidence for up-to-date examples; mark those tasks requires_research=True and requires_citations=True.
- open_book: weekly/news roundup:
  - Set blog_kind="news_roundup"
  - No tutorial content unless requested
  - If evidence is weak, plan should explicitly reflect that (don't invent events).

Output must match Plan schema.
"""

def orchestrator_node(state: State) -> dict:
    planner = _get_llm().with_structured_output(Plan)
    mode = state.get("mode", "closed_book")
    evidence = state.get("evidence", [])
    forced_kind = "news_roundup" if mode == "open_book" else None

    plan = planner.invoke([
        SystemMessage(content=ORCH_SYSTEM),
        HumanMessage(content=(
            f"Topic: {state['topic']}\n"
            f"Mode: {mode}\n"
            f"As-of: {state['as_of']} (recency_days={state['recency_days']})\n"
            f"{'Force blog_kind=news_roundup' if forced_kind else ''}\n\n"
            f"Evidence:\n{[e.model_dump() for e in evidence][:16]}"
        )),
    ])
    if forced_kind:
        plan.blog_kind = "news_roundup"

    return {"plan": plan}


# ============================================================
# SECTION 9: Human-in-the-Loop — Plan Review
#
# interrupt() raises GraphInterrupt which must be caught by the
# streaming loop in the frontend. The frontend resumes via:
#   app.invoke(Command(resume={...}), config=config)
# ============================================================

def human_plan_review(state: State) -> dict:
    plan = state["plan"]
    assert plan is not None

    plan_summary = {
        "blog_title": plan.blog_title,
        "audience": plan.audience,
        "tone": plan.tone,
        "blog_kind": plan.blog_kind,
        "constraints": plan.constraints,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "goal": t.goal,
                "target_words": t.target_words,
                "requires_code": t.requires_code,
                "requires_citations": t.requires_citations,
                "bullets": t.bullets,
                "tags": t.tags,
            }
            for t in plan.tasks
        ],
    }

    # INTERRUPT: execution pauses here until the host resumes
    human_response = interrupt({
        "action": "review_plan",
        "plan": plan_summary,
    })

    if human_response.get("abort"):
        raise RuntimeError("Run aborted by human reviewer.")

    if not human_response.get("approved") and human_response.get("edited_plan"):
        edited = Plan(**human_response["edited_plan"])
        return {"plan": edited}

    return {}


# ============================================================
# SECTION 10: Fanout
# ============================================================

def fanout(state: State):
    assert state["plan"] is not None

    critique = state.get("critique")
    rewrite_instructions = (
        CritiqueReport(**critique).rewrite_instructions if critique else None
    )

    return [
        Send(
            "worker",
            {
                "task": task.model_dump(),
                "topic": state["topic"],
                "mode": state["mode"],
                "as_of": state["as_of"],
                "recency_days": state["recency_days"],
                "plan": state["plan"].model_dump(),
                "evidence": [e.model_dump() for e in state.get("evidence", [])],
                "style_profile": state.get("style_profile"),
                "rewrite_instructions": rewrite_instructions,
            },
        )
        for task in state["plan"].tasks
    ]


# ============================================================
# SECTION 11: Worker
# ============================================================

WORKER_SYSTEM = """You are a senior technical writer and developer advocate.
Write ONE section of a technical blog post in Markdown.

Constraints:
- Cover ALL bullets in order.
- Target words ±15%.
- Output only section markdown starting with "## <Section Title>".
- No filler openers ("In this section…", "Today we'll…").
- No closing summary paragraphs unless the task explicitly asks for a conclusion.

Scope guard:
- If blog_kind=="news_roundup", do NOT drift into tutorials.
  Focus on events + implications.

Grounding:
- If mode=="open_book": do not introduce any specific event/company/model/funding/policy
  claim unless supported by provided Evidence URLs.
  For each supported claim, attach a Markdown link ([Source](URL)).
  If unsupported, write "Not found in provided sources."
- If requires_citations==true (hybrid tasks): cite Evidence URLs for external claims.

Code:
- If requires_code==true, include at least one minimal snippet.
"""

def worker_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

    style_block = ""
    raw_style = payload.get("style_profile")
    if raw_style:
        style_block = "\n" + StyleProfile(**raw_style).as_prompt_block() + "\n"

    rewrite_block = ""
    rewrite_instructions = payload.get("rewrite_instructions")
    if rewrite_instructions:
        rewrite_block = (
            "\n=== REWRITE INSTRUCTIONS (from critic) ===\n"
            f"{rewrite_instructions}\n"
            "Apply these fixes while keeping everything else intact.\n"
            "==========================================\n"
        )

    bullets_text = "\n- " + "\n- ".join(task.bullets)
    evidence_text = "\n".join(
        f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}"
        for e in evidence[:20]
    )

    system_content = WORKER_SYSTEM + style_block + rewrite_block

    section_md = _get_llm().invoke([
        SystemMessage(content=system_content),
        HumanMessage(content=(
            f"Blog title: {plan.blog_title}\n"
            f"Audience: {plan.audience}\n"
            f"Tone: {plan.tone}\n"
            f"Blog kind: {plan.blog_kind}\n"
            f"Constraints: {plan.constraints}\n"
            f"Topic: {payload['topic']}\n"
            f"Mode: {payload.get('mode')}\n"
            f"As-of: {payload.get('as_of')} (recency_days={payload.get('recency_days')})\n\n"
            f"Section title: {task.title}\n"
            f"Goal: {task.goal}\n"
            f"Target words: {task.target_words}\n"
            f"Tags: {task.tags}\n"
            f"requires_research: {task.requires_research}\n"
            f"requires_citations: {task.requires_citations}\n"
            f"requires_code: {task.requires_code}\n"
            f"Bullets:{bullets_text}\n\n"
            f"Evidence (ONLY cite these URLs):\n{evidence_text}\n"
        )),
    ]).content.strip()

    return {"sections": [(task.id, section_md)]}


# ============================================================
# SECTION 12: Reducer Subgraph
# ============================================================

def merge_content(state: State) -> dict:
    plan = state["plan"]
    if plan is None:
        raise ValueError("merge_content called without plan.")
    ordered = [md for _, md in sorted(state["sections"], key=lambda x: x[0])]
    body = "\n\n".join(ordered).strip()
    merged_md = f"# {plan.blog_title}\n\n{body}\n"
    return {"merged_md": merged_md}


CRITIC_SYSTEM = """You are a ruthlessly honest senior technical editor.
You are reviewing a COMPLETE draft of a technical blog post.

Score the draft on exactly these 5 dimensions (1–10 each):

1. depth           — Does each section adequately explain the concept? No hand-waving?
2. citation_density — Are external claims backed by URLs where required?
3. flow            — Does the post read coherently from section to section?
4. seo             — Is the title compelling? Are keywords used naturally?
5. tone_match      — Does the writing voice match the stated audience and tone?

Rules:
- Be strict. A score of 8 means genuinely good. 10 means publish-ready with no changes.
- overall_score = weighted average (depth×0.3, citation×0.2, flow×0.2, seo×0.15, tone×0.15).
- overall_pass = True  if overall_score >= """ + str(CRITIC_PASS_THRESHOLD) + """.
- rewrite_instructions: if overall_pass=False, write a tight, numbered list of the most
  important fixes (max 6 items). Be concrete — reference section titles and specific issues.
  If overall_pass=True, set rewrite_instructions="PASS".
"""

def critic_node(state: State) -> dict:
    plan = state["plan"]
    assert plan is not None

    critic = _get_llm().with_structured_output(CritiqueReport)
    report = critic.invoke([
        SystemMessage(content=CRITIC_SYSTEM),
        HumanMessage(content=(
            f"Blog title: {plan.blog_title}\n"
            f"Audience: {plan.audience}\n"
            f"Tone: {plan.tone}\n"
            f"Blog kind: {plan.blog_kind}\n"
            f"Mode: {state.get('mode', 'closed_book')}\n\n"
            f"=== DRAFT ===\n{state['merged_md']}"
        )),
    ])

    print(f"\n[CRITIC] Overall score: {report.overall_score:.1f}/10 | Pass: {report.overall_pass}")

    return {
        "critique": report.model_dump(),
        "rewrite_count": state.get("rewrite_count", 0),
    }

def route_after_critic(state: State) -> str:
    critique = state.get("critique")
    rewrite_count = state.get("rewrite_count", 0)

    if critique and not critique["overall_pass"] and rewrite_count < MAX_REWRITES:
        return "rewrite"
    return "decide_images"

def increment_rewrite_count(state: State) -> dict:
    return {
        "rewrite_count": state.get("rewrite_count", 0) + 1,
        "sections": [],
    }


# ---- Image Nodes ----

DECIDE_IMAGES_SYSTEM = """You are an expert technical editor.
Decide if images/diagrams are needed for THIS blog.

Rules:
- Max 3 images total.
- Each image must materially improve understanding (diagram/flow/table-like visual).
- Insert placeholders exactly: [[IMAGE_1]], [[IMAGE_2]], [[IMAGE_3]].
- If no images needed: md_with_placeholders must equal input and images=[].
- Avoid decorative images; prefer technical diagrams with short labels.
Return strictly GlobalImagePlan.
"""

def decide_images(state: State) -> dict:
    planner = _get_llm().with_structured_output(GlobalImagePlan)
    merged_md = state["merged_md"]
    plan = state["plan"]
    assert plan is not None

    image_plan = planner.invoke([
        SystemMessage(content=DECIDE_IMAGES_SYSTEM),
        HumanMessage(content=(
            f"Blog kind: {plan.blog_kind}\n"
            f"Topic: {state['topic']}\n\n"
            "Insert placeholders + propose image prompts.\n\n"
            f"{merged_md}"
        )),
    ])

    return {
        "md_with_placeholders": image_plan.md_with_placeholders,
        "image_specs": [img.model_dump() for img in image_plan.images],
    }

def _openai_generate_image_bytes(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "low",
) -> bytes:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=api_key)
    effective_prompt = (
        f"{prompt}\nStyle: technical diagram, clear labels, white background. "
        f"Low-resolution draft, minimal detail, clean layout."
    )

    response = client.images.generate(
        model="gpt-image-1",
        prompt=effective_prompt,
        size=size,
        quality=quality,
    )

    item = response.data[0]
    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)

    if getattr(item, "url", None):
        req = urllib.request.Request(item.url, headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read()

    raise RuntimeError("OpenAI image response did not include image bytes.")


def _safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def generate_and_place_images(state: State) -> dict:
    plan = state["plan"]
    assert plan is not None

    md = state.get("md_with_placeholders") or state["merged_md"]
    image_specs = state.get("image_specs", []) or []

    if not image_specs:
        filename = f"{_safe_slug(plan.blog_title)}.md"
        Path(filename).write_text(md, encoding="utf-8")
        return {"final": md}

    images_dir = Path("images")
    images_dir.mkdir(exist_ok=True)

    for spec in image_specs:
        placeholder = spec["placeholder"]
        filename = spec["filename"]
        out_path = images_dir / filename

        if not out_path.exists():
            try:
                img_bytes = _openai_generate_image_bytes(
                    prompt=spec["prompt"],
                    size=spec.get("size", "1024x1024"),
                    quality="low",
                )
                out_path.write_bytes(img_bytes)
            except Exception as e:
                prompt_block = (
                    f"> **[IMAGE GENERATION FAILED]** {spec.get('caption','')}\n>\n"
                    f"> **Alt:** {spec.get('alt','')}\n>\n"
                    f"> **Prompt:** {spec.get('prompt','')}\n>\n"
                    f"> **Error:** {e}\n"
                )
                md = md.replace(placeholder, prompt_block)
                continue

        img_md = f"![{spec['alt']}](images/{filename})\n*{spec['caption']}*"
        md = md.replace(placeholder, img_md)

    filename = f"{_safe_slug(plan.blog_title)}.md"
    Path(filename).write_text(md, encoding="utf-8")
    return {"final": md}


# ============================================================
# SECTION 13: Build Reducer Subgraph
# ============================================================

reducer_graph = StateGraph(State)
reducer_graph.add_node("merge_content", merge_content)
reducer_graph.add_node("critic_node", critic_node)
reducer_graph.add_node("decide_images", decide_images)
reducer_graph.add_node("generate_and_place_images", generate_and_place_images)

reducer_graph.add_edge(START, "merge_content")
reducer_graph.add_edge("merge_content", "critic_node")
reducer_graph.add_conditional_edges(
    "critic_node",
    route_after_critic,
    {
        "rewrite": END,
        "decide_images": "decide_images",
    }
)
reducer_graph.add_edge("decide_images", "generate_and_place_images")
reducer_graph.add_edge("generate_and_place_images", END)

reducer_subgraph = reducer_graph.compile()


# ============================================================
# SECTION 14: Build Main Graph
# ============================================================

def route_after_reducer(state: State) -> str:
    critique = state.get("critique")
    rewrite_count = state.get("rewrite_count", 0)

    if critique and not critique["overall_pass"] and rewrite_count < MAX_REWRITES:
        return "rewrite_prep"
    return END


g = StateGraph(State)

g.add_node("router", router_node)
g.add_node("research", research_node)
g.add_node("orchestrator", orchestrator_node)
g.add_node("human_plan_review", human_plan_review)
g.add_node("worker", worker_node)
g.add_node("reducer", reducer_subgraph)
g.add_node("rewrite_prep", increment_rewrite_count)

g.add_edge(START, "router")
g.add_conditional_edges(
    "router",
    route_after_router,
    {"research": "research", "orchestrator": "orchestrator"}
)
g.add_edge("research", "orchestrator")
g.add_edge("orchestrator", "human_plan_review")
g.add_conditional_edges("human_plan_review", fanout, ["worker"])
g.add_edge("worker", "reducer")
g.add_conditional_edges(
    "reducer",
    route_after_reducer,
    {"rewrite_prep": "rewrite_prep", END: END}
)
g.add_conditional_edges("rewrite_prep", fanout, ["worker"])


# ============================================================
# SECTION 15: Compile with SQLite Checkpointing
# ============================================================

DB_PATH = os.getenv("CHECKPOINT_DB", "blog_checkpoints.db")
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(_conn)
app = g.compile(checkpointer=checkpointer)


# ============================================================
# SECTION 16: Convenience Runner
# ============================================================

__all__ = ["app", "State", "StyleProfile", "reset_llm"]

def run_blog(
    topic: str,
    thread_id: str = "default",
    as_of: Optional[str] = None,
    style: Optional[StyleProfile] = None,
) -> str:
    if as_of is None:
        as_of = date.today().isoformat()
    if style is None:
        style = StyleProfile()

    initial_state: State = {
        "topic": topic,
        "as_of": as_of,
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
    result = app.invoke(initial_state, config=config)
    return result["final"]


# ============================================================
# SECTION 17: Quick CLI
# ============================================================

if __name__ == "__main__":
    import sys
    from langgraph.types import Command

    topic = " ".join(sys.argv[1:]) or "Understanding Transformer Attention Mechanisms"
    thread_id = "cli-run-001"
    config = {"configurable": {"thread_id": thread_id}}

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

    print(f"\n[BlogAgent] Topic: {topic}")

    try:
        result = app.invoke(initial_state, config=config)
        print("\n=== BLOG GENERATED ===")
        print(result["final"][:500], "...")
    except Exception as interrupt_exc:
        print(f"\n[HITL] Graph paused: {interrupt_exc}")
        snapshot = app.get_state(config)
        plan_data = snapshot.values.get("plan")
        if plan_data:
            print(f"\n[HITL] Proposed Plan: {plan_data.blog_title}")
            for t in plan_data.tasks:
                print(f"    [{t.id}] {t.title} (~{t.target_words}w)")

        answer = input("\nApprove plan? [y/n]: ").strip().lower()
        if answer == "y":
            resume_payload = {"approved": True}
        else:
            resume_payload = {"approved": False, "abort": True}

        result = app.invoke(Command(resume=resume_payload), config=config)
        print("\n=== BLOG GENERATED ===")
        print(result["final"][:500], "...")