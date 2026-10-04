# Meridian documentation

Meridian crawls the open web for a set of research topics, turns what it reads into
searchable, cited passages, and builds a map and a knowledge graph on top of them. Every
claim it shows can be followed back to the passage and the file it came from.

This folder is organised by what you are trying to do. The split follows
[Diátaxis](https://diataxis.fr/): how-to guides, reference and explanation are kept in
separate places, because a page that tries to be all three serves none of them well. The
decisions behind the design are kept as [decision records](adr/README.md).

## Start here

| You want to… | Read |
|---|---|
| Understand what the system is and how it fits together | [features/README.md](features/README.md), then the [architecture spec](spec/autonomous-research-system-spec.md) |
| Run it on your machine | [guides/setup.md](guides/setup.md) |
| Deploy it on a server | [guides/deployment.md](guides/deployment.md) |
| Know why something was decided | [adr/](adr/README.md) |
| Pick up where the last session stopped | [handover.md](handover.md) and [`TASKS.md`](../TASKS.md) |
| Contribute code | [`AGENTS.md`](../AGENTS.md): conventions, standards, testing, commits |

## Layout

| Folder | What is in it | Kind |
|---|---|---|
| [`features/`](features/README.md) | One document per feature: what it does, how it works, how to configure, operate and debug it | Explanation and reference |
| [`guides/`](guides/) | Step-by-step tasks: [setup](guides/setup.md), [deployment](guides/deployment.md), [two-board deployment](guides/deploy-sbc.md), [adding a source](guides/connectors.md), [connecting an assistant](guides/connecting-an-assistant.md) | How-to |
| [`reference/`](reference/) | Exact facts: [environment variables](reference/environment.md), [scheduled jobs](reference/scheduled-jobs.md), [commands](reference/commands.md), [licences](reference/licences.md) | Reference |
| [`adr/`](adr/README.md) | Decisions the operator made, with context and consequences | Decision records |
| [`spec/`](spec/) | The architecture as designed, the scaffold, and two focused specs | Design |
| [`design/`](design/) | Interface mocks (`*.dc.html`) and the design system | Design |
| [`handover.md`](handover.md) | The state of the build, traps that cost time, and what is verified live | Running notes |
| [`roadmap.md`](roadmap.md) | Phases and what each delivers | Planning |
| [`design-questions.md`](design-questions.md) | The research questions the corpus is built to answer | Planning |

## Conventions for writing here

- Code holds short docstrings and comments. The rationale, history, measurements and traps
  live here (see `AGENTS.md`, "Code and comment standards").
- A feature doc is updated in the same commit as the code it describes.
- Section references like §6.4 point to the architecture spec unless marked otherwise.
- Task IDs (`P2-21`, `B-127`) point to [`TASKS.md`](../TASKS.md), where each entry records
  what was done and why.
- Keep the subject matter general: describe mechanisms and trade-offs, not the specific
  topics or sources being researched.
