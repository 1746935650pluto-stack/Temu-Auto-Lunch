import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from automation_pool import AutomationPool
from test_item_editor import sample_item


class FakeAI:
    configured = True

    @staticmethod
    def polish_title(title, language):
        return '优化中文标题' if language == 'zh' else 'Optimized English title'

    @staticmethod
    def weighted_length(title):
        return len(title)


class AutomationWorkflowTests(unittest.TestCase):
    def test_incomplete_size_chart_stops_before_ai_or_save(self):
        from unittest.mock import Mock
        self.source['collectBoxDetailShopList'] = [{'shopId': 1234}]
        self.source['sizeCharts'] = [{'sizeItems': [{'elementValueList': [{'elementId': 1, 'elementValue': ''}]}]}]
        job_id = 'f' * 32
        self.pool.start_job(job_id, 777, 1234, 'US')
        reads = {
            server.SHOP_LIST: {'shopList': [{'shopId': 1234}]},
            server.HALF_SITES: {'publishSiteOptions': [{'key': 'US'}]},
            server.HALF_FREIGHT: {'shopIdAndFreightTemplateListMap': {'1234': [
                {'site': 'US', 'templateName': '通用', 'templateId': 11}]}},
            server.HALF_WAREHOUSE: {'shopIdAndWarehouseListMap': {'1234': [
                {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}},
        }
        writes = []
        def vendor(path, payload):
            if path in reads:
                return {'data': reads[path]}
            writes.append(path)
            raise AssertionError('Unexpected write or late read')
        ai = Mock(configured=True)
        with patch.object(server.time, 'sleep'):
            server.run_automation_job(self.pool, job_id, vendor,
                lambda *_: copy.deepcopy(self.source), lambda *_: {}, ai)
        ai.polish_titles.assert_not_called()
        ai.polish_title.assert_not_called()
        ai.infer_attribute.assert_not_called()
        self.assertEqual(writes, [])
        self.assertIn('尺码表', self.pool.get_job(job_id)['message'])

    def test_missing_detail_image_uses_existing_main_image_and_preserves_text(self):
        raw = sample_item()
        raw['goodsLayerDecorationReqs'] = [{'type': 'text', 'content': {'text': '原始说明'}}]
        before = copy.deepcopy(raw)
        prepared = server._automation_item_for_save(raw, 123, 456, 'US', '中文标题', 'English title', '1', '2')
        self.assertEqual(raw, before)
        self.assertEqual(prepared['goodsLayerDecorationReqs'][0], raw['goodsLayerDecorationReqs'][0])
        self.assertEqual(prepared['goodsLayerDecorationReqs'][-1]['content']['imgUrl'], raw['imgUrls'][0])
        again = server._automation_item_for_save(prepared, 123, 456, 'US', '中文标题', 'English title', '1', '2')
        self.assertEqual(again['goodsLayerDecorationReqs'], prepared['goodsLayerDecorationReqs'])

    def test_miaoshou_request_retries_rate_limit_and_gateway_timeout(self):
        attempts = []

        def call():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError('账户接口每秒请求频率超限')
            if len(attempts) == 2:
                raise RuntimeError('MiaoShou returned HTTP 504: httpGatewayTimeout')
            return {'data': 'ok'}

        with patch.object(server.time, 'sleep'):
            self.assertEqual(server.miao_request(call), {'data': 'ok'})
        self.assertEqual(len(attempts), 3)

    def test_textile_composition_default_is_enabled_for_automation(self):
        self.assertEqual(server.AUTOMATION_CHOICE_DEFAULTS['织物材质成分'], '人造纤维')
        self.assertEqual(server.AUTOMATION_CHOICE_DEFAULTS['刀片材质'], '不锈钢')
        self.assertEqual(server.AUTOMATION_CHOICE_DEFAULTS['形状'], '不规则形')
        self.assertEqual(server.AUTOMATION_CHOICE_DEFAULTS['材质类型'], '非纺织品材质')
        self.assertEqual(server.AUTOMATION_CHOICE_DEFAULTS['面料纹理1'], '光面')

    def test_invalid_size_template_error_is_recognized_without_hiding_other_read_errors(self):
        self.assertTrue(server._is_invalid_size_template_id_error(
            RuntimeError('collectShowSizeTemplateIds[0]: 期望 int, 实际 string(uuid)')
        ))
        self.assertFalse(server._is_invalid_size_template_id_error(RuntimeError('详情读取超时')))

    def test_created_collect_box_detail_id_accepts_documented_response_shapes(self):
        for response, expected in (({'data': {'collectBoxDetailId': '778'}}, 778),
                                   ({'data': {'siteCollectItemInfo': {'detailId': 779}}}, 779),
                                   ({'detailId': 780}, 780)):
            with self.subTest(response=response):
                self.assertEqual(server.created_collect_box_detail_id(response), expected)
        with self.assertRaisesRegex(RuntimeError, '未返回新的采集箱 ID'):
            server.created_collect_box_detail_id({'data': {'taskId': '781'}})

    def test_duplicate_server_launch_does_not_recover_or_resume_any_jobs(self):
        with patch.object(server, 'ThreadingHTTPServer', side_effect=OSError('port occupied')), \
             patch.object(server.AUTOMATION_POOL, 'recover_interrupted_jobs') as recover, \
              patch.object(server, 'resume_queued_automation_jobs_after_start') as resume, \
             patch.object(server.os, 'chdir'):
            with self.assertRaises(OSError):
                server.main()
            recover.assert_not_called()
            resume.assert_not_called()

    def test_startup_resumes_jobs_that_were_only_queued(self):
        with patch.object(self.pool, 'has_queued_jobs', return_value=True), \
              patch.object(server, 'Thread') as worker_thread:
            self.assertTrue(server.resume_queued_automation_jobs_after_start(self.pool))
        worker_thread.assert_called_once()
        _, options = worker_thread.call_args
        self.assertIs(options['target'], server.run_queued_automation_jobs)
        self.assertTrue(options['daemon'])
        self.assertEqual(options['name'], 'automation-queue-recovery')
        worker_thread.return_value.start.assert_called_once_with()

    def test_startup_does_not_create_worker_when_queue_is_empty(self):
        with patch.object(self.pool, 'has_queued_jobs', return_value=False), \
              patch.object(server, 'Thread') as worker_thread:
            self.assertFalse(server.resume_queued_automation_jobs_after_start(self.pool))
        worker_thread.assert_not_called()

    def test_parallel_queue_refills_while_slow_task_runs_and_other_task_pauses(self):
        for detail_id in (778, 779):
            self.pool.put_item(detail_id, 'site', 0, self.source)
        self.pool.queue_jobs([777, 778, 779], 1234, 'US')
        slow_started = threading.Event()
        release_slow = threading.Event()
        third_started = threading.Event()
        errors = []

        def edit(pool, job_id, *args, **kwargs):
            detail_id = pool.get_job(job_id)['detailId']
            if not kwargs.get('defer_publish'):
                errors.append('must wait for manual publication')
            if detail_id == 777:
                slow_started.set()
                release_slow.wait(5)
            elif detail_id == 778:
                if not slow_started.wait(3):
                    errors.append('tasks were not concurrent')
                pool.pause_for_problem(job_id, 'prepare', '缺少属性')
                return
            else:
                if release_slow.is_set():
                    errors.append('next task waited for whole group')
                third_started.set()
            pool.update_job(job_id, 'publish', 'ready_publish', '已保存')

        with patch.object(server, 'run_automation_job', side_effect=edit):
            worker = threading.Thread(target=server.run_queued_automation_jobs,
                                      args=(self.pool, None, None, None, None),
                                      kwargs={'edit_concurrency': 2})
            worker.start()
            try:
                self.assertTrue(third_started.wait(4), 'empty worker slot was not refilled')
            finally:
                release_slow.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(job['status'] for job in self.pool.list_jobs()),
                         ['needs_input', 'ready_publish', 'ready_publish'])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pool = AutomationPool(Path(directory.name) / 'pool.sqlite3')
        self.source = sample_item()
        self.source['imgUrls'] = [f'https://example.com/{index}.jpg' for index in range(4)]
        self.source['collectBoxDetailShopList'] = []
        self.source['multiLanguageTitleMap'] = {'en': 'Original English title'}
        self.pool.put_item(777, 'site', 0, self.source)

    def test_one_item_flow_records_each_submission_and_keeps_first_image(self):
        self.source['imgUrls'] = [f'https://example.com/{index}.jpg' for index in range(14)]
        self.pool.put_item(777, 'site', 0, self.source)
        state = {'claimed': False, 'saved': None, 'published': False, 'writes': []}

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_CATEGORY_ATTRIBUTE_RULES:
                return {'data': {'productAttributeRules': [{
                    'name': '与食品接触部位材质', 'pid': 10, 'templatePid': 20,
                    'values': [{'name': '不锈钢', 'vid': 30}],
                }, {'name': '刀刃长度', 'pid': 40, 'templatePid': 50, 'valueUnit': ['cm']},
                    {'name': '是否可用于食品接触', 'pid': 1795, 'templatePid': 1261920,
                     'values': [{'vid': 67313, 'name': '是'}, {'vid': 67314, 'name': '否'}]},
                    {'name': '货源产地', 'pid': 1920, 'templatePid': 1186968, 'required': True,
                     'valueType': 'SELECT', 'chooseMaxNum': 1, 'values': [{'vid': 82815, 'name': '广州产区'}]}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            state['writes'].append(path)
            if path == server.HALF_CLAIM_TO_SHOP:
                state['claimed'] = True
            elif path == server.HALF_SHOP_SAVE:
                for attribute in payload['shopCollectItemInfo']['attributes']:
                    for value in attribute['values']:
                        if 'vid' not in value or value['vid'] is None:
                            raise RuntimeError('attribute.values.vid 必填')
                state['saved'] = copy.deepcopy(payload['shopCollectItemInfo'])
            elif path == server.HALF_PUBLISH:
                state['published'] = True
            return {'data': {}}

        def get_item(detail_id, mode, shop_id=0):
            self.assertEqual(detail_id, 777)
            if mode == 'site':
                value = copy.deepcopy(self.source)
                if state['claimed']:
                    value['collectBoxDetailShopList'] = [{'shopId': 1234}]
                return value
            return copy.deepcopy(state['saved'] or self.source)

        job_id = 'a' * 32
        self.pool.start_job(job_id, 777, 1234, 'US', price='199.50')
        self.source['title'] = '妙手当前已修改的标题'
        with patch.object(server.time, 'sleep'):
            server.run_automation_job(self.pool, job_id, vendor, get_item,
                                      lambda *_: {'ready': True, 'issues': []}, FakeAI())
        job = self.pool.get_job(job_id)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['price'], '199.50')
        title_event = next(event['message'] for event in job['events']
                           if event['step'] == 'titles' and event['status'] == 'done')
        self.assertIn('中文「优化中文标题」', title_event)
        self.assertIn('英文「Optimized English title」', title_event)
        self.assertEqual(job['title'], '优化中文标题')
        self.assertIn('不比较商品资料版本，按本次任务直接编辑并覆盖保存',
                      next(event['message'] for event in job['events']
                           if event['step'] == 'preflight' and event['status'] == 'done'))
        self.assertTrue(state['published'])
        self.assertEqual(state['writes'], [server.HALF_CLAIM_TO_SHOP, server.HALF_SHOP_SAVE, server.HALF_PUBLISH])
        saved = state['saved']
        self.assertEqual(saved['attributes'][0]['values'][0], {
            'vid': 30, 'name': '不锈钢', 'valueUnit': '', 'numberValue': None,
        })
        self.assertEqual(saved['attributes'][1]['values'][0]['numberValue'], '5')
        self.assertEqual(saved['attributes'][1]['values'][0]['vid'], 0)
        self.assertEqual(saved['attributes'][1]['values'][0]['valueUnit'], 'cm')
        self.assertEqual(saved['attributes'][2]['values'][0]['vid'], 67313)
        self.assertEqual(saved['attributes'][2]['values'][0]['name'], '是')
        self.assertEqual(saved['attributes'][3]['values'][0]['vid'], 82815)
        self.assertEqual(saved['imgUrls'][0], self.source['imgUrls'][0])
        self.assertEqual(len(saved['imgUrls']), 10)
        self.assertTrue(set(saved['imgUrls'][1:]).issubset(self.source['imgUrls'][1:]))
        self.assertEqual(self.pool.get_item(777)['item']['imgUrls'], self.source['imgUrls'])
        self.assertIn('原有 14 张，本次提交 10 张，本地快照不删图',
                      next(event['message'] for event in job['events']
                           if event['step'] == 'prepare' and event['status'] == 'done'))
        self.assertNotEqual(saved['imgUrls'][1:], self.source['imgUrls'][1:])
        self.assertEqual(saved['skuMap'][';sample;']['price'], '199.50')
        self.assertEqual(saved['skuMap'][';sample;']['siteAndWarehouseIdAndStockMap']['US']['22'], 1000)
        self.assertEqual(saved['siteFreightTemplateList'][0]['freightTemplateId'], '11')
        self.assertEqual(saved['siteWarehouseList'][0]['warehouseId'], '22')
        completed_steps = [event['step'] for event in job['events'] if event['status'] == 'done']
        self.assertEqual(set(completed_steps),
                         {'queued', 'claim', 'preflight', 'titles', 'prepare', 'save', 'publish', 'complete'})
        self.assertLess(completed_steps.index('claim'), completed_steps.index('preflight'))
        self.assertLess(completed_steps.index('prepare'), completed_steps.index('titles'))
        self.assertNotIn('verify', completed_steps)
        self.pool.remove_item(777)
        self.assertEqual(AutomationPool(self.pool.path).get_job(job_id)['status'], 'done')

    def test_missing_default_logistics_stops_before_remote_write(self):
        writes = []

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '其他模板', 'templateId': 19}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            writes.append(path)
            return {'data': {}}

        job_id = 'b' * 32
        self.pool.start_job(job_id, 777, 1234, 'US')
        def get_item(_detail_id, mode, _shop_id=0):
            item = copy.deepcopy(self.source)
            if mode == 'site':
                item['collectBoxDetailShopList'] = [{'shopId': 1234}]
            return item
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI())
        self.assertEqual(self.pool.get_job(job_id)['status'], 'needs_input')
        self.assertEqual(self.pool.get_job(job_id)['step'], 'freight')
        self.assertEqual(self.pool.get_job(job_id)['choices']['freight']['options'][0]['id'], '19')
        self.assertFalse(set(writes) & {server.HALF_CLAIM_TO_SHOP, server.HALF_SHOP_SAVE, server.HALF_PUBLISH})
        with self.assertRaises(ValueError):
            self.pool.resume_job(job_id, 'not-an-option')
        resumed = self.pool.resume_job(job_id, '19')
        self.assertEqual(resumed['choices']['freight']['selectedId'], '19')
        self.assertEqual(resumed['status'], 'running')

    def test_save_started_allows_a_new_task_after_failure(self):
        self.pool.start_job('c' * 32, 777, 1234, 'US')
        self.pool.update_job('c' * 32, 'save', 'running', '正在保存')
        self.pool.update_job('c' * 32, 'save', 'failed', '结果不确定')
        repeated = self.pool.start_job('d' * 32, 777, 1234, 'US')
        self.assertEqual(repeated['status'], 'running')

    def test_size_chart_null_group_map_is_normalized_for_save(self):
        source = copy.deepcopy(self.source)
        source['sizeCharts'] = [{
            'sizeTemplateId': '100',
            'sizeItems': [
                {'sizeName': '36', 'groupIdAndValueMap': None, 'elementValueList': []},
                {'sizeName': '37', 'groupIdAndValueMap': {'1': '2'}, 'elementValueList': []},
            ],
        }]
        prepared = server._automation_item_for_save(
            source, 777, 1234, 'US', '优化中文标题', 'Optimized English title', '11', '22'
        )
        rows = prepared['sizeCharts'][0]['sizeItems']
        self.assertEqual(rows[0]['groupIdAndValueMap'], {})
        self.assertEqual(rows[1]['groupIdAndValueMap'], {'1': '2'})

    def test_invalid_size_template_uuid_is_cleared_before_automation_save(self):
        source = copy.deepcopy(self.source)
        source['collectShowSizeTemplateIds'] = ['984e3e0d-1ff0-4686-8a11-16f5992213d7']
        prepared = server._automation_item_for_save(
            source, 777, 1234, 'US', '优化中文标题', 'Optimized English title', '11', '22'
        )
        self.assertEqual(prepared['collectShowSizeTemplateIds'], [])
        self.assertEqual(source['collectShowSizeTemplateIds'], ['984e3e0d-1ff0-4686-8a11-16f5992213d7'])

        source['collectShowSizeTemplateIds'] = ['18', 25, 'invalid']
        prepared = server._automation_item_for_save(
            source, 777, 1234, 'US', '优化中文标题', 'Optimized English title', '11', '22'
        )
        self.assertEqual(prepared['collectShowSizeTemplateIds'], [18, 25])

    def test_automation_caps_outgoing_images_without_changing_snapshots(self):
        for count in (1, 9, 10, 11, 14, 50):
            images = [f'https://example.com/{index}.jpg' for index in range(count)]
            for use_fallback in (False, True):
                with self.subTest(count=count, fallback=use_fallback):
                    source = copy.deepcopy(self.source)
                    source['imgUrls'] = [] if use_fallback else list(images)
                    original = copy.deepcopy(source)
                    fallback = list(images)
                    # Reverse deterministically to verify sampling happens AFTER shuffling.
                    with patch.object(server.random.SystemRandom, 'shuffle', side_effect=lambda rows: rows.reverse()):
                        prepared = server._automation_item_for_save(
                            source, 777, 1234, 'US', '中文标题', 'English title', '11', '22',
                            fallback_images=fallback,
                        )
                    self.assertEqual(prepared['imgUrls'], [images[0], *list(reversed(images[1:]))[:9]])
                    self.assertEqual(len(prepared['imgUrls']), min(count, 10))
                    self.assertEqual(source, original)
                    self.assertEqual(fallback, images)

    def test_default_publish_rewrites_then_reads_back_before_publication(self):
        state = {'claimed': True, 'saved': None, 'writes': []}

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            state['writes'].append(path)
            if path == server.HALF_SHOP_SAVE:
                state['saved'] = copy.deepcopy(payload['shopCollectItemInfo'])
            return {'data': {}}

        def get_item(_detail_id, mode, _shop_id=0):
            value = copy.deepcopy(state['saved'] or self.source)
            if mode == 'site': value['collectBoxDetailShopList'] = [{'shopId': 1234}]
            return value

        job_id = 'h' * 32
        self.pool.start_job(job_id, 777, 1234, 'US')
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': False, 'issues': ['库存未回写']}, FakeAI())
        job = self.pool.get_job(job_id)
        self.assertEqual(job['status'], 'done')
        self.assertIn(server.HALF_SHOP_SAVE, state['writes'])
        self.assertIn(server.HALF_PUBLISH, state['writes'])
        self.assertFalse(any(event['step'] == 'verify' for event in job['events']))

    def test_batch_edit_saves_then_waits_for_explicit_queue_publication(self):
        state = {'saved': None, 'writes': []}

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            state['writes'].append(path)
            if path == server.HALF_SHOP_SAVE:
                state['saved'] = copy.deepcopy(payload['shopCollectItemInfo'])
            return {'data': {}}

        def get_item(_detail_id, mode, _shop_id=0):
            item = copy.deepcopy(state['saved'] or self.source)
            if mode == 'site':
                item['collectBoxDetailShopList'] = [{'shopId': 1234}]
            if mode == 'shop':
                item['imgUrls'] = []
            return item

        job_id = 'i' * 32
        self.pool.start_job(job_id, 777, 1234, 'US', publish_after_save=False, interval_seconds=15)
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI(), defer_publish=True)
        saved = self.pool.get_job(job_id)
        self.assertEqual(saved['status'], 'ready_publish')
        self.assertEqual(saved['intervalSeconds'], 15)
        self.assertIn(server.HALF_SHOP_SAVE, state['writes'])
        self.assertNotIn(server.HALF_PUBLISH, state['writes'])
        self.assertEqual(state['saved']['imgUrls'][0], self.source['imgUrls'][0])
        self.assertIn('自动化池快照图片', next(event['message'] for event in saved['events']
                                            if event['step'] == 'prepare' and event['status'] == 'done'))
        released = self.pool.start_next_ready_publish_job()
        self.assertEqual(released['jobId'], job_id)
        server.publish_automation_job(self.pool, job_id, vendor)
        self.assertEqual(self.pool.get_job(job_id)['status'], 'done')
        self.assertIn(server.HALF_PUBLISH, state['writes'])

    def test_missing_sku_packaging_is_defaulted_and_saved_before_manual_publication(self):
        self.source['skuMap'][';sample;'].update(length=None, width='', height=None, weight=None)
        state = {'saved': None, 'writes': []}

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            state['writes'].append(path)
            if path == server.HALF_SHOP_SAVE:
                state['saved'] = copy.deepcopy(payload['shopCollectItemInfo'])
            return {'data': {}}

        def get_item(_detail_id, mode, _shop_id=0):
            item = copy.deepcopy(state['saved'] or self.source)
            if mode == 'site':
                item['collectBoxDetailShopList'] = [{'shopId': 1234}]
            if mode == 'shop':
                item['imgUrls'] = []
            return item

        job_id = 'i' * 32
        self.pool.start_job(job_id, 777, 1234, 'US', publish_after_save=False, interval_seconds=15)
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI(), defer_publish=True)
        saved = self.pool.get_job(job_id)
        self.assertEqual(saved['status'], 'ready_publish')
        self.assertEqual(saved['intervalSeconds'], 15)
        self.assertIn(server.HALF_SHOP_SAVE, state['writes'])
        self.assertNotIn(server.HALF_PUBLISH, state['writes'])
        self.assertEqual(state['saved']['imgUrls'][0], self.source['imgUrls'][0])
        self.assertIn('自动化池快照图片', next(event['message'] for event in saved['events']
                                            if event['step'] == 'prepare' and event['status'] == 'done'))
        sku = state['saved']['skuMap'][';sample;']
        self.assertEqual([sku[k] for k in ('length','width','height','weight')], ['10','10','3',200])
        self.assertIsNone(self.source['skuMap'][';sample;']['length'])
        self.assertTrue(any('用户默认规则' in e['message'] for e in saved['events']))

    def test_unknown_save_result_stops_for_review_without_replay(self):
        state = {'save_attempts': 0, 'writes': []}

        def vendor(path, payload):
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '通用', 'templateId': 11}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            state['writes'].append(path)
            if path == server.HALF_SHOP_SAVE:
                state['save_attempts'] += 1
                if state['save_attempts'] == 1:
                    raise RuntimeError('妙手保存结果暂时不确定')
            return {'data': {}}

        def get_item(_detail_id, mode, _shop_id=0):
            item = copy.deepcopy(self.source)
            if mode == 'site':
                item['collectBoxDetailShopList'] = [{'shopId': 1234}]
            return item

        job_id = 'j' * 32
        self.pool.start_job(job_id, 777, 1234, 'US', publish_after_save=False)
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI(), defer_publish=True)
        saved = self.pool.get_job(job_id)
        self.assertEqual(saved['status'], 'needs_review')
        self.assertEqual(saved['autoOverrideAttempts'], 0)
        self.assertEqual(state['save_attempts'], 1)
        self.assertNotIn(server.HALF_PUBLISH, state['writes'])
        self.assertFalse(any(event['step'] == 'verify' for event in saved['events']))

    def test_selected_nondefault_template_is_used_on_resume(self):
        calls = []

        def vendor(path, payload):
            calls.append((path, payload))
            if path == server.SHOP_LIST:
                return {'data': {'shopList': [{'shopId': 1234}]}}
            if path == server.HALF_SITES:
                return {'data': {'publishSiteOptions': [{'key': 'US'}]}}
            if path == server.HALF_FREIGHT:
                return {'data': {'shopIdAndFreightTemplateListMap': {'1234': [
                    {'site': 'US', 'templateName': '备用模板', 'templateId': 19}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data': {'shopIdAndWarehouseListMap': {'1234': [
                    {'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}}}
            return {'data': {}}

        def get_item(_detail_id, mode, _shop_id=0):
            item = copy.deepcopy(self.source)
            if mode == 'site':
                item['collectBoxDetailShopList'] = [{'shopId': 1234}]
            return item

        job_id = 'g' * 32
        self.pool.start_job(job_id, 777, 1234, 'US')
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI())
        self.assertEqual(self.pool.get_job(job_id)['status'], 'needs_input')
        self.pool.resume_job(job_id, '19')
        server.run_automation_job(self.pool, job_id, vendor, get_item,
                                  lambda *_: {'ready': True}, FakeAI())
        self.assertEqual(self.pool.get_job(job_id)['status'], 'done')
        saved = next(payload['shopCollectItemInfo'] for path, payload in calls if path == server.HALF_SHOP_SAVE)
        self.assertEqual(saved['siteFreightTemplateList'][0]['freightTemplateId'], '19')

    def test_restart_marks_running_job_interrupted(self):
        self.pool.start_job('e' * 32, 777, 1234, 'US')
        self.pool.recover_interrupted_jobs()
        self.assertEqual(AutomationPool(self.pool.path).get_job('e' * 32)['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
