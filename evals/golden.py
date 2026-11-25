"""Golden dataset: LLM generation (docs/golden-dataset.md prompt/schema) + strict validator. 250 gate set = 63/62/50/38/37."""
from __future__ import annotations
import json, os, random, re, sys
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from app.vault import Graph, load_vault

TYPES = {"Factoid": ("Easy", 63), "Complex/Reasoning": ("Medium", 62), "Multi-Hop": ("Hard", 50),
         "Summary": ("Medium", 38), "Negative/Unanswerable": ("Hard", 37)}
OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "golden_250.json"


class GoldenCase(BaseModel):
    test_case_id: str
    question: str = Field(min_length=8)
    question_type: str
    difficulty: str
    expected_answer: str = Field(min_length=1)
    ground_truth_context_ids: list[str]
    retrieval_keywords: list[str] = Field(min_length=1)
    intent_category: str
    reasoning_notes: str

    @field_validator("question_type")
    @classmethod
    def _t(cls, v):
        if v not in TYPES: raise ValueError(f"bad type {v}")
        return v


def validate(cases: list[dict], notes: dict) -> list[GoldenCase]:
    out, seen = [], set()
    for c in cases:
        g = GoldenCase(**c)
        if g.test_case_id in seen: raise ValueError(f"dup id {g.test_case_id}")
        seen.add(g.test_case_id)
        if any(i not in notes for i in g.ground_truth_context_ids): raise ValueError(f"{g.test_case_id}: unknown context id")
        if g.question_type == "Negative/Unanswerable" and g.ground_truth_context_ids: raise ValueError(f"{g.test_case_id}: negative with context")
        if g.question_type == "Multi-Hop" and len(set(g.ground_truth_context_ids)) < 2: raise ValueError(f"{g.test_case_id}: multi-hop needs >=2 chunks")
        if g.question_type not in ("Negative/Unanswerable",) and not g.ground_truth_context_ids: raise ValueError(f"{g.test_case_id}: missing context")
        out.append(g)
    return out


def load_golden(path: Path = OUT) -> list[GoldenCase]:
    return validate(json.loads(path.read_text()), load_vault())


PROMPT = """You are an expert AI Evaluation Engineer building a Golden Dataset for a RAG system.
Generate EXACTLY {n} test cases of question_type "{qtype}" (difficulty "{diff}") from the document chunks below.
Rules: expected_answer strictly from the context (no outside knowledge); natural user phrasing (do NOT copy phrases verbatim from the context; use colloquial, slightly vague wording); questions must be unambiguous; retrieval_keywords include synonyms and key entities; vary the topics and do not repeat questions.
{type_rules}
Return ONLY a JSON array. Each object: {{"question": str, "question_type": "{qtype}", "difficulty": "{diff}", "expected_answer": str, "ground_truth_context_ids": [chunk ids], "retrieval_keywords": [str], "intent_category": str, "reasoning_notes": str}}.

CHUNKS (id: text):
{chunks}
{extra}"""

RULES = {
 "Factoid": "Direct single-span factual question answerable from ONE chunk; concise answer. ground_truth_context_ids has exactly one id.",
 "Complex/Reasoning": "Requires applying a rule to a concrete scenario (numbers, dates, thresholds); the answer explains the logic. One or two chunk ids.",
 "Multi-Hop": "Cannot be answered from one chunk; needs facts from two or more of the provided chunks. List ALL required ids (>=2).",
 "Summary": "High-level overview or comparison across a topic/sections. List the chunk ids summarized (>=1).",
 "Negative/Unanswerable": 'Realistic questions in the same domain that CANNOT be answered from ANY chunk in the vault (see the titles list). expected_answer: "I am sorry, but the provided documentation does not contain information regarding <topic>." ground_truth_context_ids MUST be [].',
}


def _json(text: str):
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m[0])


def generate(client, model: str, seed: int = 7) -> list[dict]:
    notes = load_vault(); g = Graph(notes); rnd = random.Random(seed); ids = sorted(notes)
    titles = "; ".join(f"{n.title}" for n in notes.values())
    allc: list[dict] = []
    for qtype, (diff, total) in TYPES.items():
        got: list[dict] = []; attempts = 0
        while len(got) < total and attempts < 20:
            attempts += 1
            n = min(10, total - len(got) + 1)
            if qtype in ("Multi-Hop", "Summary"):
                a = rnd.choice(ids); sel = g.subgraph(a, 1)[:4]
            elif qtype == "Negative/Unanswerable":
                sel = rnd.sample(ids, 5)
            else:
                sel = rnd.sample(ids, 4)
            chunks = "\n".join(f"{i}: {notes[i].title}. {' '.join(notes[i].facts)}" for i in sel)
            extra = f"\nALL VAULT TOPICS (for negatives, avoid these): {titles}" if qtype.startswith("Negative") else ""
            p = PROMPT.format(n=n, qtype=qtype, diff=diff, type_rules=RULES[qtype], chunks=chunks, extra=extra)
            try:
                items = _json(client.complete([{"role": "user", "content": p}], model, max_tokens=4000, temperature=0.7).text)
            except Exception as e:
                print("batch failed:", e, file=sys.stderr); continue
            seen_q = {x["question"].lower() for x in got}
            for it in items:
                it["question_type"] = qtype
                if it.get("question", "").lower() in seen_q: continue
                if qtype.startswith("Negative"): it["ground_truth_context_ids"] = []
                if qtype != "Negative/Unanswerable" and any(i not in notes for i in it.get("ground_truth_context_ids", [])): continue
                if qtype == "Multi-Hop" and len(set(it.get("ground_truth_context_ids", []))) < 2: continue
                if qtype != "Negative/Unanswerable" and not it.get("ground_truth_context_ids"): continue
                try:
                    GoldenCase(test_case_id="x", **{k: v for k, v in it.items() if k != "test_case_id"})
                except Exception:
                    continue  # schema-invalid item: drop and refill
                got.append(it); seen_q.add(it["question"].lower())
            print(qtype, len(got), "/", total, file=sys.stderr)
        allc += got[:total]
    for i, c in enumerate(allc, 1):
        c["test_case_id"] = f"TC_{i:03d}"; c["difficulty"] = TYPES[c["question_type"]][0] if c.get("difficulty") not in ("Easy", "Medium", "Hard") else c["difficulty"]
    return allc


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(".env")
    from app.llm import OpenRouterClient
    cl = OpenRouterClient(os.environ["OPENROUTER_API_KEY"], timeout_s=90)
    cases = generate(cl, os.environ["LLM_MODEL_SMALL"])
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.with_suffix('.raw.json').write_text(json.dumps(cases, indent=1))
    validate(cases, load_vault())
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(cases, indent=1))
    from collections import Counter; print(Counter(c["question_type"] for c in cases), len(cases))
