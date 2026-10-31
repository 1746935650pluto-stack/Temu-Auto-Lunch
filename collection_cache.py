"""Credential-scoped, persistent read cache with page-level change detection.

No undocumented remote time filter or ordering is assumed. Only a confirmed
page is replaced; an item missing from that page is NOT treated as deleted.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Lock, Thread
import time


class CollectionReadCache:
    def __init__(self, path):
        self.path = path
        self._lock = Lock()
        self._running = set()
        self._errors = {}
        with closing(self._connect()) as db, db:
            # Include schema changes and their backfill in one transaction.
            # A failed/interrupted upgrade leaves the previous snapshot intact.
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS collection_read_cache (
                scope TEXT NOT NULL, cache_key TEXT NOT NULL, payload TEXT NOT NULL,
                checked_at REAL NOT NULL, PRIMARY KEY(scope, cache_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS collection_cached_rows (
                scope TEXT NOT NULL, detail_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope, detail_id))''')
            # A normal page cache is deliberately allowed to contain rows from
            # different reads.  Personnel filtering is different: it must use
            # one known-complete listing so removed/old rows never leak into a
            # result.  Keep its membership separate and versioned.
            db.execute('''CREATE TABLE IF NOT EXISTS collection_full_snapshots (
                scope TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                completed_at REAL NOT NULL, total_items INTEGER NOT NULL,
                last_failed_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE IF NOT EXISTS collection_snapshot_members (
                scope TEXT NOT NULL, generation INTEGER NOT NULL, position INTEGER NOT NULL,
                detail_id TEXT NOT NULL, payload TEXT NOT NULL,
                person_id TEXT NOT NULL DEFAULT '', title_fold TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(scope, generation, detail_id))''')
            self._migrate_snapshot_filters(db)
            db.execute('CREATE INDEX IF NOT EXISTS collection_snapshot_member_order '
                       'ON collection_snapshot_members(scope, generation, position)')
            db.execute('CREATE INDEX IF NOT EXISTS collection_snapshot_member_person '
                       'ON collection_snapshot_members(scope, generation, person_id, position, title_fold)')
            db.execute('CREATE INDEX IF NOT EXISTS collection_snapshot_member_title '
                       'ON collection_snapshot_members(scope, generation, position, title_fold)')

    @staticmethod
    def _filter_fields(row):
        # Preserve the former Python substring/Unicode casefold semantics.
        return str(row.get('subAppAccountId', '')).strip(), str(row.get('title') or '').casefold()

    def _migrate_snapshot_filters(self, db):
        columns = {row[1] for row in db.execute('PRAGMA table_info(collection_snapshot_members)')}
        missing = {'person_id', 'title_fold'} - columns
        if not missing:
            return
        for column in ('person_id', 'title_fold'):
            if column in missing:
                db.execute(f"ALTER TABLE collection_snapshot_members ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # Bound upgrade memory and use stable row IDs while updating each batch.
        last_rowid = 0
        while True:
            rows = db.execute('SELECT rowid,payload FROM collection_snapshot_members '
                              'WHERE rowid>? ORDER BY rowid LIMIT 500', (last_rowid,)).fetchall()
            if not rows:
                break
            db.executemany('UPDATE collection_snapshot_members SET person_id=?,title_fold=? WHERE rowid=?',
                           [(*self._filter_fields(json.loads(payload)), rowid) for rowid, payload in rows])
            last_rowid = rows[-1][0]

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _connect_reader(self):
        return sqlite3.connect(Path(self.path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)

    def _read(self, scope, key):
        with closing(self._connect()) as db:
            row = db.execute('SELECT payload, checked_at FROM collection_read_cache WHERE scope=? AND cache_key=?',
                             (scope, key)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def _save(self, scope, key, data):
        added = updated = unchanged = 0
        rows = data.get('data', {}).get('detailList') if key.startswith('products:') else None
        if key.startswith('products:') and (not isinstance(rows, list) or any(
                not isinstance(row, dict) or not str(row.get('collectBoxDetailId', '')).isdigit() for row in rows)):
            raise ValueError('采集箱接口未返回有效商品列表；保留上次缓存')
        with closing(self._connect()) as db, db:
            for row in rows or []:
                detail_id = str(row['collectBoxDetailId'])
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
                existing = db.execute('SELECT payload FROM collection_cached_rows WHERE scope=? AND detail_id=?',
                                      (scope, detail_id)).fetchone()
                if existing and existing[0] == payload:
                    unchanged += 1
                    continue
                if existing:
                    updated += 1
                else:
                    added += 1
                db.execute('INSERT INTO collection_cached_rows VALUES (?,?,?) ON CONFLICT(scope, detail_id) '
                           'DO UPDATE SET payload=excluded.payload', (scope, detail_id, payload))
            # Newly collected/deleted/reordered rows can move page boundaries.
            # Mark other pages stale, but never erase their data or assume a deletion.
            if key.startswith('products:'):
                prior = db.execute('SELECT payload FROM collection_read_cache WHERE scope=? AND cache_key=?',
                                   (scope, key)).fetchone()
                previous_ids = [str(row['collectBoxDetailId']) for row in json.loads(prior[0])['data']['detailList']] if prior else []
                if prior and previous_ids != [str(row['collectBoxDetailId']) for row in rows]:
                    db.execute("UPDATE collection_read_cache SET checked_at=0 WHERE scope=? AND cache_key LIKE 'products:%' AND cache_key<>?",
                               (scope, key))
            data = deepcopy(data)
            data['cache'] = {'added': added, 'updated': updated, 'unchanged': unchanged}
            checked = time.time()
            db.execute('INSERT INTO collection_read_cache VALUES (?,?,?,?) ON CONFLICT(scope, cache_key) '
                       'DO UPDATE SET payload=excluded.payload, checked_at=excluded.checked_at',
                       (scope, key, json.dumps(data, ensure_ascii=False), checked))
        return data, checked

    def store_listing(self, scope, rows):
        """Atomically cache a fully read listing at each supported UI page size."""
        if not isinstance(rows, list) or any(not isinstance(row, dict) or
                not str(row.get('collectBoxDetailId', '')).isdigit() for row in rows):
            raise ValueError('无效采集箱列表，保留原缓存')
        checked = time.time()
        pages = []
        for size in (20, 50, 100):
            for offset in range(0, len(rows) + size, size):
                page = offset // size + 1
                key = f'products:{page}' if size == 20 else f'products:{size}:{page}'
                data = {'data': {'detailList': rows[offset:offset+size]},
                        'cache': {'added': 0, 'updated': 0, 'unchanged': len(rows[offset:offset+size])}}
                pages.append((scope, key, json.dumps(data, ensure_ascii=False), checked))
        pages.append((scope, 'page-summary', json.dumps({'status': 'ready', 'totalItems': len(rows),
                     'totalPages': (len(rows) + 19) // 20}), checked))
        with self._lock:
            # A foreground page read owns its cache slot until it completes.
            pages = [row for row in pages if (scope, row[1]) not in self._running]
            with closing(self._connect()) as db, db:
                previous = db.execute('SELECT generation FROM collection_full_snapshots WHERE scope=?',
                                      (scope,)).fetchone()
                generation = (int(previous[0]) if previous else 0) + 1
                db.execute("UPDATE collection_read_cache SET checked_at=0 WHERE scope=? AND cache_key LIKE 'products:%'", (scope,))
                db.executemany('INSERT OR REPLACE INTO collection_cached_rows VALUES (?,?,?)',
                    [(scope, str(r['collectBoxDetailId']), json.dumps(r, ensure_ascii=False, sort_keys=True)) for r in rows])
                db.executemany('INSERT INTO collection_snapshot_members '
                               '(scope,generation,position,detail_id,payload,person_id,title_fold) VALUES (?,?,?,?,?,?,?)', [
                    (scope, generation, position, str(row['collectBoxDetailId']),
                     json.dumps(row, ensure_ascii=False, sort_keys=True), *self._filter_fields(row))
                    for position, row in enumerate(rows)
                ])
                db.execute('''INSERT INTO collection_full_snapshots
                    (scope,generation,completed_at,total_items,last_failed_at,last_error) VALUES (?,?,?,?,0,'')
                    ON CONFLICT(scope) DO UPDATE SET generation=excluded.generation,
                    completed_at=excluded.completed_at,total_items=excluded.total_items,
                    last_failed_at=0,last_error='' ''', (scope, generation, checked, len(rows)))
                db.execute('DELETE FROM collection_snapshot_members WHERE scope=? AND generation<>?',
                           (scope, generation))
                db.executemany('INSERT OR REPLACE INTO collection_read_cache VALUES (?,?,?,?)', pages)
            for _, key, _, _ in pages:
                self._errors.pop((scope, key), None)

    def mark_listing_refresh_failed(self, scope, error):
        """Retain the last complete snapshot but expose a failed refresh."""
        message = str(error or '采集箱完整同步失败')[:300]
        with closing(self._connect()) as db, db:
            db.execute('UPDATE collection_full_snapshots SET last_failed_at=?,last_error=? WHERE scope=?',
                       (time.time(), message, scope))

    def snapshot_info(self, scope):
        with closing(self._connect()) as db:
            return self._snapshot_info(db, scope)

    @staticmethod
    def _snapshot_info(db, scope):
        row = db.execute('''SELECT generation,completed_at,total_items,last_failed_at,last_error
                            FROM collection_full_snapshots WHERE scope=?''', (scope,)).fetchone()
        if not row:
            return None
        return {'generation': int(row[0]), 'completedAt': float(row[1]), 'totalItems': int(row[2]),
                'failedAt': float(row[3]), 'error': row[4]}

    def filtered_listing(self, scope, person_id='', page=1, page_size=20, detail_id='', title_keyword=''):
        """Filter only the latest complete listing; return None until initialized."""
        size = max(1, int(page_size))
        start = (max(1, int(page)) - 1) * size
        keyword = str(title_keyword or '').strip().casefold()
        with closing(self._connect_reader()) as db, db:
            # Pin metadata, count and page to the same complete generation even
            # if a background writer publishes/removes generations between reads.
            db.execute('BEGIN')
            snapshot = self._snapshot_info(db, scope)
            if not snapshot:
                return None
            conditions = ['scope=?', 'generation=?']
            params = [scope, snapshot['generation']]
            if person_id:
                conditions.append('person_id=?')
                params.append(str(person_id))
            if detail_id:
                conditions.append('detail_id=?')
                params.append(str(detail_id))
            if keyword:
                # LIKE would treat %/_ as wildcards and cannot Unicode-casefold.
                conditions.append('instr(title_fold, ?)>0')
                params.append(keyword)
            where = ' AND '.join(conditions)
            source = 'collection_snapshot_members'
            if keyword and not person_id and not detail_id:
                # A substring still scans titles, but this narrow ordered index
                # keeps COUNT from fetching every full JSON-bearing table row.
                source += ' INDEXED BY collection_snapshot_member_title'
            total = db.execute(f'SELECT COUNT(*) FROM {source} WHERE {where}', params).fetchone()[0]
            rows = []
            if start < total:
                # Check bounds in Python before binding OFFSET/LIMIT: arbitrarily
                # large page numbers must not overflow SQLite's signed integers.
                rows = db.execute(f'SELECT payload FROM {source} WHERE {where} '
                                  'ORDER BY position LIMIT ? OFFSET ?',
                                  [*params, min(size, total - start), start]).fetchall()
        return {'rows': [json.loads(payload) for (payload,) in rows], 'total': total, 'snapshot': snapshot}

    def invalidate_products(self, scope):
        with closing(self._connect()) as db, db:
            db.execute("UPDATE collection_read_cache SET checked_at=0 WHERE scope=? AND (cache_key LIKE 'products:%' OR cache_key='page-summary')",
                       (scope,))

    @staticmethod
    def _view(cached, *, refreshing=False, from_cache=True, error=''):
        data, checked = cached
        data = deepcopy(data)
        data['cache'] = {**data.get('cache', {}), 'cached': from_cache, 'checkedAt': checked,
                         'refreshing': refreshing, 'error': error}
        return data

    def get(self, scope, key, reader, *, ttl=60, force=False):
        cached = self._read(scope, key)
        token = (scope, key)
        with self._lock:
            if token in self._running:
                if cached:
                    return self._view(cached, refreshing=True)
                raise RuntimeError('此页首次同步正在进行，请稍后重试')
            failure = self._errors.get(token)
            if cached and not force:
                if failure and time.time() - failure[0] < 60:
                    return self._view(cached, error=failure[1])
                if time.time() - cached[1] < ttl:
                    return self._view(cached)
            self._running.add(token)

        def refresh():
            try:
                result = self._save(scope, key, reader())
                with self._lock:
                    self._errors.pop(token, None)
                return self._view(result, from_cache=False)
            except Exception as error:
                with self._lock:
                    self._errors[token] = (time.time(), str(error)[:300])
                if cached:
                    return self._view(cached, error=str(error)[:300])
                raise
            finally:
                with self._lock:
                    self._running.discard(token)

        if cached and not force:
            def background():
                try:
                    refresh()
                except Exception:
                    pass  # failure is exposed through cache.error; retain the snapshot
            Thread(target=background, daemon=True, name='collection-page-refresh').start()
            return self._view(cached, refreshing=True)
        return refresh()
