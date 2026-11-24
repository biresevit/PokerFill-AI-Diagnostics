# Diagnosing a worker that stops reporting

A missing heartbeat is a symptom. A useful diagnosis follows the sequence rather than treating every later error as a new root cause.

1. Establish the last successful worker heartbeat and the schedule in force during the drop. Current machine state alone cannot prove historical connectivity.
2. Read scoped controller and updater archives covering the incident, using the collector's verified timezone. Check collection status and file coverage before interpreting missing events.
3. Compare network, SQL and process errors before the lapse with the watchdog event, scheduler ownership expiry and any subsequent startup/readiness records.
4. Use `event_groups` when repeated errors fill the 24 detailed-event slots. Each group preserves counts and first/last matching observations in scanned content. Counts are log matches, not distinct incidents or proof of continuous failure.
5. Keep the PC, updater process, worker process and individual site task separate. A live updater or Python process can coexist with a worker that no longer publishes.

The parser recognizes fixed operational templates including Windows network-name loss (`WinError 64`), socket resource exhaustion, thread creation failure, closed event loops, worker readiness and lost chat ownership. It never returns arbitrary exception messages, account names, raw stack traces or local paths.

Restart and connectivity scans prioritize controller logs before updater and decision-engine logs, preserving original archive file indices. If parsing reaches its time budget, the output explicitly reports incomplete coverage.

The model receives a dedicated compact worker view containing the first/last classified failures, watchdog details and coverage flags. This fixes a context-compaction failure where useful retained events could be replaced with an unavailable marker because a generic projection exceeded the per-item token budget. Full bounded evidence remains available with its original evidence ID.

## Progress evidence for the next worker release

The companion [ScheduleController worker instrumentation](https://github.com/biresevit/ScheduleController/blob/main/schedule_controller/worker_diagnostics.py) records the active operation when the heartbeat watchdog fires:

- A fixed phase such as `refresh`, `publish`, `claim` or resource telemetry.
- Phase age, time since the last successful heartbeat, and delay observed by the watchdog's event-loop timer.
- A bounded list of allowlisted coroutine names, without arguments, stack locals or paths.
- Startup and cleanup phase start/finish events, including interrupted phases and slow completions.

These local diagnostic records use the existing log collector. They become available on a PC only after that PC receives a worker release containing the instrumentation. The cloud parser is backward compatible: older archives still work, but their missing phase information cannot be reconstructed.

Instrumentation does not change scheduling, restart behavior, timeout values or poker actions. A missing phase completion in a bounded archive is a lead, not proof of a hang. High observed event-loop delay indicates that the timer ran late; it does not alone distinguish CPU load, OS suspension or blocking application code.

## Follow-up on the featured November incident

The archive that was pending during the original investigation subsequently became available. It contains Windows connection-loss errors before and shortly after the final worker heartbeat, an explicit watchdog event at **20:09:48.811 CET**, and repeated chat ownership errors starting at **20:11:01.513 CET**. The watchdog reports **124 seconds** without a successful database heartbeat. A new worker readiness or restart completion is not established by these bounded incident records.

This strengthens the connection-failure lead and confirms the watchdog response. The later chat ownership errors follow scheduler fencing and must not be presented as the initiating cause. A Windows socket failure does not by itself establish that SQL connectivity failed or identify the blocked worker operation. This older worker did not emit the new progress snapshots, so the exact stalled operation remains unknown.
