"""Read-only collection ownership, scoped by application credentials.

Only subAppAccountId identifies a person. appAccountId, shop IDs, product IDs,
and display names must never be used as substitutes.
"""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from threading import Lock
import re
import json
import sqlite3
import time


# Personnel identity is stable data. A manual refresh is still available when
# the operator needs newer source data, but routine routing should not require
# repeatedly scanning a large collection box during the same work week.
PERSONNEL_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def collector_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ''
    text = str(value).strip()
    return str(int(text)) if re.fullmatch(r'[0-9]{1,20}', text) and int(text) > 0 else ''


def personnel_rows(response):
    """Accept explicit member records only, never infer ownership from a name."""
    value = response.get('data') if isinstance(response, dict) else None
    for _ in range(4):
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if not isinstance(value, dict):
            break
        if 'subAppAccountId' in value:
            return [value]
        value = next((value[key] for key in ('subAppAccountList', 'memberList', 'accountList',
                                            'userList', 'list', 'records', 'rows', 'data')
                      if isinstance(value.get(key), (dict, list))), None)
    raise ValueError('人员接口未返回可识别的人员列表')


def person_fields(row):
    def text(*keys):
        return next((row[key].strip()[:160] for key in keys
                     if isinstance(row.get(key), str) and row[key].strip()), '')
    return {'id': collector_id(row.get('subAppAccountId')),
            'name': text('subAppAccountName', 'realName', 'displayName', 'nickName', 'name'),
            'account': text('accountName', 'loginName', 'userName')}


class PersonnelStore:
    """Keep only IDs and names, never credentials or product details."""
    def __init__(self, path):
        self.path = Path(path)
        self._schema_lock = Lock()
        self._initialized = False

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if not self._initialized:
                with self._schema_lock:
                    if not self._initialized:
                        connection.execute('PRAGMA journal_mode=WAL')
                        self._initialize_schema(connection)
                        self._initialized = True
            return connection
        except Exception:
            connection.close()
            raise

    def _initialize_schema(self, connection):
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS people (
                scope TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                account TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(scope, id));
            CREATE TABLE IF NOT EXISTS personnel_aliases (
                scope TEXT NOT NULL, id TEXT NOT NULL, alias TEXT NOT NULL,
                updated_at REAL NOT NULL, PRIMARY KEY(scope, id));
            CREATE TABLE IF NOT EXISTS personnel_shop_bindings (
                scope TEXT NOT NULL, id TEXT NOT NULL, shop_ids TEXT NOT NULL,
                updated_at REAL NOT NULL, PRIMARY KEY(scope, id));
            CREATE TABLE IF NOT EXISTS collection_product_owners (
                scope TEXT NOT NULL, detail_id TEXT NOT NULL, person_id TEXT NOT NULL,
                updated_at REAL NOT NULL, PRIMARY KEY(scope, detail_id));
            CREATE TABLE IF NOT EXISTS personnel_migrations (name TEXT PRIMARY KEY);
        ''')
        # One atomic, local-only backfill. Never infer ownership from a name or
        # from the target copy, and never replace a newer observed association.
        with connection:
            if connection.execute("INSERT OR IGNORE INTO personnel_migrations VALUES ('product-owners-v1')").rowcount:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_cached_rows'").fetchone():
                    for row in connection.execute('SELECT scope, detail_id, payload FROM collection_cached_rows').fetchall():
                        try:
                            person = collector_id(json.loads(row['payload']).get('subAppAccountId'))
                        except (ValueError, TypeError, AttributeError):
                            continue
                        detail = collector_id(row['detail_id'])
                        if detail and person:
                            connection.execute('INSERT OR IGNORE INTO collection_product_owners VALUES (?,?,?,?)',
                                               (row['scope'], detail, person, time.time()))
        return connection

    def observe(self, scope, rows):
        people, owners = {}, {}
        for row in rows:
            person_id = collector_id(row.get('subAppAccountId'))
            if person_id:
                people[person_id] = None
                detail_id = collector_id(row.get('collectBoxDetailId'))
                if detail_id:
                    # Retain every observed person, but only the last valid
                    # association for a repeated product in this batch.
                    owners[detail_id] = person_id
        if not people:
            return
        with closing(self._connect()) as connection, connection:
            missing_people, changed_owners = self._observation_changes(connection, scope, people, owners)
            if not missing_people and not changed_owners:
                return
            # Cached reads must not acquire a write lock. If anything changed,
            # recheck the entire batch after acquiring the lock: another writer
            # may have repaired, removed or changed even initially matching IDs.
            connection.execute('BEGIN IMMEDIATE')
            missing_people, changed_owners = self._observation_changes(connection, scope, people, owners)
            if missing_people:
                connection.executemany('INSERT OR IGNORE INTO people(scope, id) VALUES (?, ?)',
                                       [(scope, person_id) for person_id in missing_people])
            if changed_owners:
                connection.executemany('INSERT INTO collection_product_owners VALUES (?,?,?,?) '
                    'ON CONFLICT(scope,detail_id) DO UPDATE SET person_id=excluded.person_id, updated_at=excluded.updated_at',
                    [(scope, detail_id, person_id, time.time()) for detail_id, person_id in changed_owners.items()])

    @staticmethod
    def _observation_changes(connection, scope, people, owners):
        known_people, known_owners = set(), {}
        # Keep the scope parameter plus IDs below SQLite's legacy 999 limit.
        for ids, table, column in ((list(people), 'people', 'id'),
                                   (list(owners), 'collection_product_owners', 'detail_id')):
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ','.join('?' for _ in chunk)
                fields = 'id' if table == 'people' else 'detail_id, person_id'
                result = connection.execute(
                    f'SELECT {fields} FROM {table} WHERE scope=? AND {column} IN ({placeholders})',
                    (scope, *chunk)).fetchall()
                if table == 'people':
                    known_people.update(row['id'] for row in result)
                else:
                    known_owners.update((row['detail_id'], row['person_id']) for row in result)
        return ([person_id for person_id in people if person_id not in known_people],
                {detail_id: person_id for detail_id, person_id in owners.items()
                 if known_owners.get(detail_id) != person_id})

    def owners(self, scope, fallback=()):
        """Display/filter evidence only; this must not replace fresh routing scans."""
        with closing(self._connect()) as connection, connection:
            known = {row['detail_id']: row['person_id'] for row in connection.execute(
                'SELECT detail_id, person_id FROM collection_product_owners WHERE scope=?', (scope,))}
            for detail, person in fallback:
                detail, person = collector_id(detail), collector_id(person)
                if detail and person and detail not in known:
                    connection.execute('INSERT OR IGNORE INTO collection_product_owners VALUES (?,?,?,?)',
                                       (scope, detail, person, time.time()))
            return {row['detail_id']: row['person_id'] for row in connection.execute(
                'SELECT detail_id, person_id FROM collection_product_owners WHERE scope=?', (scope,))}

    def preserve_owners(self, scope, evidence):
        """Retain exact legacy ownership before explicit source removal, without replacement."""
        rows = [(scope, collector_id(detail), collector_id(person), time.time())
                for detail, person in evidence]
        rows = [row for row in rows if row[1] and row[2]]
        if not rows:
            return 0
        with closing(self._connect()) as connection, connection:
            return connection.executemany('INSERT OR IGNORE INTO collection_product_owners VALUES (?,?,?,?)', rows).rowcount

    def update_names(self, scope, rows):
        people = [person_fields(row) for row in rows]
        # Conflicting duplicate records must not arbitrarily choose one person's name.
        grouped = {}
        for person in people:
            if person['id']:
                grouped.setdefault(person['id'], set()).add((person['name'], person['account']))
        with closing(self._connect()) as connection, connection:
            for person_id, values in grouped.items():
                name, account = next(iter(values)) if len(values) == 1 else ('', '')
                connection.execute('INSERT INTO people VALUES (?, ?, ?, ?, ?) '
                                   'ON CONFLICT(scope, id) DO UPDATE SET name=excluded.name, '
                                   'account=excluded.account, updated_at=excluded.updated_at',
                                   (scope, person_id, name, account, time.time()))

    def list(self, scope):
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT p.id, p.name, p.account, p.updated_at AS updatedAt, '
                "COALESCE(a.alias, '') AS alias FROM people p LEFT JOIN personnel_aliases a "
                'ON a.scope=p.scope AND a.id=p.id WHERE p.scope=? ORDER BY p.id',
                (scope,))]

    def bindings(self, scope):
        with closing(self._connect()) as connection:
            return {row['id']: json.loads(row['shop_ids']) for row in connection.execute(
                'SELECT id, shop_ids FROM personnel_shop_bindings WHERE scope=?', (scope,))}

    def save_binding(self, scope, person_id, shop_ids, valid_shop_ids):
        person_id = collector_id(person_id)
        if not person_id or not isinstance(shop_ids, list) or len(shop_ids) > 100 or any(
                type(value) is not int or value <= 0 or value not in valid_shop_ids for value in shop_ids):
            raise ValueError('人员或绑定店铺不正确，请刷新后重试')
        shops = sorted(set(shop_ids))
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM people WHERE scope=? AND id=?', (scope, person_id)).fetchone():
                raise ValueError('当前采集箱未发现该人员，请先刷新所属人员')
            if shops:
                connection.execute('INSERT INTO personnel_shop_bindings VALUES (?, ?, ?, ?) '
                                   'ON CONFLICT(scope, id) DO UPDATE SET shop_ids=excluded.shop_ids, '
                                   'updated_at=excluded.updated_at', (scope, person_id, json.dumps(shops), time.time()))
            else:
                connection.execute('DELETE FROM personnel_shop_bindings WHERE scope=? AND id=?', (scope, person_id))

    def save_aliases(self, scope, aliases):
        """Atomically update local labels for known IDs, never remote ownership."""
        if not isinstance(aliases, list) or not 1 <= len(aliases) <= 1000:
            raise ValueError('人员备注须为 1 到 1000 条记录')
        changes = {}
        for row in aliases:
            if not isinstance(row, dict):
                raise ValueError('人员备注格式不正确')
            person_id, alias = collector_id(row.get('id')), row.get('alias')
            if not person_id or person_id in changes or not isinstance(alias, str):
                raise ValueError('人员账号 ID 或备注不正确，账号 ID 不能重复')
            alias = alias.strip()
            if len(alias) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in alias):
                raise ValueError('人员备注最多 80 个字符，不能包含换行或控制字符')
            changes[person_id] = alias
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            known = {row['id'] for row in connection.execute('SELECT id FROM people WHERE scope=?', (scope,))}
            if not changes.keys() <= known:
                raise ValueError('包含当前采集箱未发现的人员账号 ID，请刷新人员后重试')
            for person_id, alias in changes.items():
                if alias:
                    connection.execute('INSERT INTO personnel_aliases VALUES (?, ?, ?, ?) '
                                       'ON CONFLICT(scope, id) DO UPDATE SET alias=excluded.alias, '
                                       'updated_at=excluded.updated_at', (scope, person_id, alias, time.time()))
                else:
                    connection.execute('DELETE FROM personnel_aliases WHERE scope=? AND id=?', (scope, person_id))


class CollectionPeople:
    """Optional name refresh and incremental full-box filtering, all via reads."""
    def __init__(self, store, page_size=500, ttl=PERSONNEL_CACHE_TTL_SECONDS):
        self.store = store
        self.page_size = page_size
        self.ttl = ttl
        self._lock = Lock()
        self._scans = {}
        self._name_refresh = {}
        self._name_locks = {}

    def refresh_names(self, scope, read_members=None, force=False):
        if read_members is None:
            return 'unconfigured'
        with self._lock:
            lock = self._name_locks.setdefault(scope, Lock())
        if not lock.acquire(blocking=False):
            return 'loading'
        try:
            previous = self._name_refresh.get(scope)
            if previous and not force and time.monotonic() - previous[0] < self.ttl:
                return previous[1]
            try:
                rows = personnel_rows(read_members())
                self.store.update_names(scope, rows)
                status = 'ready' if any(person_fields(row)['id'] and (
                    person_fields(row)['name'] or person_fields(row)['account']) for row in rows) else 'unmatched'
            except (RuntimeError, ValueError, TypeError, sqlite3.Error):
                status = 'unavailable'
            self._name_refresh[scope] = (time.monotonic(), status)
            return status
        finally:
            lock.release()

    def accept_listing(self, scope, rows):
        """Publish a confirmed full listing without interrupting a live scan."""
        with self._lock:
            current = self._scans.get(scope)
            if current and current['lock'].locked():
                return False
            self._scans[scope] = {'status': 'ready', 'started': time.monotonic(),
                'rows': {collector_id(r['collectBoxDetailId']): deepcopy(r) for r in rows},
                'page': 1, 'busy': False, 'lock': Lock(), 'error': ''}
            return True

    def scan(self, scope, read_page, refresh=False):
        """Read at most one remote page per request; callers poll for completion.

        Only a complete scan is served as filtered results. Partial scans and
        failed scans are never labelled as complete or paginated as all data.
        """
        with self._lock:
            state = self._scans.get(scope)
            if state and state['lock'].locked():
                return self._scan_status(state)
            expired = state and state['status'] != 'loading' and time.monotonic() - state['started'] >= self.ttl
            if state is None or expired or (refresh and not state['busy']):
                state = {'status': 'loading', 'started': time.monotonic(), 'rows': {},
                         'page': 1, 'busy': False, 'lock': Lock(), 'error': ''}
                self._scans[scope] = state
            state['lock'].acquire()
        try:
            if state['status'] != 'loading':
                return self._scan_status(state)
            state['busy'] = True
            try:
                response = read_page(state['page'], self.page_size)
                rows = response.get('data', {}).get('detailList')
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError('采集箱接口未返回有效商品列表')
                valid = {collector_id(row.get('collectBoxDetailId')): row for row in rows}
                valid.pop('', None)
                if rows and (not valid or all(key in state['rows'] for key in valid)):
                    raise ValueError('采集箱分页返回重复数据，请刷新后重试')
                self.store.observe(scope, rows)
                state['rows'].update(deepcopy(valid))
                state['page'] += 1
                if len(rows) < self.page_size:
                    state['status'] = 'ready'
                    state['started'] = time.monotonic()
                elif state['page'] > 2000:
                    raise ValueError('采集箱数据量超过本轮读取上限，请缩小范围后重试')
            except (RuntimeError, ValueError, TypeError, AttributeError, sqlite3.Error):
                state['status'] = 'failed'
                state['started'] = time.monotonic()
                state['error'] = '采集箱筛选读取失败，请点击“刷新人员”重试'
            finally:
                state['busy'] = False
            return self._scan_status(state)
        finally:
            state['lock'].release()

    @staticmethod
    def _scan_status(state):
        return {'status': state['status'], 'scanned': len(state['rows']), 'message': state['error']}

    def ownership(self, scope):
        """Only a fresh, complete source listing may route a remote save."""
        with self._lock:
            state = self._scans.get(scope)
            if not state:
                return {'status': 'unavailable', 'scanned': 0}, {}
            status = self._scan_status(state)
            if state['status'] == 'ready' and time.monotonic() - state['started'] >= self.ttl:
                return {**status, 'status': 'expired'}, {}
            if state['status'] != 'ready':
                return status, {}
            return status, {detail_id: collector_id(row.get('subAppAccountId'))
                            for detail_id, row in state['rows'].items()}

    def snapshot_rows(self, scope):
        """Return a completed foreground bootstrap scan for durable storage."""
        with self._lock:
            state = self._scans.get(scope)
            if not state or state['status'] != 'ready':
                return None
            return deepcopy(list(state['rows'].values()))

    def filtered(self, scope, person_id, page, page_size, detail_id='', title_keyword=''):
        with self._lock:
            state = self._scans.get(scope)
        if not state or state['status'] != 'ready':
            return [], 0
        keyword = title_keyword.strip().casefold()
        rows = [row for row in state['rows'].values()
                if (not person_id or collector_id(row.get('subAppAccountId')) == person_id)
                and (not detail_id or str(row.get('collectBoxDetailId')) == detail_id)
                and (not keyword or keyword in str(row.get('title') or '').casefold())]
        return deepcopy(rows[(page - 1) * page_size:page * page_size]), len(rows)
