"""The prompt and the parse (task `P4-16`, §11.8, §2.6, §5.4, §7.3).

The parse is the load-bearing half, and every test here is a way a real answer
arrives malformed: truncated at the token limit, fenced in markdown, wrapped in
an object, prefaced with an apology, or carrying one invented item among twenty
good ones. The property that matters in all of them is the same — **a bad
answer costs the items that were bad, never the batch and never the run**.

The prompt tests are drift tests. The vocabulary a model is offered has to be
the vocabulary the database accepts, and the way that breaks is silent: a node
type added to the schema and not to the prompt is simply never proposed, and a
value named in the prompt that the schema refuses turns every proposal using it
into a refused write nobody reads.
"""

from __future__ import annotations

import json
import re

import pytest

from meridian_core.models.graph import CERTAINTY, COMPARISON_RELATION, NODE_TYPE, STANCE
from meridian_core.proposals import (
    EDGE_RARE_KEYS,
    MAX_ITEMS_PER_PASSAGE,
    MODEL_NODE_TYPES,
    CitationOutOfRange,
    Passage,
    answer_items,
    chunk_ids_for,
    edge_schema,
    extract_prompt,
    parse_edges,
    parse_tags,
    tag_prompt,
    tag_schema,
)
from meridian_core.schemas.proposals import EdgeProposal, Mention, TagProposal

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def edge(subject: str = "A", obj: str = "B", relation: str = "evaluates", **extra) -> dict:
    return {
        "subject": {"name": subject, "node_type": "organisation"},
        "object": {"name": obj, "node_type": "intervention"},
        "relation": relation,
        "citations": [1],
        **extra,
    }


def passages(count: int = 3) -> list[Passage]:
    return [
        Passage(
            chunk_id=100 + index,
            text=f"passage {index}",
            url=f"https://e/{index}",
            source_tier="government",
        )
        for index in range(1, count + 1)
    ]


# --------------------------------------------------------------------------
# The parse survives what a model actually sends
# --------------------------------------------------------------------------


def test_a_plain_array_parses() -> None:
    parsed = parse_edges(json.dumps([edge(), edge(obj="C")]))

    assert len(parsed.accepted) == 2
    assert not parsed.rejected


def test_an_empty_list_inside_an_item_is_not_an_empty_answer() -> None:
    """`B-247`, measured: a model wrote one item with no array round it, carrying an empty
    `topic_labels`; the parse took that empty list as the answer and reported nothing
    proposed."""
    parsed = parse_edges(json.dumps(edge(topic_labels=[])))

    assert len(parsed.accepted) == 1
    assert not parsed.rejected


def test_a_wrapper_holding_an_empty_list_is_an_empty_answer() -> None:
    for text in ('{"relations": []}', '{"relations": [ ]}\n'):
        parsed = parse_edges(text)
        assert (parsed.accepted, parsed.rejected) == ([], []), text
    assert parse_edges("{}").rejected, "an object with no list is not an answer"


def test_a_fenced_answer_parses() -> None:
    """Rule 5 asks for no fence. Models add one anyway, and refusing the answer
    over its packaging would lose a batch that was entirely correct."""
    parsed = parse_edges(f"```json\n{json.dumps([edge()])}\n```")

    assert len(parsed.accepted) == 1


def test_prose_around_the_array_is_ignored() -> None:
    answer = f"Certainly [see below].\n\n{json.dumps([edge()])}\n\nLet me know if you want more."

    parsed = parse_edges(answer)

    assert len(parsed.accepted) == 1, "a bracketed aside must not be mistaken for the answer"


def test_an_object_wrapper_parses() -> None:
    parsed = parse_edges(json.dumps({"edges": [edge(), edge(obj="C")]}))

    assert len(parsed.accepted) == 2


def test_a_single_object_parses() -> None:
    """One proposal, unwrapped. The citations array inside it is a list too,
    and a parse that took the first list it found would return `[1]`."""
    parsed = parse_edges(json.dumps(edge()))

    assert len(parsed.accepted) == 1
    assert parsed.accepted[0].citations == [1]


def test_a_truncated_answer_keeps_the_items_that_completed() -> None:
    """The most common malformation there is: cut off at `max_tokens`.

    The items before the cut are complete and usable, and losing twenty good
    proposals to one half-written twenty-first is the waste this parse exists
    to avoid.
    """
    whole = json.dumps([edge(obj=f"B{n}") for n in range(5)])
    truncated = whole[: whole.rindex("{") + 40]

    parsed = parse_edges(truncated)

    assert len(parsed.accepted) == 4
    assert parsed.rejected, "the dropped tail should be reported, not swallowed"


def test_one_invalid_item_does_not_discard_its_neighbours() -> None:
    answer = json.dumps([edge(), {"subject": {"name": "A"}}, edge(obj="C")])

    parsed = parse_edges(answer)

    assert len(parsed.accepted) == 2
    assert len(parsed.rejected) == 1


def test_an_explicit_empty_array_is_an_answer_not_a_failure() -> None:
    """ "Nothing here is supportable" is the right answer to many batches, and
    a parse that reported it as a failure would train somebody to ignore the
    failures."""
    parsed = parse_edges("[]")

    assert parsed.accepted == [] and parsed.rejected == []


@pytest.mark.parametrize(
    "answer",
    [
        "",
        "   ",
        "I'm sorry, I can't help with that.",
        "{",
        "[[[",
        '{"edges": ',
        "null",
        json.dumps({"edges": "not a list"}),
        json.dumps([1, 2, 3]),
        json.dumps(["a string"]),
        "<?xml version='1.0'?><edges/>",
        json.dumps([edge()]) * 2,
    ],
)
def test_the_parse_never_raises(answer: str) -> None:
    """The whole point of the module. A parse that raised would let one bad
    response end an unattended run that should have skipped a batch."""
    for parse in (parse_edges, parse_tags):
        parsed = parse(answer)
        assert isinstance(parsed.accepted, list)
        assert isinstance(parsed.rejected, list)


def test_an_unusable_answer_says_why_and_shows_what_it_saw() -> None:
    parsed = parse_edges("I'm sorry, I can't help with that.")

    assert parsed.rejected, "silence about an unusable answer reads as an empty corpus"
    assert "can't help" in parsed.rejected[0].excerpt


def test_a_rejection_excerpt_cannot_fill_the_journal() -> None:
    """A page that is malformed on purpose must not be able to write itself
    into the log by being long."""
    parsed = parse_edges("x" * 10_000)

    assert len(parsed.rejected[0].excerpt) <= 200


def test_a_brace_inside_a_quoted_string_does_not_close_the_object() -> None:
    """A `disanalogy` quoting a document can contain a brace, and a scanner
    that counted it would truncate the item and lose it."""
    answer = json.dumps(
        [
            edge(
                relation=COMPARISON_RELATION,
                similarity_dimension="density",
                disanalogy="the source writes {governance} differently",
            )
        ]
    )

    parsed = parse_edges(answer)

    assert len(parsed.accepted) == 1
    assert "{governance}" in parsed.accepted[0].disanalogy


# --------------------------------------------------------------------------
# What the schema refuses, and why each refusal exists
# --------------------------------------------------------------------------


def test_an_uncited_proposal_is_refused() -> None:
    """§2 principle 3: nothing is assertable without a citation you can follow."""
    parsed = parse_edges(json.dumps([{**edge(), "citations": []}]))

    assert not parsed.accepted
    assert "citations" in parsed.rejected[0].reason


def test_a_comparison_without_its_limits_is_refused() -> None:
    """§7.2. Enforced by a CHECK constraint, by `add_edge` and here — the
    third one exists so a batch is not lost to one careless comparison."""
    parsed = parse_edges(json.dumps([edge(relation=COMPARISON_RELATION)]))

    assert not parsed.accepted
    assert "disanalogy" in str(parsed.rejected[0]).lower()


def test_a_comparison_with_both_halves_is_accepted() -> None:
    parsed = parse_edges(
        json.dumps(
            [
                edge(
                    relation=COMPARISON_RELATION,
                    similarity_dimension="equatorial climate",
                    disanalogy="governance capacity differs sharply",
                )
            ]
        )
    )

    assert len(parsed.accepted) == 1


def test_a_relation_that_is_prose_is_refused() -> None:
    """Free text in a column traversals group by makes every edge its own
    relation, and the grouping stops meaning anything."""
    parsed = parse_edges(json.dumps([edge(relation="is said to possibly relate to")]))

    assert not parsed.accepted


def test_a_self_edge_is_refused_by_name() -> None:
    same = {
        **edge(subject="Harbour Authority"),
        "object": {"name": "harbour authority", "node_type": "organisation"},
    }

    parsed = parse_edges(json.dumps([same]))

    assert not parsed.accepted
    assert "Harbour Authority" in str(parsed.rejected[0])


def test_one_name_under_two_node_types_is_not_a_self_edge() -> None:
    """Resolution never crosses node types (§5.5), so these are two nodes and
    an edge between them is a real claim — refusing it would lose the exact
    case the typed ontology exists for."""
    parsed = parse_edges(json.dumps([edge(subject="Riverside Pilot", obj="Riverside Pilot")]))

    assert len(parsed.accepted) == 1


def test_an_invented_node_type_is_refused() -> None:
    """The type list is the database's CHECK constraint. A value outside it
    would be refused at the insert, after the tokens were spent."""
    parsed = parse_edges(
        json.dumps([{**edge(), "subject": {"name": "A", "node_type": "municipality"}}])
    )

    assert not parsed.accepted


def test_an_invented_stance_is_refused() -> None:
    parsed = parse_edges(json.dumps([{**edge(), "stance": "enthusiastic"}]))

    assert not parsed.accepted


def test_a_tag_with_no_value_is_refused() -> None:
    parsed = parse_tags(
        json.dumps(
            [
                {
                    "entity": {"name": "A", "node_type": "place"},
                    "attribute": "density",
                    "citations": [1],
                }
            ]
        )
    )

    assert not parsed.accepted
    assert "density" in str(parsed.rejected[0])


def test_a_tag_with_a_numeric_value_is_accepted() -> None:
    parsed = parse_tags(
        json.dumps(
            [
                {
                    "entity": {"name": "A", "node_type": "place"},
                    "attribute": "density",
                    "value_numeric": 8358.0,
                    "citations": [2],
                }
            ]
        )
    )

    assert len(parsed.accepted) == 1


def test_a_proposal_cannot_smuggle_a_chunk_id() -> None:
    """The model cites passage numbers; ids belong to the server. A field it
    invented must fail loudly rather than be silently dropped — `extra=forbid`
    on the write-side DTOs is the rule this checks."""
    parsed = parse_edges(json.dumps([{**edge(), "supporting_chunk_ids": [999]}]))

    assert not parsed.accepted


# --------------------------------------------------------------------------
# Citations map to the batch that was actually sent
# --------------------------------------------------------------------------


def test_citations_become_the_chunk_ids_of_the_passages_sent() -> None:
    assert chunk_ids_for([1, 3], passages()) == [101, 103]


def test_repeated_citations_collapse() -> None:
    assert chunk_ids_for([2, 2, 1], passages()) == [101, 102]


@pytest.mark.parametrize("number", [0, 4, 99, -1])
def test_a_citation_outside_the_batch_is_refused(number: int) -> None:
    """Not dropped. A proposal citing three passages of which one does not
    exist would otherwise be written claiming support it was never given."""
    with pytest.raises(CitationOutOfRange):
        chunk_ids_for([1, number], passages())


# --------------------------------------------------------------------------
# The prompt cannot drift from the schema
# --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["extract", "tag"])
def test_the_prompt_offers_every_node_type_the_database_accepts(which) -> None:
    """Computed from `NODE_TYPE.enums`, so a type added to the schema is
    named here without anybody remembering to edit a prompt.

    `B-245`: the tagging prompt showed `"node_type": "..."` and never the list, so a
    model guessed ("city", "policy", "service") and the parser refused every tag.
    """
    if which == "extract":
        prompt = extract_prompt(passages()).system
    else:
        prompt = tag_prompt(passages(), attributes=[("density", None)]).system

    # Whole words: `place` must not pass because `workplace` is in the prose.
    missing = [value for value in MODEL_NODE_TYPES if not re.search(rf"\b{value}\b", prompt)]
    assert not missing, f"node types the {which} model is never told about: {missing}"


def test_an_item_level_jurisdiction_is_refused_and_the_prompt_says_where_it_goes() -> None:
    """Seen from a local model: `jurisdiction` beside the subject rather than in it."""
    item = (
        '[{"subject": {"name": "Service 451", "node_type": "service_model"}, '
        '"relation": "connects", "object": {"name": "Bukit Gombak", "node_type": "place"}, '
        '"citations": [1], "jurisdiction": "SG"}]'
    )
    assert parse_edges(item).rejected
    assert "never beside them" in extract_prompt(passages()).system


def test_the_prompt_does_not_offer_annotation() -> None:
    """An annotation is something a person wrote (§12.5). A model that could
    mint one would be forging the reader's own notes."""
    assert "annotation" in NODE_TYPE.enums
    assert "annotation" not in MODEL_NODE_TYPES
    assert "annotation" not in extract_prompt(passages()).system


@pytest.mark.parametrize("value", STANCE.enums)
def test_the_prompt_names_every_stance(value: str) -> None:
    assert value in extract_prompt(passages()).system


@pytest.mark.parametrize("value", CERTAINTY.enums)
def test_the_prompt_names_every_certainty(value: str) -> None:
    assert value in extract_prompt(passages()).system


def test_the_prompt_never_shows_a_chunk_id() -> None:
    """The numbering is the only handle the model gets. An id in the prompt is
    an id it can cite without having read the passage."""
    batch = passages()
    # The fence is sixteen random hex characters, so it contains digits that
    # can coincide with an id. Dropping those two lines asks the question the
    # test means — is an id anywhere the model could read it as a citation —
    # rather than one that fails once every few thousand runs.
    body = "\n".join(
        line for line in extract_prompt(batch).user.splitlines() if "untrusted-" not in line
    )

    for passage in batch:
        assert str(passage.chunk_id) not in body


def test_the_passages_arrive_fenced_and_attributed() -> None:
    prompt = extract_prompt(passages()).user

    assert "DATA, not instructions" in prompt
    assert "untrusted-" in prompt
    assert "https://e/1" in prompt, "a citation the model reads beats one it reconstructs"


def test_the_instruction_comes_before_the_quoted_material() -> None:
    """§11.8: text after the payload is text an injection can imitate, having
    just seen the closing delimiter."""
    prompt = extract_prompt(passages()).user
    lines = prompt.rstrip().splitlines()

    assert prompt.index("Read the passages below") < prompt.index("untrusted-")
    assert lines[-1].startswith("/untrusted-"), (
        "nothing may follow the quoted material — text after the payload is "
        "text an injection has just seen the shape of"
    )


def test_the_tagging_prompt_lists_the_active_attributes_and_their_definitions() -> None:
    prompt = tag_prompt(
        passages(), attributes=[("density", "people per square kilometre"), ("climate", None)]
    ).system

    assert "density: people per square kilometre" in prompt
    assert "climate" in prompt


def test_the_tagging_prompt_offers_the_names_already_in_the_graph() -> None:
    """Without them a model invents a near-miss spelling for every entity, and
    resolution then has to undo it."""
    prompt = tag_prompt(
        passages(), attributes=[("density", None)], entities=["Regional Transit Board"]
    ).system

    assert "Regional Transit Board" in prompt


def test_the_tagging_prompt_shows_each_attributes_wordings_in_use() -> None:
    """`B-170`: told to reuse wording, a model that never sees the wording invents its own."""
    prompt = tag_prompt(
        passages(),
        attributes=[("fare_level", "Fare relative to fixed route"), ("climate", None)],
        values={"fare_level": ["same as fixed-route fare", 'free "trial"']},
    ).system

    block = prompt[prompt.index("- fare_level") : prompt.index("- climate")]
    assert '"same as fixed-route fare"' in block
    assert '"free \\"trial\\""' in block, (
        "a value is quoted as JSON, so a quote inside cannot end it"
    )
    assert block.index("same as") < block.index("free"), "most used first, as given"
    climate = prompt[prompt.index("- climate") :].splitlines()
    assert not climate[1].strip().startswith("in use"), "no list for an attribute with none"


def test_the_tagging_prompt_says_how_to_use_the_wordings_only_once() -> None:
    plain = tag_prompt(passages(), attributes=[("climate", None)]).system
    assert "in use:" not in plain
    assert " ".join(plain.split()).count("reuse one when it says what the passage says") == 1


def test_topics_are_named_only_when_there_are_any() -> None:
    """An empty topic list passed as prose is one more thing for the model to
    interpret; leaving it out says nothing, which is what is meant."""
    assert "built around these topics" not in extract_prompt(passages()).system
    assert "walkability" in extract_prompt(passages(), topics=["walkability"]).system


# --------------------------------------------------------------------------
# The answer's schema, sent to a server that decodes to it (`B-247`)
# --------------------------------------------------------------------------


def _fields(model) -> tuple[set[str], set[str]]:
    """Every field of a DTO, and the required ones, read from the DTO itself."""
    return set(model.model_fields), {n for n, f in model.model_fields.items() if f.is_required()}


@pytest.mark.parametrize(
    ("schema", "model"),
    [
        (lambda: edge_schema(passages=5), EdgeProposal),
        (lambda: tag_schema(passages=5, attributes=["fare_level"]), TagProposal),
    ],
    ids=["edges", "tags"],
)
def test_the_schema_is_the_dto_field_for_field(schema, model) -> None:
    """Drift: a field added to a proposal and not to what the server enforces would be one
    the model can never write; one in the schema and not the DTO, one the parse refuses."""
    sent = schema()
    item = answer_items(sent)
    every, required = _fields(model)
    mention_every, mention_required = _fields(Mention)

    def nullable(prop) -> bool:
        return any(option.get("type") == "null" for option in prop.get("anyOf", []))

    # An object at the root, holding the one array: some engines take no other root.
    assert (sent["type"], sent["additionalProperties"]) == ("object", False)
    ((key, answer),) = sent["properties"].items()
    assert sent["required"] == [key] and answer["type"] == "array"
    assert parse_edges(json.dumps({key: [edge()]})).accepted, "the parse does not unwrap it"
    assert set(item["properties"]) == every
    assert set(item["required"]) >= required, "a key the DTO requires is left optional"
    for name, prop in item["properties"].items():
        # A key the DTO lets a model leave out is written as null, unless it is rare.
        assert (name in item["required"]) == (name not in EDGE_RARE_KEYS), name
        assert nullable(prop) == (name not in required), name
        if model.model_fields[name].annotation is Mention:
            assert set(prop["properties"]) == mention_every
            assert set(prop["required"]) == mention_every
            for key, sub in prop["properties"].items():
                assert nullable(sub) == (key not in mention_required), key
            assert prop["additionalProperties"] is False


def test_the_optional_keys_come_after_every_required_one() -> None:
    """A grammar writes required keys first. An optional key declared before a required one
    is a key the model has already passed when it could have written it."""
    for schema in (edge_schema(passages=3), tag_schema(passages=3, attributes=["a"])):
        order = list(answer_items(schema)["properties"])
        flags = [key in answer_items(schema)["required"] for key in order]
        assert flags == sorted(flags, reverse=True), order
    assert set(EdgeProposal.model_fields) >= EDGE_RARE_KEYS, "a rare key that is not a field"


def test_the_schema_is_self_contained_and_closed() -> None:
    """No `$ref` (not every engine follows one) and no object left open to invented keys."""

    def walk(node):
        if isinstance(node, dict):
            assert "$ref" not in node and "$defs" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(edge_schema(passages=3))
    walk(tag_schema(passages=3, attributes=["a"]))


def test_the_schema_offers_the_vocabularies_the_database_accepts() -> None:
    props = answer_items(edge_schema(passages=3))["properties"]

    def enum(prop):
        return next(o["enum"] for o in prop["anyOf"] if "enum" in o)

    assert enum(props["stance"]) == list(STANCE.enums)
    assert enum(props["certainty"]) == list(CERTAINTY.enums)
    assert props["subject"]["properties"]["node_type"]["enum"] == list(MODEL_NODE_TYPES)
    assert "annotation" not in props["object"]["properties"]["node_type"]["enum"]


def test_citations_are_held_to_the_batch_and_attributes_to_the_active_set() -> None:
    edges = answer_items(edge_schema(passages=7))["properties"]["citations"]
    assert (edges["items"]["minimum"], edges["items"]["maximum"], edges["minItems"]) == (1, 7, 1)

    tags = answer_items(tag_schema(passages=2, attributes=["fare_level", "climate"]))
    assert tags["properties"]["attribute"]["enum"] == ["fare_level", "climate"]
    unnamed = answer_items(tag_schema(passages=2, attributes=[]))
    assert "enum" not in unnamed["properties"]["attribute"]


def test_the_answer_is_capped_so_the_token_limit_never_cuts_an_item() -> None:
    """The grammar closes the array at the cap; a model that would have gone on to the token
    limit loses the surplus, not the item it was writing."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = tag_schema(passages=2, attributes=["climate"])
    one = {
        "entity": {"name": "A", "node_type": "place", "jurisdiction": None},
        "attribute": "climate",
        "value": "arid",
        "value_numeric": None,
        "citations": [1],
        "confidence": None,
    }
    cap = MAX_ITEMS_PER_PASSAGE * 2
    jsonschema.validate({"assignments": [one] * cap}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"assignments": [one] * (cap + 1)}, schema)


def test_what_the_schema_refuses_the_parse_refuses_too() -> None:
    """The schema is no looser than the parse on the shapes a local model got wrong: a
    `jurisdiction` beside the mentions, a stance off the list, a citation past the batch."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = edge_schema(passages=3)

    def full(**over) -> dict:
        """An edge with every key written, as a model held to the schema writes one."""
        item = {name: None for name in EdgeProposal.model_fields}
        mention = {"jurisdiction": None}
        item.update(edge(stance="supports"))
        item["subject"] = {**mention, **item["subject"], "jurisdiction": "SG"}
        item["object"] = {**mention, **item["object"]}
        return {**item, **over}

    good = full()
    jsonschema.validate({"relations": [good]}, schema)
    assert len(parse_edges(json.dumps([good])).accepted) == 1

    for bad in (full(jurisdiction="SG"), full(stance="favourable"), full(citations=[])):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"relations": [bad]}, schema)
        assert parse_edges(json.dumps([bad])).accepted == []

    past_the_batch = full(citations=[4])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"relations": [past_the_batch]}, schema)
    with pytest.raises(CitationOutOfRange):
        chunk_ids_for(parse_edges(json.dumps([past_the_batch])).accepted[0].citations, passages())


@pytest.mark.parametrize(
    ("system", "schema"),
    [
        (lambda: extract_prompt(passages()).system, lambda: edge_schema(passages=3)),
        (
            lambda: tag_prompt(passages(), attributes=[("climate", None)]).system,
            lambda: tag_schema(passages=3, attributes=["climate"]),
        ),
    ],
    ids=["edges", "tags"],
)
def test_the_schema_asks_for_the_keys_in_the_order_the_prompt_shows_them(system, schema) -> None:
    """A grammar writes keys in schema order. Out of the prompt's order, a model that had
    been made to write one key early skipped the ones it would have written before it."""
    shown = re.findall(r'^ {4}"(\w+)":', system(), flags=re.MULTILINE)
    order = list(answer_items(schema())["properties"])

    assert len(shown) >= 5, "the prompt's example is no longer read the way this test reads it"
    assert [key for key in order if key in shown] == shown


def test_an_empty_answer_is_still_allowed() -> None:
    """A batch with nothing supportable answers `[]`; a schema demanding an item would make
    the model invent one."""
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({"relations": []}, edge_schema(passages=3))
    jsonschema.validate({"assignments": []}, tag_schema(passages=3, attributes=["a"]))
    assert parse_edges('{"relations": []}').rejected == [], "an empty wrapper is an answer"
