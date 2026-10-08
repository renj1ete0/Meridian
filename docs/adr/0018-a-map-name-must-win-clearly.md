# 0018. A Map name must win clearly, not only fit

- **Status:** Accepted
- **Date:** 2026-10-08
- **Tasks:** `B-159` (amends [ADR 0017](0017-map-names-must-fit.md))

## Context

ADR 0017 set a floor: a subfield names an area only above a centred similarity of 0.20. Areas
still read wrong where several subfields cleared it by about the same amount: statute text took
"Pharmacy" with "Family Practice" and "Medical Terminology" a few
thousandths behind, and programming terms took "Algebra and Number Theory". The winner among
near-equals is noise. A plain margin was expected to drop good names too, because siblings that
both fit ("Ecology" and "Nature and Landscape Conservation" for a conservation area) are also
near-ties. The operator has left such calls to the build; `B-159` asked that the rule be
measured on areas judged by hand rather than guessed from the margins.

## Decision

A subfield names an area when its centred similarity is at least 0.33, or when it is at least
0.25 and beats the third-nearest subfield by 0.03. Otherwise the area keeps its terms.

Measured on the live build's 421 named areas. On a sample of 50 (half from the near-tied), about
41% of names, weighted back to the population, did not fit the area's terms, and among near-ties
only 6 of 25 did. The rule was chosen on that sample and checked on a fresh random 30 judged
before the rule was applied: wrong names fell from 9 to 3, right ones from 21 to 19. A plain
floor of 0.30 removed as many wrong names but lost 12 of the 21 right ones.

## Consequences

- Fewer areas carry a listed name; the rest show their terms, which are honest if plainer.
- High near-ties keep their name, so sibling subfields still name the areas they both fit.
- Three of 30 checked names remain wrong at confident similarities; those are gaps in the list
  of names, not in the rule.
- The thresholds sit in `meridian_core.fields`, held by a test that cites the measurement.

## Alternatives considered

- **A plain margin** (top beats third by 0.03): removed few wrong names, since many wrong names
  win clearly at low similarity.
- **A higher floor alone** (0.30): as accurate, at the cost of most correct names.
- **Requiring the top candidates to share a field**: keeps wrong names whose rivals are wrong in
  the same way (three psychology names for criminology).
