"""Cross-account repairs use synthetic data and isolated SQLite only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server
from shared_automation import SharedAutomation, create_copy_payload, shorten_copy_english_title
from attribute_ai import hydrate_attributes_from_source, missing_attributes, complete_attributes, category_path
from item_editor import apply_source_origin_default
from server import complete_automation_attributes, automation_logistic_rows
from test_import_template_preflight import valid_item


def component_rule():
    return dict(name='包含的组件', pid=12, templatePid=102, refPid=22,
                valueType='SELECT', required=True, chooseMaxNum=1,
                values=[{'vid': 20, 'name': '收纳袋'}, {'vid': 10, 'name': '其他'}])


class CopySourceRepairTests(unittest.TestCase):
    def test_concurrent_definite_rejection_repairs_claim_only_one_create(self):
        with self.service.db() as db:
            db.execute("""INSERT INTO shared_copies VALUES
                ('retry-key',2,'source',123,1,'target',NULL,'needs_review','{}',
                 'siteCollectItemInfo.sizeCharts[0].groupIdAndValueMap 必填')""")
            copy = dict(db.execute("SELECT * FROM shared_copies WHERE copy_key='retry-key'").fetchone())
        barrier = Barrier(2)
        self.service._prepare_copy_attributes = Mock()
        self.service._prepare_copy_logistics = lambda *_: barrier.wait(timeout=5)
        def repair():
            try:
                return self.service._retry_copy_as_source_payload(deepcopy(self.transfer), copy)
            except ValueError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(repair) for _ in range(2)]
            results = [f.result(timeout=10) for f in futures]
        self.assertEqual(results.count(456), 1)
        self.assertEqual(sum(path == 'create' for path, _ in self.calls), 1)
        self.assertTrue(any('未重复创建' in str(result) for result in results))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.backend = SimpleNamespace(HALF_CATEGORY_ATTRIBUTE_RULES='rules', HALF_CREATE='create',
            HALF_FREIGHT=server.HALF_FREIGHT, HALF_WAREHOUSE=server.HALF_WAREHOUSE,
            miao_request=lambda fn, *_, **__: fn(), created_collect_box_detail_id=lambda r: r['data']['id'],
            automation_logistic_rows=automation_logistic_rows,
            hydrate_attributes_from_source=hydrate_attributes_from_source, missing_attributes=missing_attributes,
            complete_attributes=complete_attributes, apply_source_origin_default=apply_source_origin_default,
            complete_automation_attributes=complete_automation_attributes,
            category_path=category_path, shared_category_tree=lambda *_: {'cateTree': {'123': {'cid':'123','name':'包'}}},
            os=SimpleNamespace(getenv=lambda *_: 'live'))
        self.infer = Mock(return_value={'uncertain':True})
        self.backend.DeepSeekClient = lambda account_id=None: SimpleNamespace(infer_attribute=self.infer)
        self.service = SharedAutomation(self.backend, Path(temp.name) / 'shared.sqlite3')
        self.raw = valid_item()
        self.raw['attributes'] = [dict(name='包含的组件', templatePid=999,
                                       values=[{'vid': 10, 'name': '收纳袋'}])]
        self.rules = [component_rule()]
        self.calls = []
        def vendor(path, payload):
            self.calls.append((path, deepcopy(payload)))
            shop = str(payload.get('shopId',7))
            if path == server.HALF_FREIGHT:
                return {'data':{'shopIdAndFreightTemplateListMap':{shop:[{'site':'US','templateName':'通用','templateId':'target-freight-'+shop}]}}}
            if path == server.HALF_WAREHOUSE:
                return {'data':{'shopIdAndWarehouseListMap':{shop:[{'site':'US','warehouseName':'发货','warehouseId':'target-warehouse-'+shop}]}}}
            return {'data': {'productAttributeRules': deepcopy(self.rules)} if path == 'rules' else {'id': 456}}
        self.worker = SimpleNamespace(_get_item=Mock(side_effect=lambda *_: deepcopy(self.raw)), _vendor=vendor)
        self.worker._get_source_item = self.worker._get_item
        self.service.worker = Mock(return_value=self.worker)
        self.service.check_account = Mock(side_effect=lambda a,*_: {'scope':'source' if a == 2 else 'target'})
        self.transfer = dict(sourceAccountId=2, sourceScope='source', detailId=123,
                             targetAccountId=1, targetScope='target', shopIds=[7], site='US')

    def repair(self, forced=False):
        item = create_copy_payload(self.raw, [7], 'US')
        self.service._sync_copy_components(self.raw, item, self.transfer, forced)
        return item

    def test_component_name_rebinds_conflicting_ids_without_source_mutation(self):
        original = deepcopy(self.raw)
        item = self.repair()
        self.assertEqual(item['attributes'][0]['templatePid'], 102)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 20)
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '收纳袋')
        self.assertEqual(self.raw, original)
        self.assertEqual([path for path, _ in self.calls], ['rules'])

    def test_free_text_components_keep_explicit_source_contents(self):
        self.rules[0].update(valueType='STRING', values=[])
        self.raw['attributes'][0]['values'] = [{'name': '收纳袋1个，挂钩2个'}]
        self.assertEqual(self.repair()['attributes'][0]['values'][0]['name'], '收纳袋1个，挂钩2个')

    def test_missing_components_use_same_option_defaults_as_ordinary_save(self):
        self.raw['title'] = '收纳袋包含挂钩'
        self.raw['attributes'] = []
        self.assertEqual(self.service._ensure_copy(self.transfer), 456)
        created = next(p['siteCollectItemInfo'] for path, p in self.calls if path == 'create')
        self.assertIn(created['attributes'][0]['values'][0]['vid'], (10, 20))
        self.assertEqual(self.raw['attributes'], [])
        self.assertTrue(any('按用户设置' in note for note in self.transfer['preparationNotes']))
        self.infer.assert_called_once()

    def test_missing_oil_edge_uses_defaults_but_existing_value_is_preserved(self):
        self.rules = [dict(name='油边', templatePid=103, valueType='SELECT', required=True,
                           values=[{'vid': 21, 'name': '是'}, {'vid': 22, 'name': '否'}])]
        self.raw['attributes'] = []
        item = create_copy_payload(self.raw, [7], 'US')
        self.service._prepare_copy_attributes(self.raw, item, self.transfer)
        self.assertIn(item['attributes'][0]['values'][0]['vid'], (21, 22))
        self.raw['attributes'] = [{'name':'油边','templatePid':103,'values':[{'vid':22,'name':'否'}]}]
        self.infer.reset_mock()
        item = create_copy_payload(self.raw, [7], 'US')
        self.service._prepare_copy_attributes(self.raw, item, self.transfer)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'],22)
        self.infer.assert_not_called()

    def test_copy_and_normal_save_share_default_policy(self):
        self.raw['attributes'] = []
        self.rules += [dict(name='适用年龄段', templatePid=103, valueType='SELECT', required=True,
                            values=[{'vid': 31, 'name': '6+'}, {'vid': 32, 'name': '14+'}])]
        copied = create_copy_payload(self.raw, [7], 'US')
        normal = deepcopy(copied)
        with patch('attribute_ai.random.choice', side_effect=lambda choices: choices[0]):
            self.service._prepare_copy_attributes(self.raw, copied, self.transfer)
            complete_automation_attributes(normal, self.raw, self.rules, '包', self.infer, lambda _: None)
        self.assertEqual(copied['attributes'], normal['attributes'])
        self.assertEqual(next(a for a in copied['attributes'] if a['name']=='适用年龄段')['values'][0]['name'],'6+')

    def test_free_text_and_unavailable_choices_still_block_without_creating(self):
        self.raw['attributes'] = []
        for kind in ('STRING', 'SELECT'):
            self.rules[0].update(valueType=kind, values=[])
            with self.assertRaises(ValueError):
                self.service._ensure_copy(self.transfer)
        self.assertFalse(any(path == 'create' for path, _ in self.calls))
        self.assertEqual(self.service._copies(), [])

    def weight_rule(self, name='面料克重1（g/m²）', **extra):
        return dict(name=name, templatePid=300, refPid=300, valueType='NUMBER',
                    required=True, minValue=0, maxValue=1000, valueUnit=['g/m²'], **extra)

    def test_copy_weight_defaults_reach_create_without_ai_or_source_changes(self):
        self.raw['attributes'] = []
        original = deepcopy(self.raw)
        self.rules = [self.weight_rule(),
                      {**self.weight_rule('里料克重'), 'templatePid': 301},
                      {**self.weight_rule('平方克重'), 'templatePid': 302}]
        self.assertEqual(self.service._ensure_copy(self.transfer), 456)
        created = next(p['siteCollectItemInfo'] for path, p in self.calls if path == 'create')
        self.assertEqual([a['values'][0]['numberValue'] for a in created['attributes']], ['5', '5', '10'])
        self.infer.assert_not_called()
        self.assertEqual(self.raw, original)

    def test_copy_keeps_existing_source_weight(self):
        self.rules = [self.weight_rule()]
        self.raw['attributes'] = [{'name': self.rules[0]['name'], 'templatePid': 999,
                                  'values': [{'name': '180', 'numberValue': '180'}]}]
        self.service._ensure_copy(self.transfer)
        created = next(p['siteCollectItemInfo'] for path, p in self.calls if path == 'create')
        self.assertEqual(created['attributes'][0]['values'][0]['numberValue'], '180')
        self.infer.assert_not_called()

    def test_weight_default_waits_for_texture_parent_even_when_rules_are_reversed(self):
        self.raw['attributes'] = []
        texture = dict(name='面料纹理1', templatePid=200, refPid=200, required=True,
                       valueType='SELECT', values=[{'vid': 20, 'name': '光面'}])
        weight = self.weight_rule(parentTemplatePid=200,
                                 showCondition=[{'parentRefPid': 200, 'parentVids': [20]}])
        self.rules = [weight, texture]
        self.service._ensure_copy(self.transfer)
        created = next(p['siteCollectItemInfo'] for path, p in self.calls if path == 'create')
        self.assertEqual(next(a for a in created['attributes'] if a['templatePid'] == 300)['values'][0]['numberValue'], '5')
        self.infer.assert_not_called()

    def test_out_of_range_weight_default_blocks_before_copy_creation(self):
        self.raw['attributes'] = []
        self.rules = [{**self.weight_rule(), 'minValue': 80}]
        with self.assertRaisesRegex(ValueError, '默认值 5 超出当前类目范围'):
            self.service._ensure_copy(self.transfer)
        self.assertFalse(any(path == 'create' for path, _ in self.calls))
        self.assertEqual(self.service._copies(), [])
        self.infer.assert_not_called()

    def test_missing_or_ambiguous_choice_cannot_reuse_source_id(self):
        for values in [[{'vid': 10, 'name': '其他'}],
                       [{'vid': 1, 'name': '收纳袋'}, {'vid': 2, 'name': '收纳袋'}]]:
            self.rules[0]['values'] = values
            with self.assertRaisesRegex(ValueError, '无法唯一匹配'):
                self.repair()
        self.raw['attributes'][0]['values'] = [{'vid': 10}]
        with self.assertRaisesRegex(ValueError, '缺少源属性名称'):
            self.repair()

    def test_vendor_requirement_applies_even_if_rule_is_optional(self):
        self.raw['attributes'] = []
        self.rules[0]['required'] = False
        self.repair()
        item = create_copy_payload(self.raw,[7],'US')
        self.service._prepare_copy_attributes(self.raw, item, self.transfer, True)
        self.assertIn(item['attributes'][0]['values'][0]['vid'], (10,20))
        self.rules = []
        with self.assertRaisesRegex(ValueError, '规则缺失'):
            self.repair(forced=True)

    def test_first_create_repairs_only_target_and_reuses_confirmed_copy(self):
        self.raw['multiLanguageTitleMap'] = {'en': 'Storage bag with hooks ' * 20, 'zh': '收纳袋'}
        original = deepcopy(self.raw)
        baseline = create_copy_payload(self.raw, [7], 'US')
        self.assertEqual(self.service._ensure_copy(self.transfer), 456)
        self.assertEqual(self.service._ensure_copy(self.transfer), 456)
        created = [payload['siteCollectItemInfo'] for path, payload in self.calls if path == 'create']
        self.assertEqual(len(created), 1)
        self.assertLessEqual(len(created[0]['multiLanguageTitleMap']['en']), 250)
        self.assertEqual(created[0]['attributes'][0]['values'][0]['vid'], 20)
        for field in ['title', 'imgUrls', 'siteShipmentLimitDayMap']:
            self.assertEqual(created[0][field], baseline[field])
        for sku in created[0]['skuMap'].values():
            self.assertEqual(sku['siteAndWarehouseIdAndStockMap'],{'US':{'target-warehouse-7':1000}})
        self.assertEqual(created[0]['collectBoxDetailShopList'][0]['siteFreightTemplateList'][0]['freightTemplateId'],'target-freight-7')
        self.assertEqual(self.raw, original)

    def test_explicit_title_rejection_retry_and_unknown_result_protection(self):
        self.raw['multiLanguageTitleMap']['en'] = 'word ' * 70
        with self.service.db() as db:
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('key', 2, 'source', 123, 1, 'target', None, 'needs_review', '{}', '英文标题不能超过250个字符'))
        copy = self.service._copies()[0]
        self.assertEqual(self.service._retry_copy_as_source_payload(self.transfer, copy), 456)
        self.assertEqual(self.service._copies()[0]['status'], 'ready')
        self.assertEqual(len([path for path, _ in self.calls if path == 'create']), 1)
        copy['message'] = 'Unable to reach MiaoShou API'
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, '不会自动再次创建'):
            self.service._retry_copy_as_source_payload(self.transfer, copy)
        self.assertEqual(self.calls, [])

    def test_size_chart_group_validation_is_repaired_before_initial_and_retry_create(self):
        self.raw['sizeCharts'] = [{'sizeItems': [{'name': 'S', 'groupIdAndValueMap': None}]}]
        self.raw['saleAttributes'].append({'name': '尺码', 'values': [{'name': 'S'}]})
        self.assertEqual(self.service._ensure_copy(self.transfer), 456)
        created = next(payload['siteCollectItemInfo'] for path, payload in self.calls if path == 'create')
        self.assertEqual(created['sizeCharts'][0]['sizeItems'][0]['groupIdAndValueMap'], {})
        self.assertIsNone(self.raw['sizeCharts'][0]['sizeItems'][0]['groupIdAndValueMap'])
        self.calls.clear()
        with self.service.db() as db:
            db.execute('UPDATE shared_copies SET target_id=NULL,status=?,message=?',
                       ('needs_review', 'siteCollectItemInfo.sizeCharts[0].sizeItems[0].groupIdAndValueMap 必填'))
        copy = self.service._copies()[0]
        self.assertTrue(self.service._is_definite_create_rejection(copy['message']))
        self.assertEqual(self.service._retry_copy_as_source_payload(self.transfer, copy), 456)
        retried = next(payload['siteCollectItemInfo'] for path, payload in self.calls if path == 'create')
        self.assertEqual(retried['sizeCharts'][0]['sizeItems'][0]['groupIdAndValueMap'], {})

    def test_rule_read_failure_blocks_create(self):
        self.worker._vendor = Mock(side_effect=RuntimeError('network'))
        with self.assertRaisesRegex(RuntimeError, 'network'):
            self.service._ensure_copy(self.transfer)
        self.assertEqual(self.service._copies(), [])

    def test_timeout_after_create_keeps_mapping_and_never_automatically_retries(self):
        vendor = self.worker._vendor
        def timeout(path, payload):
            result = vendor(path, payload)
            if path == 'create':
                raise RuntimeError('timeout after acceptance')
            return result
        self.worker._vendor = timeout
        with self.assertRaisesRegex(ValueError, '禁止自动重复创建'):
            self.service._ensure_copy(self.transfer)
        with self.assertRaisesRegex(ValueError, '不会自动再次创建'):
            self.service._ensure_copy(self.transfer)
        self.assertEqual(len([p for p, _ in self.calls if p == 'create']), 1)
        self.assertEqual(self.service._copies()[0]['status'], 'needs_review')

    def test_title_fact_completes_missing_attribute_before_create(self):
        self.raw['attributes'] = []
        self.raw['title'] = '收纳袋，带挂钩'
        self.infer.return_value = {'uncertain': False, 'values': [{'vid':20, 'name':'收纳袋'}], 'evidence':'收纳袋'}
        self.assertEqual(self.service._ensure_copy(self.transfer),456)
        self.assertEqual(self.service._copies()[0]['status'],'ready')

    def test_reviewed_image_fact_is_scoped_and_invalidates_after_source_change(self):
        self.raw['attributes'] = []
        before = deepcopy(self.raw)
        self.service.remember_source_evidence(2,123,self.raw,[{'name':'包含的组件','value':'收纳袋',
            'imageUrl':self.raw['imgUrls'][0],'evidence':'源图可见收纳袋主体'}])
        augmented = self.service._source_with_evidence(self.raw,self.transfer)
        self.assertEqual(augmented['attributes'][0]['values'][0]['name'],'收纳袋')
        self.assertEqual(self.raw,before)
        other = {**self.transfer, 'sourceScope':'other'}
        self.assertEqual(self.service._source_with_evidence(self.raw,other)['attributes'],[])
        self.raw['title'] += ' changed'
        self.assertEqual(self.service._source_with_evidence(self.raw,self.transfer)['attributes'],[])

    def test_resume_queues_once_and_unknown_copy_cannot_resume(self):
        import json
        with self.service.db() as db:
            db.execute('INSERT INTO shared_transfers VALUES (?,?,?,?,?,?,?)',
                ('task','batch',json.dumps(self.transfer),'needs_review','local validation',None,1))
        body={'transferId':'task','confirmation':'RESUME_SHARED_TO_QUEUE'}
        self.assertTrue(self.service.resume_transfer(body,launch=False)['queued'])
        self.assertTrue(self.service.resume_transfer(body,launch=False)['duplicate'])
        with self.service.db() as db:
            db.execute("UPDATE shared_transfers SET status='needs_review'")
            db.execute('INSERT INTO shared_copies VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('key',2,'source',123,1,'target',None,'needs_review','{}','timeout'))
        with self.assertRaisesRegex(ValueError,'结果不明'):
            self.service.resume_transfer(body,launch=False)

    def test_multi_shop_copy_uses_each_target_shop_logistics(self):
        item=create_copy_payload(self.raw,[7,8],'US')
        self.service._prepare_copy_logistics(item,{**self.transfer,'shopIds':[7,8]})
        for row in item['collectBoxDetailShopList']:
            self.assertEqual(row['siteFreightTemplateList'][0]['freightTemplateId'],'target-freight-'+str(row['shopId']))
        self.assertEqual(item['skuMap'][';sample;']['siteAndWarehouseIdAndStockMap']['US'],
                         {'target-warehouse-7':1000,'target-warehouse-8':1000})

    def test_missing_target_logistics_stops_before_create(self):
        original=self.worker._vendor
        self.worker._vendor=lambda path,payload: {'data':{}} if path==server.HALF_FREIGHT else original(path,payload)
        with self.assertRaisesRegex(ValueError,'未创建副本'):
            self.service._ensure_copy(self.transfer)
        self.assertEqual(self.service._copies(),[])
        self.assertNotIn('create',[path for path,_ in self.calls])


class CopyTitleTests(unittest.TestCase):
    def test_short_title_unchanged_and_other_languages_preserved(self):
        item = {'multiLanguageTitleMap': {'en': 'A' * 250, 'zh': '原始标题'}}
        original = deepcopy(item)
        self.assertFalse(shorten_copy_english_title(item))
        self.assertEqual(item, original)

    def test_word_boundary_unicode_and_long_token(self):
        for title in ['Storage bag with accessories ' * 20, 'A' * 251, 'Bag ' * 60 + '😀' * 20]:
            item = {'multiLanguageTitleMap': {'en': title, 'zh': '原始标题'}}
            self.assertTrue(shorten_copy_english_title(item))
            result = item['multiLanguageTitleMap']['en']
            self.assertLessEqual(len(result.encode('utf-16-le')) // 2, 250)
            self.assertTrue(title.startswith(result))
            self.assertEqual(item['multiLanguageTitleMap']['zh'], '原始标题')
        item = {'multiLanguageTitleMap': {'en': 'nice ' * 49 + 'product'}}
        shorten_copy_english_title(item)
        self.assertTrue(item['multiLanguageTitleMap']['en'].endswith('nice'))


if __name__ == '__main__':
    unittest.main()
