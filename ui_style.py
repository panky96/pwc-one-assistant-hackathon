"""Shared look for the Streamlit pages (chat_app.py and app.py). Presentation only.
All user-supplied text must pass through esc() before it is placed in HTML."""
from __future__ import annotations
import html
import streamlit as st

CSS = """
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1280px;}
h1, h2, h3 {letter-spacing: -0.01em;}
.pwc-hero {background: linear-gradient(120deg, #4338ca 0%, #7c3aed 55%, #db2777 100%); color: #fff;
  padding: 1.2rem 1.5rem; border-radius: 16px; margin-bottom: 1.1rem; box-shadow: 0 8px 24px rgba(67,56,202,.18);}
.pwc-hero h2 {color: #fff; margin: 0; font-size: 1.55rem; font-weight: 700;}
.pwc-hero p {margin: .3rem 0 0; opacity: .92; font-size: .95rem;}
.pwc-card {background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 1rem 1.15rem;
  box-shadow: 0 1px 3px rgba(15,23,42,.05); margin-bottom: .8rem;}
.pwc-card-title {font-weight: 650; color: #111827; margin-bottom: .25rem;}
.pwc-muted {color: #6b7280; font-size: .88rem;}
.pwc-answer {color: #111827; line-height: 1.55; white-space: pre-wrap;}
.pwc-badge {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem;
  font-weight: 600; margin: 0 6px 6px 0; border: 1px solid transparent;}
.pwc-kpi {font-size: 1.7rem; font-weight: 750; color: #111827; line-height: 1.1;}
.pwc-kpi-label {font-size: .8rem; color: #6b7280; text-transform: uppercase; letter-spacing: .04em;}
.pwc-divider {height: 1px; background: #eef0f4; margin: 1rem 0;}
[data-testid="stTabs"] button[role="tab"] {font-weight: 600; font-size: .95rem;}
</style>
"""

_BADGE = {
    "ok": ("#dcfce7", "#166534", "#bbf7d0"),
    "info": ("#e0e7ff", "#3730a3", "#c7d2fe"),
    "warn": ("#fef3c7", "#92400e", "#fde68a"),
    "bad": ("#fee2e2", "#991b1b", "#fecaca"),
    "neutral": ("#f3f4f6", "#374151", "#e5e7eb"),
}
ROUTE_KIND = {"HYBRID_RAG": "info", "DIRECT_ANSWER": "ok", "TRANSACTION_EXEC": "warn", "REJECT_OOS": "bad"}
MODE_KIND = {"llm": "ok", "extractive": "warn", "escalated": "bad", "vetoed": "bad", "extractive_rejected": "bad",
             "verifier_unavailable": "bad", "abstained_retrieval": "neutral", "pending_approval": "warn",
             "clarification": "neutral", "out_of_scope": "bad", "direct": "ok"}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="pwc-hero"><h2>{esc(title)}</h2><p>{esc(subtitle)}</p></div>', unsafe_allow_html=True)


def badge(text: str, kind: str = "neutral") -> str:
    bg, fg, border = _BADGE.get(kind, _BADGE["neutral"])
    return f'<span class="pwc-badge" style="background:{bg};color:{fg};border-color:{border}">{esc(text)}</span>'


def card(body_html: str, title: str | None = None) -> None:
    head = f'<div class="pwc-card-title">{esc(title)}</div>' if title else ""
    st.markdown(f'<div class="pwc-card">{head}{body_html}</div>', unsafe_allow_html=True)


def kpi(label: str, value: str, sub: str = "") -> str:
    return (f'<div class="pwc-kpi">{esc(value)}</div><div class="pwc-kpi-label">{esc(label)}</div>'
            + (f'<div class="pwc-muted">{esc(sub)}</div>' if sub else ""))
