"""Opt-in event archival; committed cold copies always precede hot deletion.

Only old, explicitly completed successful jobs qualify. Job records never move.
Databases are committed independently: a failure between commits leaves a safe,
deduplicated overlap, which a later execution verifies before retrying deletion.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


EVENT_COLUMNS = ('event_id', 'job_id', 'step', 'status', 'message', 'created_at')
EVENT_SELECT = ','.join(EVENT_COLUMNS)
MIN_RETENTION_DAYS = 7


class ArchiveScopeConflict(ValueError):
    """A frozen preview no longer matches the events about to be archived."""


def archive_path(pool_path):
    path = Path(pool_path)
    return path.with_name(path.stem + '.archive.sqlite3')


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _cutoff(retention_days, now):
    if type(retention_days) is not int or not MIN_RETENTION_DAYS <= retention_days <= 36500:
        raise ValueError(f'事件至少保留最近 {MIN_RETENTION_DAYS} 天')
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError('归档时间必须包含时区')
    return current.astimezone(timezone.utc) - timedelta(days=retention_days)


def _source_paths(paths):
    result = []
    for value in paths:
        path = Path(value)
        if (path.is_symlink() or not path.is_file() or path.suffix != '.sqlite3'
                or path.name.endswith('.archive.sqlite3')):
            raise ValueError('请选择现有的物理任务池数据库')
        path = path.resolve()
        if path not in result:
            result.append(path)
    return result


def _connect(path, *, writable=False):
    return sqlite3.connect(Path(path).resolve().as_uri() + ('?mode=rw' if writable else '?mode=ro'),
                           uri=True, timeout=3)


def _batches(values, size=500):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _candidate_ids(db, cutoff):
    return [row[0] for row in db.execute('''SELECT j.job_id FROM automation_jobs j
        WHERE j.status='done' AND j.step='complete' AND julianday(j.updated_at)<julianday(?)
        AND EXISTS (SELECT 1 FROM automation_job_events e WHERE e.job_id=j.job_id)
        ORDER BY j.job_id''', (cutoff.isoformat(),))]


def _eligible_jobs(db, job_ids, cutoff):
    if not job_ids:
        return []
    placeholders = ','.join('?' for _ in job_ids)
    jobs = db.execute(f'''SELECT job_id,status,step,updated_at FROM automation_jobs
                          WHERE job_id IN ({placeholders})''', job_ids).fetchall()
    by_job = {}
    for event in db.execute(f'SELECT {EVENT_SELECT} FROM automation_job_events '
                           f'WHERE job_id IN ({placeholders}) ORDER BY job_id,event_id', job_ids):
        by_job.setdefault(event[1], []).append(event)
    eligible = []
    for job_id, status, step, updated_at in jobs:
        completed = _timestamp(updated_at)
        events = by_job.get(job_id, [])
        if status != 'done' or step != 'complete' or completed is None or completed >= cutoff or not events:
            continue
        # Do not infer success from the job status alone. In particular, a
        # complete/running event can coexist with a historical job status=done.
        if events[-1][2:4] != ('complete', 'done'):
            continue
        dates = [_timestamp(event[5]) for event in events]
        if any(when is None or when >= cutoff for when in dates):
            continue
        eligible.append((job_id, max(completed, dates[-1]), events))
    return eligible


def _empty_pool_report(path):
    return {'pool': str(path), 'archive': str(archive_path(path)), 'jobs': 0, 'events': 0,
            'oldestEventAt': None, 'newestEventAt': None,
            'oldestCompletedAt': None, 'newestCompletedAt': None}


def _include(report, jobs):
    report['jobs'] += len(jobs)
    for _, completed, events in jobs:
        report['events'] += len(events)
        for first, last, values in (
                ('oldestCompletedAt', 'newestCompletedAt', [completed]),
                ('oldestEventAt', 'newestEventAt', [_timestamp(event[5]) for event in events])):
            low, high = min(values).isoformat(), max(values).isoformat()
            report[first] = min(report[first], low) if report[first] else low
            report[last] = max(report[last], high) if report[last] else high


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _job_digest(job):
    job_id, completed, events = job
    return _digest((job_id, completed.isoformat(), events))


def _snapshot_scope(db, cutoff, report=None):
    approved = {}
    for batch in _batches(_candidate_ids(db, cutoff)):
        jobs = _eligible_jobs(db, batch, cutoff)
        if report is not None:
            _include(report, jobs)
        approved.update({job[0]: _job_digest(job) for job in jobs})
    return approved


def _scope_digest(approved):
    return _digest(sorted(approved.items()))


def _preview_digest(reports, retention_days, cutoff):
    return _digest({'pools': sorted((row['pool'], row['scopeDigest']) for row in reports),
                    'retentionDays': retention_days, 'cutoff': cutoff.isoformat()})


def _approved_scopes(expected, paths, retention_days, cutoff):
    if expected is None:
        return None
    if (not isinstance(expected, dict) or expected.get('previewOnly') is not True
            or expected.get('retentionDays') != retention_days
            or expected.get('cutoff') != cutoff.isoformat()
            or not isinstance(expected.get('pools'), list)):
        raise ArchiveScopeConflict('归档预览时间或保留范围不一致，请重新预览')
    scopes = {}
    for row in expected['pools']:
        if not isinstance(row, dict):
            raise ArchiveScopeConflict('归档预览范围无效，请重新预览')
        pool, digest = row.get('pool'), row.get('scopeDigest')
        if (not isinstance(pool, str) or pool in scopes or not isinstance(digest, str)
                or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest)):
            raise ArchiveScopeConflict('归档预览范围无效，请重新预览')
        scopes[pool] = digest
    if (set(scopes) != {str(path) for path in paths}
            or expected.get('scopeDigest') != _preview_digest(expected['pools'], retention_days, cutoff)):
        raise ArchiveScopeConflict('物理任务池范围与归档预览不一致，请重新预览')
    return scopes


def preview_event_archives(pool_paths, *, retention_days=90, now=None):
    """Read-only per-pool counts/time ranges; creates neither files nor tables."""
    cutoff = _cutoff(retention_days, now)
    reports = []
    for path in _source_paths(pool_paths):
        report = _empty_pool_report(path)
        with closing(_connect(path)) as db, db:
            db.execute('BEGIN')
            report['scopeDigest'] = _scope_digest(_snapshot_scope(db, cutoff, report))
        reports.append(report)
    return {'previewOnly': True, 'retentionDays': retention_days, 'cutoff': cutoff.isoformat(),
            'scopeDigest': _preview_digest(reports, retention_days, cutoff),
            'pools': reports, 'jobs': sum(row['jobs'] for row in reports),
            'events': sum(row['events'] for row in reports)}


def _copy_and_verify(path, events):
    """Commit a durable independent archive, rejecting conflicting event IDs."""
    target = archive_path(path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError('事件归档必须是同目录的普通数据库文件')
    with closing(sqlite3.connect(target, timeout=3)) as db, db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS event_archive_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT OR IGNORE INTO event_archive_metadata VALUES ('source_file',?)", (path.name,))
        source = db.execute("SELECT value FROM event_archive_metadata WHERE key='source_file'").fetchone()[0]
        if source != path.name:
            raise ValueError('事件归档不属于当前物理任务池')
        db.execute('''CREATE TABLE IF NOT EXISTS automation_job_events (
            event_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, step TEXT NOT NULL,
            status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS archived_events_job_idx ON automation_job_events(job_id,event_id)')
        inserted = 0
        for batch in _batches(events):
            changed = db.total_changes
            db.executemany(f'INSERT OR IGNORE INTO automation_job_events ({EVENT_SELECT}) VALUES (?,?,?,?,?,?)', batch)
            inserted += db.total_changes - changed
            ids = [event[0] for event in batch]
            placeholders = ','.join('?' for _ in ids)
            copied = db.execute(f'SELECT {EVENT_SELECT} FROM automation_job_events '
                                f'WHERE event_id IN ({placeholders}) ORDER BY event_id', ids).fetchall()
            expected = sorted(batch, key=lambda row: row[0])
            if len(copied) != len(expected) or copied != expected:
                raise ValueError('归档事件校验失败，热库事件已保留')
        # Never DELETE from the hot database until this separate commit succeeds.
        db.commit()
        return inserted


def _delete_hot_events(db, events):
    deleted = 0
    for batch in _batches(events):
        ids = [event[0] for event in batch]
        cursor = db.execute('DELETE FROM automation_job_events WHERE event_id IN (' +
                            ','.join('?' for _ in ids) + ')', ids)
        deleted += cursor.rowcount
    if deleted != len(events):
        raise ValueError('热库删除数量发生变化，已回滚本批删除')
    return deleted


def archive_task_events(pool_paths, *, retention_days=90, now=None, batch_jobs=25, expected_preview=None):
    """Explicit execution; each short batch holds only one physical pool's writer.

    Copy verification/commit precedes hot deletion. An error stops that pool and
    is reported; already completed batches and other pools remain independent.
    With expected_preview, freeze exact approved jobs/content under each pool's
    writer lock and recheck each batch. Newly eligible jobs are never added.
    """
    cutoff = _cutoff(retention_days, now)
    if type(batch_jobs) is not int or not 1 <= batch_jobs <= 500:
        raise ValueError('每批归档任务数应在 1 到 500 之间')
    paths = _source_paths(pool_paths)
    approved_scopes = _approved_scopes(expected_preview, paths, retention_days, cutoff)
    reports, errors = [], []
    scope_conflict = False
    for path in paths:
        report = {**_empty_pool_report(path), 'archivedEvents': 0, 'deletedEvents': 0, 'recoveredEvents': 0}
        try:
            approved_jobs = None
            if approved_scopes is not None:
                with closing(_connect(path, writable=True)) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    approved_jobs = _snapshot_scope(db, cutoff)
                    if _scope_digest(approved_jobs) != approved_scopes[str(path)]:
                        raise ArchiveScopeConflict('可归档任务或事件内容已变化，请重新预览后确认')
                candidates = sorted(approved_jobs)
            else:
                with closing(_connect(path)) as db:
                    candidates = _candidate_ids(db, cutoff)
            for batch in _batches(candidates, batch_jobs):
                with closing(_connect(path, writable=True)) as db, db:
                    # Freeze status/events while the separate cold transaction
                    # commits, so a concurrent retry cannot lose new events.
                    db.execute('BEGIN IMMEDIATE')
                    jobs = _eligible_jobs(db, batch, cutoff)
                    if approved_jobs is not None:
                        current = {job[0]: _job_digest(job) for job in jobs}
                        if current != {job_id: approved_jobs[job_id] for job_id in batch}:
                            raise ArchiveScopeConflict('已确认任务的状态或事件已变化，已停止继续归档；请重新预览')
                    events = [event for _, _, rows in jobs for event in rows]
                    if not events:
                        continue
                    inserted = _copy_and_verify(path, events)
                    report['archivedEvents'] += inserted
                    report['recoveredEvents'] += len(events) - inserted
                    deleted = _delete_hot_events(db, events)
                    db.commit()
                    report['deletedEvents'] += deleted
                    _include(report, jobs)
        except Exception as error:
            report['error'] = str(error)[:300]
            entry = {'pool': str(path), 'error': report['error']}
            if isinstance(error, ArchiveScopeConflict):
                scope_conflict = True
                entry['code'] = 'scope_conflict'
            errors.append(entry)
        reports.append(report)
        if scope_conflict:
            break
    return {'previewOnly': False, 'retentionDays': retention_days, 'cutoff': cutoff.isoformat(),
            'pools': reports, 'errors': errors, 'scopeConflict': scope_conflict,
            **{key: sum(row[key] for row in reports) for key in
               ('jobs', 'events', 'archivedEvents', 'deletedEvents', 'recoveredEvents')}}


def read_events_for_jobs(pool_path, job_ids, *, connection=None):
    """Read hot first, then cold; this order cannot miss a committed move.

    The archive only gains rows. Existing caller connections are reused without
    changing their transaction. Hot rows win during a recoverable copy overlap.
    """
    ids = list(dict.fromkeys(job_ids))
    result = {job_id: [] for job_id in ids}
    if not ids:
        return result

    def read(db):
        rows = []
        for batch in _batches(ids):
            rows.extend(tuple(row) for row in db.execute(f'SELECT {EVENT_SELECT} FROM automation_job_events '
                'WHERE job_id IN (' + ','.join('?' for _ in batch) + ') ORDER BY event_id', batch))
        return rows

    if connection is None:
        with closing(_connect(pool_path)) as db:
            hot = read(db)
    else:
        hot = read(connection)
    cold = []
    target = archive_path(pool_path)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ValueError('事件归档必须是同目录的普通数据库文件')
        with closing(_connect(target)) as db:
            # A crash during initial schema creation leaves no committed tables;
            # its hot rows still exist and are sufficient until the next retry.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_job_events'").fetchone():
                source = db.execute("SELECT value FROM event_archive_metadata WHERE key='source_file'").fetchone()
                if source is None or source[0] != Path(pool_path).name:
                    raise ValueError('事件归档不属于当前物理任务池')
                cold = read(db)
    merged = {row[0]: row for row in cold}
    merged.update({row[0]: row for row in hot})
    for row in sorted(merged.values(), key=lambda row: row[0]):
        result[row[1]].append(dict(zip(EVENT_COLUMNS, row)))
    return result


def read_job_events(pool_path, job_id, *, connection=None):
    return read_events_for_jobs(pool_path, [job_id], connection=connection)[job_id]
