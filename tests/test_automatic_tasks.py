"""Pool schedules use isolated SQLite and synthetic Miaoshou only."""
import copy
import json
import os
import unittest
import uuid
from unittest.mock import patch

import automatic_tasks
import server
import test_shared_automation as fixtures
from automatic_tasks import AutomaticTasks


class AutomaticTaskTests(unittest.TestCase):
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server

    def setUp(self):
        fixtures.SharedAutomationTests.setUp(self)
        self.now = 2000000000.0
        self.auto = AutomaticTasks(server, clock=lambda: self.now)
        for mocked in (patch.object(server, 'AUTOMATIC_TASKS', {}),
                       patch.object(server, 'automatic_tasks', return_value=self.auto),
                       patch('shared_automation.Thread')):
            mocked.start(); self.addCleanup(mocked.stop)
        self.published = []

    def body(self, **extra):
        return {'requestId': uuid.uuid4().hex, 'sourceAccountId': 1, 'targetAccountId': 1,
                'shopId': 7, 'site': 'US', 'count': 1, 'mode': 'now',
                'confirmation': 'AUTOMATIC_EDIT_AND_PUBLISH', **extra}

    def create(self, **extra):
        body = self.body(**extra)
        self.auto.create(body, launch=False)
        return body

    def transfers(self):
        with self.service.db() as db:
            return list(db.execute('SELECT * FROM shared_transfers'))

    def jobs_ready(self):
        for row in self.transfers():
            if row['status'] == 'queued':
                self.service.prepare_transfer(row)
        with self.service.db() as db:
            refs = list(db.execute('SELECT * FROM shared_transfer_jobs'))
        for r in refs:
            pool = server.automation_pool(r['account_id'])
            job = pool.get_job(r['job_id'])
            if job and job['status'] == 'queued':
                pool.update_job(r['job_id'], 'save', 'ready_publish', 'synthetic save')
        return refs

    def fake_publish(self, body):
        self.published.extend(body['jobs'])
        for r in body['jobs']:
            server.automation_pool(r['accountId']).update_job(r['jobId'], 'complete', 'done', 'synthetic accepted')
        return {'status': 'running'}

    def finish(self):
        self.jobs_ready()
        with patch.object(self.service, 'publish', side_effect=self.fake_publish):
            self.auto.tick()

    def add_item(self, detail, account=1):
        raw = copy.deepcopy(self.remote[(account, 123)])
        self.remote[(account, detail)] = raw
        server.automation_pool(account).put_item(detail, 'site', 0, raw)

    def test_now_publishes_exact_quantity_and_never_existing_ready_jobs(self):
        self.add_item(124); self.add_item(125)
        old = server.automation_pool(1).queue_jobs([123], 7, 'US')[0]
        server.automation_pool(1).update_job(old['jobId'], 'save', 'ready_publish', 'unrelated')
        self.create(count=2); self.auto.tick()
        self.assertEqual(len(self.transfers()), 2)
        self.finish()
        self.assertEqual(len(self.published), 2)
        self.assertNotIn(old['jobId'], [r['jobId'] for r in self.published])
        self.assertEqual(server.automation_pool(1).get_job(old['jobId'])['status'], 'ready_publish')
        self.assertEqual(self.auto.listing()['tasks'][0]['runs'][0]['state'], 'completed')
        self.auto.tick(); self.assertEqual(len(self.published), 2)

    def test_cross_account_tracks_new_jobs_and_correct_target(self):
        self.create(targetAccountId=2); self.auto.tick(); self.finish()
        self.assertEqual([r['accountId'] for r in self.published], [2])
        job = server.automation_pool(2).get_job(self.published[0]['jobId'])
        self.assertNotEqual(job['detailId'], 123)
        self.assertEqual(job['shopId'], 7)
        self.assertEqual(sum(p == server.HALF_CREATE for _, p, _ in self.calls), 1)

    def test_pending_copy_and_existing_history_are_not_selected_twice(self):
        self.create(targetAccountId=2); self.auto.tick()
        self.create(targetAccountId=2); self.auto.tick(); self.auto.tick()
        self.assertEqual(len(self.transfers()), 1)
        self.assertTrue(any('不足' in r['message'] for t in self.auto.listing()['tasks'] for r in t['runs']))

    def test_insufficient_quantity_submits_nothing(self):
        self.create(count=2); self.auto.tick(); self.auto.tick()
        self.assertEqual(self.transfers(), [])
        self.assertFalse(any(p in {server.HALF_CREATE, server.HALF_PUBLISH} for _, p, _ in self.calls))
        self.assertIn('不足指定的 2', self.auto.listing()['tasks'][0]['runs'][0]['message'])

    def test_person_key_uses_source_even_when_ids_match_across_accounts(self):
        self.create(sourceAccountId=0, personKey='2:11'); self.auto.tick()
        self.assertEqual([json.loads(t['payload'])['sourceAccountId'] for t in self.transfers()], [2])

    def test_unknown_person_does_not_broaden_to_all_people(self):
        self.create(personKey='unknown'); self.auto.tick()
        self.assertEqual(self.transfers(), [])

    def test_options_use_scoped_local_people_even_when_pool_rows_lack_owner(self):
        scope = self.service.scope(1)
        store = server.COLLECTION_PEOPLE.store
        store.observe(scope, [{'subAppAccountId': '31'}])
        store.update_names(scope, [{'subAppAccountId': '31', 'subAppAccountName': '缓存姓名'}])
        store.save_aliases(scope, [{'id': '31', 'alias': '人员备注'}])
        # The selector must not depend on an optional field in the pool
        # snapshot, nor make a new remote people-list request.
        with patch.object(self.service, 'pool_rows', return_value=[]):
            people = self.auto.options()['people']
        self.assertIn({'key': '1:31', 'accountId': 1, 'name': '人员备注', 'personId': '31'}, people)

    def test_one_time_only_fires_at_due_time_and_survives_restart(self):
        self.create(mode='once', startAt=self.now + 60)
        self.auto.tick(); self.assertEqual(self.transfers(), [])
        self.now += 60
        restarted = AutomaticTasks(server, clock=lambda: self.now)
        restarted.tick(); self.finish(); restarted.tick()
        self.assertEqual(len(self.transfers()), 1)
        self.assertIsNone(restarted.listing()['tasks'][0]['nextAt'])

    def test_recurring_does_not_overlap_or_replay_missed_periods(self):
        self.create(mode='interval', startAt=self.now + 60, everyMinutes=60)
        self.now += 60; self.auto.tick()
        self.now += 5 * 3600; self.auto.tick()
        self.assertEqual(len(self.transfers()), 1)
        self.finish(); self.add_item(124); self.auto.tick()
        self.assertEqual(len(self.transfers()), 2)
        self.assertEqual(self.auto.listing()['tasks'][0]['nextAt'], self.now + 3600)
        self.auto.tick(); self.assertEqual(len(self.transfers()), 2)

    def test_pause_stops_due_runs_and_publish_resume_moves_overdue_interval(self):
        task = self.create(mode='interval', startAt=self.now + 60)
        self.auto.pause({'taskId': task['requestId'], 'paused': True, 'confirmation': 'PAUSE_AUTOMATIC_TASK'})
        self.now += 120; self.auto.tick(); self.assertEqual(self.transfers(), [])
        self.auto.pause({'taskId': task['requestId'], 'paused': False, 'confirmation': 'PAUSE_AUTOMATIC_TASK'})
        self.assertEqual(self.auto.listing()['tasks'][0]['nextAt'], self.now + 3600)
        self.now += 3600; self.auto.tick(); self.jobs_ready()
        self.auto.pause({'taskId': task['requestId'], 'paused': True, 'confirmation': 'PAUSE_AUTOMATIC_TASK'})
        with patch.object(self.service, 'publish') as publish:
            self.auto.tick(); publish.assert_not_called()

    def test_global_pause_stops_scheduler(self):
        self.create()
        self.service.set_paused({'paused': True, 'confirmation': 'SET_SHARED_AUTOMATION_PAUSE'})
        self.auto.tick(); self.assertEqual(self.transfers(), [])

    def test_pause_between_progress_read_and_publish_is_respected(self):
        task = self.create(); self.auto.tick(); self.jobs_ready()
        with self.service.db() as db:
            run = db.execute('SELECT * FROM pool_auto_runs').fetchone()
            config = json.loads(db.execute('SELECT config FROM pool_auto_tasks').fetchone()[0])
        ready = self.auto._progress(run)['ready']
        self.auto.pause({'taskId': task['requestId'], 'paused': True, 'confirmation': 'PAUSE_AUTOMATIC_TASK'})
        with patch.object(self.service, 'publish') as publish:
            self.auto._publish(run, config, ready); publish.assert_not_called()

    def test_progress_missing_or_deleted_job_does_not_publish(self):
        self.create(); self.auto.tick(); refs = self.jobs_ready()
        pool = server.automation_pool(1)
        pool.update_job(refs[0]['job_id'], 'save', 'failed', 'fixture')
        pool.delete_job_record(refs[0]['job_id'])
        self.assertEqual(pool.job_states([refs[0]['job_id']]), {})
        self.finish(); self.assertEqual(self.published, [])
        self.assertIn('本轮保存任务已移除', self.auto.listing()['tasks'][0]['runs'][0]['issues'])

    def test_unknown_publish_outcome_is_reserved_and_not_retried_after_restart(self):
        task = self.create(); self.auto.tick(); self.jobs_ready()
        with patch.object(self.service, 'publish', side_effect=RuntimeError('unknown remote acceptance')) as publish:
            self.auto.tick(); self.assertEqual(publish.call_count, 1)
        self.auto = AutomaticTasks(server, clock=lambda: self.now)
        self.auto.pause({'taskId': task['requestId'], 'paused': False, 'confirmation': 'PAUSE_AUTOMATIC_TASK'})
        with patch.object(self.service, 'publish') as publish:
            self.auto.tick(); publish.assert_not_called()
        self.assertEqual(self.auto.listing()['tasks'][0]['runs'][0]['state'], 'partial')

    def test_busy_publish_lock_retries_only_confirmed_not_submitted(self):
        self.create(); self.auto.tick(); self.jobs_ready()
        with patch.object(self.service, 'publish', side_effect=ValueError('已有发布任务正在进行，请稍后再试')):
            self.auto.tick()
        with self.service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pool_auto_publish_attempts').fetchone()[0], 0)
        self.finish(); self.assertEqual(len(self.published), 1)

    def test_credential_change_pauses_without_reading_or_publishing_new_account(self):
        self.create(); self.auto.tick(); self.jobs_ready()
        with patch.dict(os.environ, {'MIAOSHOU_APP_KEY': 'rotated'}), patch.object(self.service, 'publish') as publish:
            self.auto.tick(); publish.assert_not_called()
        task = self.auto.listing()['tasks'][0]
        self.assertTrue(task['paused']); self.assertTrue(task['runs'][0]['message'])

    def test_failed_save_is_not_replaced_or_published(self):
        self.add_item(124); self.create(); self.auto.tick()
        refs = self.jobs_ready()
        server.automation_pool(1).update_job(refs[0]['job_id'], 'save', 'failed', 'synthetic failure')
        self.finish()
        self.assertEqual(self.published, []); self.assertEqual(len(self.transfers()), 1)
        run = self.auto.listing()['tasks'][0]['runs'][0]
        self.assertEqual(run['state'], 'partial'); self.assertIn('synthetic failure', run['issues'])

    def test_restart_mid_prepare_never_submits_unknown_remaining_batches(self):
        self.create()
        with self.service.db() as db:
            task = db.execute('SELECT task_id FROM pool_auto_tasks').fetchone()[0]
            db.execute("INSERT INTO pool_auto_runs VALUES ('r',?,'preparing','','[]',0,?,?)", (task, self.now, self.now))
            db.execute('UPDATE pool_auto_tasks SET next_at=NULL')
        self.auto = AutomaticTasks(server, clock=lambda: self.now)
        self.auto.tick(); self.assertEqual(self.transfers(), [])
        self.assertIn('后台重启', self.auto.listing()['tasks'][0]['runs'][0]['message'])

    def test_idempotent_request_survives_time_and_rejects_changed_content(self):
        body = self.create(mode='once', startAt=self.now + 60)
        self.now += 120
        self.assertTrue(self.auto.create(body, launch=False)['duplicate'])
        with self.assertRaisesRegex(ValueError, '不能修改'):
            self.auto.create({**body, 'count': 2}, launch=False)
        self.assertEqual(len(self.auto.listing()['tasks']), 1)

    def test_more_than_one_batch_still_exact_quantity(self):
        for detail in range(124, 175):
            self.add_item(detail)
        self.create(count=51); self.auto.tick()
        self.assertEqual(len(self.transfers()), 51)
        self.assertEqual(len({t['batch_id'] for t in self.transfers()}), 2)
        self.finish(); self.assertEqual(len(self.published), 50)
        self.finish(); self.assertEqual(len(self.published), 51)

    def test_validation_is_fail_closed(self):
        for change in ({'confirmation': ''}, {'count': True}, {'count': 501}, {'mode': 'invalid'},
                       {'mode': 'once', 'startAt': self.now}, {'personKey': '2:11'},
                       {'targetAccountId': 99}, {'publishInterval': 0}, {'everyMinutes': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.auto.create(self.body(**change), launch=False)
        with patch.dict(os.environ, {'APP_MODE': 'demo'}), self.assertRaises(ValueError):
            self.auto.create(self.body(), launch=False)
        self.assertEqual(self.auto.listing()['tasks'], [])

    def test_http_routes_no_remote_write_without_explicit_create(self):
        self.assertEqual(self.request('GET', '/api/automation/shared/auto-options')[0], 200)
        self.assertEqual(self.request('GET', '/api/automation/shared/auto-tasks')[1]['data']['tasks'], [])
        with patch.object(automatic_tasks, 'Thread'):
            status, result = self.request('POST', '/api/automation/shared/auto-create', self.body())
        self.assertEqual(status, 200, result)
        self.assertEqual(self.transfers(), [])
        public = json.dumps(self.auto.listing())
        for secret in ('sourceScopes', 'targetScope', 'requestHash', 'test-one', 'test-two'):
            self.assertNotIn(secret, public)


if __name__ == '__main__':
    unittest.main()
