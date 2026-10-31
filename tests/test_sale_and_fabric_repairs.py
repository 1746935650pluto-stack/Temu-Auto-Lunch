import copy
import unittest

from item_editor import apply_fabric_weight_default, apply_required_choice_default, fill_empty_sale_options, apply_thickness_default, remove_inactive_numeric_attributes


class SaleAndFabricRepairTests(unittest.TestCase):
    def test_lining_and_fabric_weight_with_shared_pid_remain_distinct(self):
        rules = [{'name': name, 'templatePid': index, 'pid': 10, 'valueType': 'STRING'}
                 for index, name in enumerate(('面料克重1', '里料克重（g/m²）', '平方克重'), 1)]
        item = {'attributes': [{'name': '面料克重1', 'templatePid': 1, 'pid': 10,
                                'values': [{'numberValue': '20', 'name': '20'}]}]}
        apply_fabric_weight_default(item, rules)
        self.assertEqual([a['values'][0]['numberValue'] for a in item['attributes']], ['20', '5', '10'])
        self.assertFalse(apply_fabric_weight_default(item, rules))

    def test_thickness_does_not_fill_inactive_parent_condition(self):
        for name in ('厚度（cm）', '厚度（MIL）'):
            rule = {'name': name, 'templatePid': 2, 'valueType': 'STRING',
                    'showCondition': [{'parentRefPid': 7, 'parentVids': [70]}]}
            item = {'attributes': [{'name': '材料', 'refPid': 7, 'values': [{'vid': 71}]}]}
            before = copy.deepcopy(item)
            self.assertFalse(apply_thickness_default(item, [rule]))
            self.assertEqual(item, before)
            item['attributes'].append({'templatePid': 2, 'name': name, 'values': [{'numberValue': '3'}]})
            self.assertEqual(remove_inactive_numeric_attributes(item, [rule]), [name])
            self.assertEqual(item, before)
            item['attributes'][0]['values'][0]['vid'] = 70
            self.assertTrue(apply_thickness_default(item, [rule]))
            self.assertEqual(remove_inactive_numeric_attributes(item, [rule]), [])

    def test_absent_preferred_choice_uses_only_parent_allowed_options(self):
        rule = {'name': '适用年龄段', 'templatePid': 2, 'parentTemplatePid': 1, 'required': True,
                'valueType': 'SELECT', 'values': [{'vid': 18, 'name': '18+', 'parentVidList': [1]},
                                               {'vid': 3, 'name': '3+', 'parentVidList': [2]}]}
        item = {'attributes': [{'templatePid': 1, 'values': [{'vid': 1}]}]}
        self.assertTrue(apply_required_choice_default(item, [rule], rule['name'], '6+', random_fallback=True))
        self.assertEqual(item['attributes'][-1]['values'][0]['vid'], 18)
        self.assertFalse(apply_required_choice_default(item, [rule], rule['name'], '6+', random_fallback=True))

    def sale_rule(self):
        return {'name': '头发长度', 'templatePid': 990209, 'parentSpecId': 44214483,
                'required': True, 'valueType': 'SELECT', 'values': [
                    {'vid': 34601, 'name': '4inch'}, {'vid': 66350, 'name': '其他'}]}

    def test_empty_hair_spec_preserves_sku_data_and_uses_other(self):
        sku = {'price': '100', 'weight': 20, 'siteAndWarehouseIdAndStockMap': {'US': {'1': 1000}}}
        item = {'saleAttributes': [{'name': '头发长度', 'values': []}], 'skuMap': {';;': sku}}
        self.assertEqual(fill_empty_sale_options(item, [self.sale_rule()]), ['头发长度＝其他'])
        value = item['saleAttributes'][0]['values'][0]
        self.assertEqual(value['vid'], 66350)
        self.assertEqual(item['skuMap'], {';' + value['skuKey'] + ';': sku})
        self.assertEqual(fill_empty_sale_options(item, [self.sale_rule()]), [])

    def test_multiple_variants_keep_their_own_prices_and_keys(self):
        for padded in (False, True):
            item = {'saleAttributes': [
                {'name': '颜色', 'values': [{'name': '红', 'skuKey': 'red'}, {'name': '蓝', 'skuKey': 'blue'}]},
                {'name': '头发长度', 'values': []}],
                'skuMap': {f';red{";" if padded else ""};': {'price': '10'},
                           f';blue{";" if padded else ""};': {'price': '20'}}}
            fill_empty_sale_options(item, [self.sale_rule()])
            length_key = item['saleAttributes'][1]['values'][0]['skuKey']
            self.assertEqual(item['skuMap'], {f';red;{length_key};': {'price': '10'},
                                             f';blue;{length_key};': {'price': '20'}})

    def test_missing_group_can_be_appended_without_changing_existing_spec(self):
        item = {'saleAttributes': [{'name': '颜色', 'values': [{'name': '红', 'skuKey': 'red'}]}],
                'skuMap': {';red;': {'price': '10'}}}
        fill_empty_sale_options(item, [self.sale_rule()])
        self.assertEqual(item['saleAttributes'][0]['values'], [{'name': '红', 'skuKey': 'red'}])
        self.assertEqual(len(item['skuMap']), 1)

    def test_no_other_option_or_ambiguous_sku_does_not_mutate(self):
        for choices, sku_map in (([{'vid': 1, 'name': '4inch'}], {';;': {}}),
                                 ([{'vid': 2, 'name': '其他'}], {';unmapped;': {}})):
            rule = self.sale_rule()
            rule['values'] = choices
            item = {'saleAttributes': [], 'skuMap': sku_map}
            before = copy.deepcopy(item)
            with self.assertRaises(ValueError):
                fill_empty_sale_options(item, [rule])
            self.assertEqual(item, before)

    def test_coverage_then_texture_then_weight_without_changing_coverage(self):
        coverage = {'name': '是否遮住颈部、面部或肩部？', 'templatePid': 1, 'refPid': 1,
                    'valueType': 'SELECT', 'values': [{'vid': 10, 'name': '是'}, {'vid': 11, 'name': '否'}]}
        texture = {'name': '面料纹理1', 'templatePid': 2, 'refPid': 2, 'pid': 2054,
                   'valueType': 'SELECT', 'parentTemplatePid': 1,
                   'values': [{'vid': 20, 'name': '光面', 'parentVidList': [10]},
                              {'vid': 21, 'name': '绒面/PU', 'parentVidList': [10]}]}
        weight = {'name': '面料克重1（g/m²）', 'templatePid': 3, 'valueType': 'STRING',
                  'parentTemplatePid': 2, 'showCondition': [{'parentRefPid': 2, 'parentVids': [20]}]}
        rules = [coverage, texture, weight]
        item = {'attributes': [{'name': coverage['name'], 'templatePid': 1, 'refPid': 1, 'values': [{'vid': 11}]}]}
        before = copy.deepcopy(item)
        self.assertFalse(apply_required_choice_default(item, rules, '面料纹理1', '光面', required_by_vendor=True, force_choice=True))
        self.assertFalse(apply_fabric_weight_default(item, rules))
        self.assertEqual(item, before)
        item['attributes'][0]['values'][0]['vid'] = 10
        item['attributes'].append({'name': '面料纹理2', 'templatePid': 4, 'pid': 2054, 'values': [{'vid': 99}]})
        self.assertTrue(apply_required_choice_default(item, rules, '面料纹理1', '光面', required_by_vendor=True, force_choice=True))
        self.assertTrue(apply_fabric_weight_default(item, rules))
        self.assertEqual(item['attributes'][1]['values'][0]['vid'], 99)
        self.assertEqual(item['attributes'][-1]['values'][0]['numberValue'], '5')
        item['attributes'][-2]['values'][0]['vid'] = 21
        self.assertTrue(apply_required_choice_default(item, rules, '面料纹理1', '光面', required_by_vendor=True, force_choice=True))
        self.assertEqual(item['attributes'][-2]['values'][0]['vid'], 20)
