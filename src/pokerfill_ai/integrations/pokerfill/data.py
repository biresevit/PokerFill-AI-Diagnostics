"""Bounded diagnostic reads. No SQL, identifiers or customer scope supplied by the LLM."""
import json
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from bisect import bisect_right
from zoneinfo import ZoneInfo
from pathlib import Path

from .vendor import activate
activate()
from backoffice.db import connect, Database
from backoffice.store import Store, Context


class DiagnosticDatabase(Database):
    def rows(self, sql, *params):
        if not sql.lstrip().upper().startswith('SELECT') or re.search(
                r'\b(INSERT|UPDATE|DELETE|MERGE|EXEC|EXECUTE|ALTER|DROP|CREATE|TRUNCATE|GRANT|DENY|REVOKE)\b',sql,re.I):
            raise RuntimeError('Only fixed diagnostic SELECT queries are permitted')
        return super().rows(sql,*params)

    def execute(self, *args):
        raise RuntimeError('Diagnostic database writes are prohibited')


@contextmanager
def reader():
    connection = connect()
    try:
        yield DiagnosticDatabase(connection)
    finally:
        # Diagnostics never commits a transaction, including after exceptions.
        connection.rollback()
        connection.close()


class Data:
    def __init__(self, scope, factory=reader):
        self.scope, self.factory = scope, factory

    def wait_for_logs(self, seconds=45):
        """Bounded preflight, no model tokens while installed collectors upload."""
        import time
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            with self.factory() as db:
                store=Store(db,Context(self.scope.customer_id,self.scope.provider,self.scope.subject,'viewer'))
                store.site(self.scope.site_id)
                pending=db.one("""SELECT COUNT(*) AS Count FROM backoffice.MachineLogRequest r
                    JOIN dbo.CustomerMachine m ON m.MachineId=r.MachineId AND m.CustomerId=r.CustomerId
                    JOIN scheduler.MachineAssignment a ON a.MachineId=m.MachineId
                    WHERE r.CustomerId=%s AND a.SiteId=%s AND m.IsActive=1
                    AND r.Status IN ('queued','collecting','uploading') AND r.ExpiresUtc>SYSUTCDATETIME()""",
                    self.scope.customer_id,self.scope.site_id)
            if not pending or not pending['Count']:return
            time.sleep(min(3,max(0,deadline-time.monotonic())))

    def read(self, name, hours=24, tournament='', game_format='RING', *, machine_id=0, around_utc='', topic='all', from_utc=None, to_utc=None):
        if name not in TOOLS or not 1 <= hours <= 168 or len(tournament) > 200 or game_format not in ('RING','SNG','MTT'):
            raise ValueError('Invalid diagnostic query')
        now=datetime.now(timezone.utc)
        start=datetime.fromisoformat(from_utc) if from_utc else now-timedelta(hours=hours)
        end=datetime.fromisoformat(to_utc) if to_utc else now
        sql_window=tuple(value.astimezone(timezone.utc).replace(tzinfo=None) for value in (start,end))
        s = self.scope
        with self.factory() as db:
            store = Store(db, Context(s.customer_id, s.provider, s.subject, 'viewer'))
            site = store.site(s.site_id)  # Independent tenant check on every tool call.
            customer = db.one('SELECT CustomerName AS name FROM dbo.Customer WHERE CustomerId=%s',s.customer_id)
            sid = s.site_id
            if name == 'get_schedule_compliance':
                # Read only schedule/entry evidence; the full Backoffice overview
                # also queries unrelated hand-history data which this identity cannot read.
                from backoffice.store import scheduled_target, ENTRY_FORMAT_SQL, ENTRY_LOBBY_JOIN
                from backoffice.mtts import planned_target
                schedules=store.schedules(sid)
                targets={fmt:scheduled_target(schedule) for fmt,schedule in schedules.items()}
                stamp=datetime.now(timezone.utc)
                targets['MTT']=planned_target(db,sid,stamp,stamp)(stamp)
                entries=db.rows(f'''SELECT x.format,x.status,COUNT(*) AS count FROM (
                    SELECT f.format,CASE WHEN f.format<>'RING' THEN CASE WHEN p.Status='seated' THEN 'seated'
                        WHEN p.Status='joining' THEN 'registered' WHEN p.Status='reserved' THEN 'reserved' ELSE 'uncertain' END
                        WHEN p.IsConfirmedSeat=1 THEN 'seated' WHEN p.Status IN ('reserved','joining') THEN p.Status ELSE 'uncertain' END AS status
                    FROM backoffice.EntryPresence p {ENTRY_LOBBY_JOIN} CROSS APPLY(SELECT {ENTRY_FORMAT_SQL} AS format) f
                    WHERE p.SiteId=%s AND p.IsOpen=1 AND p.IsLiveOwner=1) x GROUP BY x.format,x.status''',sid)
                result={'targets':targets,'entries':entries}
                result.update(timezone=site['timezone'], schedules=schedules,
                              joins_paused=bool(site.get('joins_paused')), enabled=site['enabled'])
                result['semantics'] = 'Ring counts confirmed seats; SNG/MTT count live registration commitments. MTT targets use per-tournament plans.'
            elif name == 'get_historical_ring_target':
                result = historical_ring_target(db, store, s, sid, start, end)
            elif name == 'get_schedule_history':
                zone = ZoneInfo(site['timezone'])
                metrics = store.entry_metrics(sid, {'format':game_format,
                    'from':start.astimezone(zone).date().isoformat(), 'to':end.astimezone(zone).date().isoformat()})
                points=metrics['points']
                if from_utc:
                    points=[point for point in points if start<=point['at']<=end]
                changes=[{'from':a['at'],'to':b['at'],'running_before':a['running'],'running_after':b['running'],
                          'delta':b['running']-a['running'],'scheduled_before':a['scheduled'],'scheduled_after':b['scheduled']}
                         for a,b in zip(points,points[1:]) if b['running']<a['running']]
                result = {'format':game_format,'timezone':site['timezone'],'from':from_utc or metrics['from'],'to':to_utc or metrics['to'],
                          'largest_drops':sorted(changes,key=lambda x:x['delta'])[:12],
                          'largest_shortfalls':sorted(points,key=lambda p:p['scheduled']-p['running'],reverse=True)[:12],
                          'limitations':['Use get_historical_ring_target for the ring schedule in force at the time. The scheduled values in this seat series may use the current plan.',
                              'Presence history uses retained entry timestamps and may miss observer/heartbeat interruptions.']}
            elif name == 'get_site_constraints':
                result = {'stakes':store.stakes(sid), 'scheduler':db.one('''SELECT MaxJoinsPerTick,MinJoinGapSeconds,
                    MaxJoinGapSeconds,SiteJoinsPerMinute,FillMinutes,TableFreshnessSeconds,HeartbeatTimeoutSeconds,
                    LastReconciledUtc,NextJoinUtc,NextMttUtc,NextSngUtc
                    FROM scheduler.Site WHERE SiteId=%s''',sid)}
            elif name == 'get_machine_health':
                result = db.rows('''SELECT a.MachineId,a.IsReady,a.IsEnabled,a.LastHeartbeatUtc,
                    COALESCE(v.PokerControllerVersion,a.ControllerVersion) AS ControllerVersion,a.MaxEntries,s.HeartbeatTimeoutSeconds,
                    t.SampledUtc,t.CpuPercent,t.MemoryPercent,t.ActiveEntries,
                    g.CurrentReleaseId,v.Label AS ReleaseLabel,g.AppsHealthy,g.Stage AS UpdaterStage,
                    g.LastHeartbeatUtc AS AgentHeartbeatUtc,u.CompletedUtc AS LastUpdateCompletedUtc
                    FROM scheduler.MachineAssignment a JOIN scheduler.Site s ON s.SiteId=a.SiteId
                    JOIN dbo.CustomerMachine m ON m.MachineId=a.MachineId AND m.CustomerId=s.CustomerId
                    LEFT JOIN scheduler.MachineUpdateAgent g ON g.MachineId=a.MachineId
                    LEFT JOIN scheduler.UpdateRelease v ON v.ReleaseId=g.CurrentReleaseId
                    OUTER APPLY(SELECT TOP(1) x.CompletedUtc FROM scheduler.MachineUpdateTask x
                        JOIN scheduler.UpdateRollout r ON r.RolloutId=x.RolloutId
                        WHERE x.MachineId=a.MachineId AND r.CustomerId=s.CustomerId AND r.ReleaseId=g.CurrentReleaseId
                            AND x.Status='completed' ORDER BY x.CompletedUtc DESC) u
                    OUTER APPLY(SELECT TOP(1) SampledUtc,CpuPercent,MemoryPercent,ActiveEntries
                        FROM scheduler.MachineTelemetry t WHERE t.SiteId=a.SiteId AND t.MachineId=a.MachineId
                        ORDER BY SampledUtc DESC) t WHERE a.SiteId=%s AND m.IsActive=1 ORDER BY a.MachineId''', sid)
            elif name == 'get_lobby_snapshot':
                result = db.rows('''SELECT TOP(100) TableId,TableName,TableType,GameType,IsOpen,
                    BigBlind,MinBuyIn,MaxBuyIn,CurrencyCode,MaxSeats,OccupiedSeats,ObservedUtc,
                    FullSnapshotUtc,ObserverMachineId,StartUtc,TournamentState
                    FROM scheduler.LobbyTable WHERE SiteId=%s ORDER BY ObservedUtc DESC,TableId''', sid)
            elif name == 'get_account_eligibility':
                result = db.rows('''SELECT p.IsEnabled,b.IsActive,p.IsSynthetic,
                    CASE WHEN p.CredentialRef IS NOT NULL OR (NULLIF(b.LoginEmail,'') IS NOT NULL
                        AND NULLIF(b.LoginPassword,'') IS NOT NULL) THEN 1 ELSE 0 END AS CredentialsConfigured,
                    CASE WHEN x.SessionId IS NOT NULL THEN 1 ELSE 0 END AS InSession,
                    CASE WHEN l.LeaseUntilUtc>SYSUTCDATETIME() THEN 1 ELSE 0 END AS Leased,
                    COUNT(*) AS Accounts FROM scheduler.AccountPolicy p
                    JOIN dbo.BotAccount b ON b.BotAccountId=p.BotAccountId
                    OUTER APPLY(SELECT TOP(1) SessionId FROM scheduler.AccountSession x
                        WHERE x.SiteId=p.SiteId AND x.BotAccountId=p.BotAccountId AND x.IsOpen=1) x
                    LEFT JOIN scheduler.AccountLease l ON l.BotAccountId=p.BotAccountId
                    WHERE p.SiteId=%s AND p.DeletedUtc IS NULL
                    GROUP BY p.IsEnabled,b.IsActive,p.IsSynthetic,
                    CASE WHEN p.CredentialRef IS NOT NULL OR (NULLIF(b.LoginEmail,'') IS NOT NULL
                        AND NULLIF(b.LoginPassword,'') IS NOT NULL) THEN 1 ELSE 0 END,
                    CASE WHEN x.SessionId IS NOT NULL THEN 1 ELSE 0 END,
                    CASE WHEN l.LeaseUntilUtc>SYSUTCDATETIME() THEN 1 ELSE 0 END''', sid)
                result = {'groups':result, 'capacity':db.one('''SELECT COUNT(*) AS EnabledActiveAccounts,
                    SUM(CASE WHEN DATEADD(minute,p.SessionCooldownMinutes,prior.ActualEndUtc)>SYSUTCDATETIME() THEN 1 ELSE 0 END) AS CoolingDown,
                    SUM(p.MaxTables) AS ConfiguredTableCapacity,
                    SUM(CASE WHEN p.MaxSngEntries=0 THEN 1 ELSE 0 END) AS SngDisabledAccounts,
                    SUM(CASE WHEN p.MaxMttEntries=0 THEN 1 ELSE 0 END) AS MttDisabledAccounts
                    FROM scheduler.AccountPolicy p JOIN dbo.BotAccount b ON b.BotAccountId=p.BotAccountId
                    OUTER APPLY(SELECT MAX(x.ActualEndUtc) AS ActualEndUtc FROM scheduler.AccountSession x
                        WHERE x.BotAccountId=p.BotAccountId AND x.SiteId=p.SiteId AND x.IsOpen=0) prior
                    WHERE p.SiteId=%s AND p.DeletedUtc IS NULL AND p.IsEnabled=1 AND p.IsSynthetic=0 AND b.IsActive=1''',sid),
                    'limitations':['Configured capacity is not immediately available capacity. Cooldowns, credentials, funds, game/stake limits and ownership can reduce it.',
                        'CredentialsConfigured reports stored credentials only. EvenBet workers use their platform-default authentication for configured usernames, so missing stored email/password is not evidence of an unusable login. Login usability requires observed outcomes.']}
            elif name == 'get_join_outcomes':
                result = db.rows('''SELECT TOP(100) Kind,Status,ResultCode,COUNT(*) AS Count,
                    MIN(UpdatedUtc) AS FirstUtc,MAX(UpdatedUtc) AS LastUtc
                    FROM scheduler.Command WHERE SiteId=%s
                    AND UpdatedUtc>=%s AND UpdatedUtc<%s
                    GROUP BY Kind,Status,ResultCode ORDER BY COUNT(*) DESC''', sid, *sql_window)
            elif name == 'get_tournament_diagnostics':
                result = {'plans': db.rows('''SELECT TOP(100) SeriesName,TotalBots,EarlyPercent,EarlyHours
                    FROM scheduler.MttPlan WHERE SiteId=%s AND (%s='' OR CHARINDEX(%s,SeriesName)>0)
                    ORDER BY SeriesName''', sid, tournament, tournament),
                    'lobby': db.rows('''SELECT TOP(100) t.TableId,t.TableName,t.TableType,t.StartUtc,
                        t.TournamentState,t.ObservedUtc,COUNT(e.EntryId) AS OpenEntries
                        FROM scheduler.LobbyTable t LEFT JOIN scheduler.Entry e
                        ON e.SiteId=t.SiteId AND e.TableId=t.TableId AND e.IsOpen=1
                        WHERE t.SiteId=%s AND t.TableType IN ('SNG','MTT')
                        AND (%s='' OR CHARINDEX(%s,t.TableName)>0)
                        GROUP BY t.TableId,t.TableName,t.TableType,t.StartUtc,t.TournamentState,t.ObservedUtc
                        ORDER BY t.ObservedUtc DESC''', sid, tournament, tournament),
                    'limitations': ['EvenBet does not publish all tournament registration metadata. Open entries alone do not prove a seat or successful registration.']}
            elif name == 'get_worker_log_signals':
                from .log_signals import read
                result = read(db, s, hours, machine_id=machine_id, around_utc=around_utc, topic=topic, from_utc=from_utc, to_utc=to_utc)
            elif name == 'get_incident_timeline':
                result = {'commands': db.rows('''SELECT TOP(100) CommandId,MachineId,Kind,Status,ResultCode,
                        CreatedUtc,UpdatedUtc FROM scheduler.Command WHERE SiteId=%s
                        AND UpdatedUtc>=%s AND UpdatedUtc<%s ORDER BY UpdatedUtc DESC''', sid, *sql_window),
                    'session_ends': db.rows('''SELECT TOP(100) MachineId,ActualEndUtc,EndReason
                        FROM scheduler.AccountSession WHERE SiteId=%s
                        AND ActualEndUtc>=%s AND ActualEndUtc<%s ORDER BY ActualEndUtc DESC''', sid, *sql_window),
                    'updates':db.rows('''SELECT TOP(24) t.MachineId,t.Status,t.StartedUtc,t.CompletedUtc,
                        r.Immediate,v.Label FROM scheduler.UpdateRollout r
                        JOIN scheduler.MachineUpdateTask t ON t.RolloutId=r.RolloutId
                        JOIN scheduler.UpdateRelease v ON v.ReleaseId=r.ReleaseId
                        WHERE r.CustomerId=%s AND r.SiteId=%s
                        AND t.StartedUtc>=%s AND t.StartedUtc<%s ORDER BY t.StartedUtc DESC''',s.customer_id,sid,*sql_window),
                    'limitations': ['Entry history is reconstructed from retained data, not a continuous observer recording. Use get_worker_log_signals for scoped timestamped logs and coverage. Update records prove rollout timing, not all possible process restart causes.']}
            else:
                installed = db.one("SELECT OBJECT_ID(N'scheduler.AccountDiagnosticSnapshot') AS object_id")
                if not installed or not installed.get('object_id'):
                    result = {'available': False, 'reason': 'Wallet telemetry is not installed. Insufficient funds cannot be confirmed. Diagnostic requests never log accounts in.'}
                else:
                    result = {'snapshots': db.rows('''SELECT x.BalanceState,x.CurrencyCode,x.MoneyType,COUNT(*) AS Accounts,
                        MIN(x.AvailableNative) AS MinimumNativeAvailable,MAX(x.AvailableNative) AS MaximumNativeAvailable,
                        SUM(CONVERT(decimal(38,0),x.AvailableNative)) AS TotalNativeAvailable,
                        MIN(x.ObservedUtc) AS OldestUtc,MAX(x.ObservedUtc) AS LatestUtc,
                        MAX(x.LastAttemptUtc) AS LatestAttemptUtc
                        FROM (SELECT d.CurrencyCode,w.available_native AS AvailableNative,w.money_type AS MoneyType,d.ObservedUtc,d.LastAttemptUtc,
                            CASE WHEN w.available_native IS NULL THEN 'unknown'
                                WHEN EXISTS(SELECT 1 FROM scheduler.AccountSession s
                                    WHERE s.SiteId=p.SiteId AND s.BotAccountId=p.BotAccountId AND s.IsOpen=1) THEN 'in_session'
                                WHEN EXISTS(SELECT 1 FROM scheduler.AccountSession s
                                    WHERE s.SiteId=p.SiteId AND s.BotAccountId=p.BotAccountId
                                    AND s.StartedUtc>d.ObservedUtc) THEN 'session_since_snapshot'
                                WHEN d.Outcome<>'balance_observed' THEN 'read_failed_cached'
                                WHEN d.ObservedUtc<DATEADD(hour,-24,SYSUTCDATETIME()) THEN 'stale'
                                WHEN d.SnapshotSource='logout' THEN 'cached_after_logout'
                                ELSE 'cached_during_session' END AS BalanceState
                            FROM scheduler.AccountPolicy p JOIN dbo.BotAccount b ON b.BotAccountId=p.BotAccountId
                            LEFT JOIN scheduler.AccountDiagnosticSnapshot d ON d.SiteId=p.SiteId AND d.BotAccountId=p.BotAccountId
                            OUTER APPLY OPENJSON(d.WalletsJson) WITH(money_type int '$.money_type',available_native bigint '$.available_native') w
                            WHERE p.SiteId=%s AND p.DeletedUtc IS NULL AND p.IsEnabled=1
                                AND p.IsSynthetic=0 AND b.IsActive=1) x
                        GROUP BY x.BalanceState,x.CurrencyCode,x.MoneyType''', sid),
                        'limitations': ['Includes idle accounts with their last observed funds; accounts without snapshots are counted as unknown.',
                            'Balances are cached observations, not guaranteed spendable now. Deposits, withdrawals, other sessions and pending settlements can change them.',
                            'Logout snapshots older than 24 hours are labelled stale. Session samples remain provisional. Failed reads preserve prior funds and timestamps.',
                            'Amounts retain native BluffPoker account-summary units. Do not compare with ring buy-ins or fiat without a verified conversion.',
                            'EvenBet money types are separate native wallets (cashInfoList mt/ma); do not sum different money types or assume their fiat conversion. An account with multiple wallets appears once in each money-type group.',
                            'CurrencyCode is the site label; EvenBet money-type-to-fiat mapping must be verified before buy-in affordability comparisons.']}
        return {'source': name, 'captured_utc': datetime.now(timezone.utc).isoformat(),
                'site_id': sid, 'site_name':site.get('name'), 'customer_id':s.customer_id,
                'customer_name':customer.get('name') if customer else None,
                'platform': site.get('platform'), 'row_limit': 100,
                'coverage': 'Current database state; result sets are bounded and may be truncated at 100 rows.', 'data': result}


def historical_ring_target(db, store, scope, site_id, start, end):
    from backoffice.store import scheduled_target
    if hasattr(store,'historical_schedule'):
        versions = store.historical_schedule(site_id,'RING',start,end)
    else:
        # Production diagnostics can read the schedule-only projection without
        # access to audit subjects or unrelated audit payloads.
        bounds=(start.replace(tzinfo=None),end.replace(tzinfo=None))
        params=(scope.customer_id,site_id)
        rows=db.rows("""SELECT AuditId, CreatedUtc, DetailJson FROM (
                SELECT TOP(1) AuditId, CreatedUtc, DetailJson FROM backoffice.DiagnosticRingScheduleHistory
                WHERE CustomerId=%s AND SiteId=%s AND CreatedUtc<=%s ORDER BY CreatedUtc DESC, AuditId DESC
            ) baseline
            UNION ALL
            SELECT AuditId, CreatedUtc, DetailJson FROM backoffice.DiagnosticRingScheduleHistory
            WHERE CustomerId=%s AND SiteId=%s AND CreatedUtc>%s AND CreatedUtc<=%s
            ORDER BY CreatedUtc, AuditId""",*params,bounds[0],*params,*bounds)
        versions=[]
        for row in rows:
            detail=json.loads(row['DetailJson'])
            versions.append((row['CreatedUtc'].replace(tzinfo=timezone.utc),{
                'timezone':detail['timezone'],'enabled':detail.get('enabled',True),
                'windows':[dict(weekday=w['Weekday'],start_minute=w['StartMinute'],
                                end_minute=w['EndMinute'],target=w['TargetEntries']) for w in detail['windows']]}))
    stamps=[stamp for stamp,_ in versions]
    candidates={start,*(stamp for stamp in stamps if start<stamp<=end)}
    minute=start.replace(second=0,microsecond=0)+timedelta(minutes=1)
    while minute<=end:
        candidates.add(minute)
        minute+=timedelta(minutes=1)
    changes=[]
    for stamp in sorted(candidates):
        index=bisect_right(stamps,stamp)-1
        target=scheduled_target(versions[index][1],stamp) if index>=0 else None
        if not changes or target!=changes[-1]['target']:
            changes.append(dict(at_utc=stamp.isoformat(),target=target,
                                cause='initial' if stamp==start else 'schedule_edit' if stamp in stamps else 'weekly_schedule_boundary'))
    return dict(from_utc=start.isoformat(),to_utc=end.isoformat(),changes=changes[:24],
                changes_truncated=len(changes)>24,source='timestamped schedule audit snapshots',
                limitations=['Missing audit baseline means target is unknown. Historic snapshots without an enabled flag assume scheduling was enabled.'])


from ...tools import TOOLS
