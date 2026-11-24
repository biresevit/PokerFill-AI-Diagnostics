"""Fixed operational tools; identifiers and SQL are never supplied by the model."""
TOOLS = {
    'get_schedule_compliance': 'Current ring/SNG/MTT targets and actual entries, pause state and weekly schedules.',
    'get_schedule_history': 'Largest actual entry drops and schedule shortfalls reconstructed over the last 1–168 hours (whole local dates). Choose game_format RING, SNG or MTT. Targets use current plans, not historic schedule edits.',
    'get_historical_ring_target': 'Actual ring schedule in force during the incident from timestamped schedule audit snapshots. Returns target changes, including recurring schedule boundaries and live edits. Use before interpreting ring drops.',
    'get_site_constraints': 'Stake ranges, tournament exclusions, join pacing, fill window and freshness limits. Operational constraints only, no playing strategy.',
    'get_machine_health': 'Worker readiness, heartbeat and latest machine telemetry.',
    'get_lobby_snapshot': 'Latest retained lobby including stale observations, vacancies and buy-ins. Compare observation times with current time.',
    'get_account_eligibility': 'Aggregated account eligibility, credential presence, sessions and leases. Never credentials.',
    'get_funding_capacity': 'Wallet evidence availability. Missing snapshots mean funds are unknown, never infer a balance.',
    'get_join_outcomes': 'Grouped worker command results within 1–168 hours. Result codes are evidence, not automatically a root cause.',
    'get_tournament_diagnostics': 'MTT plans and SNG/MTT lobby/entry counts. Optional tournament name substring.',
    'get_incident_timeline': 'Recent command and session-end timeline within 1–168 hours.',
    'get_worker_log_signals': 'Scoped worker, updater and PDecision logs: collection status, timestamped network/watchdog/ownership/start/stop errors, first/last event groups and file coverage. Use topic restart for worker_restarted or missing heartbeats, machine_id for a PC, around_utc (ISO with offset) for the first failure. Collector timezone resolves local clocks when verified; otherwise local timestamps remain unverified. Explicit incident windows filter verified clocks. At most one machine archive per call. No raw lines.'}
