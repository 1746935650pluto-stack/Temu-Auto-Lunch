"""Historical order imports use synthetic pages and isolated databases only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from daily_statistics import build_report
from order_sync import OrderSyncStore


class OrderHistoryPagingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name); self.store = OrderSyncStore(self.root/'order_sync.sqlite3')
        setting = patch.object(server, 'MIAOSHOU_PACKAGE_LIST_PAYLOAD', '{"page":1,"keepFilter":"unchanged"}')
        setting.start(); self.addCleanup(setting.stop)

    def row(self, index):
        return {'packageId': index, 'orderNo': f'order-{index}', 'shopId': 's',
                'orderTime': f'2026-11-{10-index//400:02d} 08:00:00', 'itemDetails': [{'quantity': 1}]}

    def pages(self, count, calls):
        def vendor(path, payload):
            calls.append(payload.copy())
            start = (payload['page']-1)*100
            return {'data': {'total': count, 'packageList': [self.row(i) for i in range(start, min(start+100, count))]}}
        return vendor

    def test_manual_history_continues_after_one_thousand_and_daily_report_reads_all_days(self):
        calls = []; count = server.sync_miaoshou_order_pages(self.pages(2407,calls), self.store,
                                                           max_records=server.ORDER_HISTORY_MAX_RECORDS)
        self.assertEqual(count, 2407); self.assertEqual(len(calls), 25)
        self.assertEqual(calls[-1]['page'],25); self.assertTrue(all(c['keepFilter']=='unchanged' for c in calls))
        self.assertEqual(len(self.store.list()),1000)
        report = build_report([{'id':1,'name':'MM'}],self.root/'absent.sqlite3',self.store.path,
                              {'accountId':['1'],'start':['2026-11-04'],'end':['2026-11-10']})
        self.assertEqual(report['totals']['orders'],2407)
        self.assertEqual(len([r for r in report['rows'] if r['orders']]),7)

    def test_periodic_refresh_keeps_the_existing_one_thousand_record_budget_and_retains_history(self):
        server.sync_miaoshou_order_pages(self.pages(2407,[]), self.store,max_records=server.ORDER_HISTORY_MAX_RECORDS)
        calls=[];self.assertEqual(server.sync_miaoshou_order_pages(self.pages(2407,calls),self.store),1000)
        self.assertEqual(len(calls),10)
        report=build_report([{'id':1,'name':'MM'}],self.root/'absent.sqlite3',self.store.path,
                            {'accountId':['1'],'start':['2026-11-04'],'end':['2026-11-10']})
        self.assertEqual(report['totals']['orders'],2407)

    def test_repeated_pages_cannot_replace_a_good_snapshot(self):
        self.store.upsert([self.row(9999)])
        with self.assertRaisesRegex(ValueError,'分页重复'):
            server.sync_miaoshou_order_pages(lambda *args:{'data':{'packageList':[self.row(i) for i in range(100)]}},
                                             self.store,max_records=server.ORDER_HISTORY_MAX_RECORDS)
        self.assertEqual([r['orderNo'] for r in self.store.list()],['order-9999'])

    def test_later_page_failure_preserves_original_orders(self):
        self.store.upsert([self.row(9999)])
        def vendor(path,payload):
            if payload['page']==2:raise RuntimeError('remote failure')
            return {'data':{'packageList':[self.row(i) for i in range(100)]}}
        with self.assertRaisesRegex(RuntimeError,'remote failure'):
            server.sync_miaoshou_order_pages(vendor,self.store,max_records=server.ORDER_HISTORY_MAX_RECORDS)
        self.assertEqual([r['orderNo'] for r in self.store.list()],['order-9999'])

    def test_history_import_stops_at_bound_and_does_not_mix_another_account(self):
        other=OrderSyncStore(self.root/'order_sync.account-2.sqlite3');other.upsert([self.row(55)])
        calls=[]
        self.assertEqual(server.sync_miaoshou_order_pages(self.pages(5000,calls),self.store,max_records=1200),1200)
        self.assertEqual(len(calls),12);self.assertEqual(len(other.list()),1)
        for invalid in (0,20001,True):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                server.sync_miaoshou_order_pages(self.pages(1,[]),self.store,max_records=invalid)
