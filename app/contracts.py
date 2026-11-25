"""Typed contracts (Pydantic v2, strict)."""
from __future__ import annotations
import enum
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Tier(str, enum.Enum):
    READ = "TIER_1_READ"
    COMPUTE = "TIER_2_COMPUTE"
    MUTATE = "TIER_3_MUTATE"


class Route(str, enum.Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    HYBRID_RAG = "HYBRID_RAG"
    TRANSACTION_EXEC = "TRANSACTION_EXEC"
    REJECT_OOS = "REJECT_OOS"


class TenantContext(Strict):
    """Derived ONLY from validated token claims, never request fields."""
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    roles: tuple[str, ...] = ()


class HybridRetrieveInput(Strict):
    query: str = Field(min_length=1, max_length=2000)
    candidate_k: int = Field(default=50, ge=1, le=200)
    output_k: int = Field(default=4, ge=1, le=20)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tenant fields in body are untrusted & ignored
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=64)
    operation_id: str | None = None


class Citation(Strict):
    chunk_id: str
    score: float


class ChatResponse(BaseModel):
    route: Route
    answer: str
    citations: list[Citation] = []
    clarification: list[str] = []
    abstained: bool = False
    operation_id: str | None = None
