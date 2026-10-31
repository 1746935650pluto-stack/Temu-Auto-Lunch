"""Read-only collection import and hourly account sync, independent of browsers."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from threading import Event, RLock, Thread
import time


class HourlySync:
    def __init__(self, backend, path, *, interval=3600, clock=time.time, orders_only=False):
        self.b, self.path, self.interval, self.clock = backend, path, interval, clock
        self.orders_only = orders_only
        self.stop_event, self.lock = Event(), RLock()
        self.thread = self.worker = None
        self.running = False
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS collection_sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            stored = {row[0]: json.loads(row[1]) for row in db.execute('SELECT key,value FROM collection_sync_state')}
        self.enabled = stored.get('enabled', True)
        self.next_run = stored.get('nextRunAt', self.clock() + interval)
        self.results = stored.get('accounts', {})
        for result in self.results.values():
            if result.get('status') in {'running', 'queued'}:
                result.update(status='interrupted', stage='interrupted',
                              error='后台重启，同步已中断；再次同步会跳过已入池商品')
        self._persist()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def _persist(self):
        # Called with the state lock (except during construction).
        with closing(self._connect()) as db, db:
            db.executemany('INSERT OR REPLACE INTO collection_sync_state VALUES (?,?)',
                           [(key, json.dumps(value, ensure_ascii=False)) for key, value in
                            [('enabled', self.enabled), ('nextRunAt', self.next_run), ('accounts', self.results)]])

    def start_timer(self):
        if self.thread and self.thread.is_alive():
            return
        name = 'order-auto-sync' if self.orders_only else 'hourly-local-sync'
        self.thread = Thread(target=self._timer, name=name, daemon=True)
        self.thread.start()

    def _timer(self):
        while not self.stop_event.wait(15):
            try:
                self.tick()
            except Exception:
                # A transient DB error is retried on the next tick. Never stop
                # the timer thread or clear an earlier usable snapshot.
                continue

    def tick(self, *, launch=True):
        with self.lock:
            if not self.enabled or self.running or self.clock() < self.next_run:
                return False
        # Scheduled reads yield to real editing/publishing. The due time stays
        # pending, so the next tick catches up once rather than losing an hour.
        activity = self.b.shared_automation().activity()
        if activity['editing'] or activity['publishing'] or activity['activeTransfers']:
            return False
        return self.start_sync(None, launch=launch, scheduled=True)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动同步开关不正确')
        with self.lock:
            if enabled and not self.enabled:
                self.next_run = self.clock() + self.interval
            self.enabled = enabled
            self._persist()

    def snapshot(self, account_id=None):
        scope = self.b.shared_automation().scope(account_id) if account_id else None
        with self.lock:
            result = deepcopy(self.results.get(str(account_id))) if account_id else None
            if result and result.pop('scope', None) != scope:
                result = None
            return {'enabled': self.enabled, 'intervalSeconds': self.interval,
                    'nextRunAt': self.next_run if self.enabled else None,
                    'running': self.running, 'accountId': account_id, 'result': result}

    def start_sync(self, account_id, *, launch=True, scheduled=False):
        # Register before launching so deletion/configuration cannot race the
        # background job after its HTTP request has already returned.
        with self.b.ACCOUNT_CONFIG_LOCK:
            if account_id is not None:
                accounts = [self.b.shared_automation().check_account(account_id)]
            else:
                accounts = [a for a in self.b.shared_automation().accounts()
                            if a['configured'] and not a['blocked']]
            with self.lock:
                if self.running:
                    if scheduled:
                        return False
                    raise ValueError('已有同步任务正在进行，请等待本轮完成')
                if scheduled and (not self.enabled or self.clock() < self.next_run):
                    return False
                self.running = True
                now = self.clock()
                for account in accounts:
                    self.results[str(account['id'])] = {
                        'scope': account['scope'], 'status': 'queued', 'stage': 'queued',
                        'startedAt': now, 'finishedAt': None, 'total': 0, 'processed': 0,
                        'added': 0, 'skipped': 0, 'replicas': 0, 'failed': 0, 'failures': [],
                        'orders': None, 'orderError': '', 'error': '', 'scheduled': scheduled}
                if scheduled:
                    self.next_run = now + self.interval
                try:
                    self._persist()
                except Exception:
                    self.running = False
                    raise
                self.b.ACCOUNT_MUTATIONS_ACTIVE += 1
        if launch:
            self.worker = Thread(target=self._run, args=(accounts, scheduled),
                                 name='collection-pool-sync', daemon=True)
            try:
                self.worker.start()
            except Exception:
                self._finish()
                raise
        return accounts

    def _update(self, account, **changes):
        with self.lock:
            self.results[str(account['id'])].update(changes)
            self._persist()

    def _finish(self):
        with self.b.ACCOUNT_CONFIG_LOCK:
            with self.lock:
                self.running = False
                # Long imports never stack another overdue run immediately.
                if self.next_run <= self.clock():
                    self.next_run = self.clock() + self.interval
                try:
                    self._persist()
                finally:
                    self.b.ACCOUNT_MUTATIONS_ACTIVE -= 1

    def _error(self, account, error):
        text = str(error)
        client = self.b.MiaoShouClient(account['id'])
        for secret in (client.app_key, client.app_secret):
            if secret:
                text = text.replace(secret, '[已隐藏]')
        return text[:300]

    def _worker(self, account):
        worker = self.b.BackgroundAutomationWorker(account['id'])
        original = worker._vendor
        allowed = {self.b.HALF_BOX_LIST, self.b.HALF_SITE_DETAIL, self.b.HALF_SHOP_DETAIL,
                   self.b.MIAOSHOU_PACKAGE_LIST_PATH}
        def read(path, payload):
            if path not in allowed:
                raise ValueError('同步只允许读取采集箱详情和订单')
            self.b.shared_automation().check_account(account['id'], account['scope'])
            result = self.b.miao_request(lambda: original(path, payload), account['id'])
            self.b.shared_automation().check_account(account['id'], account['scope'])
            if path == self.b.MIAOSHOU_PACKAGE_LIST_PATH:
                data = result.get('data')
                envelopes = data if isinstance(data, list) else [data]
                for envelope in envelopes:
                    if isinstance(envelope, dict) and 'orderPackageList' in envelope:
                        rows = envelope['orderPackageList']
                        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                            raise ValueError('订单未返回有效列表；已保留原有订单')
            return result
        worker._vendor = read
        return worker

    def _orders(self, account):
        self._update(account, status='running', stage='orders')
        lock = self.b.ORDER_SYNC_LOCKS[account['id']]
        if not self.b.MIAOSHOU_PACKAGE_LIST_PATH:
            self._update(account, orderError='订单接口未配置')
            return
        if not lock.acquire(blocking=False):
            self._update(account, orderError='订单正在由其他同步任务读取，本轮已跳过')
            return
        try:
            count = self.b.sync_miaoshou_order_pages(self._worker(account)._vendor,
                                                     self.b.order_store(account['id']))
            self._update(account, orders=count)
        except Exception as error:
            self._update(account, orderError=self._error(account, error))
        finally:
            lock.release()

    def _collection(self, account):
        worker = self._worker(account)
        self._update(account, status='running', stage='scanning')
        found = {}
        for page in range(1, 2001):
            response = worker._vendor(self.b.HALF_BOX_LIST, {'pageNo': page, 'pageSize': 500})
            rows = (response.get('data') or {}).get('detailList')
            if not isinstance(rows, list) or any(not isinstance(r, dict) or
                    not self.b.collector_id(r.get('collectBoxDetailId')) for r in rows):
                raise ValueError('采集箱未返回有效列表；已保留原有数据')
            previous = len(found)
            found.update({int(r['collectBoxDetailId']): r for r in rows})
            if rows and len(found) == previous:
                raise ValueError('采集箱分页重复，请稍后重新同步')
            self._update(account, total=len(found))
            if len(rows) < 500:
                break
        else:
            raise ValueError('采集箱超过本轮读取上限，未标记为完整同步')
        self.b.shared_automation().check_account(account['id'], account['scope'])
        rows = list(found.values())
        self.b.COLLECTION_PEOPLE.store.observe(account['scope'], rows)
        self.b.collection_read_cache().store_listing(account['scope'], rows)
        self.b.COLLECTION_PEOPLE.accept_listing(account['scope'], rows)
        with self.b._collection_summary_lock:
            summary = {'status': 'ready', 'totalItems': len(rows),
                       'totalPages': (len(rows) + 19) // 20, 'updatedAt': time.monotonic()}
            if account['id'] == 1:
                self.b._collection_summary = summary
            else:
                self.b.ACCOUNT_SUMMARIES[account['id']] = summary
        pool = self.b.automation_pool(account['id'])
        existing = pool.item_ids()
        replicas = {c['target_id'] for c in self.b.shared_automation()._copies()
                    if c['target_account'] == account['id'] and c['target_scope'] == account['scope'] and c['target_id']}
        skipped = len(existing & found.keys())
        replica_count = len((replicas - existing) & found.keys())
        added = failed = 0
        failures = []
        inputs = []
        for detail_id, row in found.items():
            if detail_id in existing or detail_id in replicas:
                continue
            mode = 'shop' if row.get('editModel') == 'shop' else 'site'
            shops = row.get('collectBoxDetailShopList') or []
            shop = shops[0].get('shopId', 0) if isinstance(shops, list) and shops and isinstance(shops[0], dict) else 0
            try:
                shop_id = int(shop or 0) if mode == 'shop' else 0
            except (ValueError, TypeError):
                failed += 1
                if len(failures) < 50:
                    failures.append({'detailId': detail_id, 'error': '采集箱返回的店铺编号无效'})
                continue
            inputs.append((detail_id, mode, shop_id))
        self._update(account, stage='importing', skipped=skipped, replicas=replica_count, failed=failed,
                     failures=failures, processed=skipped + replica_count + failed)
        # Reuse the interactive import batch size. The pool executor applies
        # one global concurrency limit, so this reduces idle batch handoffs
        # without increasing simultaneous MiaoShou reads.
        for offset in range(0, len(inputs), self.b.POOL_IMPORT_BATCH_SIZE):
            self.b.shared_automation().check_account(account['id'], account['scope'])
            result = self.b.stage_pool_batch(pool, inputs[offset:offset+self.b.POOL_IMPORT_BATCH_SIZE], worker._get_item)
            added += len(result['items'])
            skipped += len(result['skipped'])
            failed += len(result['failures'])
            failures.extend({'detailId': r['detailId'], 'error': self._error(account, r['error'])}
                            for r in result['failures'][:max(0, 50-len(failures))])
            self._update(account, added=added, skipped=skipped, failed=failed, failures=failures,
                         processed=added + skipped + failed + replica_count)

    def _run(self, accounts, scheduled):
        try:
            if self.orders_only:
                for account in accounts:
                    try:
                        self._orders(account)
                        with self.lock:
                            result = self.results[str(account['id'])]
                            status = 'partial' if result['orderError'] else 'completed'
                        self._update(account, status=status, stage='finished', finishedAt=self.clock())
                    except Exception as error:
                        self._update(account, status='failed', stage='finished', finishedAt=self.clock(),
                                     error=self._error(account, error))
                return
            # Read orders for every account before potentially long detail
            # imports, so a large collection does not delay all other orders.
            if scheduled:
                for account in accounts:
                    self._orders(account)
            for account in accounts:
                try:
                    self._collection(account)
                    with self.lock:
                        result = self.results[str(account['id'])]
                        status = 'partial' if result['failed'] or result['orderError'] else 'completed'
                    self._update(account, status=status, stage='finished', finishedAt=self.clock())
                except Exception as error:
                    # The previous complete local snapshot remains usable for
                    # personnel filtering if this scheduled scan fails.
                    self.b.collection_read_cache().mark_listing_refresh_failed(account['scope'], error)
                    self._update(account, status='failed', stage='finished', finishedAt=self.clock(),
                                 error=self._error(account, error))
        finally:
            self._finish()
