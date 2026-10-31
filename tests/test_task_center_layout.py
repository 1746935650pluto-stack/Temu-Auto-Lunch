import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TaskCenterLayoutTests(unittest.TestCase):
    def test_collection_pager_stays_outside_scroll_content(self):
        from html.parser import HTMLParser

        class ParentTracker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.parents = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                key = attrs.get('id') or attrs.get('class', '')
                self.parents[key] = tuple(self.stack)
                if tag not in {'input', 'img', 'br', 'hr', 'meta', 'link'}:
                    self.stack.append(key)

            def handle_endtag(self, tag):
                if self.stack:
                    self.stack.pop()

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                if tag not in {'input', 'img', 'br', 'hr', 'meta', 'link'}:
                    self.handle_endtag(tag)

        parser = ParentTracker()
        parser.feed((ROOT / 'index.html').read_text(encoding='utf-8'))
        self.assertIn('live-source-content', parser.parents['liveKeyword'])
        self.assertNotIn('live-source-content', parser.parents['liveProducts'])
        self.assertNotIn('live-source-content', parser.parents['livePageGo'])
        self.assertIn('liveSourceStep', parser.parents['livePageGo'])
        css = (ROOT / 'styles.css').read_text(encoding='utf-8')
        self.assertIn('@media (pointer: fine) and (min-width: 721px)', css)
        self.assertIn('body.workbench-active .main-content { display: flex; flex-direction: column; }', css)
        self.assertIn('height: auto; min-height: 0; flex: 1 1 0; margin: 0;', css)
        self.assertIn('flex-direction: column; overflow: auto; scrollbar-gutter: stable;', css)
        self.assertIn('flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-start;', css)

    def test_batch_retry_button_follows_unsaved_filter(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('data-save-filter="unsaved"'), html.index('id="retryAutomationJobsBatch"'))
        self.assertLess(html.index('id="retryAutomationJobsBatch"'), html.index('id="automationJobList"'))
        self.assertEqual(html.count('id="retryAutomationJobsBatch"'), 1)

    def test_automation_progress_lives_only_in_task_center(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        automation_start = html.index('<section class="page" id="automation">')
        tasks_start = html.index('<section class="page" id="tasks">')
        next_page = html.index('<section class="page" id="publishRecords">')
        automation = html[automation_start:tasks_start]
        tasks = html[tasks_start:next_page]
        self.assertIn('id="automationOpenTasks"', automation)
        self.assertNotIn('id="automationJobList"', automation)
        self.assertIn('id="automationJobList"', tasks)
        self.assertIn('id="automationJobFeedback"', tasks)
        self.assertIn('id="refreshAutomationJobs"', tasks)
        self.assertEqual(html.count('id="automationJobList"'), 1)

    def test_sidebar_badge_counts_all_submitted_automation_jobs(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('const submitted = automationState.jobs.length;', javascript)
        self.assertIn("badge.textContent = String(submitted)", javascript)
        self.assertIn('renderAutomationJobs();\n    }', javascript)

    def test_failed_task_offers_editor_and_uses_shop_name(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        editor = (ROOT / 'live-editor.js').read_text(encoding='utf-8')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn("automationShopName(job.shopId)", javascript)
        self.assertIn("editAutomationJob(job)", javascript)
        self.assertIn("retry.addEventListener('click', () => retryAutomationJob(job))", javascript)
        self.assertIn("/api/automation/retry", javascript)
        self.assertIn("retry.disabled = automationState.batchRetrying || automationState.batchRestoring || automationState.retryingJobIds.has(job.jobId)", javascript)
        self.assertNotIn("force.addEventListener('click', () => forcePublishAutomationJob(job))", javascript)
        self.assertIn("重试失败·需核查", javascript)
        self.assertIn("openEditorForAutomationTask", editor)
        self.assertIn('id="editSizeCharts"', html)

    def test_paused_tasks_offer_editor_without_starting_a_retry(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        editor = (ROOT / 'live-editor.js').read_text(encoding='utf-8')
        start = javascript.index("if (['needs_input', 'failed', 'needs_review', 'interrupted'].includes(job.status))")
        end = javascript.index("if (['failed', 'needs_review', 'interrupted'].includes(job.status))", start)
        block = javascript[start:end]
        self.assertIn("edit.addEventListener('click', () => editAutomationJob(job))", block)
        self.assertNotIn('retryAutomationJob', block)
        self.assertIn('再返回任务中心点击“已处理问题，重新检查”', editor)

    def test_terminal_task_records_can_be_collapsed(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        css = (ROOT / 'styles.css').read_text(encoding='utf-8')
        self.assertIn("jobRecordExpanded: new Map()", javascript)
        self.assertIn("automation-job-overview-toggle", javascript)
        self.assertIn("overview.addEventListener('click'", javascript)
        self.assertIn("event.target.closest('button, a, input, select, label')", javascript)
        self.assertIn("details.hidden = !expanded", javascript)
        self.assertIn('.automation-job-details[hidden]', css)

    def test_automation_save_does_not_prompt_before_default_force_overwrite(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        submit_start = javascript.index('async function claimAutomationSelection()')
        submit_end = javascript.index('function appendOption(', submit_start)
        self.assertNotIn('window.confirm', javascript[submit_start:submit_end])
        self.assertNotIn("force.className = 'button primary automation-job-force'", javascript)

    def test_ai_title_result_replaces_the_current_title_and_records_show_thumbnails(self):
        editor = (ROOT / 'live-editor.js').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        css = (ROOT / 'styles.css').read_text(encoding='utf-8')
        self.assertIn("editEl(targetId).value = suggestion", editor)
        self.assertIn("updateTitleCounts();", editor)
        self.assertIn("record.thumbnail", javascript)
        self.assertIn("publish-record-thumbnail", javascript)
        self.assertIn(".publish-record-thumbnail", css)

    def test_automation_pool_supports_batch_selection_queue_and_saved_targets(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="publishQueuePublishInterval"', html)
        self.assertIn('id="automationEditConcurrency"', html)
        self.assertIn('id="automationSkuPrice"', html)
        self.assertIn('id="automationPriceMode"', html)
        self.assertIn('id="automationPriceMultiplier"', html)
        self.assertIn('SKU 申报价下限 X（人民币）', html)
        self.assertIn('X ～ X+20', html)
        self.assertIn('max="999980"', html)
        self.assertIn('id="automationBatchRemove"', html)
        self.assertNotIn('id="automationAllowDuplicate"', html)
        self.assertIn("automationPreferencesKey", javascript)
        self.assertIn("price: $('#automationSkuPrice').value", javascript)
        self.assertIn("priceMode: $('#automationPriceMode').value", javascript)
        self.assertIn("priceMultiplier: $('#automationPriceMultiplier').value", javascript)
        self.assertIn("/api/automation/batch-run", javascript)
        self.assertIn("if (response.status === 404)", javascript)
        self.assertNotIn("waitForCompatibilityAutomationJob", javascript)
        self.assertNotIn("/api/automation/run", javascript)
        self.assertIn("/api/automation/pool/batch", javascript)
        self.assertIn("legacyRoute = response.status === 404", javascript)
        self.assertIn("invalid literal.*['\"]batch['\"]", javascript)

    def test_task_center_supports_batch_deleting_finished_records(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="selectAllAutomationJobs"', html)
        self.assertIn('id="deleteAutomationJobsBatch"', html)
        self.assertIn('selectedJobIds: new Set()', javascript)
        self.assertIn('toggleSelectAllAutomationJobs', javascript)
        self.assertIn('deleteSelectedAutomationJobs', javascript)
        self.assertIn('/api/automation/jobs/batch', javascript)

    def test_task_center_contains_two_columns_and_no_separate_queue_navigation(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertNotIn('data-page="publishQueue"', html)
        self.assertNotIn('<section class="page" id="publishQueue">', html)
        tasks = html[html.index('<section class="page" id="tasks">'):html.index('<section class="page" id="publishRecords">')]
        for marker in ('task-center-columns', 'task-records-column', 'task-publish-column',
                       'data-save-filter="saved"', 'data-save-filter="unsaved"', 'id="publishQueueList"'):
            self.assertIn(marker, tasks)
        for identifier in ('publishQueueList', 'publishQueueBadge', 'publishQueuePublishInterval', 'publishQueuePublishCount', 'automationJobFeedback'):
            self.assertEqual(html.count(f'id="{identifier}"'), 1)
        self.assertIn('id="publishQueueList"', html)
        self.assertIn('id="publishReadyAutomationJobs"', html)
        self.assertIn("job.status === 'ready_publish'", javascript)
        self.assertIn("/api/automation/publish-ready", javascript)

    def test_collect_box_supports_selecting_multiple_products_for_local_staging(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="liveAddAutomation"', html)
        self.assertNotIn('id="liveAddAutomationBatch"', html)
        self.assertIn('id="livePoolSelectAll"', html)
        self.assertIn('poolSelected: new Map()', javascript)
        self.assertIn('addPoolSelectionToAutomationPool', javascript)
        self.assertIn('addToAutomationPool', javascript)
        self.assertIn("if (response.status === 404)", javascript)
        self.assertIn("fetch('/api/automation/pool'", javascript)

    def test_automation_pool_uses_store_sync_labels_without_changing_claim_filter_values(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('待同步到店铺', html)
        self.assertIn('已同步到店铺', html)
        self.assertIn('店铺同步状态筛选', html)
        self.assertIn("data-automation-claim-filter=\"pending\"", html)
        self.assertIn("data-automation-claim-filter=\"claimed\"", html)
        self.assertIn("'该目标店铺已同步'", javascript)
        self.assertIn("'待同步'", javascript)

    def test_collect_box_and_editor_are_one_miaoshou_navigation_entry(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        editor = (ROOT / 'live-editor.js').read_text(encoding='utf-8')
        self.assertIn('id="miaoshouCollectionToggle"', html)
        self.assertIn('data-page="workbench" data-account-id="1"><span>1</span>妙手采集箱 1', html)
        self.assertLess(html.index('id="miaoshouCollectionNav"'), html.index('id="orderAccountsNav"'))
        self.assertLess(html.index('id="orderAccountsNav"'), html.index('data-page="collectionAutomation"'))
        self.assertNotIn('data-page="productEditor"', html)
        self.assertNotIn('id="productEditor"', html)
        self.assertNotIn("pageId === 'productEditor'", javascript)
        self.assertIn(".nav-item[data-page=\"workbench\"]", editor)
        self.assertNotIn(".nav-item[data-page=\"productEditor\"]", editor)

    def test_collection_automation_module_is_a_no_write_placeholder(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        collect_box = html.index('data-page="workbench"')
        collection = html.index('data-page="collectionAutomation"')
        listing = html.index('data-page="automation"')
        self.assertLess(collect_box, collection)
        self.assertLess(collection, listing)
        self.assertIn('id="collectionAutomation"', html)
        self.assertIn('当前仅提供模块框架', html)
        self.assertIn('不会读取妙手采集箱', html)
        self.assertNotIn('openAutomationCollectBox', html)
        self.assertNotIn('openAutomationListing', html)

    def test_deleted_collect_box_jobs_offer_one_click_batch_restore(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="restoreDeletedAutomationJobsBatch"', html)
        self.assertIn('deletedCollectBoxRestoreCandidates', javascript)
        self.assertIn('restoreDeletedAutomationJobsBatch', javascript)
        self.assertIn("'/api/automation/restore-deleted'", javascript)
        self.assertIn("batchRestoring: false", javascript)

    def test_collect_box_list_is_prioritized_over_optional_catalog_reads(self):
        javascript = (ROOT / 'app.js').read_text(encoding='utf-8')
        catalog_start = javascript.index('async function loadLiveCatalog(')
        catalog_end = javascript.index('async function loadLiveProducts(', catalog_start)
        catalog = javascript[catalog_start:catalog_end]
        self.assertLess(catalog.index('const productTask = loadLiveProducts('), catalog.index('Promise.allSettled([apiGet'))


if __name__ == '__main__':
    unittest.main()
