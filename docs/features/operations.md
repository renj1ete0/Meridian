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

## Design choices

- **No cron files** (§13.1). The timetable is data, editable from a UI, and travels with a
  snapshot.
- **`python -m <module>`, never a shell**, because the module name comes from an editable row.
- **Alert on conditions, not events.** A channel that reports every timeout trains its reader
  to ignore it.
- **Backups are a systemd timer, not a job.** `backup.sh` needs the Docker socket, and the
  container that fetches hostile pages should not have it.

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
