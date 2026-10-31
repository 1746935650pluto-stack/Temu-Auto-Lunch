import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from attribute_ai import (AttributeInferenceError, category_path, complete_attributes,
                          attribute_for, hydrate_attributes_from_source, missing_attributes,
                          missing_from_error, normalize_values)
from automation_pool import AutomationPool
from test_item_editor import sample_item
from test_automation_workflow import FakeAI


def select_rule(name='适用年龄段', pid=1, required=True):
    return {'name': name, 'pid': pid, 'templatePid': pid + 100, 'refPid': pid,
            'valueType': 'SELECT', 'required': required, 'chooseMaxNum': 1,
            'values': [{'vid': 10, 'name': '18+'}, {'vid': 20, 'name': '3+'}]}


def answer(vid=10, evidence='成人帽子'):
    return {'uncertain': False, 'values': [{'vid': vid}], 'evidence': evidence}


class AttributeAITests(unittest.TestCase):
    def test_confirmed_wood_defaults_fill_entire_chain_without_ai_and_keep_existing_species(self):
        from item_editor import apply_required_choice_default
        wood = {'name': '木材类型', 'pid': 2230, 'templatePid': 1420294, 'valueType': 'SELECT',
                'required': True, 'parentTemplatePid': 1203213, 'values': [
                    {'vid': 213321, 'name': '原木', 'parentVidList': [410]},
                    {'vid': 213322, 'name': '再生木', 'parentVidList': [410]}]}
        species = {'name': '木种', 'pid': 2231, 'templatePid': 1423606, 'valueType': 'SELECT',
                   'required': True, 'parentTemplatePid': 1420294, 'values': [
                       {'vid': 221039, 'name': '樟子松', 'parentVidList': [213321]},
                       {'vid': 237612, 'name': '马尾松', 'parentVidList': [213321]}]}
        rules = [species, wood]
        item = {'attributes': [{'name': '材质', 'templatePid': 1203213, 'values': [{'vid': 410, 'name': '木材'}]}]}
        for name, value in server.AUTOMATION_CHOICE_DEFAULTS.items():
            apply_required_choice_default(item, rules, name, value)
        self.assertEqual(item['attributes'][-1]['values'][0]['vid'], 221039)
        infer = Mock()
        complete_attributes(item, item, rules, '玩具', infer, lambda _: None)
        infer.assert_not_called()
        item['attributes'][-1]['values'][0].update(vid=237612, name='马尾松')
        self.assertFalse(apply_required_choice_default(item, rules, '木种', '樟子松'))
        item['attributes'].pop()
        item['attributes'][-1]['values'][0].update(vid=213322, name='再生木')
        self.assertFalse(apply_required_choice_default(item, rules, '木种', '樟子松'))

    def test_generic_fields_are_completed_and_valid_values_preserved(self):
        item = {'title': '成人帽子', 'cid': '123', 'attributes': []}
        rules = [select_rule(name='适用人群')]
        infer = Mock(return_value=answer())
        complete_attributes(item, copy.deepcopy(item), rules, '女士帽子', infer, lambda _: None)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 10)
        before = copy.deepcopy(item)
        complete_attributes(item, item, rules, '女士帽子', infer, lambda _: None)
        self.assertEqual(item, before)
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(infer.call_args.args[0]['category'], '女士帽子')

    def test_source_choice_name_rebinds_to_target_account_choice_id(self):
        rule = select_rule('货源产地')
        rule['values'] = [{'vid': 82815, 'name': '广州产区'}, {'vid': 82959, 'name': '义乌产区'}]
        # The source account returned a different internal choice ID.  The
        # name is an API fact and must map to the target account's ID.
        values = normalize_values([{'vid': 991234, 'name': '义乌产区'}], rule)
        self.assertEqual(values, [{'vid': 82959, 'name': '义乌产区', 'numberValue': None, 'valueUnit': ''}])

        source = {'attributes': [{'name': '货源产地', 'templatePid': 1,
                                  'values': [{'vid': 991234, 'name': '义乌产区'}]}]}
        item = {'attributes': []}
        self.assertEqual(hydrate_attributes_from_source(item, source, [rule]), ['货源产地'])
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 82959)

    def test_parent_is_filled_first_and_filters_child_options(self):
        parent = select_rule('使用群体是否为成人', 2)
        parent['values'] = [{'vid': 30, 'name': '是'}, {'vid': 40, 'name': '否'}]
        child = select_rule()
        child['parentTemplatePid'] = 102
        child['values'][0]['parentVidList'] = [30]
        child['values'][1]['parentVidList'] = [40]
        item = {'title': '成人帽子', 'attributes': []}
        infer = Mock(side_effect=[answer(30), answer(10)])
        complete_attributes(item, copy.deepcopy(item), [child, parent], '帽子', infer, lambda _: None)
        self.assertEqual(infer.call_args.args[0]['options'], [{'vid': 10, 'name': '18+'}])
        self.assertEqual([a['name'] for a in item['attributes']], ['使用群体是否为成人', '适用年龄段'])

    def test_user_default_is_applied_after_ai_completes_missing_parent(self):
        from item_editor import apply_required_choice_default
        parent = select_rule('材质', 2)
        child = select_rule('木材类型')
        child.update(parentTemplatePid=102, values=[{'vid': 30, 'name': '原木', 'parentVidList': [10]}])
        item = {'title': '木制玩具', 'attributes': []}
        infer = Mock(return_value=answer(evidence='木制玩具'))
        def defaults(current, rule, forced):
            return rule['name'] == '木材类型' and apply_required_choice_default(current, [rule], '木材类型', '原木', required_by_vendor=forced)
        complete_attributes(item, copy.deepcopy(item), [child, parent], '玩具', infer, lambda _: None, default_resolver=defaults)
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(item['attributes'][1]['values'][0]['name'], '原木')

    def test_invalid_id_and_unsubstantiated_response_do_not_mutate_item(self):
        for reply in (answer(999), answer(evidence='不存在'), {'uncertain': True}, {'values': []}):
            item = {'title': '成人帽子', 'attributes': []}
            with self.subTest(reply=reply), self.assertRaises(AttributeInferenceError):
                complete_attributes(item, item, [select_rule()], '帽子', lambda _: reply, lambda _: None)
            self.assertEqual(item['attributes'], [])

    def test_composition_requires_explicit_percentage_and_exact_sum(self):
        rule = select_rule('成分')
        rule.update(valueType='TABLE', valueInputSum=100, valueUnit=['%'], minValue='0',
                    maxValue='100', valuePrecision=2, chooseMaxNum=2,
                    values=[{'vid': 10, 'name': '棉'}, {'vid': 20, 'name': '涤纶'}])
        for title, percent, valid in [('100%棉帽子', '100', True), ('棉帽子', '100', False),
                                     ('95%棉帽子', '95', False), ('100个棉帽子', '100', False)]:
            item = {'title': title, 'attributes': []}
            reply = answer(evidence=title)
            reply['values'][0]['numberValue'] = percent
            if valid:
                complete_attributes(item, item, [rule], '帽子', lambda _: reply, lambda _: None)
                self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '100')
            else:
                with self.subTest(title=title), self.assertRaises(AttributeInferenceError):
                    complete_attributes(item, item, [rule], '帽子', lambda _: reply, lambda _: None)
        with self.assertRaises(ValueError):
            normalize_values([{'vid': 10, 'numberValue': 'NaN'}], rule)

    def test_optional_absent_and_inactive_children_are_not_guessed(self):
        optional = select_rule(required=False)
        inactive = select_rule('电池容量', 2)
        inactive['parentTemplatePid'] = 999
        infer = Mock()
        item = {'attributes': []}
        complete_attributes(item, item, [optional, inactive], '帽子', infer, lambda _: None)
        infer.assert_not_called()

    def test_show_condition_and_existing_invalid_field(self):
        rule = select_rule()
        rule['showCondition'] = [{'parentRefPid': 5, 'parentVids': [30]}]
        item = {'attributes': [{'refPid': 5, 'values': [{'vid': 40}]}]}
        self.assertEqual(missing_attributes(item, [rule]), [])
        item['attributes'][0]['values'][0]['vid'] = 30
        self.assertEqual(len(missing_attributes(item, [rule])), 1)

    def test_optional_sibling_with_same_pid_is_not_mistaken_for_existing_attribute(self):
        first = select_rule('面料纹理1', 2054)
        first['templatePid'] = 1292473
        second = select_rule('面料纹理2', 2054, required=False)
        second['templatePid'] = 1293664
        item = {'attributes': [{'name': '面料纹理1', 'pid': 2054, 'templatePid': 1292473,
                                'values': [{'vid': 10, 'name': '18+'}]}]}
        self.assertIsNone(attribute_for(item, second))
        self.assertEqual(missing_attributes(item, [first, second]), [])
        infer = Mock()
        complete_attributes(item, item, [first, second], '服装', infer, lambda _: None)
        infer.assert_not_called()

    def test_string_and_number_values(self):
        rule = select_rule('刀刃长度')
        rule.update(valueType='STRING', propertyValueType=1, minValue='0', maxValue='100', valuePrecision=2, valueUnit=['cm'])
        self.assertEqual(normalize_values([{'name': '5', 'numberValue': '5'}], rule),
                         [{'vid': 0, 'name': '5', 'numberValue': '5', 'valueUnit': 'cm'}])
        with self.assertRaises(ValueError):
            normalize_values([{'name': '5.123', 'numberValue': '5.123'}], rule)

    def test_fixed_fabric_weight_band_does_not_require_a_composition_number(self):
        rule = select_rule('面料平方克重')
        rule.update(propertyValueType=1, values=[{'vid': 75445, 'name': '140-160g'}])
        item = {'attributes': [{'name': '面料平方克重', 'templatePid': rule['templatePid'],
                                'values': [{'vid': 75445, 'name': '140-160g', 'numberValue': ''}]}]}
        self.assertEqual(normalize_values(item['attributes'][0]['values'], rule), [
            {'vid': 75445, 'name': '140-160g', 'numberValue': None, 'valueUnit': ''},
        ])
        self.assertEqual(missing_attributes(item, [rule]), [])

    def test_all_missing_fields_attempted_even_when_one_is_uncertain(self):
        rules = [select_rule('材质'), select_rule('适用年龄段', 2)]
        infer = Mock(side_effect=[{'uncertain': True}, answer()])
        with self.assertRaises(AttributeInferenceError):
            complete_attributes({'attributes': []}, {'title': '成人帽子'}, rules, '帽子', infer, lambda _: None)
        self.assertEqual(infer.call_count, 2)

    def test_bounded_repair_and_explicit_vendor_error_parsing(self):
        item = {'title': '成人帽子', 'attributes': []}
        attempted = set()
        infer = Mock(return_value=answer())
        complete_attributes(item, item, [select_rule()], '帽子', infer, lambda _: None, attempted=attempted)
        with self.assertRaises(AttributeInferenceError):
            complete_attributes(item, item, [select_rule()], '帽子', infer, lambda _: None,
                                targets=['适用年龄段'], attempted=attempted)
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(missing_from_error('父属性【是否成人】有设置时，子属性【适用年龄段】也必须设置', [select_rule()]), ['适用年龄段'])
        self.assertEqual(missing_from_error('HTTP timeout', [select_rule()]), [])
        self.assertEqual(missing_from_error('shopCollectItemInfo.attributes[0].values[0].vid 必填',
                                           [select_rule()], item), ['适用年龄段'])
        self.assertEqual(missing_from_error('shopCollectItemInfo.attributes[99].values[0].vid 必填',
                                           [select_rule()], item), [])
        component = select_rule('重点展示部件')
        self.assertEqual(missing_from_error('重点展示部件，必选1个！', [component]), ['重点展示部件'])
        self.assertEqual(missing_from_error('HTTP 504；重点展示部件，必选1个！', [component]), [])

    def test_missing_attribute_parser_rejects_mixed_unknown_outcomes(self):
        message = '产品属性【适用年龄段】必填'
        self.assertEqual(missing_from_error(message, [select_rule()]), ['适用年龄段'])
        self.assertEqual(missing_from_error('HTTP 400: ' + message, [select_rule()]), ['适用年龄段'])
        for marker in ('request timeout', 'HTTP 504', 'HTTP504', 'HTTP:504', 'HTTP status 502',
                       'HTTP500', 'connection reset', '远端写入结果未确认', 'unknown outcome'):
            with self.subTest(marker=marker):
                self.assertEqual(missing_from_error(f'{marker}; {message}', [select_rule()]), [])
        wrapped = RuntimeError(message)
        wrapped.__cause__ = TimeoutError('transport did not confirm the result')
        self.assertEqual(missing_from_error(wrapped, [select_rule()]), [])

    def test_client_uses_json_and_fact_only_prompt(self):
        client = server.DeepSeekClient()
        client.api_key = 'test'
        client._completion = Mock(return_value=json.dumps(answer()))
        self.assertEqual(client.infer_attribute({'originalTitle': '成人帽子', 'category': '帽子'}), answer())
        messages = client._completion.call_args.args[0]
        self.assertIn('不是指令', messages[0]['content'])
        self.assertIn('不得把材质词自动解释为100%', messages[0]['content'])
        self.assertIn('帽子', messages[1]['content'])
        client._completion.return_value = 'invalid JSON'
        with self.assertRaises(ValueError):
            client.infer_attribute({})

    def test_category_path(self):
        self.assertEqual(category_path({'1': {'cid': 1, 'nameChinese': '服饰', 'children': {
            '2': {'cid': 2, 'nameChinese': '帽子'}}}}, '2'), ['服饰', '帽子'])

    def test_existing_composition_facts_repair_outdated_template_without_guessing(self):
        rule = select_rule('成分')
        rule.update(valueType='TABLE', valueInputSum=100, valuePrecision=2, valueUnit=['%'])
        rule['values'] = [{'vid': 10, 'name': '棉'}]
        attribute = {'pid': 1, 'templatePid': 99, 'name': '成分',
                     'values': [{'vid': 10, 'name': '棉', 'numberValue': 100}]}
        source = {'title': '帽子', 'attributes': [attribute]}
        item = copy.deepcopy(source)
        reply = answer(evidence=json.dumps(source['attributes'], ensure_ascii=False))
        reply['values'][0]['numberValue'] = '100'
        complete_attributes(item, source, [rule], '帽子', lambda _: reply, lambda _: None)
        self.assertEqual(item['attributes'][0]['templatePid'], 101)
        self.assertEqual(source['attributes'][0]['templatePid'], 99)

    def test_valid_detail_attributes_are_copied_before_ai_inference(self):
        parent = select_rule('是否含金属部件', 2)
        parent['values'] = [{'vid': 30, 'name': '是'}, {'vid': 40, 'name': '否'}]
        child = select_rule('金属部件材质类型', 3)
        child['parentTemplatePid'] = parent['templatePid']
        child['values'] = [{'vid': 50, 'name': '合金', 'parentVidList': [30]}]
        source = {'attributes': [
            {'name': '是否含金属部件', 'templatePid': 102, 'values': [{'vid': 30, 'name': '是'}]},
            {'name': '金属部件材质类型', 'templatePid': 103, 'values': [{'vid': 50, 'name': '合金'}]},
        ]}
        item = {'attributes': []}
        self.assertEqual(hydrate_attributes_from_source(item, source, [child, parent]),
                         ['是否含金属部件', '金属部件材质类型'])
        self.assertEqual(missing_attributes(item, [child, parent]), [])
        infer = Mock()
        complete_attributes(item, source, [child, parent], '饰品', infer, lambda _: None)
        infer.assert_not_called()

    def test_semantic_miaoshou_facts_map_material_and_wireless_types(self):
        material = select_rule('材质类型', 2)
        material['values'] = [{'vid': 30, 'name': '纺织品材质'}, {'vid': 40, 'name': '非纺织品材质'}]
        wireless = select_rule('无线属性', 3)
        wireless['values'] = [{'vid': 50, 'name': '无'}, {'vid': 60, 'name': '含蓝牙功能'}]
        source = {'attributes': [
            {'name': '面料材质', 'values': [{'name': 'PU'}]},
            {'name': '电源方式', 'values': [{'name': '不带电'}]},
            {'name': '电池属性', 'values': [{'name': '不带电池'}]},
        ]}
        item = {'attributes': []}
        self.assertEqual(hydrate_attributes_from_source(item, source, [material, wireless]),
                         ['材质类型', '无线属性'])
        self.assertEqual([attribute['values'][0]['name'] for attribute in item['attributes']],
                         ['非纺织品材质', '无'])

    def test_shared_tree_cache_avoids_repeated_vendor_reads(self):
        vendor = Mock(return_value={'data': {'cateTree': {}}})
        with patch.object(server, '_category_cache', None):
            server.shared_category_tree(vendor)
            server.shared_category_tree(vendor)
        self.assertEqual(vendor.call_count, 1)


class AttributeWorkflowTests(unittest.TestCase):
    def test_missing_attribute_completion_and_vendor_required_retry_are_save_only(self):
        for deferred_requirement, uncertain in ((False, False), (True, False), (False, True)):
            with self.subTest(deferred=deferred_requirement, uncertain=uncertain), tempfile.TemporaryDirectory() as folder:
                pool = AutomationPool(Path(folder) / 'pool.sqlite3')
                source = sample_item()
                source['title'] = '成人帽子'
                pool.put_item(777, 'site', 0, source)
                pool.start_job('a' * 32, 777, 1234, 'US')
                rule = select_rule(name='适用人群', required=not deferred_requirement)
                writes = []
                saved_item = None
                def vendor(path, payload):
                    nonlocal saved_item
                    data = {
                        server.SHOP_LIST: {'shopList': [{'shopId': 1234}]},
                        server.HALF_SITES: {'publishSiteOptions': [{'key': 'US'}]},
                        server.HALF_FREIGHT: {'shopIdAndFreightTemplateListMap': {'1234': [{'site': 'US', 'templateName': '通用', 'templateId': 11}]}},
                        server.HALF_WAREHOUSE: {'shopIdAndWarehouseListMap': {'1234': [{'site': 'US', 'warehouseName': '发货', 'warehouseId': 22}]}},
                        server.HALF_CATEGORY_ATTRIBUTE_RULES: {'productAttributeRules': [rule]},
                        server.HALF_CATEGORIES: {'cateTree': {'123': {'cid': '123', 'nameChinese': '帽子'}}},
                    }
                    if path == server.HALF_SHOP_SAVE:
                        writes.append(copy.deepcopy(payload))
                        if not payload['shopCollectItemInfo']['attributes']:
                            raise RuntimeError('产品属性【适用人群】必填')
                        saved_item = copy.deepcopy(payload['shopCollectItemInfo'])
                    self.assertNotEqual(path, server.HALF_PUBLISH)
                    return {'data': data.get(path, {})}
                ai = FakeAI()
                ai.infer_attribute = Mock(return_value={'uncertain': True} if uncertain else answer())
                with patch.object(server, '_category_cache', None):
                    server.run_automation_job(pool, 'a' * 32, vendor,
                                              lambda *_: copy.deepcopy(saved_item or source),
                                              lambda *_: {'ready': True}, ai, defer_publish=True)
                job = pool.get_job('a' * 32)
                self.assertEqual(job['status'], 'ready_publish')
                self.assertEqual(ai.infer_attribute.call_count, 1)
                self.assertEqual(len(writes), 2 if deferred_requirement else 1)
                if not uncertain:
                    self.assertEqual(writes[-1]['shopCollectItemInfo']['attributes'][0]['values'][0]['vid'], 10)
                self.assertEqual(pool.get_item(777)['item']['attributes'], [])


if __name__ == '__main__':
    unittest.main()
