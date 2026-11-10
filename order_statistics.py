"""Shared read-only order aggregation for account and shop reports."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

BEIJING = timezone(timedelta(hours=8))
ORDER_COUNTERS = ('orders', 'packages', 'quantity', 'quantityMissing')


def local_day(value):
    try:
        raw = str(value).strip()
        if raw.replace('.', '', 1).isdigit():
            stamp = float(raw)
            parsed = datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=BEIJING).date().isoformat() if parsed.tzinfo is None else parsed.astimezone(BEIJING).date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def read_orders(path):
    """Read all retained rows, with names from old-schema current snapshots."""
    path = Path(path)
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = 'order_statistics_history' if 'order_statistics_history' in tables else 'synced_orders'
        columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        name_field = 'shop_name' if 'shop_name' in columns else "'' AS shop_name"
        rows = [dict(row) for row in db.execute(f'''SELECT record_key,order_no,shop_id,status,
            amount,currency,ordered_at,item_details,synced_at,{name_field}
            FROM {table} ORDER BY synced_at,record_key''')]
        by_record, by_shop = {}, {}
        if table != 'synced_orders' and 'synced_orders' in tables:
            for row in db.execute("SELECT record_key,shop_id,shop_name FROM synced_orders WHERE shop_name!='' ORDER BY synced_at,record_key"):
                by_record[row['record_key'], row['shop_id']] = row['shop_name']
                if row['shop_id']:
                    by_shop[row['shop_id']] = row['shop_name']
        for row in rows:
            if not row['shop_name']:
                row['shop_name'] = by_record.get((row['record_key'], row['shop_id']), by_shop.get(row['shop_id'], ''))
        return rows


def shop_identity(row):
    shop_id, name = str(row['shop_id'] or '').strip(), str(row['shop_name'] or '').strip()
    return ('id', shop_id) if shop_id else ('name', name) if name else ('unknown', '')


def group_orders(rows):
    grouped = {}
    for row in rows:
        number = str(row['order_no'] or '').strip()
        key = (shop_identity(row), ('order', number) if number else ('package', row['record_key']))
        grouped.setdefault(key, []).append(row)
    return grouped


def package_quantities(packages):
    known = missing = 0
    for package in packages:
        try:
            items = json.loads(package['item_details'] or '[]')
        except (ValueError, TypeError):
            items = []
        if not isinstance(items, list):
            items = []
        values = [item.get('quantity') for item in items if isinstance(item, dict)]
        if items and len(values) == len(items) and all(type(value) is int and value >= 0 for value in values):
            known += sum(values)
        else:
            missing += 1
    return known, missing


def build_shop_report(account, order_path, start, end):
    aid = account['id']
    path = Path(order_path) if aid == 1 else Path(order_path).with_name(f'order_sync.account-{aid}.sqlite3')
    rows = read_orders(path)
    shops = {}
    for row in rows:
        identity = shop_identity(row)
        shop = shops.setdefault(identity, {'accountId': aid, 'shopId': str(row['shop_id'] or '').strip(),
            'shopKey': str(row['shop_id'] or row['shop_name'] or '').strip(),
            'shopName': '', **dict.fromkeys(ORDER_COUNTERS, 0)})
        if row['shop_name']:
            shop['shopName'] = str(row['shop_name']).strip()
    missing_dates = missing_numbers = missing_ids = 0
    for packages in group_orders(rows).values():
        order = packages[-1]
        day = local_day(order['ordered_at'])
        if day is None:
            missing_dates += 1
            continue
        if not start.isoformat() <= day <= end.isoformat():
            continue
        shop = shops[shop_identity(order)]
        shop['orders'] += 1
        shop['packages'] += len(packages)
        quantity, missing = package_quantities(packages)
        shop['quantity'] += quantity
        shop['quantityMissing'] += missing
        missing_numbers += not bool(order['order_no'])
        missing_ids += not bool(order['shop_id'])
    names = {}
    for shop in shops.values():
        shop['shopName'] = shop['shopName'] or (f"店铺 {shop['shopId']}" if shop['shopId'] else '未识别店铺')
        names[shop['shopName']] = names.get(shop['shopName'], 0) + 1
    for shop in shops.values():
        shop['displayName'] = shop['shopName'] + (f"（{shop['shopId'] or '无编号'}）" if names[shop['shopName']] > 1 else '')
    result = sorted(shops.values(), key=lambda row: (-row['orders'], row['shopName'], row['shopKey']))
    warnings = []
    if missing_dates:
        warnings.append(f'{missing_dates} 笔订单缺少有效下单时间，未归入日期统计')
    if missing_numbers:
        warnings.append(f'{missing_numbers} 笔记录缺少订单号，按包裹记录计单')
    if missing_ids:
        warnings.append(f'{missing_ids} 笔订单缺少店铺编号，按名称或未识别店铺归类')
    totals = {key: sum(shop[key] for shop in result) for key in ORDER_COUNTERS}
    return {'accountId': aid, 'accountName': account['name'], 'start': start.isoformat(), 'end': end.isoformat(),
            'timezone': 'Asia/Shanghai', 'shops': result, 'totals': totals, 'warnings': warnings,
            'lastOrderSync': max((row['synced_at'] for row in rows), default=''),
            'generatedAt': datetime.now(BEIJING).isoformat(timespec='seconds')}
