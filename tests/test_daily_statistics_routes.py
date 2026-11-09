import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import server
from daily_statistics import DailyStatistics
from order_sync import OrderSyncStore


class DailyStatisticsRoutes(unittest.TestCase):
    def test_readonly_cross_account_query_errors_and_origin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = OrderSyncStore(root / 'order_sync.sqlite3')
            store.upsert([{'packageId': 1, 'orderNo': '1', 'orderTime': '2026-11-09', 'amount': '2', 'currency': 'USD'}])
            patches = [patch.object(server, 'miaoshou_account_slots', return_value=[{'id': 1, 'name': 'one'}, {'id': 2, 'name': 'two'}]),
                       patch.object(server, 'AUTOMATION_POOL', type('Pool', (), {'path': root / 'missing.sqlite3'})()),
                       patch.object(server, 'ORDER_SYNC_STORE', store), patch.object(server, 'DAILY_STATISTICS', DailyStatistics())]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.AppHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                def get(query, headers=None):
                    connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
                    try:
                        connection.request('GET', '/api/statistics/daily?' + query, headers=headers or {})
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()
                status, body = get('start=2026-11-09&end=2026-11-09&accountId=all', {'X-Miaoshou-Account-Id': '2'})
                self.assertEqual(status, 200)
                self.assertEqual(body['data']['totals']['orders'], 1)
                self.assertEqual(len(body['data']['rows']), 2)
                self.assertEqual(get('accountId=unknown')[0], 400)
                self.assertEqual(get('accountId=1&accountId=2')[0], 400)
                self.assertEqual(get('start=2026-11-10&end=2026-11-09')[0], 400)
                self.assertEqual(get('', {'Origin': 'https://example.test'})[0], 403)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join(3)
