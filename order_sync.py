"""Local, read-only storage for MiaoShou package/order synchronization."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlsplit


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ''):
            return value
    return ''


def extract_package_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Read package-list responses without retaining recipient information."""
    data = response.get('data', {}) if isinstance(response, dict) else {}

    def official_package_rows(package_list: Any) -> list[dict[str, Any]]:
        if not isinstance(package_list, list):
            return []
        packages: list[dict[str, Any]] = []
        for package in package_list:
            if not isinstance(package, dict):
                continue
            order_info = package.get('orderInfo')
            items = package.get('items')
            item_titles = []
            item_details = []
            image_url = ''
            if isinstance(items, list):
                item_titles = [str(item.get('title', '')).strip() for item in items
                               if isinstance(item, dict) and item.get('title')]
                item_details = [{
                    'title': str(item.get('title') or '').strip(),
                    'specification': str(item.get('skuSubName') or '').strip(),
                    'skuId': str(item.get('platformSkuId') or '').strip(),
                } for item in items if isinstance(item, dict)]
                for item in items:
                    if isinstance(item, dict) and item.get('picUrl'):
                        image_url = str(item['picUrl']).strip()
                        break
            if not image_url and isinstance(order_info, dict):
                image_url = str(order_info.get('platformOrderDetailUrl') or '').strip()
            packages.append({
                'opOrderPackageId': package.get('opOrderPackageId'),
                'appPackageNo': package.get('appPackageNo'),
                'shopId': package.get('shopId'),
                'shopName': package.get('shopNick') or package.get('shopName'),
                'appPackageStatus': package.get('appPackageStatus'),
                'appPackageStatusText': package.get('appPackageStatusText'),
                'logisticsNo': package.get('logisticsNo'),
                'itemSummary': ' / '.join(item_titles),
                'itemDetails': item_details,
                'imageUrl': image_url,
                **(order_info if isinstance(order_info, dict) else {}),
            })
        return packages

    # The released endpoint currently responds with this direct shape.
    if isinstance(data, dict) and 'orderPackageList' in data:
        return official_package_rows(data.get('orderPackageList'))
    if isinstance(data, list):
        # MiaoShou returns page envelopes, each with ``orderPackageList``.
        # Deliberately copy only the fields used by this read-only view: the
        # response's consignee information never leaves the process or lands
        # in the local database.
        packages: list[dict[str, Any]] = []
        for envelope in data:
            if not isinstance(envelope, dict):
                continue
            packages.extend(official_package_rows(envelope.get('orderPackageList')))
        if packages or any(isinstance(row, dict) and 'orderPackageList' in row for row in data):
            return packages
        # Keep support for a plain list response from older API revisions.
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        raise ValueError('妙手订单接口未返回列表数据')
    for key in ('packageList', 'orderList', 'list', 'records', 'rows', 'data'):
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    raise ValueError('妙手订单接口未返回可识别的列表字段')


def normalize_package(row: dict[str, Any]) -> dict[str, str]:
    """Keep only display fields needed by the read-only order centre."""
    package_no = str(_first(row, 'appPackageNo', 'packageNo', 'packageNumber', 'packageCode'))
    order_no = str(_first(row, 'platformOrderSn', 'orderNo', 'platformOrderNo', 'orderNumber'))
    remote_id = str(_first(row, 'opOrderPackageId', 'packageId', 'orderId', 'id', 'packageDetailId'))
    record_key = remote_id or package_no or order_no
    if not record_key:
        raise ValueError('订单记录缺少包裹号、订单号或唯一 ID')
    image_url = str(_first(row, 'imageUrl', 'picUrl', 'image')).strip()
    parsed_image_url = urlsplit(image_url)
    if parsed_image_url.scheme not in {'http', 'https'} or not parsed_image_url.netloc:
        image_url = ''
    status_code = str(_first(row, 'appPackageStatus', 'packageStatus', 'status', 'orderStatus'))
    status_labels = {
        'pending': '待处理', 'unpaid': '未付款', 'wait_confirmed': '待审核',
        'wait_seller_send': '待发货', 'wait_receiver_confirm': '已发货',
        'finished': '已完成', 'cancelled': '已关闭', 'returned': '已退款', 'refunding': '售后中',
    }
    status_label = status_labels.get(status_code) or str(_first(row, 'appPackageStatusText', 'packageStatusName') or status_code)
    item_details = row.get('itemDetails')
    if not isinstance(item_details, list):
        item_details = []
    item_details = [{key: str(item.get(key) or '').strip() for key in ('title', 'specification', 'skuId')}
                    for item in item_details if isinstance(item, dict)]
    return {
        'recordKey': record_key,
        'packageNo': package_no,
        'orderNo': order_no,
        'shopId': str(_first(row, 'shopId', 'storeId')),
        'shopName': str(_first(row, 'shopName', 'shopNick', 'storeName'))[:200],
        'status': status_label,
        'trackingNo': str(_first(row, 'trackingNo', 'trackingNumber', 'waybillNo', 'logisticsNo')),
        'amount': str(_first(row, 'orderAmount', 'amount', 'totalAmount', 'payAmount')),
        'currency': str(_first(row, 'currency', 'currencyCode')),
        'orderedAt': str(_first(row, 'gmtOrderStart', 'orderTime', 'createTime', 'createdAt', 'orderCreateTime')),
        'updatedAtRemote': str(_first(row, 'gmtOrderModified', 'updateTime', 'updatedAt', 'modifyTime')),
        'itemSummary': str(_first(row, 'itemSummary', 'productName', 'productTitle', 'skuName', 'itemName'))[:300],
        'imageUrl': image_url[:2000],
        'itemDetailsJson': json.dumps(item_details, ensure_ascii=False),
    }


class OrderSyncStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS synced_orders (
                    record_key TEXT PRIMARY KEY,
                    package_no TEXT NOT NULL DEFAULT '',
                    order_no TEXT NOT NULL DEFAULT '',
                    shop_id TEXT NOT NULL DEFAULT '',
                    shop_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    tracking_no TEXT NOT NULL DEFAULT '',
                    amount TEXT NOT NULL DEFAULT '',
                    currency TEXT NOT NULL DEFAULT '',
                    ordered_at TEXT NOT NULL DEFAULT '',
                    updated_at_remote TEXT NOT NULL DEFAULT '',
                    item_summary TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    item_details TEXT NOT NULL DEFAULT '[]',
                    synced_at TEXT NOT NULL
                )
            ''')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(synced_orders)').fetchall()}
            if 'shop_name' not in columns:
                connection.execute("ALTER TABLE synced_orders ADD COLUMN shop_name TEXT NOT NULL DEFAULT ''")
            if 'image_url' not in columns:
                connection.execute("ALTER TABLE synced_orders ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
            if 'item_details' not in columns:
                connection.execute("ALTER TABLE synced_orders ADD COLUMN item_details TEXT NOT NULL DEFAULT '[]'")

    def upsert(self, rows: list[dict[str, Any]]) -> int:
        normalized: list[dict[str, str]] = []
        for row in rows:
            try:
                normalized.append(normalize_package(row))
            except ValueError:
                continue
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.executemany('''
                INSERT INTO synced_orders (
                    record_key, package_no, order_no, shop_id, shop_name, status, tracking_no,
                    amount, currency, ordered_at, updated_at_remote, item_summary, image_url, item_details, synced_at
                ) VALUES (
                    :recordKey, :packageNo, :orderNo, :shopId, :shopName, :status, :trackingNo,
                    :amount, :currency, :orderedAt, :updatedAtRemote, :itemSummary, :imageUrl, :itemDetailsJson, :syncedAt
                ) ON CONFLICT(record_key) DO UPDATE SET
                    package_no = excluded.package_no, order_no = excluded.order_no,
                    shop_id = excluded.shop_id, shop_name = excluded.shop_name, status = excluded.status,
                    tracking_no = excluded.tracking_no, amount = excluded.amount,
                    currency = excluded.currency, ordered_at = excluded.ordered_at,
                    updated_at_remote = excluded.updated_at_remote,
                    item_summary = excluded.item_summary, image_url = excluded.image_url,
                    item_details = excluded.item_details,
                    synced_at = excluded.synced_at
            ''', [{**row, 'syncedAt': now} for row in normalized])
        return len(normalized)

    def replace_snapshot(self, rows: list[dict[str, Any]]) -> int:
        """Atomically replace the local list with one confirmed MiaoShou snapshot."""
        normalized: list[dict[str, str]] = []
        for row in rows:
            try:
                normalized.append(normalize_package(row))
            except ValueError:
                continue
        # A malformed empty response must never erase a previously successful
        # local snapshot. An account with no orders returns an explicit empty
        # list and is handled by the caller before it reaches this method.
        if not normalized:
            return 0
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with closing(self._connect()) as connection, connection:
            connection.execute('DELETE FROM synced_orders')
            connection.executemany('''
                INSERT INTO synced_orders (
                    record_key, package_no, order_no, shop_id, shop_name, status, tracking_no,
                    amount, currency, ordered_at, updated_at_remote, item_summary, image_url, item_details, synced_at
                ) VALUES (
                    :recordKey, :packageNo, :orderNo, :shopId, :shopName, :status, :trackingNo,
                    :amount, :currency, :orderedAt, :updatedAtRemote, :itemSummary, :imageUrl, :itemDetailsJson, :syncedAt
                )
            ''', [{**row, 'syncedAt': now} for row in normalized])
        return len(normalized)

    def list(self, limit: int = 1000) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute('''
                SELECT record_key, package_no, order_no, shop_id, shop_name, status, tracking_no,
                       amount, currency, ordered_at, updated_at_remote, item_summary, image_url, item_details, synced_at
                FROM synced_orders
                ORDER BY CASE WHEN ordered_at = '' THEN 1 ELSE 0 END,
                         ordered_at DESC, record_key DESC
                LIMIT ?
            ''', (limit,)).fetchall()
        return [{
            'recordKey': row['record_key'], 'packageNo': row['package_no'], 'orderNo': row['order_no'],
            'shopId': row['shop_id'], 'shopName': row['shop_name'], 'status': row['status'], 'trackingNo': row['tracking_no'],
            'amount': row['amount'], 'currency': row['currency'], 'orderedAt': row['ordered_at'],
            'updatedAtRemote': row['updated_at_remote'], 'itemSummary': row['item_summary'],
            'imageUrl': row['image_url'], 'syncedAt': row['synced_at'],
            'itemDetails': json.loads(row['item_details']),
        } for row in rows]

    def summary(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            count = connection.execute('SELECT COUNT(*) FROM synced_orders').fetchone()[0]
            last = connection.execute('SELECT MAX(synced_at) FROM synced_orders').fetchone()[0] or ''
        return {'count': count, 'lastSyncedAt': last}
