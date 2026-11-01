"""The graph audit, as Admin grades and reports it (task `P7-10`).

See docs/features/knowledge-graph.md#the-graph-audit.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import AuditKind, AuditVerdict

#: A note on a grade is a reason, not an essay.
NOTE_MAX_CHARS = 2000


class AuditTallyRead(BaseModel):
    """Grades of one kind of item, in one draw or for one producer."""

    drawn: int
    graded: int
    correct: int
    wrong: int
    unsure: int
    #: correct / (correct + wrong); None until one of them exists. `unsure` is left out.
    precision: float | None
    #: The 95% Wilson interval around it: thirty grades cannot say more than a range.
    low: float | None
    high: float | None


class AuditProducerRead(BaseModel):
    """Precision for one producer: an edge's agent and model, or whoever decided a merge."""

    kind: AuditKind
    produced_by: str | None
    model: str | None
    tally: AuditTallyRead


class AuditDrawRead(BaseModel):
    draw_id: int
    drawn_at: str
    window_start: str
    window_end: str
    seed: str
    drawn_by: str
    edge_population: int
    merge_population: int
    merges_reversed: int
    edges: AuditTallyRead
    merges: AuditTallyRead
    by_producer: list[AuditProducerRead]


class AuditSampleRead(BaseModel):
    sample_id: int
    draw_id: int
    kind: AuditKind
    item_id: int
    position: int
    #: What the grader is shown; see the feature doc for its two shapes.
    shown: dict
    grade: AuditVerdict | None
    note: str | None
    graded_by: str | None
    graded_at: str | None


class GraphAuditRead(BaseModel):
    """Every draw, newest first: precision over time."""

    draws: list[AuditDrawRead]
    #: Sampled items nobody has graded yet, across every draw.
    ungraded: int


class AuditDrawDetailRead(BaseModel):
    draw: AuditDrawRead
    samples: list[AuditSampleRead]


class AuditGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: AuditVerdict
    note: str | None = Field(default=None, max_length=NOTE_MAX_CHARS)
