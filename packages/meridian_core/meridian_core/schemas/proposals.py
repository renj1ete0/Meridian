"""What a model may propose, and the shape it has to propose it in.

Task `P4-16`; spec §11.6, §11.8, §2.6.

Every other DTO in this package describes a boundary between parts of this
system. These describe the one boundary where the input was *generated* rather
than typed — and §2.6 is unambiguous about it: "never trust model output for
structure". So these are the strictest schemas here, and they are deliberately
not the last word: the write tools re-check everything through `validation.py`,
because a DTO is a shape and the guards are the rules.

Three decisions worth stating, because each is a way this goes wrong:

**A proposal cites passage numbers, never chunk ids.** The passages a model
sees are numbered `[1]`, `[2]`, … by `framing.frame_passages`, and that is the
only handle it is given. An id it supplied would be an id it could invent —
and an invented one that happens to exist attaches a fabricated claim to a
real chunk, which is the failure that makes every citation in the corpus worth
less. The mapping from number back to `chunk_id` belongs to the caller, which
holds the batch it just sent.

**A mention is a name and a type, not an id.** Resolution happens at write
time against the graph (§5.5), so the model says what it saw; the server says
which node that is. This also means the model cannot point an edge at an
arbitrary row by number.

**`comparable_to` states its limits here as well as in the database.** §7.2's
rule is enforced by a CHECK constraint, by `add_edge`, and by this model —
three times, on purpose. The constraint is the one that cannot be bypassed;
this one exists so the refusal names the rule while the batch is still being
parsed, and so a whole batch is not lost to one comparison that forgot its
disanalogy.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from meridian_core.models.graph import COMPARISON_RELATION

# The length limit comes from the guard that enforces it, not from a second
# copy of the number: a relation this schema accepted and `check_relation_type`
# then refused would be a batch lost between two rules that agreed in spirit.
from meridian_core.validation import MAX_RELATION_TYPE

from .common import Confidence, CreateBase
from .enums import Certainty, NodeType, Stance

__all__ = [
    "Citations",
    "EdgeProposal",
    "Mention",
    "TagProposal",
]

#: At least one passage number. An uncited claim is not assertable (§2
#: principle 3), and "the model did not say where it read this" is the most
#: common malformed answer there is — so it is refused by the type rather than
#: checked for later.
Citations = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]

#: An identifier, not prose. The same shape `validation.check_relation_type`
#: refuses, expressed where a batch can survive one bad item.
RELATION_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"


class Mention(BaseModel):
    """One thing the model says it saw, before the graph has an opinion.

    Not a `CreateBase`: a mention is read out of an answer rather than
    submitted, and the extra strictness that makes sense for a tool call —
    refusing unknown keys — would here discard an otherwise good edge because
    a model added a field nobody asked for. The fields that matter are typed;
    the rest is ignored deliberately, and this is the only schema in this
    package where that is the right trade.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    node_type: NodeType
    #: ISO country code where the model could tell. Part of entity identity
    #: (§5.5): the same name is routinely a different thing elsewhere.
    jurisdiction: str | None = Field(default=None, max_length=8)

    def __str__(self) -> str:  # pragma: no cover - log and journal readability
        return f"{self.name} ({self.node_type})"


class EdgeProposal(CreateBase):
    """One relation the model claims a passage supports (§5.4, §8).

    `stance` and `certainty` are §8's observable properties rather than a
    verdict: what position the passage argues and how hedged it is, never
    whether the source is biased or true. Both are optional because a model
    that has to guess one will, and an invented stance is worse than an absent
    one — the graph reads a missing value as unknown and a wrong one as fact.
    """

    subject: Mention
    object: Mention
    relation: str = Field(min_length=1, max_length=MAX_RELATION_TYPE, pattern=RELATION_PATTERN)
    citations: Citations

    stance: Stance | None = None
    certainty: Certainty | None = None
    confidence: Confidence | None = None
    topic_labels: list[str] | None = None

    #: §7.2, and required for `comparable_to` by the validator below.
    similarity_dimension: str | None = Field(default=None, max_length=200)
    disanalogy: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _comparison_states_its_limits(self) -> EdgeProposal:
        if self.relation == COMPARISON_RELATION and not (
            self.similarity_dimension and self.disanalogy
        ):
            raise ValueError(
                f"a {COMPARISON_RELATION!r} edge must give both similarity_dimension "
                "and disanalogy (§7.2) — a comparison without stated limits is "
                "the inference this system exists to avoid"
            )
        return self

    @model_validator(mode="after")
    def _not_a_self_edge(self) -> EdgeProposal:
        # Caught again in `check_not_self_edge`. Here it saves a round trip and,
        # more usefully, says which mention was duplicated while the answer is
        # still in hand.
        same_name = self.subject.name.casefold() == self.object.name.casefold()
        if same_name and self.subject.node_type == self.object.node_type:
            raise ValueError(f"subject and object are both {self.subject.name!r}")
        return self


class TagProposal(CreateBase):
    """One attribute value the model claims a passage supports (§7.3).

    The attribute must already exist and be active — this schema cannot check
    that, `tag_entity` does — so the name here is a claim about the *active
    set the prompt listed*, not a proposal of new schema. `P7-01` is the only
    route by which a new attribute comes into being.
    """

    entity: Mention
    attribute: str = Field(min_length=1, max_length=100)
    citations: Citations

    value: str | None = Field(default=None, max_length=500)
    value_numeric: float | None = None
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _a_tag_has_a_value(self) -> TagProposal:
        if self.value is None and self.value_numeric is None:
            raise ValueError(f"{self.attribute!r} was proposed with no value")
        return self
