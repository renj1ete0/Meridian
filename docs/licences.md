# Licences

Task `B-11`. Whether every dependency is usable commercially, and where the
answer is conditional.

**Nothing had ever checked.** "Open source" is not one licence: AGPL,
non-commercial research terms and model-weight restrictions all look identical
to `uv add`, and the place they surface is a conversation nobody wants to be
having later. This is the audit, and
[`tests/unit/test_licences.py`](../tests/unit/test_licences.py) is the gate that
keeps it from going stale — a dependency arriving with a licence not on the
allowlist fails the suite.

Meridian itself is **MIT** ([`LICENSE`](../LICENSE)), and as of `B-11` the three
workspace packages declare it rather than inheriting nothing.

## Method

Everything below was read off the installed artefact — distribution metadata,
image labels, the model card on disk, the `LICENSE` inside a source tarball —
rather than recalled or looked up. Where that was not possible it says so. The
commands are in the test file, so this document can be regenerated rather than
re-researched.

One dependency is **compiled from source into an image we build**: Apache AGE,
in `deploy/postgres/Dockerfile`. Its tarball is verified against the checksum
Apache publishes before anything is unpacked, and its `LICENSE` and `NOTICE`
are copied into the image — so the verdict can be re-checked from the running
container rather than by downloading the source again.

## Verdict

**Nothing here blocks commercial use.** One dependency needed a stated choice
and one needed a stated boundary. Both were put to the operator and **both were
accepted on 2026-09-20**:

| Decision | Accepted |
|---|---|
| **SearXNG's AGPL boundary.** Run unmodified, in its own container, with no published port — aggregation, not derivative work. The commitment taken on is *not* to patch it and expose it. | yes |
| **`tld` is taken under MPL-1.1**, out of the three its author offers. We do not modify it, so MPL-1.1's file-level copyleft attaches to nothing. | yes |

Both are recorded here rather than only in a commit message, because the
question a year from now is "what did we agree to", and the answer needs to sit
next to the evidence. The `tld` choice is additionally recorded in code —
`CHOICES` in `tests/unit/test_licences.py`, with a test that fails if the
package stops offering MPL-1.1.

These are engineering readings of the licence text, not legal advice. This
document is written to be handed to a lawyer if Meridian ever carries
commercial weight: every dependency, the evidence for each verdict, and the two
questions that needed a human.

## Python dependencies

116 distributions in the default install. Every one resolves to a permissive
licence: MIT, BSD (2- and 3-clause), Apache-2.0, ISC, PSF, MPL, or a
combination of those.

No GPL. No AGPL. No non-commercial terms.

Four are worth naming because their expression is not a single permissive
licence:

| Package | Expression | Reading |
|---|---|---|
| `tld` | `MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later` | **A choice, and we take MPL-1.1.** Tri-licensed means the user picks; picking MPL-1.1 carries file-level copyleft on modifications to `tld` itself and no obligation on anything that imports it. We do not modify it. |
| `certifi` | `MPL-2.0` | Fine. MPL-2.0 is file-level copyleft — obligations attach to modified MPL files, not to software that uses them. We ship it unmodified. |
| `tqdm` | `MPL-2.0 AND MIT` | Same reading as `certifi`. |
| `numpy` | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` | A bundle of permissive licences covering vendored components. Nothing restrictive. |

`tld` is the only one that requires a decision rather than a reading, and the
decision is recorded here so it is not re-made differently later.

## Services and images

| Component | Licence | Evidence | Verdict |
|---|---|---|---|
| **SearXNG** | **AGPL-3.0-or-later** | `org.opencontainers.image.licenses` on `searxng/searxng:latest` | **Conditional — see below** |
| Crawl4AI 0.9.2 | Apache-2.0 | distribution metadata inside the image | Fine |
| Playwright | Apache-2.0 | distribution metadata inside the image | Fine |
| PostgreSQL 17 | PostgreSQL licence | `/usr/share/doc/postgresql-17/copyright` | Fine (permissive, BSD-like) |
| pgvector | PostgreSQL licence | `/usr/share/doc/pgvector/LICENSE` | Fine |
| `python:3.12-slim-bookworm` | Debian: a collection, predominantly permissive with GPL **programs** | no image label; Debian base | Fine — see "base images" |
| `nginx:1.27-alpine` | nginx: BSD-2-Clause; Alpine base | no image label | Fine |
| `node:22-slim` | Node.js: MIT; Debian base | no image label | Fine |
| **Apache AGE 1.7.0** | **Apache-2.0** | the `LICENSE` inside `apache-age-1.7.0-src.tar.gz` from downloads.apache.org, and the copy the image now ships at `/usr/share/doc/apache-age/LICENSE` | Fine |

### SearXNG is AGPL, and that is the one to understand

This is precisely the case the task anticipated, so the reasoning is written out
rather than summarised as "fine".

AGPL-3.0's distinguishing clause is §13: if you *modify* the program and let
users interact with it **over a network**, you must offer those users the
modified source.

What Meridian does:

- runs SearXNG **unmodified**, as a stock upstream image, in its own container;
- talks to it over HTTP from the worker, as a separate process;
- does not expose it to anyone — it sits on the `egress` network with no
  published port, and `docs/setup.md` never opens one.

That is aggregation, not derivative work, and it triggers no obligation. The
boundary matters and is worth stating plainly:

- **Patching SearXNG and exposing it** — even indirectly, via a proxy — turns on
  §13 and requires offering that source.
- **Vendoring any of its code into Meridian** would make Meridian AGPL.
- Neither is on any roadmap, and `config/searxng/` holds *settings*, not code.

If either becomes tempting, the cheap alternative is to keep the fork private to
a machine nobody else interacts with, or to replace the component.

### Base images

Debian and Alpine images are collections of separately licensed packages, not
single-licence artefacts, and some carry GPL **programs** (coreutils, bash).
Using a GPL program inside a container does not license the application: the
obligation is to offer the source of *those programs* if you redistribute the
image, which Debian and Alpine already satisfy upstream.

This matters only if Meridian's images are distributed to third parties. Today
they are pushed to a private registry for one operator's own machines
(scaffold §5), which is not distribution in the sense that triggers anything.

## Model weights

The task called these the likeliest problem — "a permissive *library* routinely
ships weights that are not" — and they are clean:

| Weights | Licence | Evidence |
|---|---|---|
| `BAAI/bge-m3` | **MIT** | `license: mit` in the model card of the snapshot on disk |
| `en_core_web_sm` (spaCy, `ner` extra) | MIT | upstream; **not verified from an artefact** — the extra is not installed |

`bge-m3` being MIT is the single most load-bearing finding here: it is the
embedding model the whole retrieval half depends on, and a field-of-use
restriction would have meant re-embedding the corpus against something else.

## What is not covered

Stated so the gaps are visible rather than implied:

- **Transitive licences of the base images' packages**, individually. The
  reasoning above is at the distribution level.
- **`en_core_web_sm`**, which is not installed here — it arrives with the
  `ner` extra. Apache AGE was in this list and has since been verified from the
  source tarball; it is in the table above.
- **Content licences.** This audit is about software. What a crawler may fetch,
  store and redistribute is §14.2 and a different question — `MERIDIAN_SERVE_RAW`
  defaults to off for that reason.
