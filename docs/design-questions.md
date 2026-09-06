# The ten questions

The §14.3 design exercise: *write down the ten questions most worth asking this
system in a year, then check the schema can actually answer them.*

Done before any content exists, because the point is to surface a missing node
type or edge attribute while changing the schema is still free. It found five
gaps in a schema that had looked complete.

Each question below records what it needs, whether the schema serves it, and
what changed as a result. Re-run this exercise whenever the ontology changes.

---

## 1. What modes of autonomous vehicle are deployed worldwide, and examples?

*e.g. mid-sized minibuses, 9–20 seaters*

**Needs:** `vehicle_class` and `service_model` nodes, `place` and `organisation`
for where and who, seating capacity as a fact, deployment period.

**Verdict:** works, after two changes. The node types already existed. Seating
capacity had nowhere to live — `attribute_values` holds the ~12 capped, audited
comparison dimensions of §7.3, not arbitrary facts. And "deployed" had no time:
a pilot that ran 2019–2021 was indistinguishable from one still running.

**Changed:** `observations` table; `edges.valid_from` / `valid_to`.

## 2. What is the market share of AVs worldwide by deployment?

**Needs:** a number with a unit, a denominator, a geography and a period.

**Verdict:** impossible before the change. `finding` is a text node — a name and
a description. The question could only be answered by retrieving chunks and
reading them, which is RAG; the graph contributed nothing. That is a serious gap
for a transport corpus, where much of the value *is* numbers.

**Changed:** `observations`, keyed to one subject entity so a time series
accumulates against a stable node rather than spawning near-identical `finding`
rows that entity resolution would later mis-merge.

## 3. What are the prevailing or upcoming AV testing standards?

**Verdict:** works. `regulatory_requirement` nodes with `applies_in` edges to
`place`. "Upcoming" falls out of `edges.valid_from` being in the future, and
"superseded" out of `valid_to` being in the past — no status column needed.

**Open:** SAE J3016 (voluntary technical standard) and UNECE WP.29 (binding
regulation) are both `regulatory_requirement`. Distinguish by relation type
unless the difference proves load-bearing.

## 4. What must an operator satisfy to hold an AV licence, worldwide?

**Verdict:** works after the change. Jurisdiction is an edge to a `place`;
numeric conditions (minimum insurance, test mileage) and categorical ones
(safety driver required) are observations; `valid_from`/`valid_to` separate the
current rule from a superseded one.

## 5. What are the key principles of walkability in tropical climate?

**Verdict:** works unchanged — this is the case the attribute system was built
for. `intervention` → `finding` → `place`, filtered on the `climate` global
attribute. Three hops, one recursive CTE.

**Note:** nothing ranks "key". Edge confidence and supporting-source count are
the available proxy.

## 6. What transport integration levels exist in first-world countries, and how do strategies differ by regional attributes?

**Verdict:** the flagship question for the comparison machinery — and it found
the most serious gap. §7.2 requires that *"every comparison edge stores both the
dimension of similarity and the disanalogy"*, and the schema had neither. A
comparison edge could say Singapore and Jakarta are comparable but not on what
axis, nor where the comparison breaks down.

That is precisely the shallow inference the spec warns about: the two share
climate and almost nothing on governance capacity or income. *"Comparisons
without stated limits are how bad policy papers get written."*

**Changed:** `edges.similarity_dimension` and `edges.disanalogy`, with a CHECK
constraint rejecting any `comparable_to` edge missing either. Enforced in
Postgres rather than in a prompt, per §2 principle 6.

## 7. What are the requirements for starting a bus company, worldwide?

**Verdict:** works — structurally identical to question 4. Regulatory
requirements per jurisdiction, with capital, fleet and insurance thresholds as
observations.

## 8. What unique types of transport exist worldwide, and how do they benefit the world?

**Verdict:** works, with a deliberate caveat. The system will not return a
verdict on "benefits". Per §8 it extracts stance, certainty and contestedness,
so the answer is *"these sources argue X, hedged; these dispute it"* with
observations and contested pairs attached — structure to read, not a judgement.

## 9. What are the differences between types of taxi modality?

**Verdict:** works unchanged. Street-hail, e-hail, pooled and robotaxi as
`vehicle_class` / `service_model` nodes linked by `subtype_of`. Taxonomy
traversal is exactly what recursive CTEs over adjacency tables do well — part of
why the graph is relational rather than AGE-backed.

## 10. What is the price sensitivity of different user groups across public transport modalities in Singapore?

**Verdict:** found the last gap. Elasticity is a number, so `observations` held
the value — but *user group* had nowhere to go. Observations carried a subject
and a geography and nothing else, so students, elderly and commuters would each
need their own subject node, returning to the name-explosion problem.

It generalises beyond segments: time of day, trip purpose, peak versus off-peak.
A column per dimension does not scale, and "students at peak on the MRT" needs
three at once.

**Changed:** `observations.qualifiers` (JSONB, GIN-indexed) — the mechanism §4
already nominates for absorbing schema evolution without a document store.

---

## What the exercise cost and returned

One session, before any code depended on the schema. It produced one new table,
five new columns, and two CHECK constraints:

| Change | Question that found it |
|---|---|
| `observations` table | 1, 2 |
| `edges.valid_from` / `valid_to` | 1, 3, 4 |
| `edges.similarity_dimension` / `disanalogy` + CHECK | 6 |
| `observations.qualifiers` | 10 |

Five of the ten questions were unanswerable beforehand. Discovering that in
month four, with a corpus already built on the wrong shape, is the outcome §14.3
exists to avoid.
