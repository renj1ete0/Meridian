"""What a model may propose, and the shape it has to propose it in (`P4-16`, §11.6, §2.6).

The strictest schemas here, re-checked by `validation.py` on write. Proposals cite
passage numbers, never chunk ids, and name mentions rather than ids. See
docs/reference/data-model.md#model-proposals.
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

#: At least one passage number: an uncited claim is not assertable (§2 principle 3).
Citations = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]

#: An identifier, not prose. The same shape `validation.check_relation_type`
#: refuses, expressed where a batch can survive one bad item.
RELATION_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"


class Mention(BaseModel):
    """One thing the model says it saw, before the graph has an opinion.

    Not a `CreateBase`: unknown keys are ignored, deliberately, so an extra field does
    not discard a good edge.
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

    `stance` and `certainty` are observable properties, not verdicts, and optional so a
    model need not guess them.
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

    The attribute must already be active, which `tag_entity` checks; new attributes come
    only through `P7-01`.
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
