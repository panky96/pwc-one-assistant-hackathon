"""Context retention & drift control: immutable goal anchor at index 0, 3-turn hot window,
observation compression (180 tokens), verified-only session facts, goal-alignment drift score."""
from __future__ import annotations
from collections import deque
from mcp_servers.rag_server import cosine, hash_embed


def compress(text, max_tokens: int = 180) -> str:
    return " ".join(str(text).split()[:max_tokens])


class ReactContext:
    def __init__(self, anchor: str, hot_window: int = 3, obs_max_tokens: int = 180):
        self._anchor = anchor
        self._window: deque[str] = deque(maxlen=hot_window)
        self.obs_max = obs_max_tokens
        self.facts: dict[str, str] = {}

    @property
    def anchor(self) -> str:
        return self._anchor

    def add_observation(self, obs) -> None:
        self._window.append(compress(obs, self.obs_max))

    def prompt(self) -> list[str]:
        return [self._anchor, *self._window]

    def promote_fact(self, key: str, value: str, verified: bool) -> None:
        if not verified:
            raise ValueError("unverified model speculation never enters session_facts")
        self.facts[key] = value

    def drift(self, goal: str) -> float | None:
        """Mean cosine alignment between the goal and the hot-window observations (KPI-18)."""
        if not self._window:
            return None
        g = hash_embed(goal)
        sims = [cosine(g, hash_embed(o)) for o in self._window]
        return sum(sims) / len(sims)
