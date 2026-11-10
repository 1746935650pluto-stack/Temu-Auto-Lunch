"""Local account/shop/site logistics defaults. Holds IDs, never credentials."""
from contextlib import closing
from pathlib import Path
import sqlite3


class ShippingBindings:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS shipping_bindings (
                scope TEXT NOT NULL, account_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
                site TEXT NOT NULL, freight_id TEXT NOT NULL, warehouse_id TEXT NOT NULL,
                revision INTEGER NOT NULL, PRIMARY KEY (scope, shop_id, site))''')
            db.commit()
            return db
        except Exception:
            db.close()
            raise

    @staticmethod
    def record(row):
        return {'freightId': row['freight_id'], 'warehouseId': row['warehouse_id'],
                'revision': row['revision']} if row else {'freightId': '', 'warehouseId': '', 'revision': 0}

    def get(self, scope, shop, site):
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM shipping_bindings WHERE scope=? AND shop_id=? AND site=?',
                             (scope, shop, site)).fetchone()
        return self.record(row)

    def shop(self, scope, shop):
        with closing(self.connect()) as db:
            return {row['site']: self.record(row) for row in db.execute(
                'SELECT * FROM shipping_bindings WHERE scope=? AND shop_id=?', (scope, shop))}

    def save(self, scope, account, shop, site, freight, warehouse, revision):
        if type(revision) is not int or revision < 0:
            raise ValueError('绑定版本不正确，请刷新后重试')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM shipping_bindings WHERE scope=? AND shop_id=? AND site=?',
                                 (scope, shop, site)).fetchone()
            if revision != (current['revision'] if current else 0):
                raise ValueError('绑定已在其他页面修改，请刷新后核对')
            db.execute('INSERT OR REPLACE INTO shipping_bindings VALUES (?,?,?,?,?,?,?)',
                       (scope, account, shop, site, freight, warehouse, revision + 1))
        return {'freightId': freight, 'warehouseId': warehouse, 'revision': revision + 1}
