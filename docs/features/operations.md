# Operations

What keeps the stack running unattended: a scheduler that reads its timetable from the
database, a health line and alerts on sustained conditions, an optional Telegram bot for
reports and commands, heartbeats that let Docker restart a wedged process, and Admin's Crawl
health screen for the hours in between.

- **Code:** `services/worker/worker/scheduler.py`, `digest.py`, `telegram.py`, `bot.py`,
  `commands.py`, `liveness.py`; `packages/meridian_core/meridian_core/schedule.py`,
  `alerts.py`, `crawlhealth.py`, `attempts.py`, `logging.py`
- **Tasks:** `P5-06`–`P5-08`, `P6-08`, `P6-25`, `B-15`, `B-19`

## How it works

**The scheduler** claims due rows from `scheduled_jobs` with `SKIP LOCKED` and a lease, the
same way the crawl claims queue rows. It runs each job as a `python -m` subprocess and
enforces a timeout. Two schedulers can run safely; a dead one's claim expires. A missed run
is run once, then rescheduled from now. The full list is in
[reference/scheduled-jobs.md](../reference/scheduled-jobs.md).

**The digest** (daily) sends the health line: queue depth, fetch success, novelty pass rate,
and corpus growth.

**Alerts** fire on sustained conditions only, each measured over a window and quiet for a
cooldown afterwards (`MERIDIAN_ALERT_COOLDOWN_H`, 6 h):

- fetch success below threshold for an hour;
- no successful fetch recently;
- the queue drained;
- the disk holding the raw store above 80%;
- the embedding backlog only growing.

Findings are written to `notifications` *before* they are sent, so a failed send loses the
delivery, not the evidence. The in-app notifications panel reads the same rows, so a
deployment without Telegram still has monitoring.

**The Telegram bot** (optional) long-polls, so it needs no inbound port. Messages from any
chat but `TELEGRAM_CHAT_ID` get no reply at all. Commands: `/status`, `/weights`, `/pause`,
`/boost`, `/seed`. Commands that need synthesis refuse by name. Nothing reachable deletes.
The update backlog is dropped at start-up, because a command is an instruction about now.

**Liveness.** The worker and scheduler touch a heartbeat file each iteration; the container
healthcheck reads its age. A process that is running but stuck is restarted, not just one that
exits.

**Logs** are one JSON object per line on stdout, collected by Docker's json-file driver with
rotation. Every run logs its `run_id`, carried through async tasks by a context variable.

**Crawl health** (Admin) answers "when did it stop, and why": a verdict in words, 24 hours of
attempts by outcome with empty hours drawn, and the queue beside the embedding backlog.

<a id="display-time-zone"></a>**Display time zone** (`B-145`, ADR 0009). Every instant is stored
as `timestamptz` (UTC) and sent as ISO 8601 with an offset. Only text a person reads is
converted, into one display zone: `display_timezone` in the global fetch-policy row,
`Asia/Singapore` (GMT+8) unless changed in **Admin → Display**. An unknown stored value falls
back to the default, with a warning. The browser formats through `web/src/lib/time.ts`, which
loads the zone from `GET /api/explore/settings` at start-up. The server formats notification
and confirmation text through `meridian_core.timefmt`, labelled with the offset
(`2026-10-05 08:00 GMT+8`). Calendar dates (publication dates) are not shifted. Budget months
and the Ask panel's daily cap still reset at UTC midnight, where provider billing resets. A
web test fails if any file but `time.ts` formats a time.

The zone list holds every zone the browser knows, over four hundred, so **Admin → Display**
narrows it by any part of a name (a space for the underscore) or by an offset (`GMT+8`), and a
text that leaves one zone selects it (`B-210`). The stored zone is always in the list, even
when the browser's is not: `Intl.supportedValuesOf` omits `UTC`, and a stored `UTC` used to
read as the list's first zone.

## Design choices

- **No cron files** (§13.1). The timetable is data, editable from a UI, and travels with a
  snapshot.
- **`python -m <module>`, never a shell**, because the module name comes from an editable row.
- **Alert on conditions, not events.** A channel that reports every timeout trains its reader
  to ignore it.
- **Backups are a systemd timer, not a job.** `backup.sh` needs the Docker socket, and the
  container that fetches hostile pages should not have it.

### The scheduler

`worker/scheduler.py` and `meridian_core/schedule.py` (`P5-06`, §13.1).

- **No crontab.** A crontab is configuration nobody can see from the interface, cannot be
  changed without SSH, and does not travel with a snapshot: a corpus restored elsewhere would
  arrive with no idea what was supposed to run.
- **Jobs are subprocesses.** In-process, one crash would take the scheduler with it and one
  job's memory would be the scheduler's; a subprocess is also something a timeout can kill.
- **`python -m <module>`, never a shell**, because the module comes from a row a UI can edit
  (§13.2); a shell would make the timetable a remote execution surface.
- **The queue's shape** (`P1-01`): `FOR UPDATE SKIP LOCKED` and a lease, so two schedulers
  running by accident (a timer and a container, a deploy overlapping a restart) get different
  jobs. A lease, not a status flip, so a scheduler that dies mid-job leaves a claim that
  expires rather than a job "running" forever.
- **Missed runs are run once, not caught up.** Rescheduling from now means one run and then the
  normal cadence, rather than a burst the moment the machine comes back.
- **Not its own supervisor.** `restart: unless-stopped` owns restarts (`P1-30`); two supervisors
  racing is how a crash loop goes invisible.
- **Its heartbeat** is beaten after the claim, not before (`B-19`): the round trip to claim is
  what can wedge here, unlike the worker, whose lanes beat before their work. While a job runs
  the beat continues, or a half-hour backfill would get the scheduler restarted mid-job. That
  beat proves only that a job is in flight and under its timeout, and the timeout's kill is what
  stops it keeping a hung job looking alive.

### Alerts and the digest

`alerts.py` and `worker/digest.py` (`P5-07`, §13.3). §13.3: "Single-event alerting teaches me to
ignore the channel, which is the real failure mode." An unread channel is the appearance of
monitoring without the fact of it.

- **Suppression is in the database.** The digest runs on a timer and exits, so anything it
  remembered in memory would be forgotten, and the same alert would arrive on every run.
  `notifications` holds it, and the in-app panel (`P6-08`) shows exactly what was sent.
- **Everything is measured from rows or the disk.** A rate derived from `fetch_attempts` cannot
  drift from them, as kept counters could.
- **The digest is not an alert.** It is sent every run, because a daily line is worth having
  when things went fine; the alerts under it are suppressed by cooldown, so a condition lasting
  all night is one message.
- **Fields are read directly, never through `getattr(..., default)`.** A defaulted lookup turns a
  renamed attribute into a permanent zero, so the reporter reports nothing: the digest once read
  `fetch.total` and `novelty.duplicate_rate`, neither of them real, and said "nothing attempted,
  0% duplicate" on a corpus that had both.
- **The queue drained** (`v0.26.0`). A crawl that drains its frontier and idles logs and reports
  exactly what a healthy one does; there are no failures, only no work, for days.
- **The embedding backlog** (`B-22`). The embedding pass once ran hourly inside a 30-minute
  ceiling, was killed at the ceiling every time, and left a backlog no later window could clear;
  the only trace was `last_status`. The corpus grew and became less searchable, and search does
  not say so: `degraded` means an arm is absent, while an arm covering a third of the corpus
  returns fewer, worse results silently. Lag itself is normal (§6.1's pipeline is three passes);
  a window that only widens is not. Two thresholds: an absolute count, set where a CPU-only box
  needs most of an hour to catch up, and a share, since a small corpus 90% unembedded is in
  trouble the count would call fine.

### Telegram

`telegram.py` (transport), `bot.py` (the loop) and `commands.py` (meaning), `P5-07`, §13.3.

- **A control surface, not a notifier**: it can change steering and trigger runs. So the chat
  id is configured, never discovered; a message from any other chat is dropped *before parsing*
  and gets no reply, since error messages would map the command surface and any answer confirms
  the bot is listening. An unset `TELEGRAM_CHAT_ID` refuses every chat rather than allowing any.
- **The token is environment, never database** (§11.11): the database is snapshotted off-device.
- **Absent is supported.** With no token the digest still evaluates and records; the bot
  container idles rather than exits, because `restart: unless-stopped` restarts a clean exit too
  and "no bot configured" would become a restart every second. It keeps beating, so an idle bot
  is distinguishable from a dead one.
- **Long polling**, so no inbound port, certificate or public hostname. `poll` returns `[]` for
  "nothing said" and `None` for "could not ask", which a loop must not conflate or a dead network
  becomes a hot loop.
- **The backlog is dropped at start-up.** Telegram replays undelivered updates for 24 hours, so a
  bot restarted after a night down would run every command sent meanwhile; `/run` at midnight is
  not a request for a run at breakfast, and a doubled `/boost` is one nobody asked for. The first
  poll asks for only the last update (`offset=-1`) and acknowledges past it, reading nothing.
- **The offset advances before the work**, even for a command that fails: an unacknowledged
  update that makes a handler raise is redelivered forever, and no later command is seen.
- **Sending never raises and uses no Markdown.** It is the channel that reports failures, and
  findings are already recorded; an unbalanced asterisk from a crawled title would make Telegram
  reject the whole message.
- **Commands parse purely, then execute**, so every refusal and bound is testable without a
  database or a token. Commands that need the orchestrator **refuse by name** rather than being
  absent: a command that silently does nothing is worse, on a surface used from a phone. Nothing
  reachable deletes (§10.2): the worst a compromised chat can do is misweight the crawl, visibly
  and reversibly. Every change is logged with `actor="telegram"`, since a weight moved from a
  phone at midnight is reviewed differently from one changed in Admin.

### Liveness

`liveness.py` (`P5-08`, §13.4). `restart: unless-stopped` handles a process that exits, not one
still running and no longer working (a wedged fetch, a pool that never recovers, a lane stuck on
a lock).

- **A file, not a port**: the worker serves no HTTP and should not start to be observable, and
  the file must be readable without credentials. `/tmp` is a tmpfs in a read-only container
  (`P1-30`), so the file cannot outlive a restart.
- **Staleness is measured** by mtime: a process that died leaves its file behind, and a probe
  that passed on a dead process would also suppress the restart. A missing file is not alive;
  the healthcheck's `start_period` covers start-up.
- **The threshold is generous**: one fetch can take a minute against a slow origin, and a probe
  that fires during normal work trains its reader to ignore it.

### Crawl health

`crawlhealth.py` (`P6-25`). `/progress` answers "is anything happening" for a fresh install's
first hour. An unattended run of days asks *when did it stop, and why*: a day of history, the
outcome mix, the queue beside the backlog, and a verdict in words. The failure is silence: a
stalled or drained crawl logs exactly what a healthy one does, and the alerts catch it hours
later on purpose.

- **A stall is no attempt for one claim lease** with ready work: the codebase's own statement of
  "longer than any fetch should take". Shorter, a slow site reads as an outage; much longer, a
  dead worker costs an afternoon.
- **The verdict is judged from numbers alone**, so every branch can be driven by a test against a
  database that holds a real crawl. A recent fetch is `crawling` even with an empty queue, since
  the last rows are still being worked.
- **Everything is as of one instant** (`now`), counting only queue rows that existed then, which
  lets tests choose an instant no real row is near.
- **Rolling hours, not clock hours**: a clock-aligned chart always ends in a partial, short bar,
  a false alarm on a screen for spotting a slowdown.

### Logging

`logging.py`. An unattended system fails silently, so every record must be parseable from day
one. Docker's json-file driver makes stdout the only collection point, so the format is decided
once: one JSON object per line. Standard library only, to keep the dependency list short.

- **`run_id` is a `ContextVar`**: each asyncio task gets its own copy, so concurrent requests
  never bleed ids into each other, and it is ambient rather than threaded through every call.
  The service name is one too, so there is one mechanism to reason about.
- Caller fields are whatever a record carries beyond the standard `LogRecord` attributes, with
  no allowlist to keep in step with `logging`'s internals.
- Third-party libraries are quietened (every SQL statement, every checkout), but stay overridable
  for debugging.
- `trafilatura` is held at CRITICAL (`B-154`): it logs an empty or unparseable page at ERROR,
  which the extractor already records as a failed extraction, so each one counted as a worker
  error.
- Exceptions are formatted into the record, since `exc_info` is not JSON-serialisable and a
  check grepping for "Traceback" needs the text inline.
- `configure_logging` is idempotent: a second call updates the level and service rather than
  stacking a second handler and doubling every line.

## Operating it

- Change a job's interval or enable it: update its `scheduled_jobs` row (an Admin screen is
  planned). **A new job's row must be inserted by hand on a live stack**; the seed runs only on
  first boot.
- `docker compose logs -f scheduler` shows each job starting and its exit status;
  `scheduled_jobs.last_status` records `ok`, `failed` or `timeout`.

## Tests

`tests/integration/test_schedule.py`, `test_alerts.py`, `test_crawl_health.py`,
`test_commands.py`; `tests/unit/test_bot.py`, `test_telegram.py`, `test_commands.py`,
`test_liveness.py`, `test_scheduler_liveness.py`, `test_crawlhealth.py`.
