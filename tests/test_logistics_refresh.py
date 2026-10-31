import copy
import unittest
from unittest.mock import patch

import server
import test_shared_automation as fixtures


class LogisticsRefreshTests(unittest.TestCase):
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server

    def setUp(self):
        fixtures.SharedAutomationTests.setUp(self)
        # These tests assert template synchronization/enqueueing, not worker
        # execution. Keep a resumed worker from outliving the fixture mocks.
        launch = patch('shared_automation.Thread')
        launch.start()
        self.addCleanup(launch.stop)

    def paused(self, account=1, detail=123, kind='freight'):
        pool = server.automation_pool(account)
        if detail != 123:
            pool.put_item(detail, 'site', 0, copy.deepcopy(self.remote[(account, 123)]))
        job_id = f'{account * 1000000 + detail:032x}'
        pool.start_job(job_id, detail, 7, 'US')
        pool.pause_for_choice(job_id, kind, [{'id': 'old', 'name': '旧模板名称'}])
        return pool, job_id

    def action(self, account, job_id):
        return self.request('POST', '/api/automation/shared/action', {
            'accountId': account, 'jobId': job_id, 'action': 'refresh-logistics',
            'confirmation': 'SHARED_JOB_ACTION'})

    def test_renamed_template_refreshes_stored_options_and_fences_cache_without_resuming(self):
        pool, job_id = self.paused()
        worker = self.service.worker(1)
        worker._vendor(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 0})
        response = {'result': 'success', 'code': 'success', 'data': {'shopIdAndFreightTemplateListMap': {'7': [
            {'templateId': 'old', 'templateName': '通用', 'site': 'US'},
            {'templateId': 'eu', 'templateName': '欧洲模板', 'site': 'EU'}]}}}
        with patch.object(server.MiaoShouClient, 'post', return_value=response) as remote:
            status, body = self.action(1, job_id)
            self.assertEqual(status, 200, body)
            remote.assert_called_once_with(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 1})
            fresh = body['data']['choices']['freight']
            self.assertEqual(fresh['options'], [{'id': 'old', 'name': '通用'}])
            self.assertTrue(fresh['refreshedAt'])
            self.assertIn('自动选择默认', body['data']['message'])
            self.assertEqual(pool.get_job(job_id)['status'], 'queued')
            self.assertEqual(pool.list_jobs()[0]['choices']['freight'], fresh)
            self.assertEqual(worker._vendor(server.HALF_FREIGHT, {'shopId': 7, 'isRsync': 0}), response)
            self.assertEqual(remote.call_count, 2)

    def test_refresh_targets_second_account_and_each_added_physical_pool(self):
        for detail, pool_id in ((124, 2), (125, 3)):
            with self.subTest(pool=pool_id):
                pool, job_id = self.paused(2, detail, 'warehouse')
                self.assertEqual(pool.get_job(job_id)['poolId'], pool_id)
                self.calls.clear()
                status, body = self.action(2, job_id)
                self.assertEqual(status, 200, body)
                self.assertEqual(self.calls, [(2, server.HALF_WAREHOUSE, {'shopId': 7, 'isRsync': 1})])
                self.assertEqual(body['data']['choices']['warehouse']['options'][0]['name'], '发货')
                self.assertIsNone(server.automation_pool(1).get_job(job_id))

    def test_unique_default_refresh_requeues_all_matching_store_tasks(self):
        primary, first = self.paused(1, 123, 'freight')
        secondary, second = self.paused(1, 124, 'freight')
        self.assertEqual(secondary.get_job(second)['poolId'], 2)
        tertiary, third = self.paused(1, 125, 'freight')
        self.assertEqual(tertiary.get_job(third)['poolId'], 3)
        response = {'result': 'success', 'code': 'success', 'data': {'shopIdAndFreightTemplateListMap': {'7': [
            {'templateId': 'new-default', 'templateName': '通用', 'site': 'US'}]}}}
        with patch('shared_automation.Thread') as start, patch.object(server.MiaoShouClient, 'post', return_value=response):
            status, body = self.action(1, first)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['data']['autoQueuedJobs'], 3)
        self.assertEqual(primary.get_job(first)['status'], 'queued')
        self.assertEqual(secondary.get_job(second)['status'], 'queued')
        self.assertEqual(tertiary.get_job(third)['status'], 'queued')
        self.assertEqual(primary.get_job(first)['choices']['freight']['selectedId'], 'new-default')
        self.assertEqual(secondary.get_job(second)['choices']['freight']['selectedId'], 'new-default')
        self.assertEqual(tertiary.get_job(third)['choices']['freight']['selectedId'], 'new-default')
        start.assert_called_once()

    def test_failed_or_malformed_sync_keeps_previous_choices(self):
        pool, job_id = self.paused()
        original = pool.get_job(job_id)
        for response in ({'data': {}}, {'data': {'shopIdAndFreightTemplateListMap': {'7': 'invalid'}}}):
            response.update(result='success', code='success')
            with patch.object(server.MiaoShouClient, 'post', return_value=response):
                self.assertEqual(self.action(1, job_id)[0], 400)
            self.assertEqual(pool.get_job(job_id), original)
        with patch.object(server.MiaoShouClient, 'post', side_effect=RuntimeError('配置同步失败')):
            self.assertEqual(self.action(1, job_id)[0], 502)
        self.assertEqual(pool.get_job(job_id), original)

    def test_empty_current_site_removes_obsolete_options_without_resuming(self):
        pool, job_id = self.paused()
        with patch.object(server.MiaoShouClient, 'post', return_value={
                'result': 'success', 'code': 'success',
                'data': {'shopIdAndFreightTemplateListMap': {'7': []}}}):
            status, body = self.action(1, job_id)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['data']['choices']['freight']['options'], [])
        with self.assertRaisesRegex(ValueError, '不在当前可选列表'):
            pool.resume_job(job_id, 'old')

    def test_sync_does_not_reopen_task_canceled_during_remote_request(self):
        pool, job_id = self.paused()
        def remote(*_):
            pool.cancel_edit_job(job_id)
            return {'result': 'success', 'code': 'success', 'data': {'shopIdAndFreightTemplateListMap': {'7': []}}}
        with patch.object(server.MiaoShouClient, 'post', side_effect=remote):
            status, _ = self.action(1, job_id)
        self.assertEqual(status, 400)
        self.assertEqual(pool.get_job(job_id)['status'], 'canceled')
        self.assertEqual(pool.get_job(job_id)['choices']['freight']['options'][0]['name'], '旧模板名称')

    def test_running_task_rejects_refresh_before_remote_request(self):
        pool, job_id = self.paused()
        pool.resume_job(job_id, 'old')
        self.calls.clear()
        self.assertEqual(self.action(1, job_id)[0], 400)
        self.assertEqual(self.calls, [])

    def test_preflight_resyncs_missing_default_before_pausing(self):
        calls = []
        def vendor(path, payload):
            calls.append((path, payload))
            return {'data': {'shopIdAndFreightTemplateListMap': {'7': [
                {'templateId': '1', 'templateName': '通用' if payload['isRsync'] else '旧名称', 'site': 'US'}]}}}
        rows = server.automation_logistic_rows(vendor, 'freight', 7, 'US')
        self.assertEqual(rows[0]['templateName'], '通用')
        self.assertEqual([r[1]['isRsync'] for r in calls], [0, 1])
        calls.clear()
        server.automation_logistic_rows(vendor, 'freight', 7, 'US', '1')
        self.assertEqual([r[1]['isRsync'] for r in calls], [0])


if __name__ == '__main__':
    unittest.main()
