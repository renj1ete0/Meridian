# Scheduled jobs

The scheduler (`python -m worker.scheduler`) reads its timetable from the `scheduled_jobs`
table. There are no cron files. `config/schedule.yaml` seeds the table at first boot; after
that the database is authoritative, and a new job's row has to be inserted on a live stack
by hand (see [features/operations.md](../features/operations.md)).

Each job runs as `python -m <module> <args>` in a subprocess. It is never run through a shell,
because the row is editable from a UI. A missed run is run once and then rescheduled from now;
it is not caught up.

| Job | Command | Every | Seeded | What it does | Feature |
|---|---|---|---|---|---|
| `topics` | `worker.retopic --apply` | 1 h | on | Labels sources and passages with topics from their vectors | [topics](../features/topics.md) |
| `places` | `worker.places --apply` | 1 h | on | Tags sources with the places they are about | [places-and-terms](../features/places-and-terms.md) |
| `novelty` | `worker.novelty --once` | 1 h | on | Marks near-duplicate passages | [duplicates](../features/duplicates.md) |
| `hostscore` | `worker.hostscore --once` | 1 h | on | Rebuilds per-host relevance; blocks domains that refuse everything | [discovery](../features/discovery.md) |
| `sitemapmine` | `worker.sitemapmine --apply` | 1 h | on | Queues the sitemaps of proven hosts | [discovery](../features/discovery.md) |
| `requeue_links` | `worker.requeue_links --apply` | 1 h | on | Moves links from off-topic pages to the bottom of the queue | [discovery](../features/discovery.md) |
| `requeue_dois` | `worker.requeue_dois --apply` | 1 h | off | Ranks queued papers by the pages that cite them | [discovery](../features/discovery.md) |
| `steerproposals` | `worker.steerproposals --once` | 1 h | on | Proposes steering changes and applies unopposed ones | [steering](../features/steering.md) |
| `seedsearch` | `worker.seedsearch --once` | 3 h | on | Queues new search queries per topic and from the graph | [discovery](../features/discovery.md) |
| `digest` | `worker.digest` | 1 d | on | Health line and sustained-condition alerts | [operations](../features/operations.md) |
| `areas` | `worker.areas --once` | 1 d | on | Rebuilds the map's areas and names them | [map](../features/map.md) |
| `boilerplate` | `worker.boilerplate --once` | 1 d | on | Recomputes the lines each site repeats | [extraction](../features/extraction.md) |
| `harvest` | `worker.harvest` | 1 d | on | Harvests acronym definitions from on-topic documents | [places-and-terms](../features/places-and-terms.md) |
| `translate` | `worker.translate --once` | 1 d | on | Looks up other-language names for the search vocabulary | [discovery](../features/discovery.md) |
| `requeue` | `worker.requeue --apply` | 1 d | on | Applies host verdicts to links already queued | [discovery](../features/discovery.md) |
| `docdupes` | `worker.docdupes --apply` | 1 d | off | Marks sources that are copies of earlier ones | [duplicates](../features/duplicates.md) |
| `sweep` | `worker.sweep` | 1 d | on | Retention sweep, report only (deletes only with `--apply`, by hand) | [source-quality](../features/source-quality.md) |
| `embed` | `worker.embed --once` | 1 h | off | Superseded by the `embed` service, which runs continuously | [embedding](../features/embedding.md) |

"Seeded" is the state `config/schedule.yaml` gives a fresh install. Change it in the
database, which is where it is read from.
