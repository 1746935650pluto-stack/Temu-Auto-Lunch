from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from automation_pool import AutomationPool
from account_runtime import RUNTIME
import test_shared_automation as fixtures

class SchedulingTests(unittest.TestCase):
    def test_pure_reader_does_not_wait_for_writer_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=AutomationPool(Path(folder)/'pool.sqlite3')
            pool.put_item(1,'site',0,{'title':'test'})
            done=threading.Event();errors=[]
            def read():
                try:self.assertEqual(pool.read_rows('SELECT COUNT(*) n FROM automation_items')[0]['n'],1)
                except Exception as error:errors.append(error)
                finally:done.set()
            with closing(pool._connect()):
                thread=threading.Thread(target=read);thread.start()
                ready=done.wait(2)
            thread.join(3)
            self.assertTrue(ready);self.assertEqual(errors,[])

    def test_light_claim_is_atomic_and_never_loads_history(self):
        with tempfile.TemporaryDirectory() as folder:
            pool=AutomationPool(Path(folder)/'pool.sqlite3')
            pool.put_item(1,'site',0,{'title':'test'})
            job=pool.queue_jobs([1],7,'US')[0]
            with patch.object(pool,'_read_job',side_effect=AssertionError('history hydration')):
                self.assertEqual(pool.start_next_queued_jobs(1,light=True),[{'jobId':job['jobId']}])
                self.assertEqual(pool.start_next_queued_jobs(1,light=True),[])
            self.assertEqual(pool.get_job_state(job['jobId'])['status'],'running')

class ServiceTests(unittest.TestCase):
    setUp=fixtures.SharedAutomationTests.setUp
    request=fixtures.SharedAutomationTests.request
    stop_server=fixtures.SharedAutomationTests.stop_server
    ready_fixture=fixtures.SharedAutomationTests.ready_fixture

    def test_drain_check_never_trusts_idle_display_cache(self):
        with patch.object(self.service,'activity',return_value={'paused':False,'running':1}), \
             patch.object(self.service.activity_events,'snapshot',return_value={'paused':True,'running':0,'drained':True}):
            status,body=self.request('GET','/api/automation/shared/drain-check')
        self.assertEqual(status,200)
        self.assertFalse(body['data']['drained'])

    def test_one_pool_claim_error_does_not_stop_other_pools_or_duplicate(self):
        pool=fixtures.server.automation_pool(1);jobs=[]
        for i,physical in enumerate(pool.pools):
            physical.put_item(700+i,'site',0,fixtures.valid_item())
            job=physical.queue_jobs([700+i],7,'US')[0]
            physical.update_job(job['jobId'],'save','ready_publish','saved');jobs.append(job['jobId'])
        batch=pool.begin_publish_batch(jobs,1);lane=RUNTIME.lane(1)
        self.assertTrue(lane.publish_lock.acquire(False));original=pool.start_next_ready_publish_job
        failed=[];seen=[]
        def claim(token):
            if token.startswith('1:') and not failed:
                failed.append(True);raise sqlite3.OperationalError('injected busy')
            return original(token)
        def publish(router,job,vendor):
            seen.append(job);router.update_job(job,'complete','done','accepted')
        with patch.object(pool,'start_next_ready_publish_job',claim),patch.object(fixtures.server,'publish_automation_job',publish),patch.object(pool,'wait_for_running_publish_interval',return_value=True):
            self.service._publish_worker([(1,batch)],None)
        self.assertCountEqual(seen,jobs)
        self.assertNotEqual(seen[0],jobs[0]);self.assertFalse(lane.publish_lock.locked())

    def test_receipt_replay_returns_original_without_second_worker(self):
        jobs=self.ready_fixture()
        body={'confirmation':'PUBLISH_SHARED_READY_JOBS','requestId':'stable-id','jobs':[{'accountId':1,'jobId':jobs[0]['jobId']}]}
        with patch.object(fixtures.shared_automation,'Thread') as thread:
            first=self.service.publish(body);self.assertEqual(self.service.publish(body),first)
            self.assertEqual(thread.call_count,1)
        RUNTIME.lane(1).publish_lock.release()

    def test_receipt_storage_failure_rolls_back_before_any_worker_starts(self):
        jobs=self.ready_fixture();write=self.service._local_write;calls=[]
        def fail_receipt(action):
            calls.append(1)
            if len(calls)==2:raise sqlite3.OperationalError('injected receipt failure')
            return write(action)
        with patch.object(self.service,'_local_write',fail_receipt),patch.object(fixtures.shared_automation,'Thread') as thread:
            with self.assertRaises(sqlite3.OperationalError):
                self.service.publish({'confirmation':'PUBLISH_SHARED_READY_JOBS','jobs':[{'accountId':1,'jobId':jobs[0]['jobId']}]})
            thread.assert_not_called()
        self.assertFalse(RUNTIME.lane(1).publish_lock.locked())
        self.assertEqual(fixtures.server.automation_pool(1).publish_progress()['status'],'interrupted')
        self.assertEqual(fixtures.server.automation_pool(1).get_job_state(jobs[0]['jobId'])['status'],'ready_publish')

