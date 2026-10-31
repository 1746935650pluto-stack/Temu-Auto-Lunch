"""Read-side queries for independent pools, without taking their writer gates.

Only a page's display fields are hydrated.  Task totals and personnel options
use compact routing metadata; item pages are selected in SQLite before JSON
snapshot fields are read.  Databases are never attached for writes.
"""
from contextlib import contextmanager, closing, ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time


GROUPS = {'processing': ('queued', 'running'),
          'attention': ('needs_input', 'needs_review', 'failed', 'interrupted'),
          'publish': ('ready_publish',), 'history': ('done', 'canceled')}

FAILURE_CATEGORIES = frozenset({'template', 'size_chart', 'attributes', 'database', 'transient', 'unknown', 'none'})


def explicit_attribute_rejection(message):
    """Recognize a vendor validation rejection persisted with a safety suffix.

    Save errors were historically recorded as "result unconfirmed" after a
    generic exception.  A plain required-attribute validation is proof that
    the save was rejected, so it can be re-run through the regular preflight.
    Transport and HTTP failures remain ineligible.
    """
    text = str(message or '')
    head = text.split('；远端写入结果未确认', 1)[0].strip()
    lowered = head.casefold()
    if any(marker in lowered for marker in ('timeout', 'timed out', '超时', 'connection', '连接',
                                              'unable to reach', 'gateway', 'http 5')):
        return False
    return bool(re.search(r'(?:【[^】]{1,100}】|[\u4e00-\u9fffA-Za-z0-9]{2,100})\s*[，,:：]?\s*'
                          r'(?:必填|必选|必须设置|必须输入|不能为空|至少选择)', head))


def failure_category(status, step, message):
    """Display classification only; message matching never authorizes a write."""
    if status not in GROUPS['attention']:
        return 'none'
    text = str(message or '').casefold()
    if any(word in text for word in ('尺码', '尺寸表', 'size chart', 'sizechart', 'size template', 'sizetemplate', 'groupidandvaluemap')):
        return 'size_chart'
    if step in {'freight', 'warehouse'} or any(word in text for word in ('运费模板', '仓库', '物流配置', 'freight template', 'warehouse')):
        return 'template'
    if any(word in text for word in ('属性', 'attribute')):
        return 'attributes'
    if any(word in text for word in ('database is locked', 'database is busy', '数据库占用', 'sqlite')):
        return 'database'
    if any(word in text for word in ('timeout', 'timed out', '超时', '限流', 'rate limit', 'too many requests', '连接失败', 'connection reset', 'temporarily unavailable',
                                     'unable to reach deepseek api', 'unable to reach miaoshou api',
                                     'miaoshou returned http 500', 'miaoshou returned http 502', 'miaoshou returned http 503', 'miaoshou returned http 504')):
        return 'transient'
    return 'unknown'


def job_failure_metadata(row):
    category = failure_category(row.get('status'), row.get('step'), row.get('message'))
    action = ''
    reason = '当前记录没有需要批量处理的失败' if category == 'none' else '缺少可证明安全重试的状态，请查看详情后处理'
    if row.get('step') == 'publish':
        reason = '发布结果必须人工核查，批量处理不会重新发布'
    elif row.get('status') == 'needs_review':
        reason = '远端写入结果待核查，批量处理不会重放'
    elif not row.get('available', True):
        reason = '来源或目标账号不可用，请先核查账号'
    elif row.get('status') == 'needs_input':
        # Logistics choices are an explicit, supported pause. Other supported
        # pauses are written only while run_automation_job.wrote_remote is false.
        if row.get('step') in {'freight', 'warehouse'}:
            action, reason = 'refresh-logistics', '同步最新物流配置；仅存在唯一默认选项时自动续队'
        elif row.get('step') in {'claim', 'preflight', 'prepare', 'attributes', 'titles'} and category in {
                'size_chart', 'attributes', 'database', 'transient'}:
            action, reason = 'resume', '任务在写入前暂停，将沿用原有检查和默认值流程重新处理'
    return {'failureCategory': category, 'safeBatchAction': action,
            'safeBatchEligible': bool(action), 'safeBatchReason': reason}


def batches(values, size=500):
    values = list(dict.fromkeys(values))
    for start in range(0, len(values), size):
        yield values[start:start + size]


def placeholders(values):
    return ','.join('?' for _ in values)


def pagination(query, total):
    size = query.get('pageSize', ['50'])[0]
    if size not in ('50', '100', '200', '500'):
        raise ValueError('每页条数只能选择 50、100、200 或 500')
    size = int(size)
    pages = max(1, (total + size - 1) // size)
    page = min(max(1, int(query.get('page', ['1'])[0])), pages)
    return {'page': page, 'pageSize': size, 'pages': pages, 'total': total}


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (TypeError, ValueError):
        return 0.0


@contextmanager
def reader(path, attachments=(), *, timeout=3):
    """Short-lived WAL reader, separate from the serialized write connection."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)) as db:
        db.row_factory = sqlite3.Row
        db.create_function('casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        for name, attached in attachments:
            # Names are internal constants; values are parameterized read-only URIs.
            if name not in {'shared', 'people'}:
                raise ValueError('Invalid query database alias')
            db.execute(f'ATTACH DATABASE ? AS {name}', (Path(attached).resolve().as_uri() + '?mode=ro',))
        db.execute('PRAGMA query_only=ON')
        yield db


class PersonnelResolver:
    """Resolve exact source IDs in batches, never scan unrelated snapshots."""
    def __init__(self, service, accounts=None):
        self.service = service
        self.accounts = {a['id']: a for a in (accounts if accounts is not None else service.accounts())}
        with service.db() as db:
            self.retained = {r['account_id']: r['scope'] for r in db.execute('SELECT * FROM shared_accounts')}
        self.resolved = {}
        self.names = {}

    def _scope(self, source, scope):
        return scope or self.retained.get(source) or self.accounts.get(source, {}).get('scope')

    def preload(self, refs):
        grouped = {}
        for source, detail, scope in refs:
            scope = self._scope(source, scope)
            key = (source, str(detail), scope)
            if key not in self.resolved:
                grouped.setdefault((source, scope), set()).add(str(detail))
        store = self.service.b.COLLECTION_PEOPLE.store
        for (source, scope), ids in grouped.items():
            if not scope:
                for detail in ids:
                    self.resolved[source, detail, scope] = ''
                continue
            if scope not in self.names:
                self.names[scope] = {p['id']: p for p in store.list(scope)}
            owners = {}
            with reader(store.path) as db:
                for selected in batches(ids):
                    for row in db.execute('SELECT detail_id,person_id FROM collection_product_owners '
                            'WHERE scope=? AND detail_id IN (' + placeholders(selected) + ')', [scope, *selected]):
                        owners[row['detail_id']] = self.service.b.collector_id(row['person_id'])
            missing = ids - owners.keys()
            # Transfer evidence is scoped to the original credential identity.
            if missing:
                with reader(self.service.path) as db:
                    for selected in batches(missing):
                        rows = db.execute("SELECT json_extract(payload,'$.detailId') AS detail, "
                            "json_extract(payload,'$.personId') AS person FROM shared_transfers "
                            "WHERE json_extract(payload,'$.sourceScope')=? AND "
                            "CAST(json_extract(payload,'$.detailId') AS TEXT) IN (" + placeholders(selected) + ') '
                            'ORDER BY updated,transfer_id', [scope, *selected])
                        for row in rows:
                            person = self.service.b.collector_id(row['person'])
                            if person:
                                owners.setdefault(str(row['detail']), person)
            missing = ids - owners.keys()
            if missing and scope == (self.retained.get(source) or self.accounts.get(source, {}).get('scope')):
                pool = self.service.b.automation_pool(source)
                for physical in getattr(pool, 'pools', (pool,)):
                    if not physical.path.exists():
                        continue
                    with reader(physical.path) as db:
                        for selected in batches(missing):
                            rows = db.execute("SELECT detail_id,json_extract(snapshot_json,'$.subAppAccountId') AS person "
                                'FROM automation_items WHERE detail_id IN (' + placeholders(selected) + ')', selected)
                            for row in rows:
                                person = self.service.b.collector_id(row['person'])
                                if person:
                                    owners.setdefault(str(row['detail_id']), person)
                    missing -= owners.keys()
                    if not missing:
                        break
            for detail in ids:
                self.resolved[source, detail, scope] = owners.get(detail, '')

    def __call__(self, source, detail, scope=None):
        scope = self._scope(source, scope)
        key = (source, str(detail), scope)
        if key not in self.resolved:
            self.preload([(source, detail, scope)])
        person = self.resolved[key]
        label = self.names.get(scope, {}).get(person, {})
        return {'personId': person, 'personKey': f'{source}:{person}' if person else 'unknown',
                'personName': label.get('alias') or label.get('name') or ('未匹配人员' if person else '未知归属')}


class TaskQueries:
    def __init__(self, service):
        self.s = service
        self.accounts = service.accounts()
        self.available = {a['id'] for a in self.accounts if a['configured'] and not a['blocked']}
        with service.db() as db:
            self.scopes = {r['account_id']: r['scope'] for r in db.execute('SELECT * FROM shared_accounts')}

    def shards(self):
        for account in self.accounts:
            pool = self.s.b.automation_pool(account['id'])
            for number, physical in enumerate(getattr(pool, 'pools', (pool,)), 1):
                if physical.path.exists():
                    yield account, number, physical

    def summary(self):
        """Local counters only: never load products, owners, media or events."""
        counts, transfer_counts = dict.fromkeys(GROUPS, 0), dict.fromkeys(GROUPS, 0)
        total_all = 0
        for _, _, physical in self.shards():
            with reader(physical.path) as db:
                rows = db.execute('''SELECT j.status,COUNT(*) AS n FROM automation_jobs j
                    WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                    GROUP BY j.status''').fetchall()
            for row in rows:
                total_all += row['n']
                for group, statuses in GROUPS.items():
                    if row['status'] in statuses:
                        counts[group] += row['n']
        with reader(self.s.path) as db:
            rows = db.execute('''SELECT t.status,COUNT(*) AS n FROM shared_transfers t
                JOIN shared_batches b ON b.batch_id=t.batch_id
                WHERE t.status NOT IN ('done','discarded') GROUP BY t.status''').fetchall()
        for row in rows:
            for group, statuses in GROUPS.items():
                if row['status'] in statuses:
                    transfer_counts[group] += row['n']
        return {'counts': counts, 'transferCounts': transfer_counts, 'totalAll': total_all}

    def job_metadata(self, include_deleted=False, *, with_title=False, source_refs=None):
        result = []
        for account, number, physical in self.shards():
            with reader(physical.path, [('shared', self.s.path)]) as db:
                title = ",COALESCE(m.title,'') AS title" if with_title else ",'' AS title"
                media = ' LEFT JOIN automation_job_media m ON m.job_id=j.job_id' if with_title else ''
                sql = '''SELECT j.job_id,j.detail_id,j.shop_id,j.site,j.status,j.created_at,
                    COALESCE(o.source_account,c.source_account,?) AS source_account,
                    COALESCE(o.source_id,c.source_id,j.detail_id) AS source_id,
                    COALESCE(o.source_scope,c.source_scope,?) AS source_scope''' + title + '''
                    FROM automation_jobs j
                    LEFT JOIN shared.shared_job_origins o ON o.account_id=? AND o.job_id=j.job_id
                    LEFT JOIN shared.shared_copies c ON c.target_account=? AND c.target_id=j.detail_id AND c.target_scope=?''' + media
                if not include_deleted:
                    sql += ' WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)'
                scope = self.scopes.get(account['id'], account['scope'])
                parameters=[account['id'], scope, account['id'], account['id'], scope]
                if source_refs:
                    marks=','.join('(?,?)' for _ in source_refs)
                    sql += (' WHERE ' if include_deleted else ' AND ') + f'(COALESCE(o.source_account,c.source_account,?),COALESCE(o.source_id,c.source_id,j.detail_id)) IN ({marks})'
                    parameters.extend([account['id'],*[v for ref in source_refs for v in ref]])
                for row in db.execute(sql, parameters):
                    result.append({'type': 'job', 'key': f"{account['id']}:{row['job_id']}",
                        'accountId': account['id'], 'jobId': row['job_id'], 'detailId': row['detail_id'],
                        'shopId': row['shop_id'], 'site': row['site'], 'status': row['status'],
                        'createdAt': row['created_at'], 'queuedAt': row['created_at'], 'title': row['title'],
                        'sourceAccountId': row['source_account'], 'sourceDetailId': row['source_id'],
                        '_sourceScope': row['source_scope'], '_path': physical.path, 'poolId': number,
                        'available': account['id'] in self.available and row['source_account'] in self.available})
        return result

    @staticmethod
    def resolve_jobs(rows, resolver):
        refs = [(r['sourceAccountId'], r['sourceDetailId'], r['_sourceScope']) for r in rows]
        if hasattr(resolver, 'preload'):
            resolver.preload(refs)
        for row, ref in zip(rows, refs):
            row.update(resolver(*ref))

    def hydrate_jobs(self, rows):
        grouped = {}
        for row in rows:
            grouped.setdefault(row['_path'], []).append(row['jobId'])
        hydrated = {}
        for path, ids in grouped.items():
            with reader(path) as db:
                for selected in batches(ids):
                    for row in db.execute("SELECT j.job_id,j.status,j.step,j.message,COALESCE(m.title,'') AS title,"
                            "COALESCE(m.thumbnail,'') AS thumbnail FROM automation_jobs j "
                            'LEFT JOIN automation_job_media m ON m.job_id=j.job_id WHERE j.job_id IN (' +
                            placeholders(selected) + ')', selected):
                        hydrated[path, row['job_id']] = dict(row)
        result = []
        for row in rows:
            details = hydrated.get((row['_path'], row['jobId']))
            if details is not None:
                entry = {**{k: v for k, v in row.items() if not k.startswith('_')},
                    **{k: v for k, v in details.items() if k != 'job_id'}}
                result.append({**entry, **job_failure_metadata(entry)})
        return result

    def jobs(self, include_deleted=False, resolver=None):
        rows = self.job_metadata(include_deleted)
        self.resolve_jobs(rows, resolver or PersonnelResolver(self.s, self.accounts))
        return self.hydrate_jobs(rows)

    def tasks(self, kind, query):
        group = 'publish' if kind == 'queue' else query.get('taskGroup', [''])[0]
        if group and group not in GROUPS:
            raise ValueError('任务分组不正确')
        self.s.filter_rows([], query)
        pagination(query, 0)
        # The task center periodically refreshes its unfiltered tabs. Walking
        # every job, ownership row and historical metadata on every physical
        # pool makes that harmless refresh contend with active edit/publish
        # writes.  The compact path counts in SQLite and hydrates only the
        # visible page; any filter whose meaning needs full routing data keeps
        # the complete, exact path below.
        complex_filters = ('sourceAccountId', 'personId', 'personKey', 'keyword', 'failureCategory')
        if not any(query.get(name, [''])[0] for name in complex_filters):
            return self._tasks_fast(query, group)
        return self._tasks_full(query, group)

    def _status_person_options(self, resolver):
        """Cache only the global selector, never task filters or their counts."""
        store = self.s.b.COLLECTION_PEOPLE.store
        # Include historical scopes and aliases so label/credential changes
        # invalidate immediately. This is a small configuration table.
        with reader(store.path) as db:
            names = tuple(tuple(row) for row in db.execute('''SELECT p.scope,p.id,p.name,COALESCE(a.alias,'')
                FROM people p LEFT JOIN personnel_aliases a ON a.scope=p.scope AND a.id=p.id
                ORDER BY p.scope,p.id'''))
        for scope, person, name, alias in names:
            resolver.names.setdefault(scope, {})[person] = {'name': name, 'alias': alias}
        context = (tuple((a['id'], a['scope'], self.scopes.get(a['id']), a['configured'], a['blocked'])
                         for a in self.accounts), names)
        # setdefault publishes one service-local lock for concurrent requests.
        cache = self.s.__dict__.setdefault('_task_person_options_cache', {'lock': Lock()})
        with cache['lock']:
            cached = cache.get('entry')
            if cached is None or cached[1] != context or time.monotonic() - cached[0] >= 15:
                options = self._read_job_person_options(resolver)
                cache['entry'] = (time.monotonic(), context, options)
            else:
                options = cached[2]
            return {key: dict(value) for key, value in options.items()}

    def _read_job_person_options(self, resolver):
        """Preserve the full path's observed owners without page hydration."""
        options = {}
        store = self.s.b.COLLECTION_PEOPLE.store
        for account, _, physical in self.shards():
            scope = self.scopes.get(account['id'], account['scope'])
            params = [account['id'], scope, account['id'], account['id'], scope]
            prefix = '''WITH routing AS (
                SELECT COALESCE(o.source_account,c.source_account,?) AS source_account,
                    COALESCE(o.source_id,c.source_id,j.detail_id) AS source_id,
                    COALESCE(o.source_scope,c.source_scope,?) AS source_scope
                FROM automation_jobs j
                LEFT JOIN shared.shared_job_origins o ON o.account_id=? AND o.job_id=j.job_id
                LEFT JOIN shared.shared_copies c ON c.target_account=? AND c.target_id=j.detail_id AND c.target_scope=?
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id))'''
            with reader(physical.path, [('shared', self.s.path), ('people', store.path)]) as db:
                missing = db.execute(prefix + ''' SELECT DISTINCT r.source_account,r.source_id,r.source_scope
                    FROM routing r LEFT JOIN people.collection_product_owners p
                    ON p.scope=r.source_scope AND p.detail_id=CAST(r.source_id AS TEXT)
                    WHERE p.person_id IS NULL''', params).fetchall()
                resolver.preload((r['source_account'], r['source_id'], r['source_scope']) for r in missing)
                db.create_function('fallback_owner', 3,
                    lambda source, detail, scope: resolver.resolved.get((source, str(detail), scope), ''), deterministic=True)
                rows = db.execute(prefix + ''', base AS (SELECT r.source_account,r.source_scope,
                    COALESCE(p.person_id,fallback_owner(r.source_account,r.source_id,r.source_scope)) AS person_id
                    FROM routing r LEFT JOIN people.collection_product_owners p
                    ON p.scope=r.source_scope AND p.detail_id=CAST(r.source_id AS TEXT))
                    SELECT DISTINCT source_account,source_scope,person_id FROM base WHERE person_id!='' ''', params)
                for row in rows:
                    scope = row['source_scope']
                    if scope not in resolver.names:
                        resolver.names[scope] = {p['id']: p for p in store.list(scope)}
                    person = self.s.b.collector_id(row['person_id'])
                    if not person:
                        continue
                    label = resolver.names[scope].get(person, {})
                    key = f"{row['source_account']}:{person}"
                    options[key] = {'sourceAccountId': row['source_account'], 'personId': person, 'personKey': key,
                                    'personName': label.get('alias') or label.get('name') or '未匹配人员'}
        return options

    def _tasks_fast(self, query, group):
        """Page ordinary task views with indexed counts and page-only routing."""
        resolver = PersonnelResolver(self.s, self.accounts)
        store = self.s.b.COLLECTION_PEOPLE.store
        for account in self.accounts:
            resolver.names[account['scope']] = {p['id']: p for p in store.list(account['scope'])}

        transfers = self.s.transfer_rows(resolver)
        for row in transfers:
            row.update(type='transfer', key='transfer:' + row['transfer_id'])
        status = query.get('status', [''])[0]
        options = {}
        # This small local configuration table is sufficient for the empty
        # filter selector; it avoids scanning all job ownership rows.
        for account in self.accounts:
            for person, label in resolver.names.get(account['scope'], {}).items():
                person = self.s.b.collector_id(person)
                if person:
                    key = f"{account['id']}:{person}"
                    options[key] = {'sourceAccountId': account['id'], 'personId': person,
                                    'personKey': key,
                                    'personName': label.get('alias') or label.get('name') or '未匹配人员'}
        for row in transfers:
            if row.get('personId'):
                options[row['personKey']] = {k: row[k] for k in ('sourceAccountId', 'personId', 'personKey', 'personName')}
        if status:
            # Status previously used the full path: retain exactly its global
            # observed-owner selector, including owners in historical scopes.
            options = {r['personKey']: {k: r[k] for k in ('sourceAccountId', 'personId', 'personKey', 'personName')}
                       for r in transfers if r.get('personId')}
            options.update(self._status_person_options(resolver))

        transfers = self.s.filter_rows(transfers, query)
        transfer_counts = {key: sum(r['status'] in statuses for r in transfers) for key, statuses in GROUPS.items()}
        selected = [r for r in transfers if (not status or r['status'] == status)
                    and (not group or r['status'] in GROUPS[group])]
        pending, counts, total_all, statements = {}, dict.fromkeys(GROUPS, 0), 0, []
        total = len(selected)
        aggregate = '''SELECT j.status,COUNT(*) AS n FROM automation_jobs j
            WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)'''
        shop_column = 'CAST(j.shop_id AS TEXT)' if status else 'j.shop_id'
        for account, number, physical in self.shards():
            scope = self.scopes.get(account['id'], account['scope'])
            with reader(physical.path, [('shared', self.s.path)]) as db:
                status_counts = {r['status']: r['n'] for r in db.execute(aggregate + ' GROUP BY j.status')}
                total_all += sum(status_counts.values())
                matches = query.get('accountId',[''])[0] in ('',str(account['id'])) and query.get('poolId',[''])[0] in ('',str(number))
                shop = query.get('shopId',[''])[0]
                scoped = ({r['status']: r['n'] for r in db.execute(aggregate + ' AND ' + shop_column + '=? GROUP BY j.status', (shop,))}
                          if matches and shop else status_counts)
                if matches:
                    for key, statuses in GROUPS.items():
                        counts[key] += sum(scoped.get(value, 0) for value in statuses)
                for pending_row in db.execute("SELECT shop_id,COUNT(*) AS n FROM automation_jobs j WHERE status='ready_publish' "
                                              '''AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                                              AND NOT EXISTS (SELECT 1 FROM automation_publish_batch_items i
                                                JOIN automation_publish_batches b ON b.batch_id=i.batch_id
                                                WHERE i.job_id=j.job_id AND b.status='running'
                                                  AND i.status IN ('pending','publishing')) GROUP BY shop_id'''):
                    key = account['id'], pending_row['shop_id']
                    pending[key] = pending.get(key, 0) + pending_row['n']
            if not matches: continue
            # Reuse the status aggregate for the exact page total. Tab counts
            # retain all statuses; only the result total honors status/group.
            count = sum(n for value, n in scoped.items()
                        if (not status or value == status) and (not group or value in GROUPS[group]))
            total += count
            if not count: continue
            status_where, parameters = '', [account['id'], scope, account['id'], account['id'], scope]
            if status:
                status_where = ' AND j.status=?'
                parameters.append(status)
            if group:
                status_where += ' AND j.status IN (' + placeholders(GROUPS[group]) + ')'
                parameters.extend(GROUPS[group])
            if shop:
                status_where += ' AND ' + shop_column + '=?'
                parameters.append(shop)
            statements.append((account, number, physical, status_where, parameters))

        data = pagination(query, total)
        limit = data['page'] * data['pageSize']
        for account, number, physical, status_where, parameters in statements:
            sql = '''SELECT j.job_id,j.detail_id,j.shop_id,j.site,j.status,j.created_at,
                    COALESCE(o.source_account,c.source_account,?) AS source_account,
                    COALESCE(o.source_id,c.source_id,j.detail_id) AS source_id,
                    COALESCE(o.source_scope,c.source_scope,?) AS source_scope
                FROM automation_jobs j
                LEFT JOIN shared.shared_job_origins o ON o.account_id=? AND o.job_id=j.job_id
                LEFT JOIN shared.shared_copies c ON c.target_account=? AND c.target_id=j.detail_id AND c.target_scope=?
                WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)''' + status_where + \
                ' ORDER BY j.created_at DESC,j.job_id DESC LIMIT ?'
            with reader(physical.path, [('shared', self.s.path)]) as db:
                for row in db.execute(sql, [*parameters, limit]):
                    selected.append({'type': 'job', 'key': f"{account['id']}:{row['job_id']}",
                        'accountId': account['id'], 'jobId': row['job_id'], 'detailId': row['detail_id'],
                        'shopId': row['shop_id'], 'site': row['site'], 'status': row['status'],
                        'createdAt': row['created_at'], 'queuedAt': row['created_at'],
                        'sourceAccountId': row['source_account'], 'sourceDetailId': row['source_id'],
                        '_sourceScope': row['source_scope'], '_path': physical.path, 'poolId': number,
                        'available': account['id'] in self.available and row['source_account'] in self.available})
        selected.sort(key=lambda r: (timestamp(r.get('queuedAt', r.get('createdAt'))), r['key']), reverse=True)
        start = (data['page'] - 1) * data['pageSize']
        page = selected[start:start + data['pageSize']]
        jobs_to_resolve = [r for r in page if r['type'] == 'job']
        self.resolve_jobs(jobs_to_resolve, resolver)
        jobs = {r['key']: r for r in self.hydrate_jobs(jobs_to_resolve)}
        entries = [jobs[r['key']] if r['type'] == 'job' else r for r in page if r['type'] != 'job' or r['key'] in jobs]
        return {**data, 'entries': entries, 'jobs': [r for r in entries if r['type'] == 'job'],
                'transfers': [r for r in entries if r['type'] == 'transfer'], 'counts': counts,
                'transferCounts': transfer_counts, 'totalAll': total_all, 'poolCount': 3,
                'personOptions': sorted(options.values(), key=lambda p: (p['sourceAccountId'], p['personName'], p['personId'])),
                'shopPendingCounts': [{'accountId': a, 'shopId': s, 'count': n} for (a, s), n in sorted(pending.items())]}

    def _tasks_full(self, query, group):
        resolver = PersonnelResolver(self.s, self.accounts)
        store = self.s.b.COLLECTION_PEOPLE.store
        for account in self.accounts:
            resolver.names[account['scope']] = {p['id']: p for p in store.list(account['scope'])}
        transfers = self.s.transfer_rows(resolver)
        for row in transfers:
            row.update(type='transfer', key='transfer:' + row['transfer_id'])
        options = {r['personKey']: {k: r[k] for k in ('sourceAccountId', 'personId', 'personKey', 'personName')}
                   for r in transfers if r.get('personId')}
        pending = {}
        total_all = 0
        counts = dict.fromkeys(GROUPS, 0)
        transfers = self.s.filter_rows(transfers, query)
        transfer_counts = {key: sum(r['status'] in statuses for r in transfers) for key, statuses in GROUPS.items()}
        status = query.get('status', [''])[0]
        category = query.get('failureCategory', [''])[0]
        selected = [r for r in transfers if (not status or r['status'] == status)
                    and (not group or r['status'] in GROUPS[group])]
        total = len(selected)
        statements = []
        term = query.get('keyword', [''])[0].strip().casefold()
        with ExitStack() as stack:
            for account, number, physical in self.shards():
                db = stack.enter_context(reader(physical.path, [('shared', self.s.path), ('people', store.path)]))
                db.create_function('failure_category', 3, failure_category, deterministic=True)
                # The indexed owner table covers current data.  Only legacy
                # missing IDs consult transfer evidence / source snapshots.
                prefix = '''WITH routing AS (
                    SELECT j.job_id,j.detail_id,j.shop_id,j.site,j.status,j.created_at,''' + (
                        'j.step,j.message,' if category else '') + '''
                        COALESCE(o.source_account,c.source_account,?) AS source_account,
                        COALESCE(o.source_id,c.source_id,j.detail_id) AS source_id,
                        COALESCE(o.source_scope,c.source_scope,?) AS source_scope
                    FROM automation_jobs j
                    LEFT JOIN shared.shared_job_origins o ON o.account_id=? AND o.job_id=j.job_id
                    LEFT JOIN shared.shared_copies c ON c.target_account=? AND c.target_id=j.detail_id AND c.target_scope=?
                    WHERE NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id))'''
                scope = self.scopes.get(account['id'], account['scope'])
                params = [account['id'], scope, account['id'], account['id'], scope]
                missing = db.execute(prefix + ''' SELECT DISTINCT r.source_account,r.source_id,r.source_scope
                    FROM routing r LEFT JOIN people.collection_product_owners p
                    ON p.scope=r.source_scope AND p.detail_id=CAST(r.source_id AS TEXT)
                    WHERE p.person_id IS NULL''', params).fetchall()
                resolver.preload((r['source_account'], r['source_id'], r['source_scope']) for r in missing)
                db.create_function('fallback_owner', 3,
                    lambda source, detail, scope: resolver.resolved.get((source, str(detail), scope), ''), deterministic=True)
                base = prefix + ''', base AS (SELECT r.*,
                    COALESCE(p.person_id,fallback_owner(r.source_account,r.source_id,r.source_scope)) AS person_id
                    FROM routing r LEFT JOIN people.collection_product_owners p
                    ON p.scope=r.source_scope AND p.detail_id=CAST(r.source_id AS TEXT))'''
                total_all += db.execute('SELECT COUNT(*) FROM automation_jobs j WHERE NOT EXISTS '
                    '(SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)').fetchone()[0]
                for row in db.execute("SELECT shop_id,COUNT(*) AS n FROM automation_jobs j WHERE status='ready_publish' "
                        '''AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                        AND NOT EXISTS (SELECT 1 FROM automation_publish_batch_items i
                          JOIN automation_publish_batches b ON b.batch_id=i.batch_id
                          WHERE i.job_id=j.job_id AND b.status='running'
                            AND i.status IN ('pending','publishing')) GROUP BY shop_id'''):
                    key = account['id'], row['shop_id']
                    pending[key] = pending.get(key, 0) + row['n']
                for row in db.execute(base + " SELECT DISTINCT source_account,source_scope,person_id FROM base WHERE person_id!=''", params):
                    scope = row['source_scope']
                    if scope not in resolver.names:
                        resolver.names[scope] = {p['id']: p for p in store.list(scope)}
                    person = self.s.b.collector_id(row['person_id'])
                    if not person:
                        continue
                    label = resolver.names[scope].get(person, {})
                    key = f"{row['source_account']}:{person}"
                    options[key] = {'sourceAccountId': row['source_account'], 'personId': person, 'personKey': key,
                                    'personName': label.get('alias') or label.get('name') or '未匹配人员'}
                if query.get('accountId', [''])[0] not in ('', str(account['id'])) or query.get('poolId', [''])[0] not in ('', str(number)):
                    continue
                conditions, filters = [], []
                for field, column in (('sourceAccountId', 'source_account'), ('shopId', 'shop_id'), ('personId', 'person_id')):
                    value = query.get(field, [''])[0]
                    if value:
                        conditions.append(f'CAST(b.{column} AS TEXT)=?')
                        filters.append(value)
                person_key = query.get('personKey', [''])[0]
                if person_key:
                    conditions.append("(CASE WHEN b.person_id='' THEN 'unknown' ELSE CAST(b.source_account AS TEXT)||':'||b.person_id END)=?")
                    filters.append(person_key)
                join = ''
                if category:
                    conditions.append('failure_category(b.status,b.step,b.message)=?')
                    filters.append(category)
                if term:
                    join = ' LEFT JOIN automation_job_media m ON m.job_id=b.job_id'
                    conditions.append('(instr(casefold(m.title),?)>0 OR CAST(b.detail_id AS TEXT)=? OR CAST(b.source_id AS TEXT)=?)')
                    filters.extend([term, term, term])
                where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
                status_counts = {row['status']: row['n'] for row in db.execute(base +
                    ' SELECT b.status,COUNT(*) AS n FROM base b' + join + where + ' GROUP BY b.status', params + filters)}
                for value, count in status_counts.items():
                    for key, statuses in GROUPS.items():
                        if value in statuses:
                            counts[key] += count
                if status:
                    conditions.append('b.status=?')
                    filters.append(status)
                if group:
                    conditions.append('b.status IN (' + placeholders(GROUPS[group]) + ')')
                    filters.extend(GROUPS[group])
                where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
                count = sum(n for value, n in status_counts.items()
                            if (not status or value == status) and (not group or value in GROUPS[group]))
                total += count
                if count:
                    statements.append((db, base + ' SELECT b.* FROM base b' + join + where +
                        ' ORDER BY b.created_at DESC,b.job_id DESC LIMIT ?', params + filters, account, number, physical))
            data = pagination(query, total)
            stop = data['page'] * data['pageSize']
            for db, sql, parameters, account, number, physical in statements:
                for row in db.execute(sql, parameters + [stop]):
                    person = self.s.b.collector_id(row['person_id'])
                    label = resolver.names.get(row['source_scope'], {}).get(person, {})
                    selected.append({'type': 'job', 'key': f"{account['id']}:{row['job_id']}", 'accountId': account['id'],
                        'jobId': row['job_id'], 'detailId': row['detail_id'], 'shopId': row['shop_id'], 'site': row['site'],
                        'status': row['status'], 'createdAt': row['created_at'], 'queuedAt': row['created_at'],
                        'sourceAccountId': row['source_account'], 'sourceDetailId': row['source_id'],
                        'personId': person, 'personKey': f"{row['source_account']}:{person}" if person else 'unknown',
                        'personName': label.get('alias') or label.get('name') or ('未匹配人员' if person else '未知归属'),
                        '_path': physical.path, 'poolId': number,
                        'available': account['id'] in self.available and row['source_account'] in self.available})
        selected.sort(key=lambda r: (timestamp(r.get('queuedAt', r.get('createdAt'))), r['key']), reverse=True)
        start = (data['page'] - 1) * data['pageSize']
        page = selected[start:start + data['pageSize']]
        jobs = {r['key']: r for r in self.hydrate_jobs([r for r in page if r['type'] == 'job'])}
        entries = [jobs[r['key']] if r['type'] == 'job' else r for r in page if r['type'] != 'job' or r['key'] in jobs]
        return {**data, 'entries': entries, 'jobs': [r for r in entries if r['type'] == 'job'],
                'transfers': [r for r in entries if r['type'] == 'transfer'], 'counts': counts,
                'transferCounts': transfer_counts, 'totalAll': total_all, 'poolCount': 3,
                'personOptions': sorted(options.values(), key=lambda p: (p['sourceAccountId'], p['personName'], p['personId'])),
                'shopPendingCounts': [{'accountId': a, 'shopId': s, 'count': n} for (a, s), n in sorted(pending.items())]}

    def pool(self, query, *, all_rows=False):
        # Validate pagination and integer input before opening any databases.
        pagination(query, 0)
        self.s.filter_rows([], query)
        candidates, total, sources = [], 0, {}
        statements = []
        store = self.s.b.COLLECTION_PEOPLE.store
        for account, number, physical in self.shards():
            if query.get('sourceAccountId', [''])[0] not in ('', str(account['id'])):
                continue
            if query.get('poolId', [''])[0] not in ('', str(number)):
                continue
            names = {p['id']: p for p in store.list(account['scope'])}
            sources[account['id'], number] = (account, physical, names)
            where = ['NOT EXISTS (SELECT 1 FROM shared.shared_copies c WHERE c.target_account=? '
                     'AND c.target_scope=? AND c.target_id=i.detail_id)']
            args = [account['id'], account['scope']]
            person = query.get('personId', [''])[0]
            joins = ''
            if person:
                joins = ' LEFT JOIN people.collection_product_owners o ON o.scope=? AND o.detail_id=CAST(i.detail_id AS TEXT)'
                where.append("CAST(COALESCE(o.person_id,json_extract(i.snapshot_json,'$.subAppAccountId'),'') AS TEXT)=?")
                args.append(person)
            term = query.get('keyword', [''])[0].strip().casefold()
            if term:
                where.append('(instr(casefold(i.title),?)>0 OR CAST(i.detail_id AS TEXT)=?)')
                args.extend([term, term])
            # These fields have no meaning on source-pool rows, as before.
            if any(query.get(field, [''])[0] for field in ('accountId', 'shopId')) or query.get('personKey', [''])[0] not in ('', 'unknown'):
                where.append('0')
            sql = ' FROM automation_items i' + joins + ' WHERE ' + ' AND '.join(where)
            parameters = ([account['scope']] if person else []) + args
            with reader(physical.path, [('shared', self.s.path), ('people', store.path)]) as db:
                total += db.execute('SELECT COUNT(*)' + sql, parameters).fetchone()[0]
            statement = 'SELECT i.detail_id,i.created_at' + sql + ' ORDER BY i.created_at DESC,CAST(i.detail_id AS TEXT) DESC'
            statements.append((physical, account, number, statement, parameters))
        # Clamp against the full filtered count before constructing SQLite's
        # signed-integer LIMIT, including after deletion or an oversized page.
        data = pagination(query, total)
        limit = data['page'] * data['pageSize']
        for physical, account, number, statement, parameters in statements:
            with reader(physical.path, [('shared', self.s.path), ('people', store.path)]) as db:
                if not all_rows:
                    statement += ' LIMIT ?'
                for row in db.execute(statement, parameters if all_rows else [*parameters, limit]):
                    candidates.append({'sourceAccountId': account['id'], 'detailId': row['detail_id'], 'poolId': number,
                        'key': f"{account['id']}:{row['detail_id']}", 'createdAt': row['created_at']})
        candidates.sort(key=lambda r: (r['createdAt'], r['key']), reverse=True)
        start = (data['page'] - 1) * data['pageSize']
        selected = candidates if all_rows else candidates[start:start + data['pageSize']]
        hydrated = {}
        grouped = {}
        for row in selected:
            grouped.setdefault((row['sourceAccountId'], row['poolId']), []).append(row['detailId'])
        for shard, ids in grouped.items():
            account, physical, names = sources[shard]
            with reader(physical.path, [('shared', self.s.path), ('people', store.path)]) as db:
                for ids_batch in batches(ids):
                    rows = db.execute("SELECT i.detail_id,i.title,i.thumbnail,i.version,i.created_at,i.updated_at,"
                        "COALESCE(o.person_id,json_extract(i.snapshot_json,'$.subAppAccountId')) AS person,"
                        "json_extract(i.snapshot_json,'$.gmtCreate') AS collected_at FROM automation_items i "
                        'LEFT JOIN people.collection_product_owners o ON o.scope=? AND o.detail_id=CAST(i.detail_id AS TEXT) '
                        'WHERE i.detail_id IN (' + placeholders(ids_batch) + ')', [account['scope'], *ids_batch]).fetchall()
                    # Detail responses can omit the collection-list timestamp.
                    # Recover display metadata only for this page, using the
                    # same credential scope and exact source product IDs.
                    missing_times = [str(row['detail_id']) for row in rows if not row['collected_at']]
                    cached_times = {}
                    if missing_times and db.execute("SELECT 1 FROM people.sqlite_master WHERE type='table' "
                                                    "AND name='collection_cached_rows'").fetchone():
                        cached_times = {row['detail_id']: row['collected_at'] for row in db.execute(
                            "SELECT detail_id,CASE WHEN json_valid(payload) THEN json_extract(payload,'$.gmtCreate') "
                            "END AS collected_at FROM people.collection_cached_rows WHERE scope=? AND detail_id IN (" +
                            placeholders(missing_times) + ')', [account['scope'], *missing_times])}
                    copies = {}
                    for copy in db.execute('SELECT source_id,target_account,target_id,status FROM shared.shared_copies '
                            'WHERE source_account=? AND source_scope=? AND source_id IN (' + placeholders(ids_batch) + ')',
                            [account['id'], account['scope'], *ids_batch]):
                        copies.setdefault(copy['source_id'], []).append({'targetAccountId': copy['target_account'],
                            'detailId': copy['target_id'], 'status': copy['status']})
                    for row in rows:
                        person = self.s.b.collector_id(row['person'])
                        name = names.get(person, {})
                        key = f"{account['id']}:{row['detail_id']}"
                        hydrated[key] = {'key': key, 'sourceAccountId': account['id'], 'sourceName': account['name'],
                            'detailId': row['detail_id'], 'title': row['title'], 'thumbnail': row['thumbnail'],
                            'version': row['version'], 'createdAt': row['created_at'],
                            'collectedAt': row['collected_at'] or cached_times.get(str(row['detail_id'])) or '',
                            'poolId': shard[1], 'personId': person, 'personName': name.get('alias') or name.get('name') or '未匹配人员',
                            'available': account['configured'] and not account['blocked'], 'copies': copies.get(row['detail_id'], [])}
        return {**data, 'items': [hydrated[r['key']] for r in selected if r['key'] in hydrated], 'poolCount': 3}
