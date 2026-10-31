import http.client
import json
from queue import Empty
import threading
import time
import unittest

from activity_stream import ActivityStream
import test_shared_automation as fixtures


class ActivityStreamTests(unittest.TestCase):
    def test_blocked_database_sample_does_not_block_http_snapshots_or_spawn_more_readers(self):
        entered, release=threading.Event(),threading.Event();calls=[]
        def source():calls.append(1);entered.set();release.wait(3);return {'queued':1}
        broker=ActivityStream(source)
        try:
            started=time.monotonic();self.assertTrue(broker.snapshot(wait=.02)['loading']);self.assertLess(time.monotonic()-started,.3)
            self.assertTrue(entered.wait(1))
            for _ in range(4):self.assertTrue(broker.snapshot(wait=.005)['loading'])
            self.assertEqual(len(calls),1)
            release.set()
            first=broker.snapshot(wait=2);self.assertEqual(first['queued'],1)
            started=time.monotonic()
            for _ in range(100):self.assertEqual(broker.snapshot()['queued'],1)
            self.assertLess(time.monotonic()-started,.3)
        finally:release.set();self.assertTrue(broker.close())

    def test_invalidation_rejects_an_inflight_pre_mutation_sample(self):
        entered,release=threading.Event(),threading.Event();calls=[]
        def source():
            calls.append(1)
            if len(calls)==1:entered.set();release.wait(3);return {'paused':False}
            return {'paused':True}
        broker=ActivityStream(source)
        try:
            broker.snapshot(wait=0);self.assertTrue(entered.wait(1));generation=broker.invalidate();release.set()
            latest=broker.snapshot(wait=2)
            self.assertTrue(latest['paused']);self.assertEqual(latest['_snapshot']['generation'],generation)
        finally:release.set();self.assertTrue(broker.close())

    def test_two_subscribers_share_one_sampler_and_slow_client_has_no_backlog(self):
        entered, release = threading.Event(), threading.Event()
        calls=[]
        def source():
            calls.append(1);entered.set();release.wait(3)
            return {'queued':1,'sequence':len(calls)}
        broker=ActivityStream(source,active_interval=.03,idle_interval=.03)
        first,second=broker.subscribe(),broker.subscribe()
        try:
            self.assertTrue(entered.wait(2));self.assertEqual(len(calls),1)
            release.set()
            self.assertIn('"data":',first.get(timeout=2))
            self.assertIn('"data":',second.get(timeout=2))
            for n in range(50):broker.offer(second,str(n))
            self.assertEqual(second.qsize(),1);self.assertEqual(second.get_nowait(),'49')
        finally:
            release.set();self.assertTrue(broker.close())

    def test_no_clients_perform_no_sampling_and_limit_releases_on_unsubscribe(self):
        calls=[];broker=ActivityStream(lambda:calls.append(1) or {},max_clients=1)
        self.assertEqual(calls,[])
        first=broker.subscribe()
        with self.assertRaises(RuntimeError):broker.subscribe()
        broker.unsubscribe(first)
        second=broker.subscribe();broker.unsubscribe(second)
        self.assertTrue(broker.close())

    def test_provider_error_is_explicit_and_does_not_replace_last_success(self):
        def failed():raise RuntimeError('synthetic unavailable')
        broker=ActivityStream(failed)
        first=broker.subscribe()
        try:
            value=json.loads(first.get(timeout=2));self.assertIn('error',value);self.assertIsNone(broker.last)
        finally:self.assertTrue(broker.close())


class ActivityRouteTests(unittest.TestCase):
    setUp=fixtures.SharedAutomationTests.setUp
    request=fixtures.SharedAutomationTests.request
    stop_server=fixtures.SharedAutomationTests.stop_server

    def test_event_endpoint_is_read_only_stream_and_disconnect_cleanup_is_bounded(self):
        self.service.activity_events.source=lambda:{'activeTransfers':0,'inFlightTransfers':0,'paused':True,'queueRevision':'fixture'}
        connection=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
        try:
            connection.request('GET','/api/automation/shared/events')
            response=connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertTrue(response.getheader('Content-Type').startswith('text/event-stream'))
            lines=[]
            for _ in range(8):
                line=response.readline().decode().strip();lines.append(line)
                if line.startswith('data: '):break
            payload=json.loads(next(line[6:] for line in lines if line.startswith('data: ')))
            self.assertEqual(payload['data']['queueRevision'],'fixture')
            self.assertEqual(self.calls,[])
        finally:connection.close();self.assertTrue(self.service.activity_events.close())

    def test_revision_changes_for_task_events_and_deletions_and_uses_indexed_transfer_clock(self):
        import server
        pool=server.automation_pool(1)
        job=pool.queue_jobs([123],7,'US')[0]
        first=self.service.activity()['queueRevision']
        pool.update_job(job['jobId'],'complete','done','finished')
        second=self.service.activity()['queueRevision'];self.assertNotEqual(first,second)
        pool.delete_job_record(job['jobId'])
        self.assertNotEqual(second,self.service.activity()['queueRevision'])
        with self.service.db() as db:
            plan=db.execute('EXPLAIN QUERY PLAN SELECT MAX(updated) FROM shared_transfers').fetchall()
        self.assertTrue(any('shared_transfer_updated' in str(tuple(row)) for row in plan))


if __name__ == '__main__':unittest.main()
