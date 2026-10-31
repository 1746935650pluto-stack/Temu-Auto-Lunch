"""Account-qualified, local orchestration over the existing isolated pools.

Remote creates are deliberately at-most-once after an ambiguous outcome. A
confirmed copy is recorded before any target edit job is queued.
"""
from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from account_runtime import RUNTIME
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import sqlite3
import time
import uuid

from item_editor import normalize_item
from attribute_ai import constrained_rule, normalize_values
from automation_read_cache import AutomationReadCache
from activity_stream import ActivityStream
from task_queries import (TaskQueries, PersonnelResolver, reader, FAILURE_CATEGORIES,
                          failure_category, job_failure_metadata)


# Copy preparation follows the target account's adaptive MiaoShou lane. The
# hard ceiling keeps a single large target from occupying all shared workers;
# the lane still controls request pacing, its own remote-call cap and backoff.
COPY_PREPARATION_PER_TARGET = 4
COPY_PREPARATION_MAX_WORKERS = 8


class LocalDatabaseBusy(RuntimeError):
    """A local SQLite write remained busy after bounded retries."""


def copy_preparation_limit(account_id):
    """Allow only the capacity that the target account currently recovered."""
    try:
        remote_limit = int(RUNTIME.lane(account_id).requests.limit)
    except (TypeError, ValueError):
        remote_limit = 1
    return max(1, min(COPY_PREPARATION_PER_TARGET, remote_limit))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('账号、商品或店铺编号必须是正整数')
    return value


def source_ref(value):
    if not isinstance(value, dict):
        raise ValueError('商品引用必须包含来源账号和商品 ID')
    return {'sourceAccountId': positive(value.get('sourceAccountId')), 'detailId': positive(value.get('detailId'))}


def target_ref(value):
    if not isinstance(value, dict):
        raise ValueError('店铺引用必须包含目标账号和店铺 ID')
    return {'targetAccountId': positive(value.get('targetAccountId')), 'shopId': positive(value.get('shopId'))}


def copy_signature(item):
    # Compare actual product identity, not target-generated IDs or timestamps.
    fields = ('title', 'cid', 'imgUrls', 'saleAttributes')
    data = {key: item.get(key) for key in fields}
    data['skus'] = sorted((str(key), str(value.get('itemNum', '')))
                          for key, value in (item.get('skuMap') or {}).items())
    return hashlib.sha256(encoded(data).encode()).hexdigest()


def shorten_copy_english_title(item):
    """Fit only an overlong outgoing English title, preferably at a word boundary."""
    titles = item.get('multiLanguageTitleMap')
    title = titles.get('en') if isinstance(titles, dict) else None
    if not isinstance(title, str) or len(title.encode('utf-16-le')) // 2 <= 250:
        return False
    units, end = 0, 0
    for character in title:
        units += 2 if ord(character) > 0xFFFF else 1
        if units > 250:
            break
        end += 1
    fitted = title[:end]
    if end < len(title) and not title[end].isspace() and not fitted[-1].isspace():
        boundary = max((i for i, char in enumerate(fitted) if char.isspace()), default=-1)
        if boundary > 0:
            fitted = fitted[:boundary]
    fitted = fitted.rstrip(' ,，、;；:-—/ ')
    if not fitted.strip():
        raise ValueError('英文标题无法安全精简，请手动填写有效标题')
    titles['en'] = fitted
    return True


def create_copy_payload(raw, shop_ids, site):
    item = deepcopy(raw)
    for key in ('detailId', 'collectBoxDetailId', 'shopId', 'subAppAccountId',
                'shopIdAndGoodsModelReqMap', 'siteWarehouseList', 'siteFreightTemplateList',
                'siteAndSkuPriceMap', 'goodsModelReqs',
                'collectShowSizeTemplateIds', 'sizeAttributeValueGroupId'):
        item.pop(key, None)
    item['collectBoxDetailShopList'] = [{'shopId': shop, 'publishSiteList': [site]} for shop in shop_ids]
    item['publishSiteList'] = [site]
    item['publishEuSiteList'] = []
    for sku in (item.get('skuMap') or {}).values():
        sku.pop('siteAndWarehouseIdAndStockMap', None)
    item = normalize_item(item, 'site')
    normalize_copy_size_chart_groups(item)
    shorten_copy_english_title(item)
    return item


def normalize_copy_size_chart_groups(item):
    """Supply the empty map required by MiaoShou's size-chart schema.

    A missing/``null`` groupIdAndValueMap is not an uncertain create outcome:
    MiaoShou rejects the request before it creates a collection-box product.
    Work only on the already-copied payload so the source item remains intact.
    """
    changed = False
    for chart in item.get('sizeCharts') or []:
        if not isinstance(chart, dict):
            continue
        for size_item in chart.get('sizeItems') or []:
            if not isinstance(size_item, dict):
                continue
            if not isinstance(size_item.get('groupIdAndValueMap'), dict):
                size_item['groupIdAndValueMap'] = {}
                changed = True
    return changed


def _site_delivery_day(item, site):
    """Read the source product's delivery promise without changing it."""
    by_site = item.get('siteShipmentLimitDayMap')
    value = by_site.get(site) if isinstance(by_site, dict) and site in by_site else item.get('shipmentLimitDay')
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_apparel_payload(item):
    """Conservatively identify apparel only when its own data proves it."""
    if item.get('sizeCharts'):
        return True
    specs = item.get('saleAttributes') or []
    names = {str(row.get('name') or '').strip().casefold() for row in specs if isinstance(row, dict)}
    has_colour = any(word in name for name in names for word in ('颜色', 'color', 'colour'))
    has_size = any(word in name for name in names for word in ('尺码', '尺寸', 'size'))
    title = str(item.get('title') or '').casefold()
    apparel_words = ('服装', '服饰', '上衣', '外套', '夹克', '裤', '裙', '内衣', '袜', '泳装', '连衣裙')
    return (has_colour or has_size) and any(word in title for word in apparel_words)


def template_preflight(item, site):
    """Validate cross-account source facts against the supplied import templates.

    This check reports missing data before a create request. It never fills,
    translates, or changes source product data.
    """
    issues = []
    english_title = (item.get('multiLanguageTitleMap') or {}).get('en')
    if not str(item.get('title') or '').strip():
        issues.append('缺少产品标题')
    if not str(english_title or '').strip():
        issues.append('缺少英文标题')
    delivery_day = _site_delivery_day(item, site)
    if delivery_day is None or delivery_day <= 0:
        issues.append(f'{site} 站缺少承诺发货时效')
    elif site == 'US' and delivery_day not in {1, 2, 9}:
        issues.append(f'美国站承诺发货时效为 {delivery_day}，模板仅支持 1、2 或 9 天')
    if not str(item.get('productOriginCountry') or '').strip():
        issues.append('缺少产地')
    elif str(item.get('productOriginCountry')).strip().upper() == 'CN' and not str(item.get('productOriginProvince') or '').strip():
        issues.append('中国商品缺少产地省份')
    if not any(isinstance(url, str) and url.strip() for url in item.get('imgUrls') or []):
        issues.append('缺少产品轮播图')

    specs = [row for row in item.get('saleAttributes') or [] if isinstance(row, dict)
             and str(row.get('name') or '').strip() and any(isinstance(value, dict) and str(value.get('name') or '').strip()
                                                            for value in row.get('values') or [])]
    if not specs:
        issues.append('缺少规格名称和规格属性值')
    skus = item.get('skuMap') or {}
    if not isinstance(skus, dict) or not skus:
        issues.append('缺少 SKU')
    for key, sku in list(skus.items())[:10]:
        if not isinstance(sku, dict):
            issues.append(f'SKU {key} 资料不正确')
            continue
        if not str(sku.get('imgUrl') or '').strip():
            issues.append(f'SKU {key} 缺少预览图')
        for field, label in (('price', '申报价'), ('length', '长'), ('width', '宽'), ('height', '高'), ('weight', '重量')):
            if sku.get(field) in (None, ''):
                issues.append(f'SKU {key} 缺少{label}')
        classification = sku.get('skuClassification')
        combo = str(classification).strip() in {'2', '3', '组合装', '混合套装'}
        if combo and not isinstance(sku.get('productSkuAccessories'), list):
            issues.append(f'SKU {key} 为组合/混合套装，缺少包装清单（包含的组件）')

    if _is_apparel_payload(item):
        if len(specs) < 2:
            issues.append('服饰商品缺少第二组规格')
        colour_spec = next((row for row in specs if any(word in str(row.get('name') or '').casefold()
                                                          for word in ('颜色', 'color', 'colour'))), None)
        if colour_spec and any(not any(isinstance(url, str) and url.strip() for url in value.get('imgUrls') or [])
                               for value in colour_spec.get('values') or [] if isinstance(value, dict)):
            issues.append('服饰颜色规格缺少颜色图')
    return list(dict.fromkeys(issues))


class SharedAutomation:
    def __init__(self, backend, path=None):
        self.b = backend
        self.path = Path(path or backend.AUTOMATION_POOL.path.parent / 'shared_automation.sqlite3')
        self.dispatch_lock = Lock()
        self.publish_action_lock = Lock()
        # Distinct from dispatch_lock: this guards only an explicit operator
        # request to re-load a persisted queue, so double clicks cannot start
        # multiple dispatchers while the normal scheduler still owns its lock.
        self.reload_lock = Lock()
        self.prepare_lock = Lock()
        self.transfer_action_lock = Lock()
        self.failure_action_lock = Lock()
        self.failure_pending = set()
        self.job_creation_lock = Lock()
        self.config_reads = AutomationReadCache()
        self.source_reads = AutomationReadCache()
        self.daily_stats_lock = Lock()
        self.daily_stats_refresh_lock = Lock()
        self.daily_stats_lifecycle_lock = Lock()
        self.daily_stats_closed = Event()
        self.daily_stats_thread = None
        self.daily_stats_cache = None
        self.account_display_cache = None
        self.activity_events = ActivityStream(self.activity)
        with self.db() as connection:
            # WAL lets the frequent task/status reads coexist with the queue
            # writer.  The busy timeout below still protects the rare writer
            # collision without treating it as an unknown remote create.
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS shared_accounts (account_id INTEGER PRIMARY KEY, scope TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_bindings (
                    source_account INTEGER NOT NULL, source_scope TEXT NOT NULL, person_id TEXT NOT NULL,
                    targets TEXT NOT NULL, PRIMARY KEY(source_account,source_scope,person_id));
                CREATE TABLE IF NOT EXISTS shared_copies (
                    copy_key TEXT PRIMARY KEY, source_account INTEGER NOT NULL, source_scope TEXT NOT NULL,
                    source_id INTEGER NOT NULL, target_account INTEGER NOT NULL, target_scope TEXT NOT NULL,
                    target_id INTEGER, status TEXT NOT NULL, expected TEXT, message TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_scope,source_id,target_scope), UNIQUE(target_scope,target_id));
                CREATE TABLE IF NOT EXISTS shared_batches (
                    batch_id TEXT PRIMARY KEY, request TEXT NOT NULL, plan TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, concurrency INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_transfers (
                    transfer_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', target_id INTEGER,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_job_origins (
                    account_id INTEGER NOT NULL, job_id TEXT NOT NULL, source_account INTEGER NOT NULL,
                    source_scope TEXT NOT NULL, source_id INTEGER NOT NULL, target_scope TEXT NOT NULL,
                    PRIMARY KEY(account_id,job_id));
                CREATE TABLE IF NOT EXISTS shared_transfer_jobs (
                    transfer_id TEXT NOT NULL, account_id INTEGER NOT NULL, job_id TEXT NOT NULL,
                    PRIMARY KEY(account_id,job_id));
                CREATE INDEX IF NOT EXISTS shared_transfer_batch ON shared_transfers(batch_id);
                CREATE INDEX IF NOT EXISTS shared_transfer_job_lookup ON shared_transfer_jobs(transfer_id);
                CREATE INDEX IF NOT EXISTS shared_transfer_source_owner ON shared_transfers(
                    json_extract(payload,'$.sourceScope'), CAST(json_extract(payload,'$.detailId') AS TEXT));
                CREATE INDEX IF NOT EXISTS shared_copy_target_lookup ON shared_copies(target_account,target_id);
                CREATE INDEX IF NOT EXISTS shared_copy_source_lookup ON shared_copies(source_account,source_id);
                CREATE INDEX IF NOT EXISTS shared_transfer_status_updated ON shared_transfers(status,updated);
                CREATE INDEX IF NOT EXISTS shared_transfer_updated ON shared_transfers(updated);
                CREATE TABLE IF NOT EXISTS shared_publish_receipts (
                    request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL,
                    status TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_publish_previews (
                    preview_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shared_source_evidence (
                    source_scope TEXT NOT NULL, source_id INTEGER NOT NULL,
                    signature TEXT NOT NULL, facts TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(source_scope,source_id));
                CREATE TABLE IF NOT EXISTS shared_controls (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO shared_controls(name,value) VALUES ('editing_paused','0');
            ''')

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA busy_timeout=30000')
            with connection:
                yield connection

    @staticmethod
    def _database_busy(error):
        return isinstance(error, LocalDatabaseBusy) or (isinstance(error, sqlite3.OperationalError) and any(
            phrase in str(error).lower() for phrase in ('database is locked', 'database is busy')))

    def _local_write(self, action):
        """Retry only local SQLite writes; never repeat a remote MiaoShou call."""
        error = None
        for attempt in range(6):
            try:
                with self.db() as connection:
                    return action(connection)
            except sqlite3.OperationalError as current:
                if not self._database_busy(current):
                    raise
                error = current
                time.sleep(0.15 * (attempt + 1))
        raise LocalDatabaseBusy(str(error)) from error

    @staticmethod
    def _paused(connection):
        row = connection.execute("SELECT value FROM shared_controls WHERE name='editing_paused'").fetchone()
        return bool(row and row['value'] == '1')

    def is_paused(self):
        with self.db() as connection:
            return self._paused(connection)

    def queued_transfer_count(self):
        """Count only untouched shared work; review records are never replayed."""
        with self.db() as connection:
            return connection.execute("SELECT COUNT(*) FROM shared_transfers WHERE status='queued'").fetchone()[0]

    def reload_queued(self, body):
        """Safely wake persisted queues after the operator explicitly asks.

        ``dispatch`` claims each shared transfer atomically and the normal edit
        scheduler claims local jobs atomically, so this operation never retries
        a running, review, failed, published, or indeterminate remote action.
        """
        if body.get('confirmation') != 'RELOAD_SHARED_QUEUE':
            raise ValueError('请确认重新加载排队任务')
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前服务为只读模式，不能重新加载任务')
        if self.is_paused():
            raise ValueError('自动处理已暂停；请先恢复自动处理后再重新加载')
        if not self.reload_lock.acquire(blocking=False):
            raise ValueError('正在重新加载排队任务，请稍后查看进度')
        try:
            shared = self.queued_transfer_count()
            local_accounts = []
            for account in self.accounts():
                if not account['configured'] or account['blocked']:
                    continue
                pool = self.b.automation_pool(account['id'])
                if pool.path.exists() and pool.has_queued_jobs():
                    local_accounts.append(account['id'])
            if not shared and not local_accounts:
                self.reload_lock.release()
                return {'started': False, 'sharedTransfers': 0, 'localAccounts': [],
                        'message': '没有可安全重新加载的排队任务'}
            if self.dispatch_lock.locked() or self.b.AUTOMATION_EDIT_SCHEDULER_LOCK.locked():
                self.reload_lock.release()
                raise ValueError('已有保存调度正在运行，请稍后刷新任务中心')
            Thread(target=self._reload_worker, daemon=True, name='shared-queue-reload').start()
            return {'started': True, 'sharedTransfers': shared, 'localAccounts': local_accounts}
        except Exception:
            if self.reload_lock.locked():
                self.reload_lock.release()
            raise

    def _reload_worker(self):
        try:
            self.dispatch()
        finally:
            self.reload_lock.release()

    def set_paused(self, body):
        """Persist a safe scheduler pause across a controlled backend restart."""
        if body.get('confirmation') != 'SET_SHARED_AUTOMATION_PAUSE':
            raise ValueError('缺少暂停或恢复自动处理确认')
        paused = body.get('paused')
        if type(paused) is not bool:
            raise ValueError('暂停状态不正确')
        with self.db() as connection:
            connection.execute("UPDATE shared_controls SET value=? WHERE name='editing_paused'", ('1' if paused else '0',))
        if not paused and self.b.os.getenv('APP_MODE', 'demo') == 'live':
            RUNTIME.work_available.set()
            Thread(target=self.dispatch, daemon=True, name='shared-automation-resume').start()
        # A committed control must not fail its response because a later
        # statistics read is busy. The shared sampler reports drain progress.
        return {'paused': paused, 'draining': paused}

    def rebalance_plain_queued_jobs(self, body):
        """Move only pre-write local queues while dispatch is safely paused."""
        if body.get('confirmation') != 'REBALANCE_UNSTARTED_QUEUED_JOBS':
            raise ValueError('缺少未开始任务均衡确认')
        account = body.get('accountId')
        limit = body.get('limit', 1000)
        if type(account) is not int or account <= 0 or type(limit) is not int:
            raise ValueError('采集箱账号或迁移数量不正确')
        if not self.is_paused():
            raise ValueError('请先暂停自动处理，再迁移未开始任务')
        activity = self.activity()
        if activity['editRunning'] or activity['publishing'] or activity['inFlightTransfers'] or activity['syncing'] or activity['reloading']:
            raise ValueError('仍有编辑、发布、同步或副本任务在运行，暂不能迁移')
        configured = {row['id'] for row in self.accounts() if row['configured']}
        if account not in configured:
            raise ValueError('采集箱账号不存在或尚未配置')
        pool = self.b.automation_pool(account)
        if not hasattr(pool, 'rebalance_plain_queued_jobs'):
            raise ValueError('当前账号没有可均衡的独立池')
        return {'accountId': account, **pool.rebalance_plain_queued_jobs(limit), 'activity': self.activity()}

    def scope(self, account):
        client = self.b.MiaoShouClient(account)
        return hashlib.sha256(f'{account}\0{client.app_key}\0{client.app_secret}'.encode()).hexdigest()

    def accounts(self):
        result = []
        with self.db() as connection:
            for account in self.b.miaoshou_account_slots(include_removed=True):
                if account.get('removed') and account.get('historyCleared'):
                    continue
                scope = self.scope(account['id'])
                stored = connection.execute('SELECT scope FROM shared_accounts WHERE account_id=?', (account['id'],)).fetchone()
                if account['configured'] and not stored:
                    connection.execute('INSERT OR IGNORE INTO shared_accounts VALUES (?,?)', (account['id'], scope))
                # Retired identities remain readable with their original aliases.
                # They are never writable, even when the retained scope matches.
                removed = account.get('removed', False)
                result.append({'id': account['id'], 'name': account['name'], 'configured': account['configured'],
                               'scope': stored['scope'] if removed and stored else scope,
                               'blocked': bool(removed or (stored and stored['scope'] != scope)),
                               **({'removed': True} if account.get('removed') else {})})
        self.account_display_cache = (time.monotonic(), deepcopy(result))
        return result

    def status_accounts(self):
        cached = self.account_display_cache
        return deepcopy(cached[1]) if cached and time.monotonic() - cached[0] < 60 else self.accounts()

    def check_account(self, account_id, expected=None):
        account = next((a for a in self.accounts() if a['id'] == positive(account_id)), None)
        if not account or not account['configured']:
            raise ValueError(f'采集箱 {account_id} 未配置 API')
        if account['blocked'] or (expected and expected != account['scope']):
            raise ValueError(f'采集箱 {account_id} 凭据已变化，旧商品和任务已暂停；请恢复原凭据或核对账号，不能直接复用旧 ID')
        return account

    def worker(self, account_id, source=None, *, cache_config=True):
        account = self.check_account(account_id)
        worker = self.b.BackgroundAutomationWorker(account_id)
        vendor = worker._vendor
        def guarded(path, payload):
            self.check_account(account_id, account['scope'])
            if source:
                self.check_account(source['sourceAccountId'], source['sourceScope'])
            schemas = {
                self.b.SHOP_LIST: ('shopList', list),
                self.b.HALF_SITES: ('publishSiteOptions', list),
                self.b.HALF_FREIGHT: ('shopIdAndFreightTemplateListMap', dict),
                self.b.HALF_WAREHOUSE: ('shopIdAndWarehouseListMap', dict),
                self.b.HALF_CATEGORY_ATTRIBUTE_RULES: ('productAttributeRules', list),
            }
            if path not in schemas or not cache_config:
                return vendor(path, payload)
            field, kind = schemas[path]
            result = self.config_reads.get((account['scope'], path, encoded(payload)),
                lambda: vendor(path, payload), ttl=60,
                valid=lambda value: isinstance(value, dict) and isinstance(value.get('data'), dict)
                    and isinstance(value['data'].get(field), kind))
            self.check_account(account_id, account['scope'])
            if source:
                self.check_account(source['sourceAccountId'], source['sourceScope'])
            return result
        worker._vendor = guarded
        def get_source(detail_id, mode, shop_id=0):
            self.check_account(account_id, account['scope'])
            if source:
                self.check_account(source['sourceAccountId'], source['sourceScope'])
            result = self.source_reads.get((account['scope'], detail_id, mode, shop_id),
                lambda: worker._get_item(detail_id, mode, shop_id), ttl=5,
                valid=lambda value: isinstance(value, dict) and bool(value.get('title'))
                    and bool(value.get('skuMap')) and bool(value.get('imgUrls')))
            self.check_account(account_id, account['scope'])
            if source:
                self.check_account(source['sourceAccountId'], source['sourceScope'])
            return result
        worker._get_source_item = get_source
        return worker

    def config(self, refresh=False):
        accounts = self.accounts()
        targets, errors = [], []
        def read(account):
            self.check_account(account['id'])
            worker = self.worker(account['id'], cache_config=not refresh)
            cache = self.b.collection_read_cache()
            shops = cache.get(account['scope'], 'shops', lambda: worker._vendor(self.b.SHOP_LIST,
                {'platform': 'pddkjChoice', 'site': 'PDDKJCHOICE', 'pageNo': 1, 'pageSize': 100}), ttl=604800, force=refresh)
            sites = cache.get(account['scope'], 'sites', lambda: worker._vendor(self.b.HALF_SITES, {}), ttl=604800, force=refresh)
            error = shops.get('cache', {}).get('error') or sites.get('cache', {}).get('error')
            if error:
                raise ValueError(f'店铺或站点同步失败：{error}')
            return {'accountId': account['id'], 'scope': account['scope'], 'name': account['name'],
                    'shops': shops.get('data', {}).get('shopList', []),
                    'sites': sites.get('data', {}).get('publishSiteOptions', [])}
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = [(account, executor.submit(read, account)) for account in accounts if account['configured']]
            for account, future in pending:
                try:
                    targets.append(future.result())
                except Exception as error:
                    errors.append({'accountId': account['id'], 'message': str(error)[:300]})
        return {'accounts': accounts, 'targets': targets, 'errors': errors,
                'mode': self.b.os.getenv('APP_MODE', 'demo'), 'paused': self.is_paused()}

    def refresh_shops(self):
        """Refresh configured shops plus their warehouse and freight caches.

        This deliberately does not start collection sync, scan products, update
        personnel, or touch any automation queue.  Logistics failures retain
        their last good cache and are reported per account/shop.
        """
        accounts = self.accounts()

        def read(account):
            self.check_account(account['id'], account['scope'])
            worker = self.worker(account['id'], cache_config=False)
            cache = self.b.collection_read_cache()
            result = cache.get(
                account['scope'], 'shops',
                lambda: worker._vendor(self.b.SHOP_LIST, {
                    'platform': 'pddkjChoice', 'site': 'PDDKJCHOICE', 'pageNo': 1, 'pageSize': 100}),
                ttl=604800, force=True)
            error = result.get('cache', {}).get('error')
            if error:
                raise ValueError(f'店铺同步失败：{error}')
            rows = (result.get('data') or {}).get('shopList')
            if not isinstance(rows, list):
                raise ValueError('妙手未返回有效店铺列表')
            shops = []
            for row in rows:
                try:
                    shop_id = positive(row.get('shopId'))
                except (TypeError, ValueError):
                    continue
                shops.append(shop_id)
            logistics = (
                ('freight', self.b.HALF_FREIGHT, 'shopIdAndFreightTemplateListMap'),
                ('warehouse', self.b.HALF_WAREHOUSE, 'shopIdAndWarehouseListMap'),
            )

            def read_shop(shop_id):
                counts, errors = {}, []
                for kind, path, field in logistics:
                    def vendor():
                        payload = worker._vendor(path, {'shopId': shop_id, 'isRsync': 1})
                        data = payload.get('data') if isinstance(payload, dict) else None
                        mapping = data.get(field) if isinstance(data, dict) else None
                        if not isinstance(mapping, dict) or not isinstance(mapping.get(str(shop_id)), list):
                            raise ValueError('妙手未返回有效' + ('运费模板' if kind == 'freight' else '仓库') + '列表')
                        return payload
                    try:
                        payload = cache.get(account['scope'], f'logistics:{kind}:{shop_id}', vendor,
                                            ttl=604800, force=True)
                        error = (payload.get('cache') or {}).get('error')
                        mapping = ((payload.get('data') or {}).get(field) or {})
                        current = mapping.get(str(shop_id), [])
                        counts[kind] = len(current) if isinstance(current, list) else 0
                        if error:
                            errors.append(f"店铺 {shop_id} {('运费模板' if kind == 'freight' else '仓库')}同步失败：{error}")
                    except Exception as error:
                        counts[kind] = 0
                        errors.append(f"店铺 {shop_id} {('运费模板' if kind == 'freight' else '仓库')}同步失败：{str(error)[:180]}")
                return counts, errors

            freight_count = warehouse_count = synced_shops = 0
            warnings = []
            # Two shops per account keeps a refresh responsive without turning
            # a settings click into an unbounded remote request burst.
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = [executor.submit(read_shop, shop_id) for shop_id in shops]
                for future in pending:
                    counts, errors = future.result()
                    freight_count += counts.get('freight', 0)
                    warehouse_count += counts.get('warehouse', 0)
                    if errors:
                        warnings.extend(errors)
                    else:
                        synced_shops += 1
            return {'accountId': account['id'], 'name': account['name'], 'shopCount': len(rows),
                    'syncedShopCount': synced_shops, 'freightTemplateCount': freight_count,
                    'warehouseCount': warehouse_count, 'warnings': warnings}

        refreshed, errors, warnings = [], [], []
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = [(account, executor.submit(read, account)) for account in accounts if account['configured']]
            for account, future in pending:
                try:
                    row = future.result()
                    refreshed.append(row)
                    warnings.extend({'accountId': row['accountId'], 'name': row['name'], 'message': message}
                                    for message in row.pop('warnings'))
                except Exception as error:
                    errors.append({'accountId': account['id'], 'name': account['name'], 'message': str(error)[:300]})
        return {'accounts': refreshed, 'errors': errors, 'warnings': warnings}

    def people(self, account_id, scan=False, refresh=False):
        account = self.check_account(account_id)
        if scan:
            worker = self.worker(account_id)
            self.b.COLLECTION_PEOPLE.scan(account['scope'], lambda page, size: worker._vendor(
                self.b.HALF_BOX_LIST, {'pageNo': page, 'pageSize': size}), refresh=refresh)
        status, owners = self.b.COLLECTION_PEOPLE.ownership(account['scope'])
        people = self.b.COLLECTION_PEOPLE.store.list(account['scope'])
        for person in people:
            person['targets'] = self.binding(account_id, account['scope'], person['id'])
        return {'accountId': account_id, 'scope': account['scope'], 'people': people, 'scan': status}

    def binding(self, source, scope, person):
        with self.db() as connection:
            row = connection.execute('SELECT targets FROM shared_bindings WHERE source_account=? AND source_scope=? AND person_id=?',
                                     (source, scope, person)).fetchone()
        if row:
            return json.loads(row['targets'])
        legacy = self.b.COLLECTION_PEOPLE.store.bindings(scope).get(person, [])
        return [{'targetAccountId': source, 'targetScope': scope, 'shopId': shop} for shop in legacy]

    def save_binding(self, body):
        source = positive(body.get('sourceAccountId'))
        account = self.check_account(source, body.get('scope'))
        person = self.b.collector_id(body.get('personId'))
        known = {row['id'] for row in self.b.COLLECTION_PEOPLE.store.list(account['scope'])}
        if not person or person not in known:
            raise ValueError('请先刷新该采集箱人员，不能绑定未知人员')
        values = body.get('targets')
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError('每个人最多绑定 100 家店铺')
        config = self.config()
        available = {(group['accountId'], int(shop['shopId'])) for group in config['targets'] for shop in group['shops']}
        targets = []
        for raw in values:
            target = target_ref(raw)
            if (target['targetAccountId'], target['shopId']) not in available:
                raise ValueError('目标店铺不可用，请刷新店铺列表')
            target['targetScope'] = self.check_account(target['targetAccountId'])['scope']
            if target not in targets:
                targets.append(target)
        with self.db() as connection:
            connection.execute('INSERT OR REPLACE INTO shared_bindings VALUES (?,?,?,?)',
                               (source, account['scope'], person, encoded(targets)))
        return {'saved': True, 'targets': targets}

    def _copies(self):
        with self.db() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM shared_copies')]

    def pool_rows(self):
        return TaskQueries(self).pool({}, all_rows=True)['items']

    def person_resolver(self, accounts=None):
        return PersonnelResolver(self, accounts)

    def job_rows(self, include_deleted=False, resolve_person=None):
        return TaskQueries(self).jobs(include_deleted, resolve_person)

    def transfer_rows(self, resolve_person):
        with reader(self.path) as connection:
            pending = list(connection.execute('''SELECT t.*, b.created AS queued_at
                FROM shared_transfers t JOIN shared_batches b ON b.batch_id=t.batch_id
                WHERE t.status NOT IN ('done','discarded') ORDER BY t.updated DESC'''))
        # Decode once, then look up the selected copy keys in bounded batches.
        # Keep credential scopes in every join; equal product IDs in different
        # accounts must never share a copy reservation or retry decision.
        decoded = [(row, json.loads(row['payload'])) for row in pending]
        copies = {}
        keys = list(dict.fromkeys((r['sourceScope'], r['detailId'], r['targetScope']) for _, r in decoded))
        with reader(self.path) as connection:
            for offset in range(0, len(keys), 250):
                selected = keys[offset:offset + 250]
                placeholders = ','.join('(?,?,?)' for _ in selected)
                rows = connection.execute('''WITH requested(source_scope,source_id,target_scope) AS (VALUES ''' +
                    placeholders + ''') SELECT c.source_scope,c.source_id,c.target_scope,c.status,c.message,c.target_id
                    FROM requested r JOIN shared_copies c ON c.source_scope=r.source_scope
                    AND c.source_id=r.source_id AND c.target_scope=r.target_scope''',
                    [value for key in selected for value in key])
                for copy in rows:
                    copies[(copy['source_scope'], copy['source_id'], copy['target_scope'])] = dict(copy)
        available = {a['id'] for a in self.accounts() if a['configured'] and not a['blocked']}
        if hasattr(resolve_person, 'preload'):
            resolve_person.preload((r['sourceAccountId'], r['detailId'], r['sourceScope']) for _, r in decoded)
        result = []
        for row, ref in decoded:
            copy = copies.get((ref['sourceScope'], ref['detailId'], ref['targetScope']))
            person = resolve_person(ref['sourceAccountId'], ref['detailId'], ref['sourceScope'])
            entry = {**dict(row), 'payload': {k: v for k, v in ref.items()
                if k in {'sourceAccountId', 'detailId', 'targetAccountId', 'shopIds', 'title'}},
                'sourceAccountId': ref['sourceAccountId'], 'accountId': ref['targetAccountId'],
                'detailId': ref['detailId'], 'title': ref.get('title', ''), 'shopIds': ref['shopIds'], **person,
                'queuedAt': row['queued_at'],
                'canResume': row['status'] == 'needs_review' and (not copy or copy['status'] == 'ready'
                    or (copy['status'] == 'needs_review' and self._is_definite_create_rejection(copy['message'])))}
            entry['available'] = ref['sourceAccountId'] in available and ref['targetAccountId'] in available
            entry.update(self.transfer_failure_metadata(entry, copy))
            result.append(entry)
        return result

    def transfer_failure_metadata(self, row, copy):
        category = failure_category(row['status'], '', row['message'])
        if category == 'unknown' and copy and self._is_definite_create_rejection(copy['message']):
            category = failure_category(row['status'], '', copy['message'])
        # A persisted reservation precedes every create request. No reservation,
        # a confirmed ID, or the existing explicit validation allowlist is the
        # evidence; a timeout/database word in the transfer message is not.
        known = (not copy or copy['status'] == 'ready' or
                 (copy['status'] == 'needs_review' and self._is_definite_create_rejection(copy['message'])))
        eligible = (row['status'] == 'needs_review' and known and row.get('available', True)
                    and category not in {'unknown', 'none'})
        reason = ('副本尚未提交、已确认存在或上次被明确拒绝；沿用副本核查及默认资料流程' if eligible else
                  '副本创建结果或失败原因需人工核查，批量处理不会重复创建')
        return {'failureCategory': category, 'safeBatchAction': 'resume-transfer' if eligible else '',
                'safeBatchEligible': eligible, 'safeBatchReason': reason}

    @staticmethod
    def page(rows, query, key='items'):
        size = 50
        if key in {'items', 'jobs'}:
            value = query.get('pageSize', ['50'])[0]
            if value not in ('50', '100', '200', '500'):
                raise ValueError('每页条数只能选择 50、100、200 或 500')
            size = int(value)
        rows = sorted(rows, key=lambda r: (r.get('createdAt', ''), r['key']), reverse=True)
        total = len(rows)
        page = max(1, int(query.get('page', ['1'])[0]))
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        return {key: rows[(page - 1) * size:page * size], 'page': page, 'pageSize': size,
                'total': total, 'pages': pages}

    @staticmethod
    def filter_rows(rows, query):
        category = query.get('failureCategory', [''])[0]
        if category:
            if category not in FAILURE_CATEGORIES:
                raise ValueError('失败原因分类不正确')
            rows = [row for row in rows if row.get('failureCategory') == category]
        for field in ('sourceAccountId', 'accountId', 'shopId', 'personId', 'poolId'):
            value = query.get(field, [''])[0]
            if value:
                rows = [row for row in rows if (value in [str(s) for s in row['shopIds']]
                        if field == 'shopId' and 'shopIds' in row else str(row.get(field, '')) == value)]
        person_key = query.get('personKey', [''])[0]
        if person_key:
            if person_key != 'unknown' and not re.fullmatch(r'[1-9][0-9]?:[1-9][0-9]{0,19}', person_key):
                raise ValueError('所属人员筛选不正确')
            rows = [row for row in rows if row.get('personKey', 'unknown') == person_key]
        term = query.get('keyword', [''])[0].strip().casefold()
        if term:
            rows = [row for row in rows if term in row['title'].casefold() or
                    term in {str(row['detailId']), str(row.get('sourceDetailId', ''))}]
        return rows

    def listing(self, kind, query):
        queries = TaskQueries(self)
        return queries.pool(query) if kind == 'pool' else queries.tasks(kind, query)

    def preview_context(self, refs=None):
        with self.db() as connection:
            pending = [json.loads(r['payload']) for r in connection.execute(
                "SELECT payload FROM shared_transfers WHERE status IN ('queued','running','needs_review')")]
        source_refs=[(r['sourceAccountId'],r['detailId']) for r in refs] if refs else None
        sql='SELECT copy_key,source_account,source_scope,source_id,target_account,target_scope,target_id,status,message FROM shared_copies'
        parameters=[]
        if source_refs:
            marks=','.join('(?,?)' for _ in source_refs)
            sql+=f' WHERE (source_account,source_id) IN ({marks}) OR (target_account,target_id) IN ({marks})'
            parameters=[v for ref in source_refs for v in ref]*2
        with reader(self.path) as connection: copies=[dict(row) for row in connection.execute(sql,parameters)]
        return {'config': self.config(), 'jobs': TaskQueries(self).job_metadata(True,source_refs=source_refs),
                'copies': copies, 'resolve': self.person_resolver(), 'pending': pending}

    def preview(self, body, persist=True, *, context=None):
        refs = body.get('items')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 500:
            raise ValueError('每批请选择 1 到 500 件商品')
        refs = list({(r['sourceAccountId'], r['detailId']): r for r in map(source_ref, refs)}.values())
        mode = body.get('targetMode', 'manual')
        if mode not in {'manual', 'personnel'}:
            raise ValueError('保存方式不正确')
        context = context or self.preview_context(refs)
        config = context['config']
        groups = {group['accountId']: group for group in config['targets']}
        available = {(group['accountId'], int(shop['shopId'])) for group in config['targets'] for shop in group['shops']}
        manual = body.get('targets', [])
        if mode == 'manual' and (not isinstance(manual, list) or not manual or len(manual) > 100):
            raise ValueError('请选择 1 到 100 家目标店铺')
        manual = list({(r['targetAccountId'], r['shopId']): r for r in map(target_ref, manual)}.values())
        site = body.get('site', 'US')
        if not isinstance(site, str) or not site.isalnum() or len(site) > 20:
            raise ValueError('发布站点不正确')
        site = site.upper()
        concurrency = self.b.normalize_edit_concurrency(body.get('editConcurrency', 8))
        price_mode = body.get('priceMode', 'floor')
        if price_mode == 'floor':
            self.b._normalize_automation_price(body.get('price', '160'))
        elif price_mode == 'source_multiplier':
            self.b._normalize_automation_price_multiplier(body.get('priceMultiplier', '1.5'))
        else:
            raise ValueError('申报价方式不正确')
        jobs, copies, resolve_person = context['jobs'], context['copies'], context['resolve']
        transfers, skipped = [], []
        for ref in refs:
            try:
                source = self.check_account(ref['sourceAccountId'])
            except ValueError as error:
                skipped.append({**ref, 'reason': str(error)})
                continue
            staged = self.b.automation_pool(source['id']).get_item(ref['detailId'])
            if not staged:
                raise ValueError(f"采集箱 {source['id']} 商品 {ref['detailId']} 不在池中")
            if any(c['target_account'] == source['id'] and c['target_scope'] == source['scope']
                   and c['target_id'] == ref['detailId'] for c in copies):
                raise ValueError('该商品是共享池已管理的目标副本，请选择原商品')
            template_issues = template_preflight(staged['item'], site)
            targets = manual
            person = resolve_person(source['id'], ref['detailId'], source['scope'])['personId']
            if mode == 'personnel':
                scan, owners = self.b.COLLECTION_PEOPLE.ownership(source['scope'])
                if scan.get('status') != 'ready':
                    skipped.append({**ref, 'reason': f"请先刷新采集箱 {source['id']} 的人员归属"})
                    continue
                person = self.b.collector_id(owners.get(str(ref['detailId'])))
                targets = self.binding(source['id'], source['scope'], person) if person else []
                if not targets:
                    skipped.append({**ref, 'reason': '所属人员未绑定店铺' if person else '未返回所属人员'})
            by_account = {}
            for target in targets:
                try:
                    account = self.check_account(target['targetAccountId'], target.get('targetScope'))
                except ValueError as error:
                    skipped.append({**ref, **target, 'reason': str(error)})
                    continue
                pair = (account['id'], target['shopId'])
                if pair not in available:
                    skipped.append({**ref, **target, 'reason': '目标店铺不可用'})
                    continue
                if site not in {str(s['key']) for s in groups[account['id']]['sites']}:
                    skipped.append({**ref, **target, 'reason': f'目标账号不支持站点 {site}'})
                    continue
                if account['id'] != source['id'] and template_issues:
                    skipped.append({**ref, **target, 'reason': '导入模板预检未通过：' + '；'.join(template_issues)})
                    continue
                copy = next((c for c in copies if c['source_scope'] == source['scope'] and c['source_id'] == ref['detailId']
                             and c['target_scope'] == account['scope']), None)
                target_id = ref['detailId'] if source['id'] == account['id'] else (copy or {}).get('target_id')
                existing = [j for j in jobs if j['accountId'] == account['id'] and j['detailId'] == target_id
                            and j['shopId'] == target['shopId'] and j['site'].upper() == site and j['status'] != 'canceled']
                if existing:
                    skipped.append({**ref, **target, 'reason': '已有目标任务或发布记录，请在任务中心查看／重试'})
                    continue
                if any(p['sourceScope'] == source['scope'] and p['detailId'] == ref['detailId']
                       and p['targetScope'] == account['scope'] and p['site'].upper() == site
                       and target['shopId'] in p['shopIds'] for p in context['pending']):
                    skipped.append({**ref, **target, 'reason': '已有排队或待核查的副本任务'})
                    continue
                by_account.setdefault(account['id'], []).append(target['shopId'])
            for target_id, shops in by_account.items():
                target = self.check_account(target_id)
                copy = next((c for c in copies if c['source_scope'] == source['scope'] and c['source_id'] == ref['detailId']
                             and c['target_scope'] == target['scope']), None)
                if copy and copy['status'] in {'creating', 'needs_review'}:
                    skipped.append({**ref, 'targetAccountId': target_id, 'reason': '副本结果待核查，请先关联目标 ID'})
                    continue
                transfers.append({**ref, 'sourceScope': source['scope'], 'targetAccountId': target_id,
                    'targetScope': target['scope'], 'shopIds': sorted(set(shops)), 'personId': person,
                    'title': staged['title'], 'sourceVersion': staged['version'], 'site': site,
                    'priceMode': price_mode, 'price': body.get('price', '160'),
                    'priceMultiplier': body.get('priceMultiplier', '1.5'),
                    'needsCopy': source['id'] != target_id and not (copy and copy['target_id'])})
        plan = {'transfers': transfers, 'skipped': skipped, 'copyCount': sum(r['needsCopy'] for r in transfers),
                'taskCount': sum(len(r['shopIds']) for r in transfers), 'editConcurrency': concurrency}
        if persist:
            preview_id = uuid.uuid4().hex
            with self.db() as connection:
                connection.execute('INSERT INTO shared_batches VALUES (?,?,?,?,?,?)',
                                   (preview_id, encoded(body), encoded(plan), 'preview', time.time(), concurrency))
            plan['previewId'] = preview_id
        return plan

    def execute(self, body, launch=True):
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前服务为只读模式，不能跨账号创建、保存或发布')
        if body.get('confirmation') != 'SAVE_SHARED_AUTOMATION_BATCH':
            raise ValueError('缺少分配明细确认')
        preview_id = body.get('previewId', '')
        with self.prepare_lock:
            with self.db() as connection:
                row = connection.execute('SELECT * FROM shared_batches WHERE batch_id=?', (preview_id,)).fetchone()
            if not row:
                raise ValueError('预览不存在，请重新预览')
            if row['status'] != 'preview':
                return {'batchId': preview_id, 'status': row['status'], 'duplicate': True}
            if time.time() - row['created'] > 600:
                raise ValueError('预览已超过 10 分钟，请重新预览')
            plan = self.preview(json.loads(row['request']), persist=False)
            if encoded(plan) != row['plan']:
                raise ValueError('商品、凭据、绑定或目标任务已变化，请重新预览')
            if not plan['transfers']:
                raise ValueError('本次没有可保存的目标，请查看跳过原因')
            with self.db() as connection:
                connection.execute("UPDATE shared_batches SET status='queued' WHERE batch_id=?", (preview_id,))
                for transfer in plan['transfers']:
                    connection.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, preview_id, encoded(transfer), 'queued', '', None, time.time()))
        if launch and not self.is_paused():
            Thread(target=self.dispatch, daemon=True, name='shared-automation-dispatch').start()
        return {'batchId': preview_id, 'status': 'queued', 'paused': self.is_paused()}

    def _set_transfer(self, transfer_id, status, message='', target_id=None):
        def update(connection):
            connection.execute('UPDATE shared_transfers SET status=?,message=?,target_id=COALESCE(?,target_id),updated=? WHERE transfer_id=?',
                               (status, message[:600], target_id, time.time(), transfer_id))
        self._local_write(update)

    def _set_copy_ready(self, copy_key, target_id, message=''):
        def update(connection):
            connection.execute('''UPDATE shared_copies SET target_id=?,status='ready',message=? WHERE copy_key=?''',
                               (target_id, message[:600], copy_key))
        self._local_write(update)

    def _set_copy_review(self, copy_key, message):
        def update(connection):
            connection.execute("UPDATE shared_copies SET status='needs_review',message=? WHERE copy_key=?",
                               (str(message)[:600], copy_key))
        self._local_write(update)

    @staticmethod
    def evidence_signature(raw):
        # Ignore timestamps/stock, but invalidate reviewed evidence when the
        # product description, category, specifications or source images change.
        keys = ('title', 'cid', 'multiLanguageTitleMap', 'attributes', 'imgUrls',
                'saleAttributes', 'goodsLayerDecorationReqs')
        return hashlib.sha256(encoded({key: raw.get(key) for key in keys}).encode()).hexdigest()

    def remember_source_evidence(self, account_id, detail_id, raw, facts):
        """Local maintenance hook for reviewed source facts; never edit the source."""
        account = self.check_account(account_id)
        for fact in facts:
            if (not fact.get('name') or not fact.get('value') or not fact.get('evidence')
                    or fact.get('imageUrl') not in (raw.get('imgUrls') or [])):
                raise ValueError('图片核查依据必须引用当前源商品图片并填写明确属性')
        with self.db() as connection:
            connection.execute('INSERT OR REPLACE INTO shared_source_evidence VALUES (?,?,?,?,?)',
                (account['scope'], positive(detail_id), self.evidence_signature(raw), encoded(facts), time.time()))

    def _source_with_evidence(self, raw, transfer):
        source = deepcopy(raw)
        with self.db() as connection:
            row = connection.execute('SELECT * FROM shared_source_evidence WHERE source_scope=? AND source_id=?',
                (transfer['sourceScope'], transfer['detailId'])).fetchone()
        if row and row['signature'] == self.evidence_signature(raw):
            names = {a.get('name') for a in source.get('attributes') or [] if isinstance(a, dict) and a.get('values')}
            for fact in json.loads(row['facts']):
                if fact['name'] not in names:
                    source.setdefault('attributes', []).append({'name': fact['name'], 'values': [{'name': fact['value']}]})
                    names.add(fact['name'])
        return source

    def _prepare_copy_attributes(self, raw, item, transfer, required_by_vendor=False):
        source = self._source_with_evidence(raw, transfer)
        rules = self._sync_copy_components(source, item, transfer, required_by_vendor)
        self._complete_copy_attributes(source, item, transfer['targetAccountId'], transfer, rules=rules)

    def _prepare_copy_logistics(self, item, transfer):
        """The create API needs target logistics too, before the later save pass."""
        site = transfer['site']
        worker = self.worker(transfer['targetAccountId'], transfer)
        for row in item['collectBoxDetailShopList']:
            shop_id = row['shopId']
            def read(path, payload):
                return self.b.miao_request(lambda: worker._vendor(path, payload), transfer['targetAccountId'])
            freight_rows = [r for r in self.b.automation_logistic_rows(read, 'freight', shop_id, site)
                            if str(r.get('templateName', '')).strip() == '通用']
            warehouse_rows = [r for r in self.b.automation_logistic_rows(read, 'warehouse', shop_id, site)
                              if str(r.get('warehouseName', '')).strip() == '发货']
            if len(freight_rows) != 1 or len(warehouse_rows) != 1:
                raise ValueError(f'目标店铺 {shop_id} 的 {site} 站缺少唯一的“通用”运费模板或“发货”仓库，未创建副本')
            freight_id, warehouse_id = str(freight_rows[0]['templateId']), str(warehouse_rows[0]['warehouseId'])
            row['siteFreightTemplateList'] = [{'site': site, 'shopId': str(shop_id), 'freightTemplateId': freight_id}]
            row['siteWarehouseList'] = [{'site': site, 'shopId': shop_id, 'warehouseId': warehouse_id}]
            for sku in item['skuMap'].values():
                sku.setdefault('siteAndWarehouseIdAndStockMap', {}).setdefault(site, {})[warehouse_id] = 1000

    def _complete_copy_attributes(self, raw, item, target, transfer, rules=None):
        """Complete target-required attributes before the first cross-account create.

        This is read-only until the caller submits HALF_CREATE. Preserve source
        facts first, then use the same configured defaults as ordinary saves.
        Unsupported types, unavailable choices and unknown create results still block.
        """
        worker = self.worker(target, transfer)
        def vendor(path, payload):
            return self.b.miao_request(lambda: worker._vendor(path, payload), target)
        if rules is None:
            data = vendor(self.b.HALF_CATEGORY_ATTRIBUTE_RULES, {'cid': item['cid']}).get('data') or {}
            rules = data.get('productAttributeRules') or []
        # The source detail is first-party data.  Rebind matching attribute
        # names and choice names to the target account's category IDs before
        # asking AI to infer anything.  Cross-account choice IDs must never be
        # submitted as-is because they are account/category scoped.
        apply_defaults = getattr(self.b, 'apply_attribute_defaults', None)
        if apply_defaults is not None:
            apply_defaults(item, raw, rules)
        else:
            # Lightweight fixture backends and older integrations keep their
            # source hydration hooks; the application uses the shared policy.
            self.b.hydrate_attributes_from_source(item, raw, rules)
            self.b.apply_source_origin_default(item, rules, required_by_vendor=True)
        if not self.b.missing_attributes(item, rules):
            return
        target_account = self.check_account(target, transfer['targetScope'])
        tree = self.b.shared_category_tree(vendor, target_account['scope']).get('cateTree') or {}
        category = ' / '.join(self.b.category_path(tree, item['cid']))
        if not category:
            raise ValueError(f'未找到目标类目 {item["cid"]}，无法自动补齐必填属性')
        notes = transfer.setdefault('preparationNotes', [])
        self.b.complete_automation_attributes(item, raw, rules, category,
                                             self.b.DeepSeekClient(target).infer_attribute, notes.append)

    @staticmethod
    def _is_definite_attribute_validation(message):
        return '产品属性【' in str(message or '') and '必填' in str(message or '')

    @staticmethod
    def _is_definite_create_rejection(message):
        """Known validation messages prove that the previous request did not create a copy."""
        text = str(message or '')
        if any(marker in text.casefold() for marker in ('timeout', 'timed out', '超时', 'connection', '连接',
                '结果不确定', '结果不明', '无法确认', 'unable to reach', 'gateway', 'http 5')):
            return False
        return (('产品属性【' in text and '必填' in text) or '承诺发货时效错误' in text
                or '英文标题不能超过250个字符' in text
                or ('groupIdAndValueMap' in text and ('必填' in text or 'required' in text.lower()))
                or ('店铺【' in text and '请选择店铺运费模板' in text))

    @staticmethod
    def _is_repairable_size_chart_rejection(message):
        text = str(message or '')
        return 'groupIdAndValueMap' in text and ('必填' in text or 'required' in text.lower())

    def _sync_copy_components(self, raw, item, transfer, required_by_vendor=False):
        """Rebind explicit source component facts, never infer them from a title/SKU."""
        result = self.b.miao_request(lambda: self.worker(transfer['targetAccountId'], transfer)._vendor(
            self.b.HALF_CATEGORY_ATTRIBUTE_RULES, {'cid': item['cid']}), transfer['targetAccountId'])
        rules = (result.get('data') or {}).get('productAttributeRules')
        if not isinstance(rules, list):
            raise ValueError('目标类目属性规则读取失败，不能核对包含的组件；请稍后重试')
        name = '包含的组件'
        matches = [r for r in rules if isinstance(r, dict) and r.get('name') == name]
        if not matches and not required_by_vendor:
            return rules
        if len(matches) != 1:
            raise ValueError('产品属性【包含的组件】必填，但目标类目规则缺失或不唯一，请人工核对')
        rule = matches[0]
        if required_by_vendor:
            rule['required'] = True
        sources = [a for a in raw.get('attributes') or [] if isinstance(a, dict) and a.get('name') == name]
        if not sources:
            # The field itself may be absent while title/description contains
            # reliable facts. Let the bounded fact-only completion pass handle it.
            return rules
        if len(sources) != 1 or not sources[0].get('values'):
            raise ValueError('产品属性【包含的组件】必填；源商品未提供唯一有效的组件资料，请在源商品补充后重试')
        active = constrained_rule(item, rule)
        if active is None:
            raise ValueError('包含的组件依赖的目标类目父属性尚未核对，请人工处理')
        values = deepcopy(sources[0]['values'])
        if not isinstance(values, list) or any(not isinstance(v, dict) or not str(v.get('name') or '').strip() for v in values):
            raise ValueError('包含的组件缺少源属性名称，不能仅按跨账号编号匹配，请人工补充')
        for value in values:
            # IDs can collide across accounts. Only explicit names are evidence.
            value.pop('vid', None)
            if active.get('valueType') not in {'STRING', 'NUMBER'}:
                names = [v for v in active.get('values') or []
                         if str(v.get('name') or '').strip().rstrip('，,;；').casefold()
                         == str(value['name']).strip().rstrip('，,;；').casefold()]
                if len(names) != 1:
                    raise ValueError('源商品包含的组件与目标允许选项无法唯一匹配，请人工核对')
        try:
            values = normalize_values(values, active)
        except (ValueError, TypeError) as error:
            raise ValueError(f'源商品包含的组件不符合目标类目要求，请人工核对：{error}') from error
        mapped = {key: rule[key] for key in ('templatePid', 'pid', 'refPid', 'name') if key in rule}
        mapped['values'] = values
        # Do not let a coincidentally equal source template ID overwrite another attribute.
        attributes = item.get('attributes') or []
        if any(a.get('name') != name and str(a.get('templatePid')) == str(rule.get('templatePid'))
               for a in attributes if isinstance(a, dict) and rule.get('templatePid') is not None):
            raise ValueError('包含的组件目标属性编号与其他源属性冲突，请人工核对')
        item['attributes'] = [a for a in attributes if not isinstance(a, dict) or a.get('name') != name] + [mapped]
        return rules

    def _retry_copy_as_source_payload(self, transfer, copy):
        """Retry only an explicit remote validation rejection with repaired source facts."""
        if not self._is_definite_create_rejection(copy['message']):
            raise ValueError('创建结果无法确认，请填写目标商品 ID 核对关联；系统不会自动再次创建')
        source, target = transfer['sourceAccountId'], transfer['targetAccountId']
        raw = self.worker(source, transfer)._get_item(transfer['detailId'], 'site')
        item = create_copy_payload(raw, transfer['shopIds'], transfer['site'])
        issues = template_preflight(item, transfer['site'])
        if issues:
            raise ValueError('导入模板预检未通过：' + '；'.join(issues))
        self._prepare_copy_attributes(raw, item, transfer, '产品属性【包含的组件】' in copy['message'])
        self._prepare_copy_logistics(item, transfer)
        def restart(connection):
            claimed = connection.execute("""UPDATE shared_copies SET expected=?,status='creating',message=?
                WHERE copy_key=? AND status='needs_review' AND message=? AND expected IS ?""",
                (encoded(item), '已补齐明确的源资料校验项，正在重新创建',
                 copy['copy_key'], copy['message'], copy['expected'])).rowcount
            if claimed != 1:
                raise ValueError('副本状态已变化或已有任务正在处理，未重复创建；请刷新核查')
        self._local_write(restart)
        try:
            result = self.b.miao_request(lambda: self.worker(target, transfer)._vendor(
                self.b.HALF_CREATE, {'siteCollectItemInfo': item}), target, retry_safe=False)
            detail_id = self.b.created_collect_box_detail_id(result)
        except Exception as error:
            self._set_copy_review(copy['copy_key'], error)
            raise ValueError(f'同步源资料后创建仍未完成：{error}') from error
        try:
            self._set_copy_ready(copy['copy_key'], detail_id, '已同步源资料创建副本（含尺码表字段校验）')
        except Exception as error:
            if self._database_busy(error):
                self._set_copy_review(copy['copy_key'], f'已确认创建目标商品 ID {detail_id}；本机数据库暂时占用，正在自动恢复')
            raise
        return detail_id

    def _ensure_copy(self, transfer):
        source, target = transfer['sourceAccountId'], transfer['targetAccountId']
        self.check_account(source, transfer['sourceScope'])
        self.check_account(target, transfer['targetScope'])
        if source == target:
            return transfer['detailId']
        key = hashlib.sha256(encoded([transfer['sourceScope'], transfer['detailId'], transfer['targetScope']]).encode()).hexdigest()
        with self.db() as connection:
            row = connection.execute('SELECT * FROM shared_copies WHERE copy_key=?', (key,)).fetchone()
        if row and row['status'] == 'ready':
            # Read the existing copy; never overwrite it from the source snapshot.
            self.worker(target, transfer)._get_item(row['target_id'], 'site')
            return row['target_id']
        if row and row['status'] == 'needs_review':
            known = re.search(r'已确认创建目标商品 ID ([1-9][0-9]*)', row['message'] or '')
            if known:
                detail_id = int(known.group(1))
                self._set_copy_ready(key, detail_id, '已确认目标商品，已自动恢复')
                return detail_id
        if row and row['status'] == 'needs_review' and self._is_definite_create_rejection(row['message']):
            return self._retry_copy_as_source_payload(transfer, row)
        if row and row['status'] in {'creating', 'needs_review'}:
            raise ValueError('副本创建结果待核查；请填写目标商品 ID 核对关联，不会自动再次创建')
        raw = self.worker(source, transfer)._get_source_item(transfer['detailId'], 'site')
        item = create_copy_payload(raw, transfer['shopIds'], transfer['site'])
        issues = template_preflight(item, transfer['site'])
        if issues:
            raise ValueError('导入模板预检未通过：' + '；'.join(issues))
        self._prepare_copy_attributes(raw, item, transfer)
        self._prepare_copy_logistics(item, transfer)
        with self.db() as connection:
            reserved = connection.execute('''INSERT OR IGNORE INTO shared_copies VALUES (?,?,?,?,?,?,NULL,'creating',?,'')''',
                (key, source, transfer['sourceScope'], transfer['detailId'], target, transfer['targetScope'], encoded(item)))
            claimed = reserved.rowcount == 1
        if not claimed:
            # Another connection reserved this copy while the source was read.
            # Never replace its state or send a second create request.
            return self._ensure_copy(transfer)
        try:
            result = self.b.miao_request(lambda: self.worker(target, transfer)._vendor(
                self.b.HALF_CREATE, {'siteCollectItemInfo': item}), target, retry_safe=False)
            detail_id = self.b.created_collect_box_detail_id(result)
            # The remote request has completed.  Retrying this local update is
            # safe and prevents a momentary SQLite lock from turning a known
            # target ID into an unnecessary manual reconciliation.
            self._set_copy_ready(key, detail_id)
        except Exception as error:
            if self._database_busy(error) and 'detail_id' in locals():
                self._set_copy_review(key, f'已确认创建目标商品 ID {detail_id}；本机数据库暂时占用，正在自动恢复')
            else:
                self._set_copy_review(key, error)
            raise ValueError(f'副本创建结果待核查：{error}；禁止自动重复创建') from error
        return detail_id

    def prepare_transfer(self, row):
        transfer = json.loads(row['payload'])
        self._set_transfer(row['transfer_id'], 'running', '正在核对源商品与目标副本')
        target_id = self._ensure_copy(transfer)
        if transfer.get('preparationNotes'):
            with self.db() as connection:
                connection.execute('UPDATE shared_transfers SET payload=? WHERE transfer_id=?',
                                   (encoded(transfer), row['transfer_id']))
        target = transfer['targetAccountId']
        pool = self.b.automation_pool(target)
        if not pool.get_item(target_id):
            snapshot = self.worker(target, transfer)._get_item(target_id, 'site')
            pool.put_item(target_id, 'site', 0, snapshot, only_if_new=True)
        existing = pool.read_rows('SELECT shop_id FROM automation_jobs WHERE detail_id=? AND UPPER(site)=? AND status!=?',
                                  (target_id, transfer['site'].upper(), 'canceled'))
        existing_shops = {row['shop_id'] for row in existing}
        shops = [shop for shop in transfer['shopIds'] if shop not in existing_shops]
        if shops:
            staged = pool.get_item(target_id)
            prices = {}
            if transfer['priceMode'] == 'source_multiplier':
                source = self.b.automation_pool(transfer['sourceAccountId']).get_item(transfer['detailId'])
                prices = self.b._source_sku_price_multiplier_declarations(source['item'],
                    self.b._normalize_automation_price_multiplier(transfer['priceMultiplier']))
                if set(prices) != set(staged['item'].get('skuMap', {})):
                    raise ValueError('目标副本 SKU 与源商品不一致，不能套用原价倍数，请人工核查')
                price = next(iter(prices.values()))
            else:
                price = self.b._randomize_automation_price(self.b._normalize_automation_price(transfer['price']))
            # Do not let the saving consumer claim jobs before their source
            # identity has been committed in the shared database.
            with self.job_creation_lock:
                created = pool.queue_jobs([target_id], shops, transfer['site'], price=[price], sku_prices=[prices], dedupe_pairs=True)
                try:
                    with self.db() as connection:
                        for job in created:
                            connection.execute('INSERT OR REPLACE INTO shared_job_origins VALUES (?,?,?,?,?,?)',
                                (target, job['jobId'], transfer['sourceAccountId'], transfer['sourceScope'],
                                 transfer['detailId'], transfer['targetScope']))
                            connection.execute('INSERT INTO shared_transfer_jobs VALUES (?,?,?)',
                                               (row['transfer_id'], target, job['jobId']))
                except Exception:
                    for job in created:
                        pool.update_job(job['jobId'], 'preflight', 'needs_review', '来源关联写入失败，未开始保存，请核查')
                    raise
        self._set_transfer(row['transfer_id'], 'done', '目标保存任务已入队', target_id)

    def dispatch(self):
        with self.dispatch_lock:
            if self.is_paused():
                return
            with self.db() as connection:
                limit = connection.execute("""SELECT MIN(b.concurrency) FROM shared_batches b
                    JOIN shared_transfers t ON t.batch_id=b.batch_id WHERE t.status='queued'""").fetchone()[0]
            concurrency = limit or 8
            finished = Event()
            available = Event()
            scheduler_ready = Event()
            # Copy preparation is account-qualified: each target account may
            # prepare four independent products, while the account-local MiaoShou
            # lane still controls request spacing and the remote-call limit.
            # The existing globally locked saving scheduler consumes confirmed
            # jobs as soon as each copy becomes ready.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='shared-save-pipeline') as executor:
                saving = executor.submit(self.run_jobs, concurrency, preparation_done=finished,
                                         work_available=available, concurrency_limit=lambda: concurrency,
                                         scheduler_ready=scheduler_ready)
                producers = ThreadPoolExecutor(max_workers=COPY_PREPARATION_MAX_WORKERS,
                                               thread_name_prefix='copy-account')
                preparing = {}
                try:
                    # Copy preparation must not wait for a busy editing
                    # coordinator. All new-version edit entry points now use
                    # run_jobs and job_creation_lock, so queued jobs cannot be
                    # consumed before their provenance is committed.
                    while True:
                        if self.is_paused():
                            break
                        if saving.done():
                            saving.result()
                        occupied = {}
                        for running in preparing.values():
                            account = json.loads(running['payload'])['targetAccountId']
                            occupied[account] = occupied.get(account, 0) + 1
                        with self.db() as connection:
                            candidates = list(connection.execute("SELECT * FROM shared_transfers WHERE status='queued' ORDER BY updated"))
                        for row in candidates:
                            account = json.loads(row['payload'])['targetAccountId']
                            if len(preparing) >= COPY_PREPARATION_MAX_WORKERS:
                                break
                            if occupied.get(account, 0) >= copy_preparation_limit(account):
                                continue
                            with self.db() as connection:
                                claimed = connection.execute("""UPDATE shared_transfers
                                    SET status='running',message='正在准备副本和保存任务',updated=?
                                    WHERE transfer_id=? AND status='queued'""", (time.time(), row['transfer_id'])).rowcount
                                batch = connection.execute('SELECT concurrency FROM shared_batches WHERE batch_id=?',
                                                           (row['batch_id'],)).fetchone()
                                if claimed:
                                    row = connection.execute('SELECT * FROM shared_transfers WHERE transfer_id=?',
                                                             (row['transfer_id'],)).fetchone()
                            if claimed:
                                concurrency = min(concurrency, batch['concurrency'])
                                occupied[account] = occupied.get(account, 0) + 1
                                preparing[producers.submit(self.prepare_transfer, row)] = row
                        if not preparing:
                            break
                        completed, _ = wait(preparing, timeout=0.25, return_when=FIRST_COMPLETED)
                        for future in completed:
                            row = preparing.pop(future)
                            try:
                                future.result()
                            except Exception as error:
                                if self._database_busy(error):
                                    self._set_transfer(row['transfer_id'], 'queued', '本机数据库暂时占用，正在自动重试')
                                    time.sleep(0.3)
                                else:
                                    self._set_transfer(row['transfer_id'], 'needs_review', str(error))
                            available.set()
                            with self.db() as connection:
                                connection.execute("UPDATE shared_batches SET status='submitted' WHERE batch_id=?", (row['batch_id'],))
                finally:
                    producers.shutdown(wait=True)
                    # An in-flight producer is allowed to finish on pause;
                    # capture errors before letting the consumer exit.
                    for future, row in preparing.items():
                        try:
                            future.result()
                        except Exception as error:
                            self._set_transfer(row['transfer_id'], 'needs_review', str(error))
                    finished.set()
                    available.set()
                saving.result()

    def assert_job(self, account, job):
        self.check_account(account)
        with self.db() as connection:
            origin = connection.execute('SELECT * FROM shared_job_origins WHERE account_id=? AND job_id=?', (account, job)).fetchone()
        if origin:
            self.check_account(origin['source_account'], origin['source_scope'])
            self.check_account(account, origin['target_scope'])
        else:
            item = self.b.automation_pool(account).get_job_state(job)
            if item:
                self.guard_target(account, item['detailId'])

    def guard_target(self, account, detail_id):
        self.check_account(account)
        with reader(self.path) as connection:
            copies = connection.execute('''SELECT source_account, source_scope, target_scope
                FROM shared_copies WHERE target_account=? AND target_id=?''', (account, detail_id)).fetchall()
        for copy in copies:
            self.check_account(copy['source_account'], copy['source_scope'])
            self.check_account(account, copy['target_scope'])

    def active_publish_ids(self, account):
        return {row['job_id'] for row in self.b.automation_pool(account).read_rows('''
            SELECT i.job_id FROM automation_publish_batches b
            JOIN automation_publish_batch_items i ON i.batch_id=b.batch_id
            WHERE b.status='running' AND i.status IN ('pending','publishing')''')}

    def run_jobs(self, concurrency=8, *, preparation_done=None, work_available=None,
                 concurrency_limit=None, scheduler_ready=None):
        from edit_scheduler import (admission_order, GLOBAL_EDIT_LIMIT, ACCOUNT_EDIT_LIMIT,
                                    linked_account_edit_limit)
        # One shared lock also gates legacy schedulers and individual retries.
        with self.b.AUTOMATION_EDIT_SCHEDULER_LOCK:
            if scheduler_ready:
                scheduler_ready.set()
            with ThreadPoolExecutor(max_workers=min(concurrency, 24), thread_name_prefix='shared-edit') as executor:
                pending = {}
                cursor = 0
                while True:
                    RUNTIME.work_available.clear()
                    if work_available:
                        work_available.clear()
                    producer_finished = preparation_done is None or preparation_done.is_set()
                    limit = min(concurrency, concurrency_limit()) if concurrency_limit else concurrency
                    limit = max(1, min(limit, GLOBAL_EDIT_LIMIT))
                    paused = self.is_paused()
                    if not paused and len(pending) < limit:
                        accounts = [a for a in self.accounts() if a['configured'] and not a['blocked']]
                        pools = {a['id']: self.b.automation_pool(a['id']) for a in accounts}
                        accounts = [a for a in accounts if pools[a['id']].has_queued_jobs()]
                        # Admit one job per pass: idle accounts reserve nothing;
                        # a newly active account gets the next released capacity.
                        while accounts and len(pending) < limit and not self.is_paused():
                            owned = {a['id']: sum(p is pools[a['id']] for p, _ in pending.values()) for a in accounts}
                            admitted = False
                            for account in admission_order(accounts, owned, cursor):
                                if len(pending) >= limit:
                                    break
                                pool = pools[account['id']]
                                remote_limit = RUNTIME.lane(account['id']).requests.limit
                                account_limit = linked_account_edit_limit(remote_limit, ACCOUNT_EDIT_LIMIT)
                                if owned[account['id']] >= account_limit:
                                    continue
                                with self.job_creation_lock:
                                    jobs = pool.start_next_queued_jobs(min(owned[account['id']] + 1, account_limit), light=True)
                                for job in jobs:
                                    admitted = True
                                    try:
                                        self.assert_job(account['id'], job['jobId'])
                                        worker = self.worker(account['id'])
                                        future = executor.submit(self.b.run_automation_job, pool, job['jobId'], worker._vendor,
                                            worker._get_item, worker._readiness, self.b.DeepSeekClient(account['id']), defer_publish=True,
                                            source_get_item=worker._get_source_item)
                                        pending[future] = (pool, job['jobId'])
                                        future.add_done_callback(lambda _: RUNTIME.work_available.set())
                                    except Exception as error:
                                        pool.update_job(job['jobId'], 'preflight', 'needs_review', str(error))
                            cursor = (cursor + 1) % max(1, len(accounts))
                            if not admitted:
                                break
                    if not pending:
                        if paused or producer_finished:
                            break
                        work_available.wait(0.25)
                        continue
                    if not any(f.done() for f in pending):
                        RUNTIME.work_available.wait(1.0)
                    finished = {f for f in pending if f.done()}
                    for future in finished:
                        pool, job_id = pending.pop(future)
                        try:
                            future.result()
                        except Exception as error:
                            pool.update_job(job_id, 'prepare', 'failed', str(error))

    def recover(self):
        with self.db() as connection:
            connection.execute("UPDATE shared_copies SET status='needs_review',message='后台重启，创建结果需人工核查' WHERE status='creating'")
            # Since dispatch claims a transfer before it can call the remote
            # create API, an untouched queued transfer is safe to continue.
            # Only a running transfer might have an unconfirmed remote write.
            connection.execute("UPDATE shared_transfers SET status='needs_review',message='后台重启，请核查副本后重试' WHERE status='running'")
            # If MiaoShou already returned an ID but the following local write
            # was momentarily locked, retain that ID in the review message and
            # finish the local association automatically after restart.
            known_copies = list(connection.execute("SELECT copy_key,source_scope,source_id,target_scope,message FROM shared_copies WHERE status='needs_review'"))
            for copy in known_copies:
                match = re.search(r'已确认创建目标商品 ID ([1-9][0-9]*)', copy['message'] or '')
                if not match:
                    continue
                target_id = int(match.group(1))
                connection.execute("UPDATE shared_copies SET target_id=?,status='ready',message='已确认目标商品，已自动恢复' WHERE copy_key=?",
                                   (target_id, copy['copy_key']))
                connection.execute("""UPDATE shared_transfers SET status='queued',message='已确认目标副本，正在自动继续处理',updated=?
                    WHERE status='needs_review' AND json_extract(payload,'$.sourceScope')=?
                      AND json_extract(payload,'$.detailId')=? AND json_extract(payload,'$.targetScope')=?""",
                                   (time.time(), copy['source_scope'], copy['source_id'], copy['target_scope']))
            # A copy reservation is written before its remote create request.
            # Therefore a restart record without a reservation cannot have
            # created a target copy, including legacy records marked for
            # review by older versions.  Put only those records back on the
            # queue; a creating/needs_review copy remains manual so that an
            # unknown remote result is never submitted twice.
            copies = {(row['source_scope'], row['source_id'], row['target_scope'])
                      for row in connection.execute('SELECT source_scope,source_id,target_scope FROM shared_copies')}
            stranded = list(connection.execute("""SELECT transfer_id,payload FROM shared_transfers
                                                 WHERE status='needs_review'
                                                   AND message='后台重启，请核查副本后重试'"""))
            for row in stranded:
                try:
                    transfer = json.loads(row['payload'])
                    key = (transfer['sourceScope'], transfer['detailId'], transfer['targetScope'])
                except (KeyError, TypeError, ValueError):
                    continue
                if key not in copies:
                    connection.execute("""UPDATE shared_transfers
                                          SET status='queued',message='后台重启后确认未创建副本，继续保存到待发布队列',updated=?
                                          WHERE transfer_id=? AND status='needs_review'""",
                                       (time.time(), row['transfer_id']))
            # A historic "database is locked" transfer without a copy
            # reservation provably never reached the target create call.  It
            # is therefore safe to resume automatically; no operator should
            # be asked to fill in a target product ID for this local error.
            locked = list(connection.execute("""SELECT t.transfer_id FROM shared_transfers t
                WHERE t.status='needs_review' AND lower(t.message) IN ('database is locked','database is busy')
                  AND NOT EXISTS (SELECT 1 FROM shared_copies c
                    WHERE c.source_scope=json_extract(t.payload,'$.sourceScope')
                      AND c.source_id=json_extract(t.payload,'$.detailId')
                      AND c.target_scope=json_extract(t.payload,'$.targetScope'))"""))
            for row in locked:
                connection.execute("""UPDATE shared_transfers
                    SET status='queued',message='本机数据库占用已恢复，正在自动继续处理',updated=?
                    WHERE transfer_id=? AND status='needs_review'""", (time.time(), row['transfer_id']))
            # MiaoShou explicitly rejects a missing size-chart group map before
            # a target product exists.  These old review records are therefore
            # safe to resume as a batch; create_copy_payload will add the empty
            # map before the next request.  Do not broaden this to other review
            # messages, whose remote result can still be unknown.
            size_chart_rejections = list(connection.execute("""SELECT t.transfer_id,c.message
                FROM shared_transfers t JOIN shared_copies c
                  ON c.source_scope=json_extract(t.payload,'$.sourceScope')
                 AND c.source_id=json_extract(t.payload,'$.detailId')
                 AND c.target_scope=json_extract(t.payload,'$.targetScope')
                WHERE t.status='needs_review' AND c.status='needs_review'"""))
            for row in size_chart_rejections:
                if not self._is_repairable_size_chart_rejection(row['message']):
                    continue
                connection.execute("""UPDATE shared_transfers
                    SET status='queued',message='已自动补齐尺码表分组字段，正在重新创建副本',updated=?
                    WHERE transfer_id=? AND status='needs_review'""", (time.time(), row['transfer_id']))

    def reconcile(self, body):
        # A clear request must not race an explicit retry/association.
        with self.transfer_action_lock:
            return self._reconcile(body)

    def resume_transfer(self, body, launch=True, *, safe_batch=False):
        """One confirmation resumes preparation through save, never publication."""
        if self.b.os.getenv('APP_MODE', 'demo') != 'live' or body.get('confirmation') != 'RESUME_SHARED_TO_QUEUE':
            raise ValueError('需要可写模式并确认提交到待发布队列')
        transfer_id = body.get('transferId')
        with self.transfer_action_lock:
            with self.db() as connection:
                row = connection.execute('SELECT * FROM shared_transfers WHERE transfer_id=?', (transfer_id,)).fetchone()
            if not row or row['status'] not in {'needs_review', 'queued', 'running', 'done'}:
                raise ValueError('该任务不能继续处理')
            transfer = json.loads(row['payload'])
            self.check_account(transfer['sourceAccountId'], transfer['sourceScope'])
            self.check_account(transfer['targetAccountId'], transfer['targetScope'])
            if row['status'] != 'needs_review':
                return {'queued': row['status'] in {'queued', 'running'}, 'duplicate': True, 'status': row['status']}
            copy = next((c for c in self._copies() if c['source_scope'] == transfer['sourceScope']
                         and c['source_id'] == transfer['detailId'] and c['target_scope'] == transfer['targetScope']), None)
            if safe_batch and not self.transfer_failure_metadata(dict(row), copy)['safeBatchEligible']:
                raise ValueError('副本状态已变化或缺少安全批量处理依据，请刷新列表')
            if copy and (copy['status'] == 'creating' or (copy['status'] == 'needs_review'
                    and not self._is_definite_create_rejection(copy['message']))):
                raise ValueError('已有副本创建结果不明，必须核查后继续，不会重复创建')
            self._set_transfer(transfer_id, 'queued', '正在自动补齐资料、复制并保存到待发布队列')
        if launch and not self.is_paused():
            Thread(target=self.dispatch, daemon=True).start()
        return {'queued': True, 'paused': self.is_paused(), 'transferId': transfer_id}

    def _reconcile(self, body):
        if self.b.os.getenv('APP_MODE', 'demo') != 'live' or body.get('confirmation') != 'RECONCILE_SHARED_COPY':
            raise ValueError('需要可写模式及核查确认，才能关联副本并继续保存')
        transfer_id = body.get('transferId', '')
        with self.db() as connection:
            row = connection.execute('SELECT * FROM shared_transfers WHERE transfer_id=?', (transfer_id,)).fetchone()
        if not row or row['status'] != 'needs_review':
            raise ValueError('仅允许核查待处理的副本任务')
        transfer = json.loads(row['payload'])
        self.check_account(transfer['sourceAccountId'], transfer['sourceScope'])
        self.check_account(transfer['targetAccountId'], transfer['targetScope'])
        copies = [c for c in self._copies() if c['source_scope'] == transfer['sourceScope']
                  and c['source_id'] == transfer['detailId'] and c['target_scope'] == transfer['targetScope']]
        copy = copies[0] if copies else None
        if copy and copy['status'] == 'needs_review':
            raw_detail_id = body.get('targetDetailId')
            if raw_detail_id in (None, ''):
                self._retry_copy_as_source_payload(transfer, copy)
            else:
                detail_id = positive(raw_detail_id)
                item = self.worker(transfer['targetAccountId'], transfer)._get_item(detail_id, 'site')
                if copy_signature(item) != copy_signature(json.loads(copy['expected'])):
                    raise ValueError('目标商品的标题、类目、图片或规格与待创建资料不一致，不能关联')
                with self.db() as connection:
                    connection.execute("UPDATE shared_copies SET target_id=?,status='ready',message='人工核查关联' WHERE copy_key=?",
                                       (detail_id, copy['copy_key']))
        self._set_transfer(transfer_id, 'queued', '已确认继续处理')
        if not self.is_paused():
            Thread(target=self.dispatch, daemon=True).start()
        return {'queued': True, 'paused': self.is_paused()}

    def discard_transfer(self, body):
        """Hide a local review task without erasing copy outcome/dedupe evidence."""
        if body.get('confirmation') != 'DISCARD_SHARED_TRANSFER':
            raise ValueError('缺少清除本地副本任务确认')
        transfer_id = body.get('transferId', '')
        if not isinstance(transfer_id, str) or not transfer_id:
            raise ValueError('请选择本地副本任务')
        with self.transfer_action_lock, self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM shared_transfers WHERE transfer_id=?', (transfer_id,)).fetchone()
            if not row or row['status'] not in {'needs_review', 'discarded'}:
                raise ValueError('仅能清除需核查的本地副本任务，运行中任务不能清除')
            # Retain payload, message and target ID for auditing. In particular,
            # never remove shared_copies after a timeout or unknown create result.
            connection.execute("UPDATE shared_transfers SET status='discarded',updated=? "
                               "WHERE transfer_id=? AND status='needs_review'", (time.time(), transfer_id))
        return {'discarded': True, 'transferId': transfer_id}

    def preserve_item_owners(self, account_id, detail_ids):
        """Keep source evidence before explicit removal; GET readers never write it."""
        account_id = positive(account_id)
        ids = sorted({positive(detail_id) for detail_id in detail_ids})
        if not ids:
            return 0
        # Register a new configured identity, but always use the retained scope
        # for existing local pools, including after credentials are replaced.
        self.accounts()
        with self.db() as connection:
            retained = connection.execute('SELECT scope FROM shared_accounts WHERE account_id=?', (account_id,)).fetchone()
        pool = self.b.automation_pool(account_id)
        evidence = []
        for physical in getattr(pool, 'pools', (pool,)):
            if not physical.path.exists():
                continue
            with reader(physical.path) as connection:
                for start in range(0, len(ids), 500):
                    selected = ids[start:start + 500]
                    rows = connection.execute("SELECT detail_id,json_extract(snapshot_json,'$.subAppAccountId') AS person "
                        'FROM automation_items WHERE detail_id IN (' + ','.join('?' for _ in selected) + ')', selected)
                    evidence.extend((row['detail_id'], row['person']) for row in rows)
        evidence = [(detail, person) for detail, person in evidence if self.b.collector_id(person)]
        if not evidence:
            return 0
        if not retained:
            raise ValueError('无法确认本地商品的原采集箱凭据，不能丢失人员归属后移出')
        return self.b.COLLECTION_PEOPLE.store.preserve_owners(retained['scope'], evidence)

    def remove_items(self, refs):
        if not isinstance(refs, list) or not 1 <= len(refs) <= 500:
            raise ValueError('请选择 1 到 500 件商品')
        jobs = self.job_rows()
        with self.db() as connection:
            transfers = [json.loads(row['payload']) for row in connection.execute("SELECT payload FROM shared_transfers WHERE status NOT IN ('done','discarded')")]
        result, errors = [], []
        for raw in refs:
            ref = source_ref(raw)
            try:
                if any(j['sourceAccountId'] == ref['sourceAccountId'] and j['sourceDetailId'] == ref['detailId']
                       and j['status'] in {'queued', 'running', 'needs_input', 'ready_publish', 'needs_review', 'interrupted'} for j in jobs):
                    raise ValueError('存在未完成的目标任务，不能移出源商品')
                if any(t['sourceAccountId'] == ref['sourceAccountId'] and t['detailId'] == ref['detailId'] for t in transfers):
                    raise ValueError('存在跨账号复制任务，不能移出源商品')
                self.preserve_item_owners(ref['sourceAccountId'], [ref['detailId']])
                self.b.automation_pool(ref['sourceAccountId']).remove_items([ref['detailId']])
                result.append(ref)
            except ValueError as error:
                errors.append({**ref, 'message': str(error)})
        return {'removed': result, 'errors': errors}

    def publish_preview(self, body):
        """Freeze the exact first N locally saved jobs; never select again on release."""
        count, interval = body.get('publishCount'), body.get('intervalSeconds', 1)
        if type(count) is not int or count < 1:
            raise ValueError('本次发布数量应为大于 0 的整数')
        if type(interval) is not int or not 1 <= interval <= 600:
            raise ValueError('发布间隔应为 1 到 600 的整数秒')
        filters = body.get('filters', {})
        shop_counts = body.get('shopCounts')
        if not isinstance(filters, dict) or set(filters) - {'accountId', 'shopId', 'sourceAccountId', 'keyword', 'status', 'shops', 'personKey', 'poolId'}:
            raise ValueError('发布筛选条件不正确')
        query = {}
        selected_shops = None
        requested_by_shop = None
        if shop_counts is not None:
            if not isinstance(shop_counts, list) or not 1 <= len(shop_counts) <= 100:
                raise ValueError('请选择 1 到 100 家发布店铺并填写数量')
            requested_by_shop = {}
            for shop in shop_counts:
                if not isinstance(shop, dict) or set(shop) != {'accountId', 'shopId', 'publishCount'}:
                    raise ValueError('店铺发布数量必须同时包含账号、店铺和数量')
                key = (positive(shop['accountId']), positive(shop['shopId']))
                shop_count = shop['publishCount']
                if type(shop_count) is not int or shop_count < 1:
                    raise ValueError('每个店铺的发布数量应为大于 0 的整数')
                if key in requested_by_shop:
                    raise ValueError('店铺发布数量不能重复填写')
                requested_by_shop[key] = shop_count
            if sum(requested_by_shop.values()) != count:
                raise ValueError('本次发布总数必须等于各店铺数量之和')
        for key, value in filters.items():
            if key == 'shops':
                if not isinstance(value, list) or not 1 <= len(value) <= 100:
                    raise ValueError('请选择 1 到 100 家发布店铺')
                selected_shops = set()
                for shop in value:
                    if not isinstance(shop, dict) or set(shop) != {'accountId', 'shopId'}:
                        raise ValueError('发布店铺必须同时包含账号和店铺编号')
                    selected_shops.add((positive(shop['accountId']), positive(shop['shopId'])))
                continue
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError('发布筛选条件不正确')
            if key.endswith('Id') and value and (not value.isascii() or not value.isdigit() or int(value) <= 0):
                raise ValueError('发布账号或店铺筛选不正确')
            query[key] = [value]
        if filters.get('shopId') and not filters.get('accountId'):
            raise ValueError('店铺筛选必须指定目标账号')
        if selected_shops is not None and (filters.get('accountId') or filters.get('shopId')):
            raise ValueError('多店铺发布请使用所选店铺列表，不要同时指定单店铺筛选')
        if requested_by_shop is not None and (selected_shops is not None or filters.get('accountId') or filters.get('shopId')):
            raise ValueError('按店铺填写数量时不要同时指定店铺筛选')
        if filters.get('status') not in (None, '', 'ready_publish'):
            raise ValueError('只能按待发布状态预览')
        rows = self.filter_rows([r for r in self.job_rows() if r['status'] == 'ready_publish'], query)
        if selected_shops is not None:
            for account, _ in selected_shops:
                self.check_account(account)
            rows = [r for r in rows if (r['accountId'], r['shopId']) in selected_shops]
        if requested_by_shop is not None:
            for account, _ in requested_by_shop:
                self.check_account(account)
            rows = [r for r in rows if (r['accountId'], r['shopId']) in requested_by_shop]
        released = {account: self.active_publish_ids(account) for account in {r['accountId'] for r in rows}}
        rows = [r for r in rows if r['jobId'] not in released[r['accountId']]]
        rows.sort(key=lambda r: (r.get('createdAt', ''), r['key']), reverse=True)
        groups = {key: 0 for key in sorted(selected_shops or [])}
        if requested_by_shop is None:
            chosen = rows[:count]
        else:
            chosen = []
            groups = {}
            for key, requested in requested_by_shop.items():
                candidates = [row for row in rows if (row['accountId'], row['shopId']) == key]
                picked = candidates[:requested]
                chosen.extend(picked)
                groups[key] = {'count': len(picked), 'requestedCount': requested, 'availableCount': len(candidates)}
        if not chosen:
            raise ValueError('当前筛选下没有待发布商品')
        scopes = {}
        for row in chosen:
            account = row['accountId']
            if account not in scopes:
                scopes[account] = self.check_account(account)['scope']
            self.assert_job(account, row['jobId'])
            key = (account, row['shopId'])
            if requested_by_shop is None:
                groups[key] = groups.get(key, 0) + 1
        refs = [{'accountId': r['accountId'], 'jobId': r['jobId'], 'scope': scopes[r['accountId']]} for r in chosen]
        preview_id = uuid.uuid4().hex
        payload = {'jobs': refs, 'intervalSeconds': interval, 'filters': filters}
        with self.db() as connection:
            connection.execute('INSERT INTO shared_publish_previews VALUES (?,?,?,?)',
                               (preview_id, encoded(payload), 'preview', time.time()))
        shops = []
        for (account, shop), group in groups.items():
            if requested_by_shop is None:
                shops.append({'accountId': account, 'shopId': shop, 'count': group})
            else:
                shops.append({'accountId': account, 'shopId': shop, **group})
        return {'previewId': preview_id, 'requestedCount': count, 'count': len(chosen), 'total': len(rows),
                'intervalSeconds': interval, 'filters': filters, 'expiresInSeconds': 600,
                'perShopCounts': requested_by_shop is not None, 'shops': shops,
                'sample': chosen[:50]}

    def publish(self, body, *, max_direct_jobs=50):
        # Serialize confirmations, never the network worker. A second click
        # must observe the first release before it can append any rows.
        with self.publish_action_lock:
            return self._publish(body, max_direct_jobs=max_direct_jobs)

    def _publish(self, body, *, max_direct_jobs=50):
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前服务为只读模式，不能发布')
        if body.get('confirmation') != 'PUBLISH_SHARED_READY_JOBS':
            raise ValueError('缺少发布确认')
        batches = []
        acquired_accounts = []
        started_accounts = set()
        joined_existing = False
        preview_id = body.get('previewId')
        consumed = False
        submission_gates = ExitStack()
        previous_batches = {}
        request_id = body.get('previewId') or body.get('requestId') or uuid.uuid4().hex
        if not isinstance(request_id,str) or len(request_id)>100:
            raise ValueError('发布申请编号不正确')
        with self.db() as connection:
            receipt=connection.execute('SELECT * FROM shared_publish_receipts WHERE request_id=?',(request_id,)).fetchone()
        if receipt:
            if json.loads(receipt['payload']) != body: raise ValueError('发布申请编号已用于其他明细')
            if receipt['status']=='submitted': return json.loads(receipt['result'])
            raise ValueError('该发布申请已记录，请刷新核对回执后重新预览；不会重复提交：'+request_id)
        try:
            if preview_id:
                if not isinstance(preview_id, str):
                    raise ValueError('发布预览编号不正确')
                with self.db() as connection:
                    row = connection.execute('SELECT * FROM shared_publish_previews WHERE preview_id=?', (preview_id,)).fetchone()
                if not row or time.time() - row['created'] > 600 or row['status'] != 'preview':
                    raise ValueError('发布预览已提交、过期或失效，请刷新后重新预览')
                plan = json.loads(row['payload'])
                refs, interval = plan['jobs'], plan['intervalSeconds']
            else:
                refs, interval = body.get('jobs'), body.get('intervalSeconds', 1)
            if not isinstance(refs, list) or not refs or type(interval) is not int or not 1 <= interval <= 600:
                raise ValueError('至少选择 1 个发布任务，发布间隔应为 1 到 600 整数秒')
            if not preview_id and len(refs) > max_direct_jobs:
                raise ValueError(f'直接发布最多选择 {max_direct_jobs} 个任务；按数量或多店铺发布请先生成发布预览')
            groups = {}
            for ref in refs:
                account = positive(ref.get('accountId'))
                if preview_id:
                    self.check_account(account, ref['scope'])
                self.assert_job(account, ref.get('jobId'))
                job = self.b.automation_pool(account).get_job_state(ref.get('jobId'))
                if not job or job['status'] != 'ready_publish':
                    raise ValueError('发布任务已变化，请刷新队列并重新预览')
                groups.setdefault(account, set()).add(job['jobId'])
            # Check every account before touching a physical pool or changing
            # its pacing; duplicate rows must not partially append a release.
            for account, ids in groups.items():
                if ids & self.active_publish_ids(account):
                    raise ValueError('所选商品已在发布队列中，未重复提交；请刷新后仅选择尚未入队的商品')
            for account in sorted(groups):
                submission_gates.enter_context(RUNTIME.lane(account).publish_submission)
                previous_batches[account]=self.b.automation_pool(account).read_rows("SELECT batch_id,interval_seconds FROM automation_publish_batches WHERE status='running'")
            self._local_write(lambda db: db.execute('INSERT INTO shared_publish_receipts VALUES (?,?,?,?,?)',
                (request_id,encoded(body),'{}','preparing',time.time())))
            for account, ids in groups.items():
                pool = self.b.automation_pool(account)
                lane = RUNTIME.lane(account)
                with lane.publish_submission:
                    acquired = lane.publish_lock.acquire(blocking=False)
                    if acquired:
                        acquired_accounts.append(account)
                    else:
                        joined_existing = True
                    batch = (pool.begin_publish_batch(sorted(ids), interval) if acquired
                             else pool.append_to_running_publish_batch(sorted(ids), interval))
                    batches.append((account, batch))
            if preview_id:
                self._local_write(lambda connection: connection.execute(
                    "UPDATE shared_publish_previews SET status='submitted' WHERE preview_id=?", (preview_id,)))
                consumed = True
            # The interval is persisted with each batch; the worker reads it
            # again between submissions so the operator can adjust pacing.
            # When another batch is already active, its worker owns the API
            # lane and will pick up the appended rows in this same batch.
            result={'submitted':sum(len(ids) for ids in groups.values()),'joinedExisting':joined_existing}
            self._local_write(lambda db: db.execute("UPDATE shared_publish_receipts SET result=?,status='submitted',updated=? WHERE request_id=?",
                (encoded(result),time.time(),request_id)))
            for account in acquired_accounts:
                Thread(target=self._publish_worker,
                       args=([(a, b) for a, b in batches if a == account], None), daemon=True,
                       name=f'publish-account-{account}').start()
                started_accounts.add(account)
        except Exception:
            try:
                for account,batch in batches:
                    if account in started_accounts: continue
                    router=self.b.automation_pool(account)
                    for physical,token in router._batch_parts(batch):
                        physical.rollback_publish_additions(token,groups[account],previous_batches.get(account,[]))
                self._local_write(lambda db: db.execute("UPDATE shared_publish_receipts SET result=?,status=?,updated=? WHERE request_id=?",
                    (encoded({'submitted':sum(len(groups[a]) for a in started_accounts),
                              'batches':batches,'startedAccounts':list(started_accounts)}),
                     'partial' if started_accounts else 'failed',time.time(),request_id)))
                if consumed:
                    with self.db() as connection:
                        connection.execute("UPDATE shared_publish_previews SET status='failed' WHERE preview_id=?", (preview_id,))
                for account, batch in batches:
                    if account in acquired_accounts and account not in started_accounts:
                        pass  # rollback_publish_additions already retained interrupted state
            finally:
                for account in acquired_accounts:
                    if account not in started_accounts:
                        RUNTIME.lane(account).publish_lock.release()
            raise
        finally:
            submission_gates.close()
        return result

    def _publish_worker(self, batches, interval):
        # One coordinator owns each account. Each physical pool gets one
        # in-flight submission/cooldown; a slow pool never sleeps the others.
        # The account's request gate and pacing still govern all remote calls.
        for account in dict.fromkeys(a for a, _ in batches):
            pool = self.b.automation_pool(account)
            lane = RUNTIME.lane(account)
            released = False
            cursor = 0
            retry_at = {}
            quarantined = set()
            def publish_one(job, batch):
                try:
                    self.assert_job(account, job['jobId'])
                    self.b.publish_automation_job(pool, job['jobId'], self.worker(account)._vendor)
                except Exception as error:
                    pool.update_job(job['jobId'], 'publish', 'needs_review', str(error))
                # A configured interval belongs to this pool, not to every
                # other pool behind the same account. Dynamic changes still
                # use the durable batch setting and never cancel a request.
                if interval is None:
                    pool.wait_for_running_publish_interval(batch)
                else:
                    time.sleep(interval)
            try:
                pending = {}
                with ThreadPoolExecutor(max_workers=len(getattr(pool, 'pools', (pool,))),
                                        thread_name_prefix=f'publish-pool-{account}') as executor:
                    while True:
                        with lane.publish_submission:
                            rows = []
                            deferred = False
                            for physical in getattr(pool, 'pools', (pool,)):
                                number = getattr(physical,'pool_id',1)
                                if time.monotonic() < retry_at.get(number,0):
                                    deferred = True
                                    continue
                                try:
                                    found=physical.read_rows("SELECT batch_id FROM automation_publish_batches WHERE status='running'")
                                    if number in quarantined:
                                        for row in found: physical.finish_publish_batch(row['batch_id'])
                                        continue
                                    rows.extend({**r,'pool_id':number} for r in found)
                                except sqlite3.Error:
                                    retry_at[number]=time.monotonic()+2
                                    deferred = True
                            tokens = [(row.get('pool_id', 1),
                                f"{row['pool_id']}:{row['batch_id']}" if 'pool_id' in row else row['batch_id'])
                                for row in rows]
                            busy = set(pending.values())
                            start = cursor
                            for offset in range(len(tokens)):
                                index = (start + offset) % len(tokens)
                                partition, batch = tokens[index]
                                if partition in busy:
                                    continue
                                try:
                                    job = pool.start_next_ready_publish_job(batch)
                                except sqlite3.Error:
                                    retry_at[partition]=time.monotonic()+2
                                    deferred = True
                                    continue
                                if job:
                                    future = executor.submit(publish_one, job, batch)
                                    pending[future] = partition
                                    busy.add(partition)
                                    cursor = index + 1
                            if not pending and deferred:
                                time.sleep(.1)
                                continue
                            if not pending:
                                for _, token in tokens:
                                    pool.finish_publish_batch(token)
                                lane.publish_lock.release()
                                released = True
                                return
                        finished, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                        for future in finished:
                            partition=pending.pop(future)
                            try:
                                future.result()
                            except sqlite3.Error:
                                # The request may have reached the vendor. Stop
                                # this pool for review, never replay its write.
                                quarantined.add(partition)
                                retry_at[partition]=time.monotonic()+2
            finally:
                if not released:
                    with lane.publish_submission:
                        try:
                            for row in pool.read_rows("SELECT batch_id FROM automation_publish_batches WHERE status='running'"):
                                token = f"{row['pool_id']}:{row['batch_id']}" if 'pool_id' in row else row['batch_id']
                                pool.finish_publish_batch(token)
                        finally:
                            lane.publish_lock.release()

    def activity(self):
        """Return a complete local scheduler snapshot across every collection box.

        ``shared_transfers`` only contains cross-account copies.  Regular
        edit/save and publish jobs live in one AutomationPool database per
        account, so reporting only transfers made a busy normal queue look
        empty in the task center.
        """
        with reader(self.path, timeout=.15) as connection:
            copy_queued = connection.execute("SELECT COUNT(*) FROM shared_transfers WHERE status='queued'").fetchone()[0]
            copy_running = connection.execute("SELECT COUNT(*) FROM shared_transfers WHERE status='running'").fetchone()[0]
            transfer_revision = connection.execute('SELECT COALESCE(MAX(updated),0) FROM shared_transfers').fetchone()[0]
            paused = self._paused(connection)
        edit_queued = edit_running = pending_publish = publishing = 0
        pool_status = []
        revisions = [('transfers', transfer_revision, copy_queued, copy_running)]
        for account in self.status_accounts():
            pool = self.b.automation_pool(account['id'])
            if not pool.path.exists():
                continue
            for number, physical in enumerate(getattr(pool, 'pools', (pool,)), 1):
                with reader(physical.path, timeout=.15) as connection:
                    row = connection.execute("""SELECT
                    SUM(CASE WHEN j.status='queued' THEN 1 ELSE 0 END) AS edit_queued,
                    SUM(CASE WHEN j.status='running' AND j.step!='publish' THEN 1 ELSE 0 END) AS edit_running,
                    SUM(CASE WHEN j.status='ready_publish' THEN 1 ELSE 0 END) AS pending_publish,
                    SUM(CASE WHEN j.status='running' AND j.step='publish' THEN 1 ELSE 0 END) AS publishing,
                    (SELECT COALESCE(MAX(event_id),0) FROM automation_job_events) AS last_event,
                    (SELECT COALESCE(MAX(rowid),0) FROM automation_job_deletions) AS last_deletion
                    FROM automation_jobs j
                    WHERE j.status IN ('queued','running','ready_publish')
                    AND NOT EXISTS (SELECT 1 FROM automation_job_deletions d WHERE d.job_id=j.job_id)
                """).fetchone()
                edit_queued += int(row['edit_queued'] or 0)
                edit_running += int(row['edit_running'] or 0)
                pending_publish += int(row['pending_publish'] or 0)
                publishing += int(row['publishing'] or 0)
                revisions.append((account['id'], number, row['last_event'], row['last_deletion']))
                pool_status.append({'accountId': account['id'], 'accountName': account['name'],
                    'poolId': number, 'editQueued': int(row['edit_queued'] or 0),
                    'editRunning': int(row['edit_running'] or 0),
                    'pendingPublish': int(row['pending_publish'] or 0), 'publishing': int(row['publishing'] or 0)})
        queued = copy_queued + edit_queued
        running = copy_running + edit_running + publishing
        return {'queueRevision': hashlib.sha256(encoded(revisions).encode()).hexdigest()[:24],
                'activeTransfers': copy_queued + copy_running, 'inFlightTransfers': copy_running,
                'queued': queued, 'running': running,
                'copyQueued': copy_queued, 'copyRunning': copy_running,
                'editQueued': edit_queued, 'editRunning': edit_running,
                'pendingPublish': pending_publish, 'publishing': publishing,
                'pools': pool_status,
                'paused': paused,
                'reloading': self.reload_lock.locked(),
                'autoTasksBusy': any(s.lock.locked() for s in getattr(self.b, 'AUTOMATIC_TASKS', {}).copy().values()),
                'editing': self.b.AUTOMATION_EDIT_SCHEDULER_LOCK.locked(),
                'publishWorker': RUNTIME.busy('publish_lock') or self.b._automation_publish_lock.locked(),
                'syncing': any(s.running for s in getattr(self.b, 'COLLECTION_SYNCS', {}).copy().values())
                           or any(lock.locked() for lock in self.b.ORDER_SYNC_LOCKS.values()),
                'todayStoreStats': self.activity_store_stats()}

    def activity_store_stats(self):
        """Never let an expired daily report delay live queue counters."""
        date = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        cached = self.daily_stats_cache
        fresh = cached and cached[0] == date and time.monotonic() - cached[1] < 15
        with self.daily_stats_lifecycle_lock:
            if not fresh and not self.daily_stats_closed.is_set() and self.daily_stats_refresh_lock.acquire(blocking=False):
                def refresh():
                    try:
                        self.today_store_stats()
                    except Exception:
                        # Keep the previous completed report and its true timestamp.
                        # A later activity request can retry after this gate releases.
                        pass
                    finally:
                        self.daily_stats_refresh_lock.release()
                try:
                    self.daily_stats_thread = Thread(target=refresh, name='daily-stats-refresh', daemon=True)
                    self.daily_stats_thread.start()
                except Exception:
                    self.daily_stats_thread = None
                    self.daily_stats_refresh_lock.release()
        result = deepcopy(cached[2]) if cached and cached[0] == date else {
            'date': date, 'timezone': 'Asia/Shanghai', 'stores': [], 'generatedAt': None}
        result['refreshing'] = not bool(fresh) and not self.daily_stats_closed.is_set()
        return result

    def close(self, timeout=5):
        """Stop report readers before shutting down their database owners."""
        events_closed = self.activity_events.close(timeout)
        with self.daily_stats_lifecycle_lock:
            self.daily_stats_closed.set()
            thread = self.daily_stats_thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0, timeout))
            return events_closed and not thread.is_alive()
        return events_closed

    def today_store_stats(self):
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        date = now.date().isoformat()
        with self.daily_stats_lock:
            cached = self.daily_stats_cache
            if self.daily_stats_closed.is_set():
                return deepcopy(cached[2]) if cached and cached[0] == date else {
                    'date': date, 'timezone': 'Asia/Shanghai', 'stores': [], 'generatedAt': None}
            if cached and cached[0] == date and time.monotonic() - cached[1] < 15:
                return deepcopy(cached[2])
            result = self._read_today_store_stats(now)
            result['generatedAt'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
            self.daily_stats_cache = (date, time.monotonic(), result)
            return deepcopy(result)

    def _read_today_store_stats(self, now):
        """Summarize today's successful saves and releases by target store.

        Automation event timestamps are stored in UTC.  Convert the Beijing
        day boundaries to UTC before querying, so midnight never leaks into
        the previous or next day's report.  We count each job once per action;
        retry logs cannot inflate a store's total.  Stores with no matching
        event are intentionally omitted.
        """
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        start_utc = start.astimezone(timezone.utc).isoformat(timespec='seconds')
        end_utc = end.astimezone(timezone.utc).isoformat(timespec='seconds')
        rows = []
        for account in self.accounts():
            if self.daily_stats_closed.is_set():
                raise RuntimeError('Daily statistics reader is closing')
            pool = self.b.automation_pool(account['id'])
            if not pool.path.exists():
                continue
            result = []
            for physical in getattr(pool, 'pools', (pool,)):
                with reader(physical.path) as connection:
                    connection.set_progress_handler(lambda: int(self.daily_stats_closed.is_set()), 1000)
                    result.extend(dict(row) for row in connection.execute("""SELECT j.shop_id,
                    COUNT(DISTINCT CASE WHEN e.step='save' AND e.status='done' THEN e.job_id END) AS processed,
                    COUNT(DISTINCT CASE WHEN e.step='publish' AND e.status='done' THEN e.job_id END) AS published
                    FROM automation_job_events e
                    JOIN automation_jobs j ON j.job_id=e.job_id
                    WHERE e.created_at>=? AND e.created_at<? AND e.status='done' AND e.step IN ('save','publish')
                    GROUP BY j.shop_id
                    HAVING processed>0 OR published>0
                    ORDER BY j.shop_id""", (start_utc, end_utc)))
            rows.extend({'accountId': account['id'], 'shopId': int(row['shop_id']),
                         'processed': int(row['processed']), 'published': int(row['published'])}
                        for row in result)
        totals = {}
        for row in rows:
            key = (row['accountId'], row['shopId'])
            total = totals.setdefault(key, {**row, 'processed': 0, 'published': 0})
            total['processed'] += row['processed']
            total['published'] += row['published']
        return {'date': start.date().isoformat(), 'timezone': 'Asia/Shanghai',
                'stores': [totals[key] for key in sorted(totals)]}

    def update_publish_interval(self, body):
        if body.get('confirmation') != 'UPDATE_SHARED_PUBLISH_INTERVAL':
            raise ValueError('缺少运行中发布间隔调整确认')
        interval = body.get('intervalSeconds')
        if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 600:
            raise ValueError('发布间隔应为 1 到 600 的整数秒')
        batches = []
        for account in self.accounts():
            changed = self.b.automation_pool(account['id']).update_running_publish_intervals(interval)
            batches.extend({'accountId': account['id'], 'batchId': batch_id} for batch_id in changed)
        if not batches:
            raise ValueError('当前没有正在发布的队列，不能调整间隔')
        return {'intervalSeconds': interval, 'batches': batches}

    def job(self, account, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('任务编号不正确')
        if not any(a['id'] == positive(account) for a in self.accounts()):
            raise ValueError('任务所属账号不存在')
        result = self.b.automation_pool(account).get_job(job_id)
        if not result:
            raise ValueError('任务不存在或已移除')
        notes = []
        with self.db() as connection:
            origin = connection.execute('SELECT * FROM shared_job_origins WHERE account_id=? AND job_id=?',
                                        (account, job_id)).fetchone()
            if origin:
                rows = connection.execute('SELECT payload FROM shared_transfers WHERE target_id=? ORDER BY updated',
                                          (result['detailId'],)).fetchall()
                for row in rows:
                    ref = json.loads(row['payload'])
                    if (ref.get('targetAccountId') == account and ref.get('targetScope') == origin['target_scope']
                            and ref.get('sourceAccountId') == origin['source_account']
                            and ref.get('sourceScope') == origin['source_scope']
                            and ref.get('detailId') == origin['source_id']):
                        notes.extend(ref.get('preparationNotes') or [])
        return {**result, 'accountId': account, 'preparationNotes': list(dict.fromkeys(notes))}

    def refresh_job_logistics(self, account, job_id):
        job = self.job(account, job_id)
        if job['status'] != 'needs_input' or job['step'] not in {'freight', 'warehouse'}:
            raise ValueError('只有等待选择运费模板或仓库的任务可以刷新物流配置')
        self.assert_job(account, job_id)
        scope = self.check_account(account)['scope']
        kind = job['step']
        path, field, id_key, name_key = (
            (self.b.HALF_FREIGHT, 'shopIdAndFreightTemplateListMap', 'templateId', 'templateName')
            if kind == 'freight' else
            (self.b.HALF_WAREHOUSE, 'shopIdAndWarehouseListMap', 'warehouseId', 'warehouseName'))
        # Fence old in-flight cache reads as well as already cached lists.
        group = (scope, path)
        self.config_reads.begin_write(group)
        try:
            worker = self.worker(account, cache_config=False)
            data = self.b.miao_request(lambda: worker._vendor(path,
                {'shopId': job['shopId'], 'isRsync': 1}), account).get('data')
            self.check_account(account, scope)
            self.assert_job(account, job_id)
            if not isinstance(data, dict) or not isinstance(data.get(field), dict):
                raise ValueError('妙手未返回有效物流配置，已保留原选项，请重试')
            rows = data[field].get(str(job['shopId']), [])
            if not isinstance(rows, list):
                raise ValueError('妙手返回的物流列表格式不正确，已保留原选项')
            options = [{'id': str(row[id_key]), 'name': str(row.get(name_key) or row[id_key])}
                       for row in rows if isinstance(row, dict) and row.get('site') == job['site']
                       and row.get(id_key)]
            defaults = [option for option in options if option['name'].strip() == ('通用' if kind == 'freight' else '发货')]
            pool = self.b.automation_pool(account)
            updated = pool.refresh_choices_for_shop(job['shopId'], job['site'], kind, options,
                                                     queue_unique_default=len(defaults) == 1)
            current = pool.get_job(job_id)
            if current is None or current['status'] not in {'needs_input', 'queued'}:
                raise ValueError('任务状态已变化，请重新打开任务详情')
            if updated['queued'] and self.b.os.getenv('APP_MODE', 'demo') == 'live' and not self.is_paused():
                Thread(target=self.run_jobs, daemon=True, name=f'shared-logistics-resume-{account}').start()
            return {**current, 'refreshedJobs': updated['refreshed'], 'autoQueuedJobs': updated['queued']}
        finally:
            self.config_reads.end_write(group)

    def delete_records(self, body):
        """Local soft deletion only; every reference is explicitly account-qualified."""
        if body.get('confirmation') != 'DELETE_SHARED_JOB_RECORDS':
            raise ValueError('缺少删除本地任务记录确认')
        refs = body.get('jobs')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 500:
            raise ValueError('每次请选择 1 到 500 条任务记录')
        normalized = {}
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError('任务引用不正确')
            if 'transferId' in ref:
                transfer_id = ref.get('transferId')
                if set(ref) != {'transferId'} or not isinstance(transfer_id, str) or not re.fullmatch(r'[0-9a-f]{32}', transfer_id):
                    raise ValueError('副本任务编号不正确')
                normalized[('transfer', transfer_id)] = {'transferId': transfer_id}
            else:
                account, job_id = positive(ref.get('accountId')), ref.get('jobId')
                if set(ref) != {'accountId', 'jobId'} or not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
                    raise ValueError('任务引用必须包含账号和有效任务 ID')
                normalized[('job', account, job_id)] = {'accountId': account, 'jobId': job_id}
        accounts = {a['id'] for a in self.accounts()}
        deleted, errors = [], []
        for key, ref in normalized.items():
            try:
                if key[0] == 'transfer':
                    # Keep the original payload and copy evidence.  This is the
                    # same guarded local cleanup as the per-card action, now
                    # available for a selected batch of review-only transfers.
                    with self.transfer_action_lock, self.db() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        row = connection.execute('SELECT status FROM shared_transfers WHERE transfer_id=?',
                                                 (ref['transferId'],)).fetchone()
                        if not row or row['status'] not in {'needs_review', 'discarded'}:
                            raise ValueError('仅能清除需核查的本地副本任务，运行中任务不能清除')
                        connection.execute("UPDATE shared_transfers SET status='discarded',updated=? "
                                           "WHERE transfer_id=? AND status='needs_review'", (time.time(), ref['transferId']))
                else:
                    _, account, job_id = key
                    if account not in accounts:
                        raise ValueError('任务所属账号不存在')
                    # Check and cancel pending work in the same SQLite transaction
                    # as the tombstone; a worker winning the race makes deletion fail.
                    self.b.automation_pool(account).delete_job_records([job_id], allow_pending=True)
                deleted.append(ref)
            except ValueError as error:
                errors.append({**ref, 'message': str(error)})
            except sqlite3.Error:
                errors.append({**ref, 'message': '本地记录保存失败，请重试'})
        return {'deleted': deleted, 'errors': errors}

    def failure_batch(self, body):
        """Explicit safe remediation using existing actions, never publication.

        ``processed`` means synchronized or accepted for the existing worker;
        it is not a claim that a remote save has completed successfully.
        """
        if body.get('confirmation') != 'HANDLE_SHARED_FAILURES':
            raise ValueError('缺少安全批量处理确认')
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前服务为只读模式，不能批量处理失败任务')
        entries = body.get('entries')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 500:
            raise ValueError('每次请选择 1 到 500 条失败记录')
        normalized = {}
        for ref in entries:
            if not isinstance(ref, dict):
                raise ValueError('失败记录引用不正确')
            if 'transferId' in ref:
                transfer = ref['transferId']
                if set(ref) != {'transferId'} or not isinstance(transfer, str) or not re.fullmatch(r'[0-9a-f]{32}', transfer):
                    raise ValueError('副本任务编号不正确')
                normalized[('transfer', transfer)] = {'transferId': transfer}
            else:
                account, job_id = positive(ref.get('accountId')), ref.get('jobId')
                if set(ref) != {'accountId', 'jobId'} or not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
                    raise ValueError('任务引用必须包含账号和有效任务 ID')
                normalized[(account, job_id)] = {'accountId': account, 'jobId': job_id}
        result = {'processed': [], 'failed': [], 'skipped': []}
        for ref in normalized.values():
            try:
                if 'transferId' in ref:
                    with self.db() as connection:
                        row = connection.execute('SELECT * FROM shared_transfers WHERE transfer_id=?', (ref['transferId'],)).fetchone()
                    if row is None:
                        raise ValueError('副本任务不存在')
                    payload = json.loads(row['payload'])
                    self.check_account(payload['sourceAccountId'], payload['sourceScope'])
                    self.check_account(payload['targetAccountId'], payload['targetScope'])
                    copy = next((c for c in self._copies() if c['source_scope'] == payload['sourceScope']
                                 and c['source_id'] == payload['detailId'] and c['target_scope'] == payload['targetScope']), None)
                    info = self.transfer_failure_metadata(dict(row), copy)
                else:
                    job = self.job(ref['accountId'], ref['jobId'])
                    self.assert_job(ref['accountId'], ref['jobId'])
                    info = job_failure_metadata(job)
                action = info['safeBatchAction']
                if not info['safeBatchEligible']:
                    result['skipped'].append({**ref, 'reason': info['safeBatchReason']})
                    continue
                if action == 'resume-transfer':
                    outcome = self.resume_transfer({**ref, 'confirmation': 'RESUME_SHARED_TO_QUEUE'}, safe_batch=True)
                    reason = '已加入副本准备队列；保存完成后仍需手动发布'
                else:
                    outcome = self.action({**ref, 'action': action, 'confirmation': 'SHARED_JOB_ACTION'}, safe_batch=True)
                    reason = (outcome.get('message') or '已提交重新检查；保存结果请在任务列表查看')
                result['processed'].append({**ref, 'action': action, 'reason': reason,
                    'accepted': bool(outcome.get('accepted') or outcome.get('queued')),
                    'autoQueuedJobs': outcome.get('autoQueuedJobs', 0)})
            except Exception as error:
                result['failed'].append({**ref, 'reason': str(error)})
        result['counts'] = {key: len(value) for key, value in result.items()}
        result['counts']['total'] = len(normalized)
        return result

    def action(self, body, *, safe_batch=False):
        account, job_id, action = positive(body.get('accountId')), body.get('jobId'), body.get('action')
        job = self.job(account, job_id)
        pool = self.b.automation_pool(account)
        if body.get('confirmation') != 'SHARED_JOB_ACTION':
            raise ValueError('缺少任务操作确认')
        if safe_batch:
            info = job_failure_metadata(job)
            if not info['safeBatchEligible'] or info['safeBatchAction'] != action:
                raise ValueError('任务状态已变化或缺少安全批量处理依据，请刷新列表')
        if action == 'refresh-logistics':
            return self.refresh_job_logistics(account, job_id)
        if action == 'remove-queue':
            return {'removed': pool.remove_publish_queue_jobs([job_id])}
        if action == 'delete-record':
            return {'deleted': pool.delete_job_records([job_id])}
        if action == 'cancel':
            return pool.cancel_edit_job(job_id)
        if action not in {'retry', 'resume'}:
            raise ValueError('不支持的任务操作')
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前服务为只读模式，不能继续编辑保存')
        if self.is_paused():
            raise ValueError('自动编辑已暂停；恢复自动处理后再重试')
        self.assert_job(account, job_id)
        # Commit the user's recovery request to the existing queue immediately.
        # Waiting for the global scheduler here leaves the old needs_input row
        # visible until every other product has finished and permits duplicates.
        with self.job_creation_lock:
            current = pool.get_job(job_id)
            if safe_batch and (not current or
                    job_failure_metadata(current)['safeBatchAction'] != action):
                raise ValueError('任务状态已变化，请刷新列表')
            if self.is_paused():
                raise ValueError('自动编辑已暂停；恢复自动处理后再重试')
            self.assert_job(account, job_id)
            if action == 'retry':
                queued = pool.retry_job(job_id, queue_only=True)
            else:
                queued = pool.resume_job(job_id, str(body.get('selectedId', '')), queue_only=True)
        RUNTIME.work_available.set()
        # A competing scheduler may be finishing its last pass. A successor
        # ensures this committed queue cannot miss its wake-up; all execution
        # still goes through the normal shared/per-account concurrency limits.
        Thread(target=self.run_jobs, daemon=True, name='shared-job-recovery').start()
        return {'accepted': True, 'status': 'queued', 'jobId': queued['jobId']}

    def http(self, handler, method, path, query):
        from http import HTTPStatus
        if not handler._same_origin():
            handler._error(HTTPStatus.FORBIDDEN, '只接受本地网页请求')
            return
        try:
            suffix = path.removeprefix('/api/automation/shared/')
            if method == 'GET':
                if suffix == 'auto-tasks':
                    data = self.b.automatic_tasks().listing()
                elif suffix == 'auto-options':
                    data = self.b.automatic_tasks().options()
                elif suffix == 'config':
                    data = self.config(query.get('refresh', [''])[0] == '1')
                elif suffix == 'accounts':
                    data = {'accounts': self.accounts()}
                elif suffix == 'events':
                    self.activity_events.serve(handler)
                    return True
                elif suffix == 'publish-receipt':
                    with reader(self.path) as connection:
                        row=connection.execute('SELECT request_id,status,result,updated FROM shared_publish_receipts WHERE request_id=?',
                            (query.get('requestId',[''])[0],)).fetchone()
                    if not row: raise ValueError('发布申请不存在')
                    data={**dict(row),'result':json.loads(row['result'])}
                elif suffix == 'drain-check':
                    data = self.activity()
                    data['drained'] = data['paused'] and not self.dispatch_lock.locked() and not any(
                        data.get(k) for k in ('running','editing','publishWorker','autoTasksBusy','reloading'))
                elif suffix == 'activity':
                    data = self.activity_events.snapshot()
                elif suffix == 'summary':
                    data = TaskQueries(self).summary()
                elif suffix in {'pool', 'jobs', 'queue'}:
                    data = self.listing(suffix, query)
                elif suffix == 'people':
                    data = self.people(int(query.get('sourceAccountId', ['0'])[0]),
                        query.get('scan', [''])[0] == '1', query.get('refresh', [''])[0] == '1')
                elif suffix in {'job', 'editor'}:
                    account, job = int(query.get('targetAccountId', ['0'])[0]), query.get('jobId', [''])[0]
                    if suffix == 'editor':
                        self.assert_job(account, job)
                    data = self.job(account, job)
                else:
                    raise ValueError('共享查询接口不存在')
            else:
                body = handler._read_body(2_000_000)
                if suffix == 'auto-create':
                    data = self.b.automatic_tasks().create(body)
                elif suffix == 'auto-pause':
                    data = self.b.automatic_tasks().pause(body)
                elif suffix == 'preview':
                    data = self.preview(body)
                elif suffix == 'run':
                    data = self.execute(body)
                elif suffix == 'bindings':
                    data = self.save_binding(body)
                elif suffix == 'reconcile':
                    data = self.reconcile(body)
                elif suffix == 'resume-transfer':
                    data = self.resume_transfer(body)
                elif suffix == 'discard-transfer':
                    data = self.discard_transfer(body)
                elif suffix == 'pause':
                    data = self.set_paused(body)
                elif suffix == 'rebalance-queued':
                    data = self.rebalance_plain_queued_jobs(body)
                elif suffix == 'reload-queue':
                    data = self.reload_queued(body)
                elif suffix == 'remove':
                    if body.get('confirmation') != 'REMOVE_SHARED_POOL_ITEMS':
                        raise ValueError('缺少移出确认')
                    data = self.remove_items(body.get('items'))
                elif suffix == 'action':
                    data = self.action(body)
                elif suffix == 'failure-batch':
                    data = self.failure_batch(body)
                elif suffix == 'delete-records':
                    data = self.delete_records(body)
                elif suffix == 'publish':
                    data = self.publish(body)
                elif suffix == 'publish-interval':
                    data = self.update_publish_interval(body)
                elif suffix == 'publish-preview':
                    data = self.publish_preview(body)
                else:
                    raise ValueError('共享操作接口不存在')
            generation = self.activity_events.invalidate() if method == 'POST' and suffix not in {'preview','publish-preview'} else self.activity_events.generation
            handler._json(HTTPStatus.OK, {'data': data, 'activityGeneration': generation, 'activityEpoch': self.activity_events.epoch})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            handler._error(HTTPStatus.BAD_REQUEST, str(error))
        except sqlite3.Error:
            handler._error(HTTPStatus.INTERNAL_SERVER_ERROR, '本地共享记录保存失败，请保留页面并重试')
        except Exception as error:
            handler._error(HTTPStatus.BAD_GATEWAY, str(error)[:500])

