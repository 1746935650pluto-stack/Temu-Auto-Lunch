import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch

from automation_pool import AutomationPool, normalize_edit_concurrency


class AutomationPoolTests(unittest.TestCase):
    def test_generated_title_replaces_staging_title_for_existing_ready_publish_jobs(self):
        job_id = 'a' * 32
        old_title = '入池时的旧标题'
        generated_title = 'AI 保存到妙手的新中文标题'
        self.pool.put_item(123, 'site', 0, {'title': old_title})
        self.pool.start_job(job_id, 123, 456, 'US', publish_after_save=False)
        self.pool.update_job(job_id, 'titles', 'done',
                             f'中英文标题已生成：中文「{generated_title}」；英文「New English title」；尚未写入妙手')
        self.pool.update_job(job_id, 'publish', 'ready_publish', '等待统一发布')
        job = self.pool.get_job(job_id)
        self.assertEqual(job['title'], generated_title)
        self.assertEqual(self.pool.ready_publish_jobs()[0]['title'], generated_title)
        self.assertEqual(self.pool.list_jobs()[0]['title'], generated_title)

    def test_list_jobs_fetches_related_records_in_batches_not_one_connection_per_job(self):
        for detail_id, job_id in ((201, 'a' * 32), (202, 'b' * 32), (203, 'c' * 32)):
            self.pool.put_item(detail_id, 'site', 0, {'title': f'商品 {detail_id}'})
            self.pool.start_job(job_id, detail_id, 456, 'US')
            self.pool.update_job(job_id, 'complete', 'done', '已完成预处理')
        with patch.object(self.pool, 'get_job', side_effect=AssertionError('list_jobs must batch query')):
            jobs = self.pool.list_jobs()
        self.assertEqual([job['detailId'] for job in jobs], [203, 202, 201])
        self.assertTrue(all(job['events'] for job in jobs))

    def test_generated_title_event_parser_is_strict_and_new_title_is_persisted(self):
        job_id = 'b' * 32
        self.pool.put_item(123, 'site', 0, {'title': '旧标题'})
        self.pool.start_job(job_id, 123, 456, 'US')
        self.pool.update_job(job_id, 'titles', 'done', '中英文标题已生成：中文「不完整记录」')
        self.assertEqual(self.pool.get_job(job_id)['title'], '旧标题')
        self.pool.update_job_title(job_id, '最新标题')
        self.assertEqual(self.pool.get_job(job_id)['title'], '最新标题')
        with self.assertRaises(ValueError):
            self.pool.update_job_title(job_id, '')
        with self.assertRaises(ValueError):
            self.pool.update_job_title('c' * 32, '标题')

    def test_edit_concurrency_accepts_above_fifty_but_only_exact_positive_integers(self):
        for value in (51, 200, 1000, '200'):
            self.assertEqual(normalize_edit_concurrency(value), int(value))
        for value in (0, -1, 2.5, True, None, '2.5', 'abc', 2 ** 53):
            with self.assertRaises(ValueError):
                self.pool.start_next_queued_jobs(value)

    def test_scheduler_can_promote_more_than_fifty_jobs_with_requested_concurrency(self):
        for detail_id in range(1, 61):
            self.pool.put_item(detail_id, 'site', 0, {'title': str(detail_id)})
        self.pool.queue_jobs(list(range(1, 51)), 456, 'US')
        self.pool.queue_jobs(list(range(51, 61)), 456, 'US')
        group = self.pool.start_next_queued_jobs(200)
        self.assertEqual(len(group), 60)
        self.assertTrue(all(job['status'] == 'running' for job in group))
        self.assertTrue(all(not job['publishAfterSave'] for job in group))
        self.assertEqual(self.pool.start_next_queued_jobs(200), [])

    def test_batch_queue_does_not_wait_for_an_unrelated_paused_job(self):
        for detail_id in (101, 102):
            self.pool.put_item(detail_id, 'site', 0, {'title': str(detail_id)})
        self.pool.start_job('a' * 32, 101, 456, 'US')
        self.pool.pause_for_problem('a' * 32, 'preflight', '等待人工处理')
        jobs = self.pool.queue_jobs([102], 456, 'US')
        self.assertEqual(jobs[0]['status'], 'queued')
        self.assertEqual(self.pool.start_next_queued_jobs(8)[0]['detailId'], 102)

    def test_remove_publish_queue_preserves_goods_history_and_skips_publication(self):
        for detail, job_id in ((123, 'a' * 32), (124, 'b' * 32)):
            self.pool.put_item(detail, 'site', 0, {'title': '已保存商品'})
            self.pool.start_job(job_id, detail, 456, 'US')
            self.pool.update_job(job_id, 'save', 'done', '已保存')
            self.pool.update_job(job_id, 'publish', 'ready_publish', '待发布')
        before = self.pool.get_job('a' * 32)['events']
        self.assertEqual(self.pool.remove_publish_queue_jobs(['a' * 32, 'a' * 32]), ['a' * 32])
        removed = self.pool.get_job('a' * 32)
        self.assertEqual(removed['status'], 'canceled')
        self.assertEqual(removed['events'][:-1], before)
        self.assertIsNotNone(self.pool.get_item(123))
        self.assertEqual(len(self.pool.list_jobs()), 2)
        self.assertEqual(self.pool.start_next_ready_publish_job()['jobId'], 'b' * 32)
        with self.assertRaises(ValueError):
            self.pool.remove_publish_queue_jobs(['b' * 32])

    def test_remove_publish_queue_batch_conflict_is_atomic_and_validates_ids(self):
        for detail, job_id in ((123, 'a' * 32), (124, 'b' * 32)):
            self.pool.put_item(detail, 'site', 0, {'title': '商品'})
            self.pool.start_job(job_id, detail, 456, 'US')
            if detail == 123:
                self.pool.update_job(job_id, 'publish', 'ready_publish', '待发布')
        for ids in (None, [], [{}], ['z' * 32], ['a' * 32, 'b' * 32], ['a' * 32, 'c' * 32]):
            with self.assertRaises(ValueError):
                self.pool.remove_publish_queue_jobs(ids)
        self.assertEqual(self.pool.get_job('a' * 32)['status'], 'ready_publish')
        self.assertEqual(self.pool.get_job('b' * 32)['status'], 'running')

    def test_remove_and_publish_claim_cannot_both_win(self):
        self.pool.put_item(123, 'site', 0, {'title': '商品'})
        self.pool.start_job('a' * 32, 123, 456, 'US')
        self.pool.update_job('a' * 32, 'publish', 'ready_publish', '待发布')
        def remove():
            try:
                return bool(self.pool.remove_publish_queue_jobs(['a' * 32]))
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            removed = executor.submit(remove)
            published = executor.submit(self.pool.start_next_ready_publish_job)
            self.assertNotEqual(removed.result(), bool(published.result()))

    def test_resume_allows_concurrent_products_but_only_claims_each_task_once(self):
        ids = ['a' * 32, 'b' * 32]
        for detail_id, job_id in zip((123, 124), ids):
            self.pool.put_item(detail_id, 'site', 0, {'title': '商品'})
            self.pool.start_job(job_id, detail_id, 456, 'US', price='163.25', publish_after_save=True)
            self.pool.pause_for_problem(job_id, 'prepare', '图片缺失')
        def resume(job_id):
            try:
                return self.pool.resume_job(job_id)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(resume, [ids[0], ids[1], ids[0], ids[1]]))
        self.assertEqual(sum(r is not None for r in results), 2)
        for job_id in ids:
            job = self.pool.get_job(job_id)
            self.assertEqual(job['status'], 'running')
            self.assertFalse(job['publishAfterSave'])
            self.assertEqual(job['price'], '163.25')
            self.assertEqual(sum(e['message'] == '用户已确认，重新检查' for e in job['events']), 1)

    def test_local_database_busy_jobs_requeue_without_publishing(self):
        for detail_id, job_id in ((123, 'a' * 32), (124, 'b' * 32)):
            self.pool.put_item(detail_id, 'site', 0, {'title': '商品'})
            self.pool.start_job(job_id, detail_id, 456, 'US', publish_after_save=True)
            self.pool.pause_for_problem(job_id, 'claim', 'database is locked')
        self.assertEqual(self.pool.requeue_local_database_busy_jobs(), 2)
        resumed = self.pool.get_job('a' * 32)
        also_resumed = self.pool.get_job('b' * 32)
        self.assertEqual((resumed['status'], resumed['step']), ('queued', 'queued'))
        self.assertFalse(resumed['publishAfterSave'])
        self.assertIn('自动重新加入', resumed['message'])
        self.assertEqual((also_resumed['status'], also_resumed['step']), ('queued', 'queued'))

    def test_pool_serializes_many_short_writes(self):
        for detail_id in range(1000, 1024):
            self.pool.put_item(detail_id, 'site', 0, {'title': '商品'})
        queued = self.pool.queue_jobs(list(range(1000, 1024)), 456, 'US')
        job_ids = [job['jobId'] for job in queued]
        self.assertEqual(len(self.pool.start_next_queued_jobs(24)), 24)
        with ThreadPoolExecutor(max_workers=24) as executor:
            list(executor.map(lambda job_id: self.pool.update_job(job_id, 'prepare', 'running', '并发进度'), job_ids))
        self.assertTrue(all(self.pool.get_job(job_id)['message'] == '并发进度' for job_id in job_ids))

    def test_resume_blocks_another_active_task_for_same_product_and_shop(self):
        self.pool.put_item(123, 'site', 0, {'title': '商品'})
        self.pool.start_job('a' * 32, 123, 456, 'US')
        self.pool.update_job('a' * 32, 'save', 'failed', '失败')
        self.pool.start_job('b' * 32, 123, 456, 'US')
        self.pool.pause_for_problem('a' * 32, 'prepare', '暂停')
        with self.assertRaisesRegex(ValueError, '该商品在此店铺'):
            self.pool.resume_job('a' * 32)
        self.assertEqual(self.pool.get_job('a' * 32)['status'], 'needs_input')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pool = AutomationPool(Path(self.directory.name) / "pool.sqlite3")

    def test_queued_resume_preserves_choice_and_rejects_duplicate_submission(self):
        self.pool.put_item(123, 'site', 0, {'title': '商品'})
        job_id = 'a' * 32
        self.pool.start_job(job_id, 123, 456, 'US', price='162.34', interval_seconds=7)
        self.pool.pause_for_choice(job_id, 'freight', [{'id': 'chosen', 'name': '通用'}])
        with self.assertRaises(ValueError):
            self.pool.resume_job(job_id, 'invalid', queue_only=True)
        self.assertEqual(self.pool.get_job(job_id)['status'], 'needs_input')
        job = self.pool.resume_job(job_id, 'chosen', queue_only=True)
        self.assertEqual((job['status'], job['step']), ('queued', 'queued'))
        self.assertEqual(job['choices']['freight']['selectedId'], 'chosen')
        self.assertFalse(job['publishAfterSave'])
        self.assertEqual(job['price'], '162.34')
        with self.assertRaises(ValueError):
            self.pool.resume_job(job_id, 'chosen', queue_only=True)
        claimed = self.pool.start_next_queued_jobs(1)
        self.assertEqual([j['jobId'] for j in claimed], [job_id])

    def test_queued_retry_keeps_history_and_cannot_revive_deleted_job(self):
        self.pool.put_item(123, 'site', 0, {'title': '商品'})
        job_id = 'a' * 32
        self.pool.start_job(job_id, 123, 456, 'US')
        self.pool.update_job(job_id, 'prepare', 'failed', 'test')
        before = self.pool.get_job(job_id)
        job = self.pool.retry_job(job_id, queue_only=True)
        self.assertEqual(job['status'], 'queued')
        self.assertEqual(job['events'][:-1], before['events'])
        self.assertFalse(job['publishAfterSave'])
        with self.assertRaises(ValueError):
            self.pool.retry_job(job_id, queue_only=True)
        self.pool.start_next_queued_jobs(1)
        self.pool.pause_for_problem(job_id, 'titles', 'timeout')
        self.pool.delete_job_records([job_id], allow_pending=True)
        with self.assertRaises(ValueError):
            self.pool.resume_job(job_id, queue_only=True)

    def test_manual_retry_preserves_parameters_and_history_and_never_publishes(self):
        self.pool.put_item(123, 'site', 0, {'title': '测试商品'})
        for index, status in enumerate(('failed', 'needs_review', 'interrupted')):
            job_id = str(index) * 32
            self.pool.start_job(job_id, 123, 456, 'US', price='162.34', interval_seconds=7)
            self.pool.begin_auto_override(job_id, 'save', '第一次保存失败')
            if status == 'interrupted':
                self.pool.recover_interrupted_jobs()
            else:
                self.pool.update_job(job_id, 'save', status, '保存失败')
            before = self.pool.get_job(job_id)
            retried = self.pool.retry_job(job_id)
            for field in ('jobId', 'detailId', 'shopId', 'site', 'price', 'intervalSeconds', 'createdAt'):
                self.assertEqual(retried[field], before[field])
            self.assertEqual(retried['status'], 'running')
            self.assertFalse(retried['publishAfterSave'])
            self.assertEqual(retried['autoOverrideAttempts'], 0)
            self.assertEqual(retried['events'][:-1], before['events'])
            with self.assertRaises(ValueError):
                self.pool.retry_job(job_id)
            self.pool.update_job(job_id, 'publish', 'ready_publish', '等待发布')
            with self.assertRaises(ValueError):
                self.pool.retry_job(job_id)
            self.pool.update_job(job_id, 'complete', 'done', '完成')
            with self.assertRaises(ValueError):
                self.pool.retry_job(job_id)
            self.pool.update_job(job_id, 'save', 'failed', '失败')
            self.pool.delete_job_record(job_id)
            with self.assertRaises(ValueError):
                self.pool.retry_job(job_id)

    def test_retry_allows_other_products_to_keep_running(self):
        self.pool.put_item(123, 'site', 0, {'title': '商品 A'})
        self.pool.put_item(124, 'site', 0, {'title': '商品 B'})
        self.pool.start_job('a' * 32, 123, 456, 'US')
        self.pool.update_job('a' * 32, 'save', 'failed', '失败')
        self.pool.start_job('b' * 32, 124, 456, 'US')
        self.assertEqual(self.pool.retry_job('a' * 32)['status'], 'running')
        self.assertEqual(self.pool.get_job('b' * 32)['status'], 'running')

    def test_full_snapshot_is_persisted_and_list_is_summary_only(self):
        item = {"title": "测试商品", "imgUrls": ["https://example.com/1.jpg"], "skuMap": {"a": {"stock": 12}}}
        staged = self.pool.put_item(123, "site", 0, item)
        self.assertEqual(staged["title"], "测试商品")
        self.assertEqual(staged["status"], "待认领")
        self.assertNotIn("item", self.pool.list_items()[0])
        self.assertEqual(self.pool.get_item(123)["item"], item)
        self.assertEqual(AutomationPool(self.pool.path).get_item(123)["item"], item)

    def test_readding_same_item_updates_snapshot_without_duplicate(self):
        first = self.pool.put_item(123, "site", 0, {"title": "旧标题"})
        updated = self.pool.put_item(123, "shop", 456, {"title": "新标题"})
        self.assertEqual(len(self.pool.list_items()), 1)
        self.assertEqual(updated["createdAt"], first["createdAt"])
        self.assertEqual(updated["mode"], "shop")
        self.assertEqual(self.pool.get_item(123)["item"]["title"], "新标题")

    def test_removal_only_changes_local_pool(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        self.pool.record_claim(123, 456, "claimed")
        self.assertTrue(self.pool.remove_item(123))
        self.assertIsNone(self.pool.get_item(123))
        self.assertFalse(self.pool.remove_item(123))

    def test_claim_results_are_kept_per_target_shop(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        self.pool.record_claim(123, 456, "claimed")
        self.pool.record_claim(123, 789, "failed", "暂时失败")
        self.pool.put_item(123, "site", 0, {"title": "更新资料"})
        claims = self.pool.get_item(123)["claims"]
        self.assertEqual([(row["shopId"], row["status"]) for row in claims], [(456, "claimed"), (789, "failed")])
        self.assertEqual(claims[1]["error"], "暂时失败")

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.pool.put_item(123, "shop", 0, {"title": "测试商品"})

    def test_job_listing_keeps_every_submitted_status_for_task_badge(self):
        for detail_id in (101, 102, 103):
            self.pool.put_item(detail_id, "site", 0, {"title": f"商品 {detail_id}"})
        self.pool.start_job("a" * 32, 101, 456, "US")
        self.pool.update_job("a" * 32, "complete", "done", "已提交")
        self.pool.start_job("b" * 32, 102, 456, "US")
        self.pool.pause_for_problem("b" * 32, "preflight", "缺少配置")
        self.pool.cancel_paused_job("b" * 32)
        self.pool.start_job("c" * 32, 103, 456, "US")
        self.pool.update_job("c" * 32, "save", "needs_review", "需核查")
        jobs = self.pool.list_jobs()
        self.assertEqual(len(jobs), 3)
        self.assertEqual({job["status"] for job in jobs}, {"done", "canceled", "needs_review"})

    def test_definite_save_rejection_gets_only_one_automatic_retry(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        job_id = "r" * 32
        self.pool.start_job(job_id, 123, 456, "US")
        self.pool.update_job(job_id, "save", "needs_review", "groupIdAndValueMap is required")
        self.assertTrue(self.pool.begin_auto_override(job_id, "save", "groupIdAndValueMap is required"))
        retried = self.pool.get_job(job_id)
        self.assertEqual((retried["status"], retried["step"], retried["autoOverrideAttempts"]),
                         ("running", "preflight", 1))
        self.assertFalse(self.pool.begin_auto_override(job_id, "save", "再次失败"))
        self.assertIn("妙手明确拒绝保存，重新检查后重试第 1 次", retried["events"][-1]["message"])

    def test_startup_claims_only_definite_save_rejections_once(self):
        for detail_id in (123, 124, 125):
            self.pool.put_item(detail_id, "site", 0, {"title": f"测试商品 {detail_id}"})
        save_job, publish_job, rejected_job = "s" * 32, "p" * 32, "r" * 32
        self.pool.start_job(save_job, 123, 456, "US")
        self.pool.update_job(save_job, "save", "needs_review", "保存不确定")
        self.pool.start_job(publish_job, 124, 456, "US")
        self.pool.update_job(publish_job, "publish", "needs_review", "发布不确定")
        self.pool.start_job(rejected_job, 125, 456, "US")
        self.pool.update_job(rejected_job, "save", "needs_review", "groupIdAndValueMap is required")
        claimed = self.pool.claim_pending_auto_overrides()
        self.assertEqual([job["jobId"] for job in claimed], [rejected_job])
        self.assertEqual(self.pool.get_job(save_job)["autoOverrideAttempts"], 0)
        self.assertEqual(self.pool.get_job(save_job)["status"], "needs_review")
        self.assertEqual(self.pool.get_job(rejected_job)["autoOverrideAttempts"], 1)
        self.assertEqual(self.pool.get_job(publish_job)["status"], "needs_review")
        self.assertEqual(self.pool.claim_pending_auto_overrides(), [])

    def test_unknown_write_outcomes_never_claim_an_automatic_retry(self):
        self.pool.put_item(123, 'site', 0, {'title': 'test'})
        job_id = 'u' * 32
        self.pool.start_job(job_id, 123, 456, 'US')
        for message in ('保存结果不确定', 'request timed out', 'connection reset',
                        'MiaoShou returned HTTP 504: httpGatewayTimeout',
                        '产品属性【材质】必填; connection reset'):
            with self.subTest(message=message):
                self.pool.update_job(job_id, 'save', 'needs_review', message)
                before = self.pool.get_job(job_id)
                self.assertFalse(self.pool.begin_auto_override(job_id, 'save', message))
                self.assertEqual(self.pool.get_job(job_id), before)
                self.assertEqual(self.pool.claim_pending_auto_overrides(), [])

    def test_startup_resumes_only_legacy_snapshot_version_pauses(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        self.pool.put_item(124, "site", 0, {"title": "等待模板"})
        direct_job, choice_job = "v" * 32, "w" * 32
        self.pool.start_job(direct_job, 123, 456, "US")
        self.pool.pause_for_problem(direct_job, "preflight", "妙手商品资料已变化，请重新加入自动化池")
        self.pool.start_job(choice_job, 124, 456, "US")
        self.pool.pause_for_problem(choice_job, "freight", "未找到唯一默认运费模板")
        resumed = self.pool.claim_pending_direct_edit_jobs()
        self.assertEqual([job["jobId"] for job in resumed], [direct_job])
        self.assertEqual(self.pool.get_job(direct_job)["status"], "running")
        self.assertEqual(self.pool.get_job(choice_job)["status"], "needs_input")

    def test_delete_hides_terminal_job_and_allows_a_new_task(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        job_id = "d" * 32
        self.pool.start_job(job_id, 123, 456, "US")
        self.pool.update_job(job_id, "save", "running", "正在保存")
        self.pool.update_job(job_id, "save", "failed", "保存结果不确定")
        self.assertTrue(self.pool.delete_job_record(job_id))
        self.assertFalse(self.pool.delete_job_record(job_id))
        self.assertIsNone(self.pool.get_job(job_id))
        self.assertEqual(self.pool.list_jobs(), [])
        repeated = self.pool.start_job("e" * 32, 123, 456, "US")
        self.assertEqual(repeated['status'], 'running')
        self.assertEqual(repeated['events'][0]['message'], '任务已创建')

    def test_running_or_paused_job_cannot_be_deleted(self):
        self.pool.put_item(123, "site", 0, {"title": "测试商品"})
        job_id = "f" * 32
        self.pool.start_job(job_id, 123, 456, "US")
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.pool.delete_job_record(job_id)
        self.pool.pause_for_problem(job_id, "preflight", "等待选择")
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.pool.delete_job_record(job_id)
        self.pool.cancel_paused_job(job_id)
        self.assertTrue(self.pool.delete_job_record(job_id))

    def test_cancel_one_edit_job_keeps_publish_queue_untouched(self):
        self.pool.put_item(123, 'site', 0, {'title': '正在编辑'})
        self.pool.put_item(124, 'site', 0, {'title': '等待发布'})
        self.pool.start_job('a' * 32, 123, 456, 'US')
        stopped = self.pool.cancel_edit_job('a' * 32)
        self.assertEqual(stopped['status'], 'canceled')
        self.assertFalse(self.pool.is_job_active('a' * 32))
        self.pool.start_job('b' * 32, 124, 456, 'US')
        self.pool.update_job('b' * 32, 'publish', 'ready_publish', '等待发布')
        with self.assertRaisesRegex(ValueError, '发布队列'):
            self.pool.cancel_edit_job('b' * 32)
        self.assertEqual([job['jobId'] for job in self.pool.ready_publish_jobs()], ['b' * 32])

    def test_stop_and_clear_preserves_publish_queue_and_prevents_worker_updates(self):
        for detail_id in (123, 124, 125):
            self.pool.put_item(detail_id, 'site', 0, {'title': f'商品 {detail_id}'})
        self.pool.start_job('a' * 32, 123, 456, 'US')
        self.pool.update_job('a' * 32, 'publish', 'ready_publish', '等待发布')
        self.pool.start_job('b' * 32, 124, 456, 'US')
        self.pool.update_job('b' * 32, 'publish', 'ready_publish', '等待发布')
        self.pool.start_job('c' * 32, 125, 456, 'US')
        self.pool.update_job('c' * 32, 'complete', 'done', '已提交')
        self.pool.update_job('a' * 32, 'preflight', 'running', '正在编辑保存')

        result = self.pool.stop_and_clear_jobs()

        self.assertEqual(result, {'cleared': 2, 'stopped': 1})
        self.assertEqual([job['jobId'] for job in self.pool.list_jobs()], ['b' * 32])
        self.assertFalse(self.pool.is_job_active('a' * 32))
        self.assertTrue(self.pool.is_job_active('b' * 32))
        self.assertEqual([job['jobId'] for job in self.pool.ready_publish_jobs()], ['b' * 32])
        self.assertIsNone(self.pool.get_job('a' * 32))
        self.assertIsNotNone(self.pool.get_item(123))
        # A worker that completes an in-flight request after the clear must
        # not bring the hidden record back or write another event.
        self.pool.update_job('a' * 32, 'save', 'done', '迟到的保存结果')
        self.pool.pause_for_problem('a' * 32, 'save', '迟到的错误')
        self.assertEqual([job['jobId'] for job in self.pool.list_jobs()], ['b' * 32])
        self.assertEqual(self.pool.stop_and_clear_jobs(), {'cleared': 0, 'stopped': 0})

    def test_job_keeps_product_image_and_title_after_pool_removal(self):
        image_url = 'https://example.com/product.jpg'
        self.pool.put_item(123, 'site', 0, {'title': '商品标题', 'imgUrls': [image_url]})
        job_id = 'a' * 32
        created = self.pool.start_job(job_id, 123, 456, 'US')
        self.assertEqual(created['title'], '商品标题')
        self.assertEqual(created['thumbnail'], image_url)
        self.pool.update_job(job_id, 'complete', 'done', '已提交')
        self.assertTrue(self.pool.remove_item(123))
        preserved = AutomationPool(self.pool.path).get_job(job_id)
        self.assertEqual((preserved['title'], preserved['thumbnail']), ('商品标题', image_url))

    def test_batch_queue_runs_one_item_at_a_time_and_blocks_batch_removal(self):
        self.pool.put_item(101, 'site', 0, {'title': '商品一'})
        self.pool.put_item(102, 'site', 0, {'title': '商品二'})
        jobs = self.pool.queue_jobs([101, 102], 456, 'US', interval_seconds=10)
        self.assertEqual([job['status'] for job in jobs], ['queued', 'queued'])
        with self.assertRaisesRegex(ValueError, '正在排队'):
            self.pool.remove_items([101, 102])
        first = self.pool.start_next_queued_job()
        self.assertEqual(first['detailId'], 101)
        self.assertEqual(first['status'], 'running')
        self.assertIsNone(self.pool.start_next_queued_job())
        self.pool.update_job(first['jobId'], 'complete', 'done', '已提交')
        second = self.pool.start_next_queued_job()
        self.assertEqual(second['detailId'], 102)
        self.assertEqual(second['status'], 'running')

    def test_batch_queue_promotes_a_configurable_parallel_edit_group(self):
        for detail_id in range(201, 207):
            self.pool.put_item(detail_id, 'site', 0, {'title': f'商品 {detail_id}'})
        self.pool.queue_jobs(list(range(201, 207)), 456, 'US', interval_seconds=12)
        group = self.pool.start_next_queued_jobs(5)
        self.assertEqual([job['detailId'] for job in group], [201, 202, 203, 204, 205])
        self.assertTrue(all(job['status'] == 'running' for job in group))
        self.assertEqual(next(job for job in self.pool.list_jobs() if job['detailId'] == 206)['status'], 'queued')
        for job in group:
            self.pool.update_job(job['jobId'], 'publish', 'ready_publish', '等待统一发布')
        self.assertEqual(len(self.pool.ready_publish_jobs()), 5)
        next_group = self.pool.start_next_queued_jobs(5)
        self.assertEqual([job['detailId'] for job in next_group], [206])


if __name__ == "__main__":
    unittest.main()
