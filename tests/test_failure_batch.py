"""Failure assistance uses synthetic jobs and remote responses, never production."""
from contextlib import closing
import copy
import json
import os
import unittest
from unittest.mock import patch

import server
import test_shared_automation as shared_fixtures
import test_task_queries as query_fixtures
from task_queries import failure_category, job_failure_metadata


class FailureClassificationTests(unittest.TestCase):
    def test_categories_do_not_confuse_size_templates_with_logistics(self):
        cases = [('freight', '请选择运费模板', 'template'), ('prepare', '尺码模板格式错误', 'size_chart'),
                 ('prepare', 'groupIdAndValueMap is required', 'size_chart'),
                 ('prepare', '产品属性【材质】必填', 'attributes'), ('claim', 'database is locked', 'database'),
                 ('titles', 'request timed out', 'transient'), ('titles', 'Unable to reach DeepSeek API', 'transient'),
                 ('claim', 'MiaoShou returned HTTP 500: ', 'transient'), ('claim', 'Unable to reach MiaoShou API', 'transient'),
                 ('preflight', 'unexpected response', 'unknown')]
        for step, message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(failure_category('needs_review', step, message), expected)
                self.assertEqual(failure_category('done', step, message), 'none')

    def test_message_alone_never_authorizes_unknown_writes_or_publication(self):
        for status in ('needs_review', 'failed', 'interrupted', 'running', 'done'):
            for step in ('claim', 'preflight', 'prepare', 'save', 'publish'):
                for message in ('timeout', 'database is locked', '产品属性【材质】必填'):
                    info = job_failure_metadata({'status': status, 'step': step, 'message': message})
                    self.assertFalse(info['safeBatchEligible'], (status, step, message))
        self.assertFalse(job_failure_metadata({'status': 'needs_input', 'step': 'publish', 'message': 'timeout'})['safeBatchEligible'])
        self.assertFalse(job_failure_metadata({'status': 'needs_input', 'step': 'save', 'message': 'timeout'})['safeBatchEligible'])
        self.assertFalse(job_failure_metadata({'status': 'needs_input', 'step': 'prepare', 'message': '属性', 'available': False})['safeBatchEligible'])

    def test_deepseek_connection_failure_is_resumable_only_before_remote_writes(self):
        safe = job_failure_metadata({'status': 'needs_input', 'step': 'attributes',
                                     'message': 'Unable to reach DeepSeek API'})
        self.assertEqual(safe['failureCategory'], 'transient')
        self.assertEqual(safe['safeBatchAction'], 'resume')
        unsafe = job_failure_metadata({'status': 'needs_review', 'step': 'save',
                                       'message': 'Unable to reach DeepSeek API'})
        self.assertFalse(unsafe['safeBatchEligible'])

    def test_miaoshou_connection_failure_is_resumable_only_before_remote_writes(self):
        safe = job_failure_metadata({'status': 'needs_input', 'step': 'claim',
                                     'message': 'MiaoShou returned HTTP 500: '})
        self.assertEqual(safe['failureCategory'], 'transient')
        self.assertEqual(safe['safeBatchAction'], 'resume')
        unsafe = job_failure_metadata({'status': 'needs_review', 'step': 'save',
                                       'message': 'Unable to reach MiaoShou API；远端写入结果未确认'})
        self.assertFalse(unsafe['safeBatchEligible'])


class FailureQueryTests(query_fixtures.QueryFixture):
    def test_classification_filter_runs_before_pagination_and_keeps_raw_message(self):
        message = '产品属性【材质】必填；原始失败信息'
        for index in range(57):
            job_id = self.job(1, 400 + index, 'needs_input')
            pool = self.pools[1]._job_pool(job_id)
            with closing(pool._connect()) as db, db:
                db.execute("UPDATE automation_jobs SET step='prepare',message=? WHERE job_id=?", (message, job_id))
        other = self.job(2, 900, 'needs_review')
        pool = self.pools[2]._job_pool(other)
        with closing(pool._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET step='save',message='request timeout' WHERE job_id=?", (other,))
        data = self.service.listing('jobs', {'failureCategory': ['attributes'], 'page': ['2']})
        self.assertEqual((data['total'], data['pages'], len(data['entries'])), (57, 2, 7))
        self.assertTrue(all(row['message'] == message and row['safeBatchAction'] == 'resume' for row in data['entries']))
        transient = self.service.listing('jobs', {'failureCategory': ['transient']})
        self.assertEqual(transient['total'], 1)
        self.assertFalse(transient['entries'][0]['safeBatchEligible'])
        self.assertEqual(transient['entries'][0]['message'], 'request timeout')
        with self.assertRaisesRegex(ValueError, '分类'):
            self.service.listing('jobs', {'failureCategory': ['all-errors']})

    def test_transfer_classification_is_filtered_in_the_same_page(self):
        self.transfers(3)
        with self.service.db() as db:
            db.execute("UPDATE shared_transfers SET message='database is locked' WHERE transfer_id='t0000'")
            db.execute("UPDATE shared_transfers SET message='groupIdAndValueMap is required' WHERE transfer_id='t0001'")
        data = self.service.listing('jobs', {'failureCategory': ['size_chart']})
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['entries'][0]['safeBatchAction'], 'resume-transfer')


class FailureBatchTests(unittest.TestCase):
    request = shared_fixtures.SharedAutomationTests.request
    stop_server = shared_fixtures.SharedAutomationTests.stop_server
    request_data = shared_fixtures.SharedAutomationTests.request_data
    prepare = shared_fixtures.SharedAutomationTests.prepare

    def setUp(self):
        shared_fixtures.SharedAutomationTests.setUp(self)
        launch = patch('shared_automation.Thread')
        self.launch = launch.start()
        self.addCleanup(launch.stop)

    def paused(self, index=1, *, account=1, step='prepare', message='产品属性【材质】必填'):
        detail = 122 + index
        pool = server.automation_pool(account)
        if detail != 123:
            pool.put_item(detail, 'site', 0, copy.deepcopy(self.remote[(account, 123)]))
        job_id = f'{account * 1000000 + detail:032x}'
        pool.start_job(job_id, detail, 7, 'US')
        if step in {'freight', 'warehouse'}:
            pool.pause_for_choice(job_id, step, [{'id': 'old', 'name': '旧配置'}])
        else:
            pool.pause_for_problem(job_id, step, message)
        return pool, {'accountId': account, 'jobId': job_id}

    def batch(self, entries):
        return self.service.failure_batch({'entries': entries, 'confirmation': 'HANDLE_SHARED_FAILURES'})

    def test_validation_finishes_before_any_action_and_demo_is_read_only(self):
        _, ref = self.paused()
        with patch.object(self.service, 'action') as action:
            with self.assertRaises(ValueError):
                self.batch([ref, {'accountId': 1, 'jobId': 'invalid'}])
            action.assert_not_called()
        with patch.dict(os.environ, {'APP_MODE': 'demo'}):
            with self.assertRaisesRegex(ValueError, '只读'):
                self.batch([ref])
        self.assertEqual(self.calls, [])

    def test_http_route_requires_confirmation_and_returns_safe_skip(self):
        _, ref = self.paused(message='unknown error')
        status, _ = self.request('POST', '/api/automation/shared/failure-batch', {'entries': [ref]})
        self.assertEqual(status, 400)
        status, body = self.request('POST', '/api/automation/shared/failure-batch',
            {'entries': [ref], 'confirmation': 'HANDLE_SHARED_FAILURES'})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['data']['counts']['skipped'], 1)
        self.assertEqual(self.calls, [])

    def test_template_refresh_reuses_default_selection_and_never_publishes(self):
        pool, ref = self.paused(step='freight')
        result = self.batch([ref])
        self.assertEqual(result['counts'], {'processed': 1, 'failed': 0, 'skipped': 0, 'total': 1})
        self.assertEqual(result['processed'][0]['action'], 'refresh-logistics')
        self.assertEqual(pool.get_job(ref['jobId'])['status'], 'queued')
        self.assertEqual([call[1] for call in self.calls], [server.HALF_FREIGHT])

    def test_ambiguous_template_keeps_user_choice_and_records_refreshed_result(self):
        pool, ref = self.paused(step='freight')
        response = {'result': 'success', 'code': 'success', 'data': {'shopIdAndFreightTemplateListMap': {'7': [
            {'templateId': 'a', 'templateName': '通用', 'site': 'US'},
            {'templateId': 'b', 'templateName': '通用', 'site': 'US'}]}}}
        with patch.object(server.MiaoShouClient, 'post', return_value=response):
            result = self.batch([ref])
        self.assertEqual(result['counts']['processed'], 1)
        self.assertEqual(result['processed'][0]['autoQueuedJobs'], 0)
        self.assertEqual(pool.get_job(ref['jobId'])['status'], 'needs_input')
        self.launch.assert_not_called()

    def test_resume_commits_queue_with_busy_scheduler_and_rejects_repeat(self):
        pool, ref = self.paused(account=2)
        with server.AUTOMATION_EDIT_SCHEDULER_LOCK:
            first = self.batch([ref, ref])
        self.assertEqual(first['counts']['processed'], 1)
        self.assertEqual(first['counts']['total'], 1)
        self.assertTrue(first['processed'][0]['accepted'])
        self.assertEqual(pool.get_job(ref['jobId'])['status'], 'queued')
        again = self.batch([ref])
        self.assertEqual(again['counts']['skipped'], 1)
        run = self.launch.call_args.kwargs['target']
        with patch.object(server, 'run_automation_job') as worker:
            run()
            self.assertEqual(worker.call_count, 1)
            self.assertIs(worker.call_args.args[0], pool)
            self.assertEqual(worker.call_args.args[1], ref['jobId'])
            self.assertTrue(worker.call_args.kwargs['defer_publish'])
        self.assertEqual(self.calls, [])

    def test_manual_resume_is_visible_while_scheduler_busy_and_duplicate_is_rejected(self):
        pool, ref = self.paused(step='titles', message='The read operation timed out')
        with server.AUTOMATION_EDIT_SCHEDULER_LOCK:
            result = self.service.action({**ref, 'action': 'resume', 'confirmation': 'SHARED_JOB_ACTION'})
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(pool.get_job(ref['jobId'])['status'], 'queued')
        with self.assertRaises(ValueError):
            self.service.action({**ref, 'action': 'resume', 'confirmation': 'SHARED_JOB_ACTION'})
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.calls, [])

    def test_state_changed_after_acceptance_is_rechecked_before_worker(self):
        pool, ref = self.paused(message='database is locked')
        self.batch([ref])
        run = self.launch.call_args.kwargs['target']
        pool.update_job(ref['jobId'], 'save', 'needs_review', 'request timed out after acceptance')
        with patch.object(server, 'run_automation_job') as worker:
            run()
            worker.assert_not_called()
        self.assertEqual(pool.get_job(ref['jobId'])['status'], 'needs_review')
        self.assertEqual(self.service.failure_pending, set())

    def test_review_publication_unknown_and_unavailable_tasks_are_not_replayed(self):
        refs = []
        for index, step in enumerate(('save', 'publish', 'prepare'), 1):
            pool, ref = self.paused(index, message='timeout')
            pool.update_job(ref['jobId'], step, 'needs_review', 'timeout')
            refs.append(ref)
        _, unknown = self.paused(4, message='unrecognized error')
        refs.append(unknown)
        result = self.batch(refs)
        self.assertEqual(result['counts']['skipped'], 4)
        self.assertEqual(result['counts']['processed'], 0)
        self.launch.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_batch_continues_after_one_error_without_claiming_success(self):
        _, first = self.paused(1, step='freight')
        _, second = self.paused(2, message='unknown')
        with patch.object(self.service, 'refresh_job_logistics', side_effect=RuntimeError('sync unavailable')):
            result = self.batch([first, second])
        self.assertEqual(result['counts'], {'processed': 0, 'failed': 1, 'skipped': 1, 'total': 2})
        self.assertEqual(result['failed'][0]['reason'], 'sync unavailable')

    def test_transfer_unknown_create_is_not_replayed_even_when_message_says_database(self):
        _, rows = self.prepare(targets=((2, 7),))
        ref = json.loads(rows[0]['payload'])
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', 'database is locked')
        with self.service.db() as db:
            db.execute('''INSERT INTO shared_copies VALUES (?,?,?,?,?,?,NULL,'needs_review',?,?)''',
                ('copy', 1, ref['sourceScope'], 123, 2, ref['targetScope'], '{}', 'request timeout after acceptance'))
        self.launch.reset_mock()
        result = self.batch([{'transferId': rows[0]['transfer_id']}])
        self.assertEqual(result['counts']['skipped'], 1, result)
        self.launch.assert_not_called()

    def test_transfer_definite_rejection_reuses_resume_without_creating_inline(self):
        _, rows = self.prepare(targets=((2, 7),))
        ref = json.loads(rows[0]['payload'])
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', '副本创建结果待核查')
        with self.service.db() as db:
            db.execute('''INSERT INTO shared_copies VALUES (?,?,?,?,?,?,NULL,'needs_review',?,?)''',
                ('copy', 1, ref['sourceScope'], 123, 2, ref['targetScope'], '{}', 'groupIdAndValueMap is required'))
        self.calls.clear()
        result = self.batch([{'transferId': rows[0]['transfer_id']}])
        self.assertEqual(result['counts']['processed'], 1, result)
        self.assertEqual(result['processed'][0]['action'], 'resume-transfer')
        self.assertEqual(self.calls, [])
        with self.service.db() as db:
            status = db.execute('SELECT status FROM shared_transfers WHERE transfer_id=?', (rows[0]['transfer_id'],)).fetchone()[0]
        self.assertEqual(status, 'queued')

    def test_copy_gateway_failure_sends_create_once_then_blocks_batch_replay(self):
        _, rows = self.prepare(targets=((2, 7),))
        ref = json.loads(rows[0]['payload'])
        original = server.MiaoShouClient.post
        creates = []
        def post(client, path, payload):
            if path == server.HALF_CREATE:
                creates.append(path)
                raise RuntimeError('MiaoShou returned HTTP 504: httpGatewayTimeout')
            return original(client, path, payload)
        with patch.object(server.MiaoShouClient, 'post', new=post), patch.object(server.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, '禁止自动重复创建'):
                self.service._ensure_copy(ref)
        self.assertEqual(creates, [server.HALF_CREATE])
        self.service._set_transfer(rows[0]['transfer_id'], 'needs_review', 'HTTP 504 gateway timeout')
        result = self.batch([{'transferId': rows[0]['transfer_id']}])
        self.assertEqual(result['counts']['skipped'], 1)


if __name__ == '__main__':
    unittest.main()
