import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import server
from order_sync import OrderSyncStore


class ShopReportRoutes(unittest.TestCase):
    def test_account_scope_validation_and_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); store = OrderSyncStore(root/'order_sync.sqlite3')
            store.upsert([{'packageId': 1, 'orderNo': 'same', 'shopId': '1', 'shopName': 'one', 'orderTime': '2026-11-10'}])
            OrderSyncStore(root/'order_sync.account-2.sqlite3').upsert([
                {'packageId': 1, 'orderNo': 'same', 'shopId': '1', 'shopName': 'two', 'orderTime': '2026-11-10'}])
            with patch.object(server, 'ORDER_SYNC_STORE', store), patch.object(server, 'miaoshou_account_slots',
                    return_value=[{'id': 1, 'name': 'one'}, {'id': 2, 'name': 'two'}]):
                httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.AppHandler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
                try:
                    def get(query, headers=None):
                        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
                        try:
                            connection.request('GET', '/api/statistics/shops?start=2026-11-10&end=2026-11-10&'+query, headers=headers or {})
                            response = connection.getresponse(); return response.status, json.loads(response.read())
                        finally:
                            connection.close()
                    status, body = get('accountId=2', {'X-Miaoshou-Account-Id': '1'})
                    self.assertEqual(status, 200); self.assertEqual(body['data']['accountId'], 2)
                    self.assertEqual(body['data']['shops'][0]['shopName'], 'two')
                    for query in ('', 'accountId=all', 'accountId=99', 'accountId=1&accountId=2', 'accountId=1&start=bad'):
                        self.assertEqual(get(query)[0], 400)
                    self.assertEqual(get('accountId=1', {'Origin': 'https://example.test'})[0], 403)
                finally:
                    httpd.shutdown(); httpd.server_close(); thread.join(3)
