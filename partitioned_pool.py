"""Multiple physical pools behind an account-scoped, backwards-compatible router.

Existing rows are never moved. Item and job identities remain account-scoped,
so cross-account provenance and idempotency keys do not change.
"""
from contextlib import closing
from datetime import datetime, timezone
from threading import RLock

from automation_pool import AutomationPool, normalize_edit_concurrency


class PartitionedPool:
    def __init__(self, primary, *parts):
        """Accept two or more physical pools followed by the account id.

        Keeping the original positional form means existing pool-1/pool-2
        rows stay addressable. A newly added pool only receives items that do
        not already exist in an older physical database.
        """
        if len(parts) < 2:
            raise ValueError('至少需要两个物理自动化池和账号编号')
        *extra_pools, account_id = parts
        if type(account_id) is not int or account_id <= 0:
            raise ValueError('自动化池账号编号不正确')
        self.path = primary.path
        self.account_id = account_id
        self.pools = (primary, *extra_pools)
        self.lock = RLock()
        self.cursor = 0
        # Rotate equal-load assignments so a quiet account does not keep
        # filling pool 1 simply because the process was restarted.
        self.assignment_cursor = 0
        self._items = {}
        self._jobs = {}
        for index, pool in enumerate(self.pools, 1):
            pool.account_id = account_id
            pool.pool_id = index
            with closing(pool._connect()):
                pass

    def read_rows(self, sql, parameters=()):
        rows = []
        for index, pool in enumerate(self.pools, 1):
            rows.extend({**row, 'pool_id': index} for row in pool.read_rows(sql, parameters))
        return rows

    def _connect(self):
        # Legacy callers inspecting the original DB only. Aggregate readers
        # must use read_rows; never ATTACH databases for cross-file writes.
        return self.pools[0]._connect()

    def _item_pool(self, detail):
        detail = int(detail)
        with self.lock:
            if detail in self._items:
                return self._items[detail]
            # A completed historical job may remain in its original database
            # after a queued successor and its current snapshot have been
            # safely balanced to another pool.  Prefer the live snapshot so
            # subsequent queuing and claim coordination use that new home.
            for pool in self.pools:
                if pool.read_rows('SELECT 1 FROM automation_items WHERE detail_id=?', (detail,)):
                    self._items[detail] = pool
                    return pool
            for pool in self.pools:
                if pool.read_rows('SELECT 1 FROM automation_jobs WHERE detail_id=? LIMIT 1', (detail,)):
                    self._items[detail] = pool
                    return pool
            # A new staged product has no durable task affinity yet.  Route it
            # to the least-loaded editing pool instead of using a detail-ID
            # modulus, which can leave a newer physical pool idle for a whole
            # submission.  Existing product/job rows above are never moved.
            pool = self._least_loaded_pool()
            self._items[detail] = pool
            return pool

    def _edit_loads(self):
        """Return only work that still consumes or awaits editing capacity."""
        return [int(pool.read_rows("""
            SELECT COUNT(*) AS n FROM automation_jobs
            WHERE status IN ('queued', 'running') AND step != 'publish'
        """)[0]['n']) for pool in self.pools]

    def _least_loaded_pool_for(self, loads, planned=None):
        """Pick a physical pool without letting publish backlog skew editing."""
        planned = planned or [0] * len(self.pools)
        totals = [load + planned[index] for index, load in enumerate(loads)]
        lowest = min(totals)
        for offset in range(len(self.pools)):
            index = (self.assignment_cursor + offset) % len(self.pools)
            if totals[index] == lowest:
                self.assignment_cursor = (index + 1) % len(self.pools)
                return self.pools[index]
        return self.pools[0]

    def _least_loaded_pool(self, planned=None):
        return self._least_loaded_pool_for(self._edit_loads(), planned)

    def _job_pool(self, job):
        with self.lock:
            if job in self._jobs:
                return self._jobs[job]
            for pool in self.pools:
                if pool.read_rows('SELECT 1 FROM automation_jobs WHERE job_id=?', (job,)):
                    self._jobs[job] = pool
                    return pool
        return self.pools[0]

    def get_item(self, detail):
        detail = int(detail)
        # A route is a hint, not proof that a product was deleted. Another
        # router may have balanced its snapshot since this process cached it.
        # Hold the routing lock through the read so local moves cannot race it.
        with self.lock:
            pool = self._item_pool(detail)
            row = pool.get_item(detail)
            if row is None:
                for candidate in self.pools:
                    if candidate is pool:
                        continue
                    row = candidate.get_item(detail)
                    if row is not None:
                        pool = candidate
                        self._items[detail] = pool
                        break
            return {**row, 'poolId': pool.pool_id} if row is not None else None

    def put_item(self, detail, *args, **kwargs):
        with self.lock:
            # Repair a stale hint before updating an existing snapshot; do not
            # create a second copy in its former physical pool.
            self.get_item(detail)
            pool = self._item_pool(detail)
            return {**pool.put_item(detail, *args, **kwargs), 'poolId': pool.pool_id}

    def get_job_state(self, job):
        pool = self._job_pool(job)
        row = pool.get_job_state(job)
        return {**row,'poolId':pool.pool_id} if row else None

    def get_job(self, job):
        pool = self._job_pool(job)
        row = pool.get_job(job)
        return {**row, 'poolId': pool.pool_id} if row else None

    def start_job(self, job, detail, *args, **kwargs):
        pool = self._item_pool(detail)
        self._jobs[job] = pool
        return pool.start_job(job, detail, *args, **kwargs)

    @staticmethod
    def _queue_options(kwargs, ids, entries):
        """Retain input order when one request is split between pools."""
        options = dict(kwargs)
        for field in ('price', 'sku_prices'):
            if isinstance(options.get(field), list):
                if len(options[field]) != len(ids):
                    raise ValueError('每件商品必须有对应的申报价')
                options[field] = [options[field][index] for index, _ in entries]
        for field in ('shops_by_detail', 'personnel_by_detail'):
            if options.get(field) is not None:
                options[field] = {detail: options[field][detail] for _, detail in entries if detail in options[field]}
        return options

    def _queue_routes(self, ids):
        """Choose an editing pool for unstarted items without moving task history."""
        planned = [0] * len(self.pools)
        loads = self._edit_loads()
        routes = {}
        for detail in ids:
            source = self._item_pool(detail)
            has_history = any(pool.read_rows(
                'SELECT 1 FROM automation_jobs WHERE detail_id=? LIMIT 1', (detail,)) for pool in self.pools)
            # Tasks (including ready-to-publish work) stay in their original
            # database.  Only an unused local snapshot may be rehomed to
            # admit a newly submitted edit job on the least-loaded pool.
            target = source if has_history else self._least_loaded_pool_for(loads, planned)
            planned[self.pools.index(target)] += 1
            routes[detail] = (source, target)
        return routes

    def _move_unstarted_item(self, detail, source, target):
        if source is target:
            return
        staged = source.get_item(detail)
        if not staged:
            raise ValueError(f'采集箱 ID {detail} 不在自动化池中')
        created_destination = False
        try:
            stored = target.put_item(detail, staged['mode'], staged['shopId'], staged['item'], only_if_new=True)
            created_destination = not stored.get('skipped', False)
            source.remove_item(detail)
        except Exception:
            # A partial destination write is safe to remove: this item had no
            # job history and has not yet been handed to an edit worker.
            if created_destination:
                target.remove_item(detail)
            raise
        self._items[detail] = target

    def queue_jobs(self, detail_ids, shop_id, site, **kwargs):
        ids = list(dict.fromkeys(int(d) for d in detail_ids))
        if not ids or len(ids) > 50 or any(d <= 0 for d in ids):
            raise ValueError('请选择 1 到 50 件有效商品')
        if kwargs.get('shops_by_detail') is not None and set(kwargs['shops_by_detail']) != set(ids):
            raise ValueError('每件商品必须有明确的目标店铺')
        with self.lock:
            source_groups = {}
            # Balancing can leave completed history in a former physical pool.
            # Keep that history, including hidden records, in the dedupe scope.
            placeholders = ','.join('?' for _ in ids)
            records = [row for pool in self.pools for row in pool.read_rows(
                'SELECT detail_id,shop_id,site,status FROM automation_jobs '
                f"WHERE detail_id IN ({placeholders}) AND status!='canceled'", ids)]
            if not kwargs.get('dedupe_pairs') and any(row['status'] in {
                    'queued', 'running', 'needs_input', 'ready_publish'} for row in records):
                raise ValueError('商品已有排队或未完成任务，请在任务中心查看')
            if kwargs.get('dedupe_pairs'):
                kwargs = {**kwargs, 'existing_pairs': {
                    (row['detail_id'], row['shop_id'], row['site'].upper()) for row in records}}
            for index, detail in enumerate(ids):
                source_groups.setdefault(self._item_pool(detail), []).append((index, detail))
            # Validate before changing any local product ownership. This keeps
            # a bad request from moving staged records between databases.
            for source, entries in source_groups.items():
                source.queue_jobs([detail for _, detail in entries], shop_id, site,
                                  **self._queue_options(kwargs, ids, entries), validate_only=True)

            routes = self._queue_routes(ids)
            for detail in ids:
                source, target = routes[detail]
                self._move_unstarted_item(detail, source, target)

            groups = {}
            for index, detail in enumerate(ids):
                groups.setdefault(routes[detail][1], []).append((index, detail))
            result = []
            for pool, entries in groups.items():
                options = self._queue_options(kwargs, ids, entries)
                for row in pool.queue_jobs([detail for _, detail in entries], shop_id, site, **options):
                    self._jobs[row['jobId']] = pool
                    result.append({**row, 'poolId': pool.pool_id})
            return result

    def item_ids(self):
        return set().union(*(p.item_ids() for p in self.pools))

    def list_items(self):
        return [{**r, 'poolId': p.pool_id} for p in self.pools for r in p.list_items()]

    def list_jobs(self):
        return [{**r, 'poolId': p.pool_id} for p in self.pools for r in p.list_jobs()]

    def job_states(self, ids):
        result = {}
        for pool in self.pools:
            result.update(pool.job_states(ids))
        return result

    def refresh_choices_for_shop(self, *args, **kwargs):
        result = {'refreshed': 0, 'queued': 0}
        for pool in self.pools:
            updated = pool.refresh_choices_for_shop(*args, **kwargs)
            result['refreshed'] += updated['refreshed']
            result['queued'] += updated['queued']
        return result

    def has_queued_jobs(self):
        return any(p.has_queued_jobs() for p in self.pools)

    @staticmethod
    def _plain_queued_details(pool):
        """Return details whose current queued jobs prove no remote write began.

        A queued row alone is not enough: a task could have returned to the
        queue after a local recovery.  Only records with the original queued
        step, no non-queued event and no publish batch are eligible. Terminal
        historical rows may remain in the source database with their media;
        they do not represent active work and are never moved.
        """
        rows = pool.read_rows("""
            SELECT i.detail_id
            FROM automation_items i
            WHERE EXISTS (SELECT 1 FROM automation_jobs q WHERE q.detail_id=i.detail_id)
              AND NOT EXISTS (
                SELECT 1 FROM automation_jobs j
                WHERE j.detail_id=i.detail_id AND (
                    (j.status='queued' AND (
                        j.step!='queued' OR j.publish_after_save!=0
                        OR EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                        OR EXISTS (SELECT 1 FROM automation_publish_batch_items b WHERE b.job_id=j.job_id)
                        OR EXISTS (SELECT 1 FROM automation_job_events e
                                   WHERE e.job_id=j.job_id AND e.step!='queued')
                    )) OR j.status NOT IN ('queued','done','canceled')
                )
              )
            ORDER BY (SELECT MIN(created_at) FROM automation_jobs q WHERE q.detail_id=i.detail_id), i.detail_id
        """)
        return [int(row['detail_id']) for row in rows]

    @staticmethod
    def _copy_rows(connection, table, columns, rows):
        if not rows:
            return
        names = ','.join(columns)
        values = ','.join('?' for _ in columns)
        connection.executemany(
            f'INSERT INTO {table} ({names}) VALUES ({values})',
            [tuple(row[column] for column in columns) for row in rows],
        )

    def _move_plain_queued_detail(self, detail, source, target):
        """Copy one untouched detail, then remove its source rows.

        The caller holds the partition router lock and the scheduler must be
        paused.  Destination data is committed before the source is removed;
        if the ordinary source deletion fails, the copied destination is
        removed again while the source remains untouched.  A rare uncertain
        source-commit failure deliberately leaves queues paused for operator
        recovery rather than risking a duplicate remote save.
        """
        if source is target:
            return 0
        first, second = sorted((source, target), key=lambda pool: pool.pool_id)
        with closing(first._connect()) as first_connection, closing(second._connect()) as second_connection:
            connections = {first: first_connection, second: second_connection}
            source_connection, target_connection = connections[source], connections[target]
            target_committed = source_committed = False
            job_ids = []
            try:
                source_connection.execute('BEGIN IMMEDIATE')
                target_connection.execute('BEGIN IMMEDIATE')
                item = source_connection.execute(
                    'SELECT * FROM automation_items WHERE detail_id=?', (detail,)
                ).fetchone()
                all_jobs = source_connection.execute(
                    'SELECT * FROM automation_jobs WHERE detail_id=? ORDER BY rowid', (detail,)
                ).fetchall()
                jobs = [row for row in all_jobs if row['status'] == 'queued']
                if item is None or not jobs or any(
                    row['status'] != 'queued' or row['step'] != 'queued' or row['publish_after_save']
                    for row in jobs
                ) or any(
                    row['status'] not in {'queued', 'done', 'canceled'} for row in all_jobs
                ):
                    return 0
                job_ids = [str(row['job_id']) for row in jobs]
                placeholders = ','.join('?' for _ in job_ids)
                unsafe = source_connection.execute(f"""
                    SELECT 1 FROM automation_jobs j
                    WHERE j.job_id IN ({placeholders}) AND (
                        EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                        OR EXISTS (SELECT 1 FROM automation_publish_batch_items b WHERE b.job_id=j.job_id)
                        OR EXISTS (SELECT 1 FROM automation_job_events e
                                   WHERE e.job_id=j.job_id AND e.step!='queued')
                    ) LIMIT 1
                """, job_ids).fetchone()
                if unsafe:
                    return 0
                conflict = target_connection.execute(
                    'SELECT 1 FROM automation_items WHERE detail_id=?', (detail,)
                ).fetchone() or target_connection.execute(
                    f'SELECT 1 FROM automation_jobs WHERE job_id IN ({placeholders}) LIMIT 1', job_ids
                ).fetchone()
                if conflict:
                    raise ValueError(f'采集箱 ID {detail} 在目标池已有本地记录，未迁移')
                claims = source_connection.execute(
                    'SELECT * FROM automation_claims WHERE detail_id=?', (detail,)
                ).fetchall()
                media = source_connection.execute(
                    f'SELECT * FROM automation_job_media WHERE job_id IN ({placeholders})', job_ids
                ).fetchall()
                choices = source_connection.execute(
                    f'SELECT * FROM automation_job_choices WHERE job_id IN ({placeholders})', job_ids
                ).fetchall()
                self._copy_rows(target_connection, 'automation_items',
                    ('detail_id', 'mode', 'shop_id', 'title', 'thumbnail', 'snapshot_json', 'version', 'created_at', 'updated_at'), [item])
                self._copy_rows(target_connection, 'automation_claims',
                    ('detail_id', 'shop_id', 'status', 'error', 'updated_at'), claims)
                self._copy_rows(target_connection, 'automation_jobs',
                    ('job_id', 'detail_id', 'shop_id', 'site', 'price', 'sku_prices_json', 'publish_after_save',
                     'interval_seconds', 'auto_override_attempts', 'status', 'step', 'message', 'created_at', 'updated_at'), jobs)
                self._copy_rows(target_connection, 'automation_job_media', ('job_id', 'title', 'thumbnail'), media)
                self._copy_rows(target_connection, 'automation_job_choices',
                    ('job_id', 'kind', 'options_json', 'selected_id', 'refreshed_at'), choices)
                event_rows = source_connection.execute(
                    f'SELECT job_id,step,status,message,created_at FROM automation_job_events '
                    f'WHERE job_id IN ({placeholders}) ORDER BY event_id', job_ids
                ).fetchall()
                self._copy_rows(target_connection, 'automation_job_events',
                    ('job_id', 'step', 'status', 'message', 'created_at'), event_rows)
                now = datetime.now(timezone.utc).isoformat(timespec='seconds')
                target_connection.executemany("""
                    INSERT INTO automation_job_events(job_id,step,status,message,created_at)
                    VALUES (?, 'routing', 'done', ?, ?)
                """, [(job_id, f'本机任务从池 {source.pool_id} 均衡迁移至池 {target.pool_id}；未发起妙手写入', now)
                       for job_id in job_ids])
                target_connection.commit()
                target_committed = True
                source_connection.execute(f'DELETE FROM automation_jobs WHERE job_id IN ({placeholders})', job_ids)
                source_connection.execute('DELETE FROM automation_items WHERE detail_id=?', (detail,))
                source_connection.commit()
                source_committed = True
            except Exception:
                if not source_committed:
                    try:
                        source_connection.rollback()
                    except Exception:
                        pass
                if target_committed and not source_committed:
                    # Compensate only when the source definitely still owns
                    # every queue row.  Otherwise leave the scheduler paused
                    # for explicit recovery instead of guessing ownership.
                    try:
                        source_count = source_connection.execute(
                            f'SELECT COUNT(*) FROM automation_jobs WHERE job_id IN ({placeholders})', job_ids
                        ).fetchone()[0]
                        if source_count == len(job_ids):
                            target_connection.execute('BEGIN IMMEDIATE')
                            target_connection.execute(f'DELETE FROM automation_jobs WHERE job_id IN ({placeholders})', job_ids)
                            target_connection.execute('DELETE FROM automation_items WHERE detail_id=?', (detail,))
                            target_connection.commit()
                    except Exception:
                        try:
                            target_connection.rollback()
                        except Exception:
                            pass
                raise
        self._items[detail] = target
        for job_id in job_ids:
            self._jobs[job_id] = target
        return len(job_ids)

    def rebalance_plain_queued_jobs(self, limit=1000):
        """Spread provably untouched queued jobs across idle physical pools.

        This is intentionally a controlled maintenance action, not ordinary
        scheduler behavior.  It preserves job IDs and source snapshots, moves
        only local pre-write work, and returns the resulting physical loads.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('单次最多迁移 1 到 1000 件未开始任务')
        with self.lock:
            loads = self._edit_loads()
            before = list(loads)
            candidates = {pool: self._plain_queued_details(pool) for pool in self.pools}
            moved_jobs = moved_details = skipped = 0
            while moved_jobs < limit:
                sources = [pool for pool in self.pools if candidates[pool]]
                if not sources:
                    break
                source = max(sources, key=lambda pool: (loads[self.pools.index(pool)], -pool.pool_id))
                target = self._least_loaded_pool_for(loads)
                source_index, target_index = self.pools.index(source), self.pools.index(target)
                if source is target or loads[source_index] <= loads[target_index] + 1:
                    break
                detail = candidates[source].pop(0)
                moved = self._move_plain_queued_detail(detail, source, target)
                if not moved:
                    skipped += 1
                    continue
                moved_jobs += moved
                moved_details += 1
                loads[source_index] -= moved
                loads[target_index] += moved
            return {'movedJobs': moved_jobs, 'movedDetails': moved_details, 'skippedDetails': skipped,
                    'beforeLoads': before, 'afterLoads': self._edit_loads()}

    def start_next_queued_jobs(self, limit=8, *, light=False):
        limit = normalize_edit_concurrency(limit)
        with self.lock:
            running = [p.read_rows("SELECT COUNT(*) AS n FROM automation_jobs WHERE status='running' AND step!='publish'")[0]['n']
                       for p in self.pools]
            result = []
            while sum(running) < limit:
                found = False
                for offset in range(len(self.pools)):
                    index = (self.cursor + offset) % len(self.pools)
                    rows = self.pools[index].start_next_queued_jobs(running[index] + 1, light=light)
                    if rows:
                        result.extend({**r, 'poolId': index + 1} for r in rows)
                        running[index] += len(rows)
                        self.cursor = (index + 1) % len(self.pools)
                        found = True
                        break
                if not found:
                    break
            return result

    def start_next_queued_job(self):
        rows = self.start_next_queued_jobs(1)
        return rows[0] if rows else None

    def ready_publish_jobs(self, *args, **kwargs):
        return [r for p in self.pools for r in p.ready_publish_jobs(*args, **kwargs)]

    def _by_ids(self, name, ids, **kwargs):
        groups = {}
        router = self._item_pool if name == 'remove_items' else self._job_pool
        for identifier in ids:
            groups.setdefault(router(identifier), []).append(identifier)
        return [r for p, selected in groups.items() for r in getattr(p, name)(selected, **kwargs)]

    def remove_items(self, ids):
        return self._by_ids('remove_items', ids)

    def remove_item(self, detail):
        return bool(self.remove_items([detail]))

    def delete_job_records(self, ids, **kwargs):
        return self._by_ids('delete_job_records', ids, **kwargs)

    def remove_publish_queue_jobs(self, ids):
        return self._by_ids('remove_publish_queue_jobs', ids)

    def _publish_batch(self, ids, interval, append=False):
        with self.lock:
            return self._publish_batch_locked(ids, interval, append)

    def _publish_batch_locked(self, ids, interval, append=False):
        groups = {}
        for job in set(ids):
            row = self.get_job_state(job)
            if not row or row['status'] != 'ready_publish':
                raise ValueError('待发布队列已变化，请刷新后重新发布')
            groups.setdefault(self._job_pool(job), []).append(job)
        if not groups or type(interval) is not int or not 1 <= interval <= 600:
            raise ValueError('没有待发布商品或发布间隔不正确')
        for pool, selected in groups.items():
            released = {r['job_id'] for r in pool.read_rows('''
                SELECT i.job_id FROM automation_publish_batches b
                JOIN automation_publish_batch_items i ON i.batch_id=b.batch_id
                WHERE b.status='running' AND i.status IN ('pending','publishing')''')}
            if released.intersection(selected):
                raise ValueError('所选商品已在发布队列中，未重复提交；请刷新后仅选择尚未入队的商品')
        tokens = []
        previous = {p.pool_id:p.read_rows("SELECT batch_id,interval_seconds FROM automation_publish_batches WHERE status='running'") for p in self.pools}
        try:
            for pool, selected in groups.items():
                running = pool.read_rows("SELECT batch_id FROM automation_publish_batches WHERE status='running'")
                method = pool.append_to_running_publish_batch if append and running else pool.begin_publish_batch
                tokens.append(f'{pool.pool_id}:{method(selected, interval)}')
            if append:
                self.update_running_publish_intervals(interval)
        except Exception:
            for token in tokens:
                for physical, batch in self._batch_parts(token):
                    physical.rollback_publish_additions(batch, groups[physical], previous[physical.pool_id])
            for physical in self.pools:
                with closing(physical._connect()) as db, db:
                    for old in previous[physical.pool_id]:
                        db.execute('UPDATE automation_publish_batches SET interval_seconds=? WHERE batch_id=?',
                                   (old['interval_seconds'],old['batch_id']))
            raise
        return ','.join(tokens)

    def begin_publish_batch(self, ids, interval):
        return self._publish_batch(ids, interval)

    def append_to_running_publish_batch(self, ids, interval):
        return self._publish_batch(ids, interval, True)

    def _batch_parts(self, token):
        for part in token.split(','):
            if ':' in part:
                index, batch = part.split(':', 1)
                yield self.pools[int(index) - 1], batch
            else:
                # Old durable batches belong to the original pool.
                yield self.pools[0], part

    def finish_publish_batch(self, token):
        for pool, batch in self._batch_parts(token):
            pool.finish_publish_batch(batch)

    def running_publish_interval(self, token):
        values = [p.running_publish_interval(b) for p, b in self._batch_parts(token)]
        return next((v for v in values if v is not None), None)

    wait_for_running_publish_interval = AutomationPool.wait_for_running_publish_interval

    def start_next_ready_publish_job(self, token=None):
        parts = list(self._batch_parts(token)) if token else [(p, None) for p in self.pools]
        with self.lock:
            for offset in range(len(parts)):
                index = (self.cursor + offset) % len(parts)
                pool, batch = parts[index]
                row = pool.start_next_ready_publish_job(batch)
                if row:
                    self.cursor = (index + 1) % len(parts)
                    return {**row, 'poolId': pool.pool_id}
        return None

    def publish_progress(self):
        rows = [r for p in self.pools if (r := p.publish_progress())]
        if not rows:
            return None
        result = dict(max(rows, key=lambda r: r['createdAt']))
        for key in ('total', 'submitted', 'needsReview', 'removed', 'remaining', 'handled'):
            result[key] = sum(r[key] for r in rows)
        if any(r['status'] == 'running' for r in rows):
            result['status'] = 'running'
        return result

    def recover_interrupted_jobs(self):
        for pool in self.pools:
            pool.recover_interrupted_jobs()

    def recover_orphaned_edit_jobs(self, cutoff):
        return self._sum_results('recover_orphaned_edit_jobs', cutoff)

    def requeue_local_database_busy_jobs(self):
        """Resume only locally-proven pre-request SQLite lock pauses in both pools."""
        return sum(pool.requeue_local_database_busy_jobs() for pool in self.pools)

    def requeue_missing_snapshot_jobs(self):
        recovered = 0
        with self.lock:
            for pool in self.pools:
                ids = [row['detail_id'] for row in pool.read_rows("""SELECT DISTINCT detail_id
                    FROM automation_jobs WHERE status='needs_input' AND step='claim'
                    AND message='商品已从自动化池移除'""")]
                if not ids:
                    continue
                blocked = set()
                for offset in range(0, len(ids), 500):
                    chunk = ids[offset:offset + 500]
                    placeholders = ','.join('?' for _ in chunk)
                    for other in self.pools:
                        if other is pool:
                            continue
                        blocked.update((row['detail_id'], row['shop_id'], row['site'].upper()) for row in
                            other.read_rows(f'SELECT detail_id,shop_id,site FROM automation_jobs '
                                f"WHERE detail_id IN ({placeholders}) AND status!='canceled'", chunk))
                recovered += pool.requeue_missing_snapshot_jobs(blocked_pairs=blocked)
        return recovered

    def stop_and_clear_jobs(self):
        return self._sum_results('stop_and_clear_jobs')

    def _sum_results(self, name, *args):
        result = {}
        for pool in self.pools:
            for key, value in getattr(pool, name)(*args).items():
                result[key] = result.get(key, 0) + value
        return result

    def update_running_publish_intervals(self, interval):
        return [f'{p.pool_id}:{b}' for p in self.pools for b in p.update_running_publish_intervals(interval)]

    def claim_pending_auto_overrides(self, limit=50):
        return self._claim_pending('claim_pending_auto_overrides', limit)

    def claim_pending_direct_edit_jobs(self, limit=50):
        return self._claim_pending('claim_pending_direct_edit_jobs', limit)

    def _claim_pending(self, name, limit):
        result = []
        for pool in self.pools:
            if len(result) < limit:
                result.extend(getattr(pool, name)(limit - len(result)))
        return result

    def __getattr__(self, name):
        item_methods = {'claim_state', 'pending_claim_shop_ids', 'record_claim'}
        job_methods = {'update_job', 'update_job_title', 'is_job_active', 'pause_for_choice',
                       'pause_for_problem', 'refresh_choice', 'begin_auto_override', 'resume_job', 'cancel_paused_job',
                       'cancel_edit_job', 'retry_job', 'force_retry_for_publish', 'delete_job_record'}
        if name not in item_methods | job_methods:
            raise AttributeError(name)
        route = self._item_pool if name in item_methods else self._job_pool
        return lambda identifier, *args, **kwargs: getattr(route(identifier), name)(identifier, *args, **kwargs)
