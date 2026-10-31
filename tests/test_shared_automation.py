"""Shared workflows use temporary databases and synthetic remote responses only."""
import copy
import json
import os
import time
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import server
import shared_automation
from shared_automation import SharedAutomation, copy_preparation_limit, create_copy_payload
from test_collection_accounts import CollectionAccountTests
from test_order_accounts import OrderAccountTests
from test_item_editor import sample_item
from test_import_template_preflight import valid_item


class SharedAutomationTests(unittest.TestCase):
    request = OrderAccountTests.request
    stop_server = OrderAccountTests.stop_server

    def setUp(self):
        CollectionAccountTests.setUp(self)
        mocked = patch.object(server, 'SHARED_SERVICES', {})
        mocked.start(); self.addCleanup(mocked.stop)
        self.service = server.shared_automation()
        self.calls = []
        self.remote = {}
        self.next_id = 10000
        self.ambiguous = False
        for account in (1, 2):
            item = valid_item(); item['title'] = f'Account {account} product'; item['subAppAccountId'] = '11'
            self.remote[(account, 123)] = item
            # Existing goods predate sharding and must remain in pool 1.
            server.automation_pool(account).pools[0].put_item(123, 'site', 0, item)

        def post(client, path, payload):
            account = 1 if client.app_key == 'test-one' else 2
            self.calls.append((account, path, copy.deepcopy(payload)))
            data = {}
            if path == server.SHOP_LIST:
                data = {'shopList': [{'shopId': 7, 'shopNick': f'Shop {account}-7'}, {'shopId': 8, 'shopNick': f'Shop {account}-8'}]}
            elif path == server.HALF_SITES:
                data = {'publishSiteOptions': [{'key': 'US', 'value': '美国站'}]}
            elif path == server.HALF_CATEGORY_ATTRIBUTE_RULES:
                data = {'productAttributeRules': []}
            elif path == server.HALF_FREIGHT:
                data = {'shopIdAndFreightTemplateListMap': {str(payload['shopId']): [
                    {'site':'US','templateName':'通用','templateId':f'{account}-freight-{payload["shopId"]}'}]}}
            elif path == server.HALF_WAREHOUSE:
                data = {'shopIdAndWarehouseListMap': {str(payload['shopId']): [
                    {'site':'US','warehouseName':'发货','warehouseId':f'{account}-warehouse-{payload["shopId"]}'}]}}
            elif path == server.HALF_CREATE:
                self.next_id += 1
                self.remote[(account, self.next_id)] = copy.deepcopy(payload['siteCollectItemInfo'])
                if self.ambiguous:
                    raise RuntimeError('request timed out after remote acceptance')
                data = {'collectBoxDetailId': self.next_id}
            elif path in {server.HALF_SITE_DETAIL, server.HALF_SHOP_DETAIL}:
                item = self.remote[(account, int(payload['detailId']))]
                data = {'siteCollectItemInfo': copy.deepcopy(item), 'shopCollectItemInfo': copy.deepcopy(item)}
            elif path == server.HALF_PUBLISH:
                data = {'accepted': True}
            return {'result': 'success', 'code': 'success', 'data': data}
        mocked = patch.object(server.MiaoShouClient, 'post', post)
        mocked.start(); self.addCleanup(mocked.stop)
        # Queue counters may refresh their daily report in the background.
        # Join those readers before restoring account globals/removing temp DBs.
        report_services = [self.service]
        original_activity_stats = SharedAutomation.activity_store_stats

        def activity_stats(service):
            if service not in report_services:
                report_services.append(service)
            result = original_activity_stats(service)
            return result

        mocked = patch.object(SharedAutomation, 'activity_store_stats', activity_stats)
        mocked.start(); self.addCleanup(mocked.stop)

        def finish_reports():
            for service in report_services:
                thread = getattr(service, 'daily_stats_thread', None)
                if thread is not None and not isinstance(thread, threading.Thread):
                    # Thread doubles used for dispatch assertions own no reader.
                    service.daily_stats_thread = None
                self.assertTrue(service.close(timeout=5), 'daily report reader outlived its fixture')

        self.addCleanup(finish_reports)

    def request_data(self, sources=(1,), targets=((2, 7), (2, 8)), mode='manual'):
        return {'items': [{'sourceAccountId': a, 'detailId': 123} for a in sources],
                'targets': [{'targetAccountId': a, 'shopId': s} for a, s in targets],
                'targetMode': mode, 'site': 'US', 'priceMode': 'floor', 'price': '160', 'editConcurrency': 2}

    def test_preview_duplicate_site_case_matches_legacy_jobs(self):
        source, target = self.service.check_account(1), self.service.check_account(2)
        pool = server.automation_pool(2)
        pool.put_item(999, 'site', 0, valid_item())
        job = pool.queue_jobs([999], 7, 'US')[0]
        with closing(pool._job_pool(job['jobId'])._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET site='us' WHERE job_id=?", (job['jobId'],))
        with self.service.db() as db:
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('case-key', 1, source['scope'], 123, 2, target['scope'], 999, 'ready', '{}', ''))
        request = self.request_data(targets=((2, 7),))
        request['site'] = 'Us'
        self.assertEqual(self.service.preview(request)['taskCount'], 0)

    def test_preview_does_not_misclassify_product_using_retired_copy_scope(self):
        source, target = self.service.check_account(1), self.service.check_account(2)
        with self.service.db() as db:
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('retired-key', 2, target['scope'], 888, 1, 'retired-source-scope', 123, 'ready', '{}', ''))
        self.assertEqual(self.service.preview(self.request_data(targets=((2, 7),)))['taskCount'], 1)

    def prepare(self, sources=(1,), targets=((2, 7), (2, 8))):
        plan = self.service.preview(self.request_data(sources, targets))
        self.service.execute({'previewId': plan['previewId'], 'confirmation': 'SAVE_SHARED_AUTOMATION_BATCH'}, launch=False)
        with self.service.db() as connection:
            rows = list(connection.execute('SELECT * FROM shared_transfers WHERE batch_id=?', (plan['previewId'],)))
        return plan, rows

    def test_copy_payload_removes_account_specific_references_and_keeps_product(self):
        raw = sample_item(); original = copy.deepcopy(raw)
        raw.update(subAppAccountId='11', shopIdAndGoodsModelReqMap={'1234': {'id': 9}}, collectShowSizeTemplateIds=[123],
                   siteShipmentLimitDayMap={'US': 7})
        raw['skuMap'][';sample;']['siteAndWarehouseIdAndStockMap'] = {'US': {'999': 2}}
        clean = create_copy_payload(raw, [7, 8], 'US')
        self.assertEqual(clean['title'], original['title'])
        self.assertEqual(clean['imgUrls'], original['imgUrls'])
        self.assertEqual([r['shopId'] for r in clean['collectBoxDetailShopList']], [7, 8])
        for key in ('detailId', 'subAppAccountId', 'shopIdAndGoodsModelReqMap', 'collectShowSizeTemplateIds'):
            self.assertNotIn(key, clean)
        self.assertNotIn('siteAndWarehouseIdAndStockMap', clean['skuMap'][';sample;'])
        self.assertIn('siteAndWarehouseIdAndStockMap', raw['skuMap'][';sample;'])
        self.assertEqual(clean['siteShipmentLimitDayMap'], {'US': 7})

    def test_pool_paginates_and_identical_ids_remain_distinct(self):
        for account in (1, 2):
            for identifier in range(200, 260):
                server.automation_pool(account).put_item(identifier, 'site', 0, sample_item())
        first = self.service.listing('pool', {})
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(first['total'], 122)
        for size, expected_pages in ((100, 2), (200, 1), (500, 1)):
            page = self.service.listing('pool', {'pageSize': [str(size)]})
            self.assertEqual(page['pageSize'], size)
            self.assertEqual(len(page['items']), min(size, 122))
            self.assertEqual(page['pages'], expected_pages)
        with self.assertRaisesRegex(ValueError, '每页条数'):
            self.service.listing('pool', {'pageSize': ['20']})
        self.assertEqual(len({r['key'] for r in self.service.pool_rows()}), 122)
        found = self.service.listing('pool', {'keyword': ['123']})['items']
        self.assertEqual({r['key'] for r in found}, {'1:123', '2:123'})
        self.assertNotIn('item', found[0]); self.assertNotIn('snapshot_json', found[0])

    def test_activity_includes_normal_edit_and_publish_queues(self):
        queued = server.automation_pool(1).queue_jobs([123], 7, 'US')[0]
        running = server.automation_pool(2).queue_jobs([123], 7, 'US')[0]
        server.automation_pool(2).start_next_queued_jobs(1)
        ready = server.automation_pool(1).queue_jobs([123], 8, 'US', dedupe_pairs=True)[0]
        server.automation_pool(1).update_job(ready['jobId'], 'save', 'ready_publish', '已保存')
        activity = self.service.activity()
        self.assertGreaterEqual(activity['editQueued'], 1)
        self.assertGreaterEqual(activity['editRunning'], 1)
        self.assertGreaterEqual(activity['pendingPublish'], 1)
        self.assertEqual(activity['queued'], activity['copyQueued'] + activity['editQueued'])
        self.assertEqual(activity['running'], activity['copyRunning'] + activity['editRunning'] + activity['publishing'])

    def test_slow_account_does_not_take_other_accounts_edit_slots(self):
        for account in (1, 2):
            server.automation_pool(account).queue_jobs([123], [7, 8, 9, 10], 'US')
        release = threading.Event()
        second_saved = threading.Event()
        failures = []
        def run(pool, job, *args, **kwargs):
            if pool.account_id == 1:
                if not release.wait(8):
                    failures.append('first account was not released')
            else:
                second_saved.set()
            pool.update_job(job, 'save', 'ready_publish', 'saved')
        with patch.object(server, 'run_automation_job', run):
            thread = threading.Thread(target=self.service.run_jobs, args=(4,))
            thread.start()
            try:
                self.assertTrue(second_saved.wait(5))
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(all(r['status'] == 'ready_publish' for r in self.service.job_rows()))

    def test_publish_workers_run_independently_and_keep_account_credentials(self):
        refs = []
        for account in (1, 2):
            pool = server.automation_pool(account)
            job = pool.queue_jobs([123], 7, 'US')[0]
            pool.update_job(job['jobId'], 'save', 'ready_publish', 'saved')
            refs.append({'accountId': account, 'jobId': job['jobId']})
        first_started, release, second_done = threading.Event(), threading.Event(), threading.Event()
        threads, errors = [], []
        def publish(pool, job, vendor):
            if pool.account_id == 1:
                first_started.set()
                if not release.wait(8):
                    errors.append('blocked account timed out')
            else:
                second_done.set()
            pool.update_job(job, 'publish', 'done', 'accepted')
            pool.update_job(job, 'complete', 'done', 'accepted')
        def thread_factory(**kwargs):
            thread = threading.Thread(**kwargs)
            threads.append(thread)
            return thread
        with patch.object(shared_automation, 'Thread', thread_factory), \
                patch.object(server, 'publish_automation_job', publish):
            try:
                self.service.publish({'confirmation': 'PUBLISH_SHARED_READY_JOBS',
                                      'jobs': refs, 'intervalSeconds': 1})
                self.assertTrue(first_started.wait(5))
                self.assertTrue(second_done.wait(5))
                self.assertFalse(release.is_set())
            finally:
                release.set()
                for thread in threads:
                    thread.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertFalse(server.RUNTIME.busy('publish_lock'))

    def test_both_pool_filters_and_activity_are_account_qualified(self):
        for account in (1, 2):
            for detail_id in (123, 124, 125):
                server.automation_pool(account).put_item(detail_id, 'site', 0, sample_item())
        rows = self.service.listing('pool', {'poolId': ['2']})
        self.assertEqual(rows['poolCount'], 3)
        # Staged-only rows use the rotating least-loaded assignment.  Verify
        # the filter against the physical pool rather than assuming a detail
        # ID always lands in a particular pool after prior fixtures ran.
        expected = {(account, detail_id) for account in (1, 2)
                    for detail_id in server.automation_pool(account).pools[1].item_ids()}
        self.assertEqual({(r['sourceAccountId'], r['detailId']) for r in rows['items']}, expected)
        pools = self.service.activity()['pools']
        self.assertTrue({(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)}.issubset(
            {(r['accountId'], r['poolId']) for r in pools}))

    def test_today_store_stats_count_successful_save_and_publish_once_per_job(self):
        job_id = 'd' * 32
        pool = server.automation_pool(1)
        pool.start_job(job_id, 123, 7, 'US', publish_after_save=False)
        pool.update_job(job_id, 'save', 'done', '已保存')
        pool.update_job(job_id, 'publish', 'ready_publish', '待发布')
        pool.update_job(job_id, 'publish', 'done', '已发布')
        pool.update_job(job_id, 'complete', 'done', '完成')
        stats = self.service.today_store_stats()
        row = next(row for row in stats['stores'] if row['accountId'] == 1 and row['shopId'] == 7)
        self.assertEqual((row['processed'], row['published']), (1, 1))

    def test_pool_rows_use_persisted_source_owner_when_snapshot_has_no_person(self):
        detail_id = 456
        raw = copy.deepcopy(self.remote[(1, 123)])
        raw.pop('subAppAccountId')
        server.automation_pool(1).put_item(detail_id, 'site', 0, raw)
        scope = self.service.scope(1)
        server.COLLECTION_PEOPLE.store.observe(scope, [
            {'collectBoxDetailId': detail_id, 'subAppAccountId': '251700'}])
        server.COLLECTION_PEOPLE.store.update_names(scope, [
            {'subAppAccountId': '251700', 'subAppAccountName': '本机缓存人员'}])
        row = next(row for row in self.service.pool_rows()
                   if row['sourceAccountId'] == 1 and row['detailId'] == detail_id)
        self.assertEqual(row['personId'], '251700')
        self.assertEqual(row['personName'], '本机缓存人员')

    def test_pool_rows_expose_miaoshou_collection_time(self):
        raw = copy.deepcopy(self.remote[(1, 123)])
        raw['gmtCreate'] = '2026-10-20 17:25:53'
        server.automation_pool(1).put_item(456, 'site', 0, raw)
        row = next(row for row in self.service.pool_rows()
                   if row['sourceAccountId'] == 1 and row['detailId'] == 456)
        self.assertEqual(row['collectedAt'], '2026-10-20 17:25:53')

    def test_pool_rows_keep_local_staged_time_when_miaoshou_omits_collection_time(self):
        raw = copy.deepcopy(self.remote[(1, 123)])
        raw.pop('gmtCreate', None)
        server.automation_pool(1).put_item(457, 'site', 0, raw)
        row = next(row for row in self.service.pool_rows()
                   if row['sourceAccountId'] == 1 and row['detailId'] == 457)
        self.assertEqual(row['collectedAt'], '')
        self.assertRegex(row['createdAt'], r'^\d{4}-\d{2}-\d{2}T')

    def test_cross_account_preview_accepts_500_and_rejects_501_items(self):
        for detail_id in range(1000, 1501):
            server.automation_pool(1).put_item(detail_id, 'site', 0, valid_item())
        request = self.request_data(targets=((2, 7),))
        request['items'] = [{'sourceAccountId': 1, 'detailId': detail_id} for detail_id in range(1000, 1500)]
        self.assertEqual(self.service.preview(request)['copyCount'], 500)
        request['items'].append({'sourceAccountId': 1, 'detailId': 1500})
        with self.assertRaisesRegex(ValueError, '1 到 500'):
            self.service.preview(request)

    def test_recover_requeues_database_lock_without_copy_reservation(self):
        _, rows = self.prepare()
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', 'database is locked')
        self.service.recover()
        with self.service.db() as connection:
            row = connection.execute('SELECT status,message FROM shared_transfers WHERE transfer_id=?',
                                     (rows[0]['transfer_id'],)).fetchone()
        self.assertEqual(row['status'], 'queued')
        self.assertIn('自动继续', row['message'])

    def test_recover_requeues_explicit_size_chart_validation_without_manual_id(self):
        _, rows = self.prepare()
        transfer = json.loads(rows[0]['payload'])
        with self.service.db() as connection:
            connection.execute('''INSERT INTO shared_copies VALUES (?,?,?,?,?,?,NULL,'needs_review',?,?)''',
                               ('size-chart-review', transfer['sourceAccountId'], transfer['sourceScope'], transfer['detailId'],
                                transfer['targetAccountId'], transfer['targetScope'], '{}',
                                'siteCollectItemInfo.sizeCharts[0].sizeItems[0].groupIdAndValueMap 必填'))
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', '副本创建结果待核查')
        self.service.recover()
        with self.service.db() as connection:
            row = connection.execute('SELECT status,message FROM shared_transfers WHERE transfer_id=?',
                                     (rows[0]['transfer_id'],)).fetchone()
        self.assertEqual(row['status'], 'queued')
        self.assertIn('尺码表分组字段', row['message'])

    def test_bidirectional_and_same_account_routes_use_correct_credentials(self):
        before = copy.deepcopy(self.remote)
        plan, rows = self.prepare((1, 2), ((1, 7), (2, 7), (2, 8)))
        self.assertEqual(plan['copyCount'], 2)
        self.assertEqual(plan['taskCount'], 6)
        for row in rows:
            self.service.prepare_transfer(row)
        creates = [call for call in self.calls if call[1] == server.HALF_CREATE]
        self.assertEqual(len(creates), 2)
        self.assertEqual({c[0] for c in creates}, {1, 2})
        for account, path, payload in creates:
            self.assertIn(f'Account {3-account}', payload['siteCollectItemInfo']['title'])
        self.assertEqual(self.remote[(1, 123)], before[(1, 123)])
        self.assertEqual(self.remote[(2, 123)], before[(2, 123)])
        self.assertEqual(len(self.service.job_rows()), 6)
        self.assertTrue(all(not server.automation_pool(r['accountId']).get_job(r['jobId'])['publishAfterSave'] for r in self.service.job_rows()))
        # Target execution snapshots do not duplicate the original shared pool rows.
        self.assertEqual(len(self.service.pool_rows()), 2)

    def test_task_and_transfer_rows_expose_original_queue_submission_time(self):
        plan, rows = self.prepare()
        queued = self.service.transfer_rows(self.service.person_resolver())
        self.assertEqual({row['transfer_id'] for row in queued}, {row['transfer_id'] for row in rows})
        self.assertTrue(all(isinstance(row['queuedAt'], float) for row in queued))
        with self.service.db() as connection:
            submitted = connection.execute('SELECT created FROM shared_batches WHERE batch_id=?',
                                           (plan['previewId'],)).fetchone()['created']
        self.assertTrue(all(row['queuedAt'] == submitted for row in queued))
        self.service.prepare_transfer(rows[0])
        jobs = self.service.job_rows()
        self.assertTrue(jobs)
        self.assertTrue(all(row['queuedAt'] == row['createdAt'] for row in jobs))

    def test_duplicate_submission_and_restart_reuse_copy(self):
        plan, rows = self.prepare()
        again = self.service.execute({'previewId': plan['previewId'], 'confirmation': 'SAVE_SHARED_AUTOMATION_BATCH'}, launch=False)
        self.assertTrue(again['duplicate'])
        self.service.prepare_transfer(rows[0])
        restarted = SharedAutomation(server, self.service.path)
        restarted.prepare_transfer(rows[0])
        self.assertEqual(sum(path == server.HALF_CREATE for _, path, _ in self.calls), 1)
        self.assertEqual(len(restarted.job_rows()), 2)
        plan = restarted.preview(self.request_data())
        self.assertEqual(plan['taskCount'], 0)
        self.assertEqual(len(plan['skipped']), 2)

    def test_pause_survives_restart_and_leaves_unclaimed_transfers_queued(self):
        _, rows = self.prepare()
        paused = self.service.set_paused({'paused': True, 'confirmation': 'SET_SHARED_AUTOMATION_PAUSE'})
        self.assertTrue(paused['paused'])
        with patch.object(self.service, 'prepare_transfer') as prepare:
            self.service.dispatch()
        prepare.assert_not_called()
        restarted = SharedAutomation(server, self.service.path)
        restarted.recover()
        with restarted.db() as connection:
            status = connection.execute('SELECT status FROM shared_transfers WHERE transfer_id=?',
                                        (rows[0]['transfer_id'],)).fetchone()['status']
        self.assertEqual(status, 'queued')
        with patch.object(shared_automation, 'Thread') as thread:
            resumed = restarted.set_paused({'paused': False, 'confirmation': 'SET_SHARED_AUTOMATION_PAUSE'})
        self.assertFalse(resumed['paused'])
        resume_calls = [call for call in thread.call_args_list
                        if call.kwargs.get('name') == 'shared-automation-resume']
        self.assertEqual(len(resume_calls), 1)

    def test_pause_acknowledges_committed_control_without_a_statistics_query(self):
        with patch.object(self.service, 'activity', side_effect=RuntimeError('busy statistics')):
            result=self.service.set_paused({'paused':True,'confirmation':'SET_SHARED_AUTOMATION_PAUSE'})
        self.assertTrue(result['paused'])
        self.assertTrue(self.service.is_paused())

    def test_recover_requeues_legacy_restart_record_without_copy_reservation(self):
        _, rows = self.prepare()
        transfer_id = rows[0]['transfer_id']
        # This models the older restart path: execution had been labelled for
        # review, but it never reached the reservation that precedes create.
        self.service._set_transfer(transfer_id, 'needs_review', '后台重启，请核查副本后重试')
        self.service.recover()
        with self.service.db() as connection:
            restored = connection.execute('SELECT status,message FROM shared_transfers WHERE transfer_id=?',
                                          (transfer_id,)).fetchone()
        self.assertEqual(restored['status'], 'queued')
        self.assertIn('确认未创建副本', restored['message'])

    def test_recover_keeps_restart_record_with_copy_reservation_for_manual_review(self):
        _, rows = self.prepare()
        transfer_id = rows[0]['transfer_id']
        transfer = json.loads(rows[0]['payload'])
        with self.service.db() as connection:
            connection.execute('''INSERT INTO shared_copies VALUES (?,?,?,?,?,?,NULL,'needs_review',NULL,?)''',
                               ('ambiguous-restart', transfer['sourceAccountId'], transfer['sourceScope'], transfer['detailId'],
                                transfer['targetAccountId'], transfer['targetScope'], 'timeout'))
        self.service._set_transfer(transfer_id, 'needs_review', '后台重启，请核查副本后重试')
        self.service.recover()
        with self.service.db() as connection:
            status = connection.execute('SELECT status FROM shared_transfers WHERE transfer_id=?',
                                        (transfer_id,)).fetchone()['status']
        self.assertEqual(status, 'needs_review')

    def test_dispatch_claims_transfer_before_preparing_remote_copy(self):
        _, rows = self.prepare()
        claimed = []
        def prepare(row):
            claimed.append(row['status'])
            self.service._set_transfer(row['transfer_id'], 'done', 'fixture')
        with patch.object(self.service, 'prepare_transfer', side_effect=prepare):
            self.service.dispatch()
        self.assertEqual(claimed, ['running'])

    def test_dispatch_prepares_two_independent_copies_for_one_target_account(self):
        item = valid_item(); item['title'] = 'Second source product'; item['subAppAccountId'] = '11'
        self.remote[(1, 124)] = item
        server.automation_pool(1).put_item(124, 'site', 0, item)
        request = self.request_data()
        request['items'].append({'sourceAccountId': 1, 'detailId': 124})
        plan = self.service.preview(request)
        self.service.execute({'previewId': plan['previewId'], 'confirmation': 'SAVE_SHARED_AUTOMATION_BATCH'}, launch=False)
        started, release = threading.Event(), threading.Event()
        active = maximum = 0
        guard = threading.Lock()

        def prepare(row):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    started.set()
            release.wait(2)
            with guard:
                active -= 1
            self.service._set_transfer(row['transfer_id'], 'done', 'fixture')

        dispatcher = threading.Thread(target=self.service.dispatch)
        with patch.object(self.service, 'prepare_transfer', side_effect=prepare):
            dispatcher.start()
            try:
                self.assertTrue(started.wait(1), '同一目标账号的第二件副本未并行准备')
            finally:
                release.set()
            dispatcher.join(3)
        self.assertFalse(dispatcher.is_alive())
        self.assertEqual(maximum, 2)

    def test_copy_preparation_limit_tracks_target_adaptive_capacity(self):
        gate = shared_automation.RUNTIME.lane(2).requests
        original = gate.limit
        self.addCleanup(setattr, gate, 'limit', original)
        gate.limit = 3
        self.assertEqual(copy_preparation_limit(2), 3)
        gate.limit = 99
        self.assertEqual(copy_preparation_limit(2), 4)
        gate.limit = 0
        self.assertEqual(copy_preparation_limit(2), 1)

    def test_manual_reload_only_starts_persisted_queued_work_once(self):
        self.prepare()
        class InlineThread:
            def __init__(self, *, target, **_):
                self.target = target
            def start(self):
                self.target()
        with patch.object(self.service, 'dispatch') as dispatch, patch.object(shared_automation, 'Thread', InlineThread):
            result = self.service.reload_queued({'confirmation': 'RELOAD_SHARED_QUEUE'})
        self.assertTrue(result['started'])
        self.assertEqual(result['sharedTransfers'], 1)
        dispatch.assert_called_once_with()
        with self.assertRaisesRegex(ValueError, '确认'):
            self.service.reload_queued({})
        self.service.set_paused({'paused': True, 'confirmation': 'SET_SHARED_AUTOMATION_PAUSE'})
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.service.reload_queued({'confirmation': 'RELOAD_SHARED_QUEUE'})

    def test_copy_preparation_notes_are_persisted_and_scoped_in_job_detail(self):
        _, rows = self.prepare()
        original = self.service._ensure_copy
        def prepare(transfer):
            transfer['preparationNotes'] = ['包含的组件按用户默认规则补全']
            return original(transfer)
        with patch.object(self.service, '_ensure_copy', side_effect=prepare):
            self.service.prepare_transfer(rows[0])
        job = self.service.job_rows()[0]
        detail = self.service.job(job['accountId'], job['jobId'])
        self.assertEqual(detail['preparationNotes'], ['包含的组件按用户默认规则补全'])
        with self.service.db() as db:
            payload = json.loads(db.execute('SELECT payload FROM shared_transfers WHERE transfer_id=?',
                                           (rows[0]['transfer_id'],)).fetchone()['payload'])
            payload['sourceScope'] = 'different-credentials'
            payload['preparationNotes'] = ['must not leak']
            db.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                       ('unrelated', rows[0]['batch_id'], json.dumps(payload), 'done', '', detail['detailId'], time.time()))
        self.assertEqual(self.service.job(job['accountId'], job['jobId'])['preparationNotes'],
                         ['包含的组件按用户默认规则补全'])

    def test_ambiguous_creation_never_retries_and_requires_matching_target(self):
        _, rows = self.prepare()
        self.ambiguous = True
        with self.assertRaisesRegex(ValueError, '待核查'):
            self.service.prepare_transfer(rows[0])
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', 'timeout')
        self.service.recover()
        with self.assertRaisesRegex(ValueError, '待核查'):
            self.service.prepare_transfer(rows[0])
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', 'timeout')
        self.assertEqual(sum(path == server.HALF_CREATE for _, path, _ in self.calls), 1)
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.service.reconcile({'transferId': rows[0]['transfer_id'], 'targetDetailId': 123, 'confirmation':'RECONCILE_SHARED_COPY'})
        with patch.object(shared_automation, 'Thread'):
            self.service.reconcile({'transferId': rows[0]['transfer_id'], 'targetDetailId': self.next_id, 'confirmation':'RECONCILE_SHARED_COPY'})
        self.assertEqual(self.service._copies()[0]['status'], 'ready')
        self.service.prepare_transfer(rows[0])
        self.assertEqual(len(self.service.job_rows()), 2)
        self.assertEqual(sum(path == server.HALF_CREATE for _, path, _ in self.calls), 1)

    def test_personnel_binding_is_source_scoped_and_legacy_is_preserved(self):
        for account in (1, 2):
            scope = self.service.scope(account)
            person = {'collectBoxDetailId': 123, 'subAppAccountId': '11'}
            server.COLLECTION_PEOPLE.store.observe(scope, [person])
            server.COLLECTION_PEOPLE._scans[scope] = {'status':'ready','rows':{'123':person},'started':time.monotonic(),'error':''}
        server.COLLECTION_PEOPLE.store.save_binding(self.service.scope(1), '11', [7], {7})
        self.assertEqual(self.service.binding(1, self.service.scope(1), '11')[0]['targetAccountId'], 1)
        self.service.save_binding({'sourceAccountId':2,'scope':self.service.scope(2),'personId':'11','targets':[{'targetAccountId':1,'shopId':8}]})
        plan = self.service.preview(self.request_data((1, 2), (), 'personnel'))
        targets = {(r['sourceAccountId'], r['targetAccountId'], tuple(r['shopIds'])) for r in plan['transfers']}
        self.assertEqual(targets, {(1,1,(7,)),(2,1,(8,))})
        self.service.save_binding({'sourceAccountId':1,'scope':self.service.scope(1),'personId':'11','targets':[]})
        self.assertEqual(self.service.binding(1,self.service.scope(1),'11'), [])
        self.assertEqual(server.COLLECTION_PEOPLE.store.bindings(self.service.scope(1))['11'], [7])

    def test_scope_change_blocks_copy_and_target_job_and_stale_preview(self):
        plan, rows = self.prepare()
        self.service.prepare_transfer(rows[0])
        job = self.service.job_rows()[0]
        with patch.dict(os.environ, {'MIAOSHOU_APP_SECRET':'rotated-secret'}):
            with self.assertRaisesRegex(ValueError, '凭据已变化'):
                self.service.assert_job(2, job['jobId'])
            with self.assertRaisesRegex(ValueError, '凭据已变化'):
                self.service.prepare_transfer(rows[0])

    def test_http_shared_scope_is_explicit_and_demo_cannot_write_remote(self):
        status, result = self.request('GET','/api/automation/shared/pool',headers={'X-Miaoshou-Account-Id':'2'})
        self.assertEqual(status, 200)
        self.assertEqual({r['sourceAccountId'] for r in result['data']['items']}, {1,2})
        plan = self.service.preview(self.request_data())
        self.calls.clear()
        with patch.dict(os.environ, {'APP_MODE':'demo'}):
            status, _ = self.request('POST','/api/automation/shared/run',{'previewId':plan['previewId'],'confirmation':'SAVE_SHARED_AUTOMATION_BATCH'})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_shared_scheduler_respects_total_concurrency_and_never_publishes(self):
        _, rows = self.prepare((1, 2), ((1,7),(2,7),(2,8)))
        for row in rows:
            self.service.prepare_transfer(row)
        import threading
        lock = threading.Lock(); active = 0; maximum = 0; seen = []
        def run(pool, job_id, vendor, get_item, readiness, ai, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1; maximum = max(maximum, active); seen.append(kwargs)
            try:
                pool.update_job(job_id, 'save', 'ready_publish', 'test saved')
            finally:
                with lock:
                    active -= 1
        with patch.object(server,'run_automation_job',run):
            self.service.run_jobs(2)
        self.assertLessEqual(maximum, 2)
        self.assertEqual(len(seen), 6)
        self.assertTrue(all(s['defer_publish'] for s in seen))
        self.assertFalse(any(path == server.HALF_PUBLISH for _,path,_ in self.calls))

    def test_shared_config_reuses_reads_across_workers_and_keeps_accounts_isolated(self):
        for account in (1, 2):
            for _ in range(5):
                result = self.service.worker(account)._vendor(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 0})
                rows = result['data']['shopIdAndFreightTemplateListMap']['7']
                self.assertEqual(rows[0]['templateId'], f'{account}-freight-7')
                rows.clear()
        self.assertEqual(sum(path == server.HALF_FREIGHT for _, path, _ in self.calls), 2)
        self.service.worker(1, cache_config=False)._vendor(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 0})
        self.assertEqual(sum(path == server.HALF_FREIGHT for _, path, _ in self.calls), 3)
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_KEY': 'changed'}):
            with self.assertRaisesRegex(ValueError, '凭据已变化'):
                self.service.worker(2)._vendor(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 0})

    def test_source_reuse_does_not_cache_confirmation_or_survive_remote_write(self):
        worker = self.service.worker(1)
        first = worker._get_source_item(123, 'site')
        worker._get_source_item(123, 'site')
        self.assertEqual(sum(path == server.HALF_SITE_DETAIL for _, path, _ in self.calls), 1)
        # Confirmation reads must always reach the remote endpoint.
        worker._get_item(123, 'site')
        self.assertEqual(sum(path == server.HALF_SITE_DETAIL for _, path, _ in self.calls), 2)
        self.remote[(1, 123)]['title'] = 'updated remotely'
        worker._vendor(server.HALF_SITE_SAVE, {'detailId': 123, 'siteCollectItemInfo': first})
        self.assertEqual(worker._get_source_item(123, 'site')['title'], 'updated remotely')
        self.assertEqual(sum(path == server.HALF_SITE_DETAIL for _, path, _ in self.calls), 3)

    def test_pipeline_saves_first_product_before_second_copy_finishes(self):
        self.prepare((1,), ((1, 7), (2, 7)))
        saved = threading.Event()
        original = self.service.prepare_transfer
        prepared = []
        overlap = []
        first_prepared = threading.Event()
        def prepare(row):
            second = json.loads(row['payload'])['targetAccountId'] == 2
            if second:
                self.assertTrue(first_prepared.wait(4))
                overlap.append(saved.wait(4))
            original(row)
            prepared.append(row['transfer_id'])
            if not second:
                first_prepared.set()
        def run(pool, job_id, vendor, get_item, readiness, ai, **kwargs):
            with self.service.db() as connection:
                origin = connection.execute('SELECT * FROM shared_job_origins WHERE job_id=?', (job_id,)).fetchone()
            self.assertIsNotNone(origin)
            self.assertTrue(kwargs['defer_publish'])
            pool.update_job(job_id, 'save', 'ready_publish', 'fixture saved')
            saved.set()
        with patch.object(self.service, 'prepare_transfer', side_effect=prepare), patch.object(server, 'run_automation_job', side_effect=run):
            self.service.dispatch()
        self.assertEqual(overlap, [True])
        self.assertEqual(len(prepared), 2)
        jobs = self.service.job_rows()
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job['status'] == 'ready_publish' for job in jobs))
        self.assertFalse(any(path == server.HALF_PUBLISH for _, path, _ in self.calls))

    def test_real_shared_save_workflow_uses_cached_configuration_and_stops_at_queue(self):
        from test_automation_workflow import FakeAI
        _, rows = self.prepare((1,), ((1, 7), (1, 8)))
        self.remote[(1, 123)]['collectBoxDetailShopList'] = [{'shopId': 7}, {'shopId': 8}]
        for row in rows:
            self.service.prepare_transfer(row)
        self.calls.clear()
        with patch.object(server, 'DeepSeekClient', return_value=FakeAI()):
            self.service.run_jobs(2)
        jobs = self.service.job_rows()
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job['status'] == 'ready_publish' for job in jobs),
                        [(job['status'], job.get('message')) for job in jobs])
        self.assertEqual(sum(path == server.HALF_SHOP_SAVE for _, path, _ in self.calls), 2)
        self.assertLessEqual(sum(path == server.SHOP_LIST for _, path, _ in self.calls), 1)
        self.assertEqual(sum(path == server.HALF_CATEGORY_ATTRIBUTE_RULES for _, path, _ in self.calls), 1)
        self.assertFalse(any(path == server.HALF_PUBLISH for _, path, _ in self.calls))

    def test_source_cannot_be_removed_while_cross_account_jobs_exist(self):
        _, rows = self.prepare()
        self.service.prepare_transfer(rows[0])
        result = self.service.remove_items([{'sourceAccountId':1,'detailId':123}])
        self.assertFalse(result['removed']); self.assertTrue(result['errors'])

    def test_concurrent_copy_reservation_is_atomic_across_service_instances(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        _, rows = self.prepare()
        transfer = json.loads(rows[0]['payload'])
        other = SharedAutomation(server, self.service.path)
        barrier = threading.Barrier(2)
        original = shared_automation.create_copy_payload
        def synchronized(*args):
            result = original(*args); barrier.wait(timeout=10); return result
        def create(service):
            try:
                return service._ensure_copy(transfer)
            except ValueError as error:
                return str(error)
        with patch.object(shared_automation, 'create_copy_payload', synchronized), ThreadPoolExecutor(2) as executor:
            results = list(executor.map(create, [self.service, other]))
        self.assertEqual(sum(path == server.HALF_CREATE for _, path, _ in self.calls), 1)
        self.assertTrue(any(type(value) is int for value in results), results)
        self.assertEqual(self.service._copies()[0]['status'], 'ready')

    def test_new_shop_reuses_copy_and_deleted_published_history_still_deduplicates(self):
        _, rows = self.prepare(targets=((2,7),))
        self.service.prepare_transfer(rows[0])
        _, more = self.prepare(targets=((2,8),))
        self.service.prepare_transfer(more[0])
        self.assertEqual(len(self.service.job_rows()), 2)
        job = self.service.job_rows()[0]; pool = server.automation_pool(2)
        pool.update_job(job['jobId'], 'complete', 'done', 'published')
        pool.delete_job_records([job['jobId']])
        self.assertEqual(self.service.preview(self.request_data())['taskCount'], 0)
        self.assertEqual(sum(path == server.HALF_CREATE for _, path, _ in self.calls), 1)

    def test_copy_provenance_survives_missing_origin_record(self):
        _, rows = self.prepare(); self.service.prepare_transfer(rows[0])
        with self.service.db() as connection:
            connection.execute('DELETE FROM shared_job_origins')
        jobs = self.service.job_rows()
        self.assertTrue(all(j['sourceAccountId'] == 1 and j['sourceDetailId'] == 123 for j in jobs))
        with patch.dict(os.environ, {'MIAOSHOU_APP_SECRET':'rotated-secret'}):
            with self.assertRaisesRegex(ValueError, '凭据已变化'):
                self.service.assert_job(2, jobs[0]['jobId'])

    def test_success_without_id_is_ambiguous_and_recovery_does_not_create(self):
        _, rows = self.prepare()
        original = server.MiaoShouClient.post
        def missing(client, path, payload):
            result = original(client, path, payload)
            if path == server.HALF_CREATE:
                result['data'] = {}
            return result
        with patch.object(server.MiaoShouClient, 'post', missing):
            with self.assertRaisesRegex(ValueError, '待核查'):
                self.service.prepare_transfer(rows[0])
        before = len(self.calls)
        SharedAutomation(server, self.service.path).recover()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.service._copies()[0]['status'], 'needs_review')

    def test_invalid_account_does_not_prevent_other_target_preview(self):
        request = self.request_data(targets=((1,7),(2,7)))
        self.service.accounts()
        with patch.dict(os.environ, {'MIAOSHOU_ACCOUNT_2_APP_SECRET':'changed'}):
            preview = self.service.preview(request)
        self.assertEqual(preview['taskCount'], 1)
        self.assertEqual(preview['transfers'][0]['targetAccountId'], 1)
        self.assertIn('凭据已变化', preview['skipped'][0]['reason'])

    def test_publish_account_failure_isolated_and_requires_separate_confirmation(self):
        _, rows = self.prepare((1,2), ((1,7),(2,7)))
        for row in rows:
            self.service.prepare_transfer(row)
        jobs = self.service.job_rows()
        for job in jobs:
            server.automation_pool(job['accountId']).update_job(job['jobId'], 'publish', 'ready_publish', 'saved')
        refs = [{'accountId':j['accountId'],'jobId':j['jobId']} for j in jobs]
        with self.assertRaisesRegex(ValueError, '确认'):
            self.service.publish({'jobs':refs})
        with patch.object(shared_automation, 'Thread') as thread:
            self.service.publish({'jobs':refs,'confirmation':'PUBLISH_SHARED_READY_JOBS','intervalSeconds':1})
            calls = [call.kwargs['args'] for call in thread.call_args_list]
        seen = []
        def publish(pool, job_id, vendor):
            seen.append(job_id)
            if pool.path == server.automation_pool(1).path:
                raise RuntimeError('account one rejected request')
            pool.update_job(job_id, 'complete', 'done', 'accepted')
        with patch.object(server, 'publish_automation_job', publish):
            for args in calls:
                self.service._publish_worker(*args)
        self.assertEqual(len(seen), 4)
        self.assertFalse(server._automation_publish_lock.locked())
        for job in self.service.job_rows():
            self.assertEqual(job['status'], 'needs_review' if job['accountId']==1 else 'done')

    def test_invalid_attachment_fails_before_create_but_other_account_can_continue(self):
        plan, rows = self.prepare((1,2), ((1,7),(2,7)))
        original = shared_automation.create_copy_payload
        def invalid(raw, *args):
            if raw['title'] == 'Account 1 product':
                raise ValueError('说明书附件不可复用，请在目标账号重新上传')
            return original(raw, *args)
        with patch.object(shared_automation, 'create_copy_payload', invalid), patch.object(self.service, 'run_jobs'):
            self.service.dispatch()
        with self.service.db() as connection:
            statuses = [r[0] for r in connection.execute('SELECT status FROM shared_transfers')]
        self.assertEqual(statuses.count('needs_review'), 1)
        self.assertEqual(statuses.count('done'), 3)
        self.assertEqual(sum(path == server.HALF_CREATE for _,path,_ in self.calls), 1)

    def test_delete_pending_records_is_account_scoped_local_and_idempotent(self):
        job_id = 'a' * 32
        for account in (1,2):
            pool = server.automation_pool(account)
            pool.start_job(job_id,123,7,'US')
            pool.pause_for_problem(job_id,'prepare','awaiting valid product')
        body = {'jobs':[{'accountId':2,'jobId':job_id}],'confirmation':'DELETE_SHARED_JOB_RECORDS'}
        self.calls.clear()
        with patch.dict(os.environ,{'APP_MODE':'demo'}):
            status, result = self.request('POST','/api/automation/shared/delete-records',body)
            self.assertEqual(status,200,result)
            self.assertEqual(result['data']['deleted'],body['jobs'])
            self.assertEqual(self.service.delete_records(body)['deleted'],body['jobs'])
        self.assertEqual(self.calls,[])
        self.assertEqual(server.automation_pool(1).get_job(job_id)['status'],'needs_input')
        pool = server.automation_pool(2)
        self.assertIsNone(pool.get_job(job_id))
        self.assertIsNotNone(pool.get_item(123))
        from contextlib import closing
        with closing(pool._connect()) as db:
            self.assertEqual(db.execute('SELECT status FROM automation_jobs WHERE job_id=?',(job_id,)).fetchone()[0],'canceled')
            self.assertEqual(db.execute("SELECT count(*) FROM automation_job_events WHERE job_id=? AND step='canceled'",(job_id,)).fetchone()[0],1)

    def test_batch_delete_skips_running_and_ready_publish_but_cancels_queued(self):
        pool = server.automation_pool(1)
        jobs = pool.queue_jobs([123],[7,8,9,10],'US')
        for job,status in zip(jobs,['queued','needs_input','running','ready_publish']):
            if status == 'needs_input':
                pool.pause_for_problem(job['jobId'],'prepare','fixture')
            elif status != 'queued':
                pool.update_job(job['jobId'],'prepare',status,'fixture')
        refs = [{'accountId':1,'jobId':j['jobId']} for j in jobs]
        result = self.service.delete_records({'jobs':refs,'confirmation':'DELETE_SHARED_JOB_RECORDS'})
        self.assertEqual(result['deleted'],refs[:2])
        self.assertEqual([r['jobId'] for r in result['errors']],[j['jobId'] for j in jobs[2:]])
        self.assertEqual(pool.get_job(jobs[2]['jobId'])['status'],'running')
        self.assertEqual(pool.get_job(jobs[3]['jobId'])['status'],'ready_publish')
        self.assertFalse(pool.has_queued_jobs())

    def test_batch_delete_can_clear_review_transfer_without_erasing_copy_evidence(self):
        _, transfers = self.prepare(targets=((2, 7),))
        transfer_id = transfers[0]['transfer_id']
        self.service._set_transfer(transfer_id, 'needs_review', 'fixture requires verification')
        copies = self.service._copies()
        result = self.service.delete_records({'jobs': [{'transferId': transfer_id}],
                                              'confirmation': 'DELETE_SHARED_JOB_RECORDS'})
        self.assertEqual(result, {'deleted': [{'transferId': transfer_id}], 'errors': []})
        with self.service.db() as connection:
            row = connection.execute('SELECT status,message FROM shared_transfers WHERE transfer_id=?',
                                     (transfer_id,)).fetchone()
        self.assertEqual(row['status'], 'discarded')
        self.assertEqual(row['message'], 'fixture requires verification')
        self.assertEqual(self.service._copies(), copies)

    def test_delete_validates_all_references_and_confirmation_before_mutating(self):
        pool = server.automation_pool(1)
        job = pool.queue_jobs([123],7,'US')[0]
        ref = {'accountId':1,'jobId':job['jobId']}
        for body in ({'jobs':[ref]}, {'jobs':[ref,{'accountId':2,'jobId':'bad'}],'confirmation':'DELETE_SHARED_JOB_RECORDS'},
                     {'jobs':[ref]*501,'confirmation':'DELETE_SHARED_JOB_RECORDS'}):
            with self.assertRaises(ValueError):
                self.service.delete_records(body)
        self.assertEqual(pool.get_job(job['jobId'])['status'],'queued')
        status,_ = self.request('POST','/api/automation/shared/delete-records',
            {'jobs':[ref],'confirmation':'DELETE_SHARED_JOB_RECORDS'},headers={'Origin':'https://foreign.example'})
        self.assertEqual(status,403)

    def test_deleting_finished_cross_account_record_preserves_copy_and_publication_dedupe(self):
        _,rows = self.prepare(targets=((2,7),)); self.service.prepare_transfer(rows[0])
        job = self.service.job_rows()[0]
        server.automation_pool(2).update_job(job['jobId'],'complete','done','published')
        copies = self.service._copies(); self.calls.clear()
        self.service.delete_records({'jobs':[{'accountId':2,'jobId':job['jobId']}],'confirmation':'DELETE_SHARED_JOB_RECORDS'})
        self.assertEqual(self.service._copies(),copies)
        self.assertFalse(self.service.job_rows())
        self.assertEqual(self.service.preview(self.request_data(targets=((2,7),)))['taskCount'],0)
        self.assertFalse(any(path in {server.HALF_CREATE,server.HALF_SHOP_SAVE,server.HALF_PUBLISH} for _,path,_ in self.calls))

    def test_pending_deletion_checks_status_and_rolls_back_atomically(self):
        pool = server.automation_pool(1); jobs = pool.queue_jobs([123],[7,8],'US')
        pool.update_job(jobs[1]['jobId'],'save','running','worker won race')
        with self.assertRaisesRegex(ValueError,'运行中'):
            pool.delete_job_records([j['jobId'] for j in jobs],allow_pending=True)
        self.assertEqual(pool.get_job(jobs[0]['jobId'])['status'],'queued')
        self.assertEqual(pool.get_job(jobs[1]['jobId'])['status'],'running')

    def test_task_groups_counts_filters_and_pagination_are_consistent(self):
        pool = server.automation_pool(1)
        statuses = ['queued','running','needs_input','failed','needs_review','interrupted','ready_publish','done','canceled']
        jobs = pool.queue_jobs([123],list(range(10,10+len(statuses))),'US')
        for job,status in zip(jobs,statuses):
            if status == 'needs_input':
                pool.pause_for_problem(job['jobId'],'prepare','missing data')
            elif status in {'interrupted','canceled'}:
                with closing(pool._connect()) as db, db:
                    db.execute('UPDATE automation_jobs SET status=? WHERE job_id=?',(status,job['jobId']))
            elif status != 'queued':
                pool.update_job(job['jobId'],'complete' if status == 'done' else 'prepare',status,'fixture')
        server.automation_pool(2).queue_jobs([123],7,'US')
        self.calls.clear()
        expected = {'processing':2,'attention':4,'publish':1,'history':2}
        for group,count in expected.items():
            data = self.service.listing('jobs',{'accountId':['1'],'taskGroup':[group]})
            self.assertEqual(data['counts'],expected)
            self.assertEqual(data['total'],count)
            self.assertEqual(data['totalAll'],10)
        narrow = self.service.listing('jobs',{'accountId':['1'],'taskGroup':['attention'],'status':['needs_input']})
        self.assertEqual(narrow['total'],1); self.assertEqual(narrow['counts'],expected)
        self.assertEqual(self.service.listing('queue',{})['total'],1)
        self.assertEqual(self.service.listing('jobs',{'keyword':['no match']})['counts'],dict.fromkeys(expected,0))
        with self.assertRaises(ValueError): self.service.listing('jobs',{'taskGroup':['invalid']})
        self.assertEqual(self.calls,[])

    def test_copy_processing_counts_are_separate_and_queue_excludes_copies(self):
        _, rows = self.prepare()
        processing = self.service.listing('jobs',{'taskGroup':['processing']})
        self.assertEqual(processing['transferCounts']['processing'],1)
        self.assertEqual(len(processing['transfers']),1)
        self.assertFalse(self.service.listing('queue',{})['transfers'])
        self.assertFalse(self.service.listing('jobs',{'taskGroup':['attention']})['transfers'])
        with self.service.db() as db:
            db.execute("UPDATE shared_transfers SET status='needs_review'")
        data = self.service.listing('jobs',{'taskGroup':['attention'],'accountId':['2']})
        self.assertEqual(data['transferCounts']['attention'],1)
        self.assertEqual(len(data['transfers']),1)
        self.assertFalse(self.service.listing('jobs',{'taskGroup':['attention'],'accountId':['1']})['transfers'])

    def test_group_pagination_retains_fifty_row_limit_and_excludes_deleted(self):
        pool = server.automation_pool(1)
        jobs = pool.queue_jobs([123],list(range(1,62)),'US')
        first = self.service.listing('jobs',{'taskGroup':['processing']})
        self.assertEqual(len(first['jobs']),50); self.assertEqual(first['counts']['processing'],61)
        second = self.service.listing('jobs',{'taskGroup':['processing'],'page':['2']})
        self.assertEqual(len(second['jobs']),11)
        self.assertFalse({r['key'] for r in first['jobs']} & {r['key'] for r in second['jobs']})
        pool.delete_job_records([jobs[0]['jobId']],allow_pending=True)
        data = self.service.listing('jobs',{'taskGroup':['processing']})
        self.assertEqual(data['totalAll'],60); self.assertEqual(data['counts']['processing'],60)


    def test_shop_pending_counts_cover_all_pages_accounts_and_ignore_filters(self):
        pool = server.automation_pool(1)
        ids = list(range(200,261))
        for detail in ids:
            pool.put_item(detail,'site',0,sample_item())
        jobs = pool.queue_jobs(ids[:50],7,'US') + pool.queue_jobs(ids[50:],7,'US') + pool.queue_jobs([123],8,'US')
        for job in jobs:
            pool.update_job(job['jobId'],'save','ready_publish','saved')
        other = server.automation_pool(2)
        job = other.queue_jobs([123],7,'US')[0]
        other.update_job(job['jobId'],'save','ready_publish','saved')
        self.calls.clear()
        expected = [{'accountId':1,'shopId':7,'count':61},
                    {'accountId':1,'shopId':8,'count':1},{'accountId':2,'shopId':7,'count':1}]
        for endpoint, query in [('queue',''),('queue','?page=2'),
                                ('jobs','?taskGroup=attention&keyword=absent&sourceAccountId=2&accountId=2&shopId=7&status=failed')]:
            status, body = self.request('GET','/api/automation/shared/'+endpoint+query)
            self.assertEqual(status,200,body)
            self.assertEqual(body['data']['shopPendingCounts'],expected)
            self.assertLessEqual(len(body['data']['jobs']),50)
        self.assertEqual(self.calls,[])
        self.assertNotIn('shopPendingCounts',self.service.listing('pool',{}))

    def test_shop_pending_counts_exclude_hidden_and_follow_publish_and_remove(self):
        pool = server.automation_pool(1)
        jobs = pool.queue_jobs([123],[7,8,9,10,11,12],'US')
        for job in jobs:
            pool.update_job(job['jobId'],'save','ready_publish','saved')
        pool.update_job(jobs[3]['jobId'],'save','failed','fixture')
        pool.update_job(jobs[4]['jobId'],'complete','done','fixture')
        pool.remove_publish_queue_jobs([jobs[5]['jobId']])
        # A legacy hidden ready row must never be included in published totals.
        with closing(pool._connect()) as db, db:
            db.execute('INSERT INTO automation_job_deletions (job_id,deleted_at) VALUES (?,?)',(jobs[2]['jobId'],'fixture'))
        self.calls.clear()
        def counts(): return self.service.listing('queue',{})['shopPendingCounts']
        self.assertEqual(counts(),[{'accountId':1,'shopId':7,'count':1},{'accountId':1,'shopId':8,'count':1}])
        batch = pool.begin_publish_batch([jobs[0]['jobId']],1)
        self.assertEqual(counts(),[{'accountId':1,'shopId':8,'count':1}])
        pool.start_next_ready_publish_job(batch)
        self.assertEqual(counts(),[{'accountId':1,'shopId':8,'count':1}])
        pool.update_job(jobs[0]['jobId'],'complete','done','submitted')
        pool.remove_publish_queue_jobs([jobs[1]['jobId']])
        self.assertEqual(counts(),[])
        self.assertEqual(self.calls,[])


    def ready_fixture(self, account=1, count=1, shop=7, start=200):
        pool = server.automation_pool(account)
        ids = list(range(start, start + count))
        for detail in ids:
            item = sample_item(); item['title'] = f'Fixture {account} glove {detail}'
            pool.put_item(detail, 'site', 0, item)
        jobs = []
        for offset in range(0, len(ids), 50):
            jobs.extend(pool.queue_jobs(ids[offset:offset+50], shop, 'US'))
        for job in jobs:
            pool.update_job(job['jobId'], 'save', 'ready_publish', 'saved')
        return jobs

    def test_publish_quantity_preview_crosses_pages_respects_filters_and_never_calls_vendor(self):
        self.ready_fixture(count=61)
        self.ready_fixture(account=2, count=3)
        self.ready_fixture(shop=8, count=2, start=800)
        filters = {'accountId':'1', 'shopId':'7', 'sourceAccountId':'1', 'keyword':'glove', 'status':'ready_publish'}
        self.calls.clear()
        with patch.dict(os.environ, {'APP_MODE':'demo'}):
            status, result = self.request('POST', '/api/automation/shared/publish-preview',
                {'publishCount':55, 'intervalSeconds':2, 'filters':filters})
            self.assertEqual(status, 200, result)
            plan = result['data']
            self.assertEqual((plan['count'],plan['total'],len(plan['sample'])), (55,61,50))
            self.assertEqual(plan['shops'], [{'accountId':1,'shopId':7,'count':55}])
            queue = self.service.listing('queue', {key:[value] for key,value in filters.items()})
            self.assertEqual([r['key'] for r in plan['sample']], [r['key'] for r in queue['jobs']])
            with self.assertRaisesRegex(ValueError, '只读'):
                self.service.publish({'previewId':plan['previewId'], 'confirmation':'PUBLISH_SHARED_READY_JOBS'})
        self.assertEqual(self.calls, [])

    def test_publish_quantity_rejects_invalid_input_and_reports_actual_remaining(self):
        self.ready_fixture()
        self.ready_fixture(account=2)
        for count in (0,-1,True,1.5,'5',None):
            with self.assertRaises(ValueError):
                self.service.publish_preview({'publishCount':count})
        for filters in ({'shopId':'7'}, {'accountId':'oops'}, {'status':'done'}, {'page':'2'}, {'keyword':False}):
            with self.assertRaises(ValueError):
                self.service.publish_preview({'publishCount':5,'filters':filters})
        with self.assertRaises(ValueError):
            self.service.publish_preview({'publishCount':5,'intervalSeconds':1.1})
        with self.assertRaisesRegex(ValueError,'没有待发布'):
            self.service.publish_preview({'publishCount':5,'filters':{'keyword':'absent'}})
        plan = self.service.publish_preview({'publishCount':209})
        self.assertEqual((plan['requestedCount'],plan['count'],plan['total']), (209,2,2))
        self.assertEqual({(r['accountId'],r['shopId'],r['count']) for r in plan['shops']}, {(1,7,1),(2,7,1)})
        unlimited = self.service.publish_preview({'publishCount':1170})
        self.assertEqual((unlimited['requestedCount'], unlimited['count'], unlimited['total']), (1170,2,2))

    def test_publish_quantity_freezes_ids_across_restart_and_rejects_duplicate_release(self):
        self.ready_fixture(count=55)
        plan = self.service.publish_preview({'publishCount':55,'intervalSeconds':1})
        expected = {r['jobId'] for r in self.service.job_rows()}
        self.ready_fixture(start=900)
        service = SharedAutomation(server, self.service.path)
        body = {'previewId':plan['previewId'], 'confirmation':'PUBLISH_SHARED_READY_JOBS', 'publishCount':999}
        with patch.object(shared_automation, 'Thread') as thread:
            result = service.publish(body)
        self.assertEqual(result['submitted'],55)
        try:
            self.assertEqual(service.publish(body),result)
            args = thread.call_args.kwargs['args']
            seen = []
            def published(pool, job, vendor):
                seen.append(job);pool.update_job(job,'complete','done','accepted')
            with patch.object(server,'publish_automation_job',published):
                service._publish_worker(*args)
            self.assertEqual(set(seen),expected)
            self.assertEqual(len(seen),55)
            self.assertEqual(service.publish(body),result)
            self.assertEqual(service.listing('queue',{})['total'],1)
        finally:
            if server._automation_publish_lock.locked():server._automation_publish_lock.release()
        self.assertEqual(self.calls,[])

    def test_publish_during_active_run_appends_to_the_existing_batch(self):
        jobs = self.ready_fixture(count=2)
        first = {'confirmation':'PUBLISH_SHARED_READY_JOBS', 'intervalSeconds':1,
                 'jobs':[{'accountId':1, 'jobId':jobs[0]['jobId']}]}
        second = {'confirmation':'PUBLISH_SHARED_READY_JOBS', 'intervalSeconds':2,
                  'jobs':[{'accountId':1, 'jobId':jobs[1]['jobId']}]}
        pool = server.automation_pool(1)
        with patch.object(shared_automation, 'Thread') as thread:
            self.assertEqual(self.service.publish(first), {'submitted':1, 'joinedExisting':False})
            self.assertEqual(self.service.publish(second), {'submitted':1, 'joinedExisting':True})
            progress = pool.publish_progress()
            self.assertEqual((progress['status'], progress['total'], progress['intervalSeconds']), ('running', 2, 2))
            seen = []
            def published(pool, job, vendor):
                seen.append(job); pool.update_job(job, 'complete', 'done', 'accepted')
            args = thread.call_args.kwargs['args']
            with patch.object(server, 'publish_automation_job', published):
                self.service._publish_worker(*args)
        self.assertEqual(set(seen), {job['jobId'] for job in jobs})
        self.assertFalse(server._automation_publish_lock.locked())

    def test_publish_preview_excludes_released_jobs_without_releasing_new_arrivals(self):
        jobs = self.ready_fixture(count=3)
        pool = server.automation_pool(1)
        pool.begin_publish_batch([jobs[0]['jobId'], jobs[1]['jobId']], 10)
        plan = self.service.publish_preview({'publishCount':99})
        self.assertEqual((plan['count'], plan['total']), (1, 1))
        self.assertEqual(plan['sample'][0]['jobId'], jobs[2]['jobId'])
        self.assertEqual(pool.publish_progress()['total'], 2)
        self.assertEqual(self.calls, [])

    def test_publish_duplicate_rejected_before_another_account_or_interval_changes(self):
        first = self.ready_fixture(account=1)
        second = self.ready_fixture(account=2)
        pool = server.automation_pool(2)
        pool.begin_publish_batch([second[0]['jobId']], 10)
        with patch.object(shared_automation, 'Thread') as thread:
            with self.assertRaisesRegex(ValueError, '已在发布队列中'):
                self.service.publish({'confirmation':'PUBLISH_SHARED_READY_JOBS', 'intervalSeconds':1,
                    'jobs':[{'accountId':1,'jobId':first[0]['jobId']},
                            {'accountId':2,'jobId':second[0]['jobId']}]})
            thread.assert_not_called()
        self.assertIsNone(server.automation_pool(1).publish_progress())
        self.assertEqual(pool.publish_progress()['intervalSeconds'], 10)
        self.assertEqual(self.calls, [])

    def test_target_guard_uses_indexed_mapping_without_reading_expected_payloads(self):
        with patch.object(self.service, '_copies', side_effect=AssertionError('full history scan')):
            self.service.guard_target(1, 123)

    def test_publish_quantity_fails_closed_on_changed_deleted_expired_or_changed_credentials(self):
        jobs = self.ready_fixture()
        for change in ('status','hidden','expired','credentials'):
            with self.subTest(change=change):
                plan = self.service.publish_preview({'publishCount':1})
                pool = server.automation_pool(1)
                if change == 'status':pool.update_job(jobs[0]['jobId'],'save','failed','fixture')
                if change == 'hidden':
                    with closing(pool._job_pool(jobs[0]['jobId'])._connect()) as db, db:
                        db.execute('INSERT INTO automation_job_deletions VALUES (?,?)',(jobs[0]['jobId'],'fixture'))
                if change == 'expired':
                    with self.service.db() as db:
                        db.execute('UPDATE shared_publish_previews SET created=0 WHERE preview_id=?',(plan['previewId'],))
                with patch.dict(os.environ, {'MIAOSHOU_APP_SECRET':'changed'} if change=='credentials' else {}), \
                        patch.object(shared_automation,'Thread') as thread:
                    with self.assertRaises(ValueError):
                        self.service.publish({'previewId':plan['previewId'],'confirmation':'PUBLISH_SHARED_READY_JOBS'})
                    thread.assert_not_called()
                self.assertFalse(server._automation_publish_lock.locked())
                pool.update_job(jobs[0]['jobId'],'save','ready_publish','saved')
                with closing(pool._job_pool(jobs[0]['jobId'])._connect()) as db, db:db.execute('DELETE FROM automation_job_deletions')
        self.assertEqual(self.calls,[])

    def test_multi_shop_publish_preview_qualifies_accounts_and_keeps_other_filters(self):
        self.ready_fixture(count=2)
        self.ready_fixture(account=2,count=2)
        self.ready_fixture(account=1,shop=8,start=800)
        self.ready_fixture(account=2,shop=9,start=900)
        shops=[{'accountId':1,'shopId':7},{'accountId':2,'shopId':7},{'accountId':2,'shopId':99}]
        plan=self.service.publish_preview({'publishCount':3,'filters':{'shops':shops,'keyword':'glove'}})
        self.assertEqual((plan['count'],plan['total']), (3,4))
        self.assertEqual(sum(r['count'] for r in plan['shops']),3)
        self.assertEqual({(r['accountId'],r['shopId']) for r in plan['shops']}, {(1,7),(2,7),(2,99)})
        self.assertTrue(all(r['shopId']==7 for r in plan['sample']))
        source=self.service.publish_preview({'publishCount':99,'filters':{'shops':shops,'sourceAccountId':'2','keyword':'glove'}})
        self.assertEqual(source['count'],2)
        self.assertTrue(all(r['accountId']==2 for r in source['sample']))
        for invalid in ([],[{'shopId':7}],[{'accountId':True,'shopId':7}],shops*34):
            with self.assertRaises(ValueError):self.service.publish_preview({'publishCount':3,'filters':{'shops':invalid}})
        with self.assertRaises(ValueError):
            self.service.publish_preview({'publishCount':3,'filters':{'shops':shops,'accountId':'1'}})
        self.assertEqual(self.calls,[])

    def test_multi_shop_publish_preview_uses_each_shop_quantity_independently(self):
        self.ready_fixture(count=5)
        self.ready_fixture(account=2,count=4)
        self.ready_fixture(shop=8,count=3,start=800)
        shop_counts = [
            {'accountId':1,'shopId':7,'publishCount':3},
            {'accountId':2,'shopId':7,'publishCount':2},
            {'accountId':1,'shopId':8,'publishCount':9},
        ]
        plan = self.service.publish_preview({
            'publishCount':14, 'intervalSeconds':1,
            'filters':{'keyword':'glove'}, 'shopCounts':shop_counts,
        })
        self.assertTrue(plan['perShopCounts'])
        self.assertEqual((plan['requestedCount'], plan['count'], plan['total']), (14, 8, 12))
        self.assertEqual(plan['shops'], [
            {'accountId':1,'shopId':7,'count':3,'requestedCount':3,'availableCount':5},
            {'accountId':2,'shopId':7,'count':2,'requestedCount':2,'availableCount':4},
            {'accountId':1,'shopId':8,'count':3,'requestedCount':9,'availableCount':3},
        ])
        self.assertEqual({(row['accountId'],row['shopId']) for row in plan['sample']}, {(1,7),(2,7),(1,8)})
        unlimited = self.service.publish_preview({
            'publishCount':1170, 'intervalSeconds':1, 'filters':{'keyword':'glove'},
            'shopCounts':[
                {'accountId':1,'shopId':7,'publishCount':500},
                {'accountId':2,'shopId':7,'publishCount':400},
                {'accountId':1,'shopId':8,'publishCount':270},
            ],
        })
        self.assertEqual((unlimited['requestedCount'], unlimited['count'], unlimited['total']), (1170,12,12))
        for invalid in (
            [{'accountId':1,'shopId':7,'publishCount':0}],
            [{'accountId':1,'shopId':7,'publishCount':1},{'accountId':1,'shopId':7,'publishCount':1}],
            [{'accountId':1,'shopId':7,'publishCount':2}],
        ):
            with self.assertRaises(ValueError):
                self.service.publish_preview({'publishCount':1,'filters':{},'shopCounts':invalid})
        with self.assertRaises(ValueError):
            self.service.publish_preview({'publishCount':3,'filters':{'shops':[{'accountId':1,'shopId':7}]},'shopCounts':shop_counts})
        self.assertEqual(self.calls,[])

    def test_multi_shop_frozen_preview_releases_exact_account_qualified_jobs(self):
        self.ready_fixture()
        self.ready_fixture(account=2)
        self.ready_fixture(shop=8,start=800)
        shops=[{'accountId':1,'shopId':7},{'accountId':2,'shopId':7}]
        plan=self.service.publish_preview({'publishCount':50,'intervalSeconds':1,'filters':{'shops':shops}})
        with patch.object(shared_automation,'Thread') as thread:
            result=self.service.publish({'previewId':plan['previewId'],'confirmation':'PUBLISH_SHARED_READY_JOBS'})
        self.assertEqual(result['submitted'],2)
        seen=[]
        def publish(pool,job,vendor):
            seen.append(pool.path);pool.update_job(job,'complete','done','accepted')
        with patch.object(server,'publish_automation_job',publish):
            for call in thread.call_args_list:
                self.service._publish_worker(*call.kwargs['args'])
        self.assertCountEqual(seen,[server.automation_pool(1).path,server.automation_pool(2).path])
        self.assertEqual(self.service.listing('queue',{})['jobs'][0]['shopId'],8)
        self.assertFalse(server._automation_publish_lock.locked())
        self.assertEqual(self.calls,[])

if __name__ == '__main__':
    unittest.main()
