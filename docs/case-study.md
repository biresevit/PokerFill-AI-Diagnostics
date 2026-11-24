# Case study: a sudden ring-entry drop

This prepared replay adapts an investigation from 24 November 2026 about an incident the previous evening. The original question estimated a drop from 86 entries to about 56 around 20:09. The public version removes customer and site names, machine identifiers, account data, request IDs and infrastructure details. The affected worker is called **Worker A**. It uses no live customer connection and makes no model calls.

## What was checked

The saved investigation was compared with fresh, read-only queries of retained worker assignment, command, account-session and entry records, plus reconstructed seat history and timestamped schedule audit snapshots. The checks support worker unavailability and its timing; they do not establish an initiating root cause.

All times below are **Europe/Rome, CET (UTC+1)** on 23 November 2026.

| Time | Retained observation | Interpretation |
|---|---|---|
| 20:07:43.900 | Last worker heartbeat | Worker reporting stopped |
| 20:08:43 | Seat history progresses 86 → 73 → 62 → 58 → 54 | A sharp recorded drop aligns within one second of the 60-second heartbeat freshness deadline |
| Throughout the drop | Historical scheduled target remains 100 | A schedule reduction does not explain this event |
| 20:10:43.900 | 34 sessions and 41 entry records end for this worker; sessions are marked `machine_offline` | Later three-minute offline cleanup; entry records are not necessarily confirmed seats |
| 20:11:00.494 | One join and one leave command expire as `machine_offline` | Scheduler recorded worker unavailability |
| 20:30 | Scheduled target rises to 140 | This later boundary is separate from the drop |

The next-day updater heartbeat was fresh while the worker heartbeat remained stale. This shows that the updater and worker have separate health signals. It does not establish the PC's connectivity at the incident time.

## Was the original diagnosis correct?

**Its main conclusion is supported:** worker heartbeat loss is a strong explanation for the recorded count drop, with an unchanged target and subsequent offline cleanup. Calling the entire PC offline would be too broad. The records confirm that the worker became unavailable to the scheduler, not that Windows shut down or the poker clients disconnected.

**One detail needed correction:** the original answer described 86 → 73 → 62 and said the approximate low of 56 was unverified. Its retained evidence also contains the subsequent 58 and 54 observations. This replay uses the complete recorded sequence, without claiming that 54 was the minimum across every observation in the hour.

**The initiating cause remains unknown.** These database records do not distinguish a process crash, stalled application, network interruption, reboot or deliberate restart. The original investigation had no completed incident-covering worker archive. This verification does not claim a new log-based root cause. Logs with verified incident-time coverage would be needed to go further.

## Why this is a useful demonstration

The assistant connects a historical symptom with independent health and command evidence, checks the schedule in force at the time, and keeps probability separate from proof. The follow-up preserves context while explaining what the evidence cannot establish. No code changes, worker restarts or account actions are performed by the agent.

The public answer is an edited, anonymized reconstruction, not an exact transcript or an unrestricted live agent response. Other demo scenarios remain synthetic. Private verification exports are excluded from Git and deployment packages.
