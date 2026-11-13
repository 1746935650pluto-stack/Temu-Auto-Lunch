"""Account/day reports from local records. No vendor calls or queue mutations."""
from contextlib import closing
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import json
from threading import Lock
import time

from event_archive import archive_path
from order_statistics import BEIJING, local_day, read_orders, group_orders, package_quantities, build_shop_report

COUNTERS = ('collected', 'tasks', 'saved', 'submitted', 'editFailed', 'publishFailed',
            'needsInput', 'orders', 'packages', 'quantity', 'quantityMissing', 'amountMissing')


def parameters(query, accounts):
    if set(query) - {'start', 'end', 'accountId'} or any(len(values) != 1 for values in query.values()):
        raise ValueError('统计参数不正确')
    today = datetime.now(BEIJING).date()
    try:
        start = date.fromisoformat(query.get('start', [(today - timedelta(days=6)).isoformat()])[0])
        end = date.fromisoformat(query.get('end', [today.isoformat()])[0])
        if start > end or (end - start).days > 92:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('请选择有效的起止日期，每次最多查询 93 天') from None
    raw_id = query.get('accountId', ['all'])[0]
    if raw_id != 'all':
        if not raw_id.isdigit() or int(raw_id) not in {a['id'] for a in accounts}:
            raise ValueError('统计账号不存在或已删除')
        accounts = [a for a in accounts if a['id'] == int(raw_id)]
    return start, end, accounts


def reader(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def account_paths(primary, account_id):
    primary = Path(primary)
    base = primary if account_id == 1 else primary.with_name(f'automation_pool.account-{account_id}.sqlite3')
    return (base, *(base.with_name(base.stem + f'.pool-{i}.sqlite3') for i in (2, 3)))


def build_report(accounts, pool_path, order_path, query):
    start, end, selected = parameters(query, accounts)
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    rows = {(a['id'], day): dict(date=day, accountId=a['id'], accountName=a['name'],
              **dict.fromkeys(COUNTERS, 0), amounts={}) for day in dates for a in selected}
    warnings = []
    missing_dates = 0
    last_synced = {}
    lower = datetime.combine(start, day_time(), BEIJING).astimezone(timezone.utc).isoformat(timespec='seconds')
    upper = datetime.combine(end + timedelta(days=1), day_time(), BEIJING).astimezone(timezone.utc).isoformat(timespec='seconds')
    seen = set()

    def count(account, day, metric, identity):
        key = (account, day, metric, identity)
        if (account, day) in rows and key not in seen:
            seen.add(key)
            rows[account, day][metric] += 1

    for account in selected:
        aid = account['id']
        for path in account_paths(pool_path, aid):
            if not path.is_file():
                continue
            with closing(reader(path)) as connection:
                for row in connection.execute("SELECT detail_id, date(created_at,'+8 hours') AS day FROM automation_items WHERE created_at>=? AND created_at<?", (lower, upper)):
                    count(aid, row['day'], 'collected', row['detail_id'])
                for row in connection.execute("SELECT job_id, date(created_at,'+8 hours') AS day FROM automation_jobs WHERE created_at>=? AND created_at<?", (lower, upper)):
                    count(aid, row['day'], 'tasks', row['job_id'])
            for events_path in (path, archive_path(path)):
                if not events_path.is_file():
                    continue
                with closing(reader(events_path)) as connection:
                    if events_path != path:
                        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        if not tables:
                            continue
                        if 'event_archive_metadata' not in tables:
                            raise ValueError('历史事件归档缺少来源标识，统计未完成')
                        source = connection.execute("SELECT value FROM event_archive_metadata WHERE key='source_file'").fetchall()
                        if len(source) != 1 or source[0][0] != path.name:
                            raise ValueError('历史事件归档来源不一致，统计未完成')
                    for event in connection.execute("""SELECT DISTINCT job_id, date(created_at,'+8 hours') AS day,
                        CASE WHEN step='save' AND status='done' THEN 'saved'
                             WHEN step='publish' AND status='done' THEN 'submitted'
                             WHEN step='publish' AND status IN ('failed','needs_review','interrupted') THEN 'publishFailed'
                             WHEN status IN ('failed','needs_review','interrupted') THEN 'editFailed'
                             WHEN status='needs_input' THEN 'needsInput' END AS metric
                        FROM automation_job_events WHERE created_at>=? AND created_at<?
                        AND (status IN ('failed','needs_review','interrupted','needs_input')
                             OR (status='done' AND step IN ('save','publish')))""", (lower, upper)):
                        if event['metric']:
                            count(aid, event['day'], event['metric'], event['job_id'])
        path = Path(order_path) if aid == 1 else Path(order_path).with_name(f'order_sync.account-{aid}.sqlite3')
        if not path.is_file():
            continue
        orders = read_orders(path)
        grouped = group_orders(orders)
        for order in orders:
            last_synced[aid] = max(last_synced.get(aid, ''), order['synced_at'])
        for packages in grouped.values():
            order = packages[-1]
            day = local_day(order['ordered_at'])
            if day is None:
                missing_dates += 1
                continue
            target = rows.get((aid, day))
            if target is None:
                continue
            target['orders'] += 1
            target['packages'] += len(packages)
            quantity, missing = package_quantities(packages)
            target['quantity'] += quantity
            target['quantityMissing'] += missing
            try:
                amount = Decimal(order['amount'])
                currency = order['currency'].strip().upper()
                if not amount.is_finite() or abs(amount) > Decimal('1e15') or not currency:
                    raise InvalidOperation()
                target['amounts'][currency] = target['amounts'].get(currency, Decimal(0)) + amount
            except (InvalidOperation, ValueError):
                target['amountMissing'] += 1
        if any(not order['order_no'] for order in orders):
            warnings.append(f"{account['name']}：部分记录缺少订单号，按包裹记录计单。")
    if missing_dates:
        warnings.append(f'{missing_dates} 笔订单缺少有效下单时间，未归入日期统计。')
    totals = dict.fromkeys(COUNTERS, 0)
    totals['amounts'] = {}
    for row in rows.values():
        for key in COUNTERS:
            totals[key] += row[key]
        for currency, amount in row['amounts'].items():
            totals['amounts'][currency] = totals['amounts'].get(currency, Decimal(0)) + amount
        row['amounts'] = {key: format(value, '.2f') for key, value in sorted(row['amounts'].items())}
    totals['amounts'] = {key: format(value, '.2f') for key, value in sorted(totals['amounts'].items())}
    return {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': 'Asia/Shanghai',
            'accounts': [{'id': a['id'], 'name': a['name']} for a in accounts],
            'rows': [rows[a['id'], day] for day in reversed(dates) for a in selected],
            'totals': totals, 'warnings': warnings, 'lastOrderSync': last_synced,
            'generatedAt': datetime.now(BEIJING).isoformat(timespec='seconds')}


class DailyStatistics:
    def __init__(self):
        self.lock = Lock()
        self.cache = {}

    def invalidate(self, account_id):
        # Serialize with report building so an in-flight old report cannot
        # repopulate the cache after invalidation.
        with self.lock:
            for key in list(self.cache):
                selected = dict(key[3]).get('accountId', ('all',))[0]
                if selected == 'all' or int(selected) == account_id:
                    del self.cache[key]

    def report(self, accounts, pool_path, order_path, query):
        parameters(query, accounts)
        key = (str(pool_path), str(order_path), tuple((a['id'], a['name']) for a in accounts),
               tuple(sorted((k, tuple(v)) for k, v in query.items())), datetime.now(BEIJING).date())
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 15:
                return cached[1]
            result = build_report(accounts, pool_path, order_path, query)
            if len(self.cache) >= 8:
                self.cache.clear()
            self.cache[key] = (time.monotonic(), result)
            return result


def shop_report(accounts, order_path, query):
    start, end, selected = parameters(query, accounts)
    if query.get('accountId', ['all'])[0] == 'all' or len(selected) != 1:
        raise ValueError('请选择单个妙手账号查看店铺数据')
    return build_shop_report(selected[0], order_path, start, end)
