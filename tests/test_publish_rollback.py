from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from automation_pool import AutomationPool
from partitioned_pool import PartitionedPool
import test_shared_automation as fixtures
from account_runtime import RUNTIME


class ReviewPublicationAtomicity(unittest.TestCase):
    def test_append_failure_keeps_partial_new_members_and_changes_existing_pacing(self):
        with tempfile.TemporaryDirectory() as folder:
            physical=[AutomationPool(Path(folder)/f'pool{i}.sqlite3') for i in range(3)]
            new_ids=[]
            for i,pool in enumerate(physical[:2]):
                pool.put_item(i+10,'site',0,{'title':'old'})
                pool.put_item(i+20,'site',0,{'title':'new'})
                old=pool.queue_jobs([i+10],7,'US')[0]
                new=pool.queue_jobs([i+20],7,'US')[0]
                for row in (old,new):pool.update_job(row['jobId'],'save','ready_publish','saved')
                pool.begin_publish_batch([old['jobId']],10)
                new_ids.append(new['jobId'])
            router=PartitionedPool(*physical,1)
            calls=[]
            originals=[p.append_to_running_publish_batch for p in physical[:2]]
            def append(number,ids,interval):
                calls.append(number)
                if len(calls)==2:raise sqlite3.OperationalError('injected local write failure')
                return originals[number](ids,interval)
            with patch.object(physical[0],'append_to_running_publish_batch',lambda ids,interval:append(0,ids,interval)),\
                 patch.object(physical[1],'append_to_running_publish_batch',lambda ids,interval:append(1,ids,interval)):
                with self.assertRaisesRegex(sqlite3.OperationalError,'injected'):
                    router.append_to_running_publish_batch(new_ids,1)
            present=[];intervals=[]
            for p in physical[:2]:
                with closing(p._connect()) as db:
                    present += [r['job_id'] for r in db.execute('SELECT job_id FROM automation_publish_batch_items') if r['job_id'] in new_ids]
                    intervals += [r['interval_seconds'] for r in db.execute("SELECT interval_seconds FROM automation_publish_batches WHERE status='running'")]
            self.assertEqual(len(present),0)
            self.assertEqual(intervals,[10,10])



