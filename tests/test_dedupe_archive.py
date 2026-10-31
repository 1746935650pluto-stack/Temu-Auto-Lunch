"""Account/shard dedupe and archival use disposable local databases only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from threading import Barrier
import unittest

from automation_pool import AutomationPool
from partitioned_pool import PartitionedPool
from event_archive import archive_task_events, preview_event_archives, read_job_events


class DedupeArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.parts = [AutomationPool(self.root / f'pool-{p}.sqlite3') for p in (1, 2, 3)]
        self.pool = PartitionedPool(*self.parts, 1)
        self.parts[0].put_item(101, 'site', 0, {'title': 'product'})

    def history_in_old_shard(self, *, hidden=False):
        job = self.parts[0].queue_jobs([101], 7, 'US')[0]
        self.parts[0].update_job(job['jobId'], 'complete', 'done', 'published')
        if hidden:
            self.parts[0].delete_job_records([job['jobId']])
        self.parts[1].put_item(101, 'site', 0, {'title': 'current snapshot'})
        with closing(self.parts[0]._connect()) as db, db:
            db.execute('DELETE FROM automation_items WHERE detail_id=101')
        self.pool = PartitionedPool(*(AutomationPool(p.path) for p in self.parts), 1)
        return job

    def test_history_in_another_shard_and_hidden_history_still_prevent_duplicate(self):
        self.history_in_old_shard(hidden=True)
        self.assertEqual(self.pool.get_item(101)['poolId'], 2)
        # Do not accidentally route back to the shard holding the history.
        self.pool.assignment_cursor = 1
        self.assertEqual(self.pool.queue_jobs([101], 7, 'US', dedupe_pairs=True), [])
        count = sum(p.read_rows('SELECT COUNT(*) AS n FROM automation_jobs')[0]['n'] for p in self.parts)
        self.assertEqual(count, 1)

    def test_different_shop_site_and_account_are_not_false_duplicates(self):
        self.history_in_old_shard()
        self.assertEqual(len(self.pool.queue_jobs([101], 8, 'US', dedupe_pairs=True)), 1)
        self.assertEqual(len(self.pool.queue_jobs([101], 7, 'UK', dedupe_pairs=True)), 1)
        other = AutomationPool(self.root / 'other-account.sqlite3')
        other.put_item(101, 'site', 0, {'title': 'same numeric ID, other account'})
        self.assertEqual(len(other.queue_jobs([101], 7, 'US', dedupe_pairs=True)), 1)

    def test_legacy_lowercase_site_is_the_same_dedupe_target(self):
        job = self.parts[0].queue_jobs([101], 7, 'US')[0]
        with closing(self.parts[0]._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET site='us' WHERE job_id=?", (job['jobId'],))
        self.assertEqual(self.pool.queue_jobs([101], 7, 'Us', dedupe_pairs=True), [])

    def test_active_job_in_another_shard_blocks_normal_submission(self):
        job = self.history_in_old_shard()
        with closing(self.parts[0]._connect()) as db, db:
            db.execute("UPDATE automation_jobs SET status='running' WHERE job_id=?", (job['jobId'],))
        with self.assertRaisesRegex(ValueError, '未完成任务'):
            self.pool.queue_jobs([101], 7, 'US')

    def test_two_connections_only_create_one_target_task(self):
        peers = [AutomationPool(self.parts[0].path) for _ in range(2)]
        for peer in peers:
            peer.get_item(101)
        barrier = Barrier(2)
        def submit(peer):
            barrier.wait(timeout=5)
            return peer.queue_jobs([101], 7, 'US', dedupe_pairs=True)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, peer) for peer in peers]
            self.assertEqual(sum(len(f.result(timeout=10)) for f in futures), 1)

    def test_archiving_preserves_summary_dedupe_and_complete_event_readback(self):
        pool = self.parts[0]
        job = pool.queue_jobs([101], 7, 'US')[0]
        pool.update_job(job['jobId'], 'complete', 'done', 'published')
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        with closing(pool._connect()) as db, db:
            db.execute('UPDATE automation_jobs SET updated_at=? WHERE job_id=?', (old, job['jobId']))
            db.execute('UPDATE automation_job_events SET created_at=? WHERE job_id=?', (old, job['jobId']))
        events = read_job_events(pool.path, job['jobId'])
        now = datetime.now(timezone.utc)
        preview = preview_event_archives([pool.path], retention_days=7, now=now)
        result = archive_task_events([pool.path], retention_days=7, now=now, expected_preview=preview)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deletedEvents'], len(events))
        self.assertEqual(pool.get_job(job['jobId'])['status'], 'done')
        self.assertEqual(read_job_events(pool.path, job['jobId']), events)
        self.assertEqual(self.pool.queue_jobs([101], 7, 'US', dedupe_pairs=True), [])
