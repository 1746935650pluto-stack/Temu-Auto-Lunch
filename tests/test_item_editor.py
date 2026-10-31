import copy
import unittest

from item_editor import (apply_blade_length_default, apply_fabric_weight_default, apply_thickness_default, apply_textile_fiber_composition_default, apply_food_contact_default, apply_source_origin_default, apply_required_choice_default,
                         apply_food_contact_usage_default, fingerprint, normalize_item,
                         normalize_show_size_template_ids)


def sample_item():
    return {
        "detailId": 777,
        "title": "Sample product",
        "cid": "123",
        "productOriginCountry": "CN",
        "productOriginProvince": "43000000000011",
        "imgUrls": ["https://example.com/image.jpg"],
        "goodsLayerDecorationReqs": [{"type": "text", "content": {"text": "Description"}}],
        "attributes": [],
        "saleAttributes": [],
        "skuMap": {";sample;": {"length": "10", "width": "10", "height": "5", "price": "99", "weight": 250, "stock": 9}},
        "publishSiteList": ["US"],
        "collectBoxDetailShopList": [{"shopId": 1234, "publishSiteList": ["US"], "shopName": "response only"}],
        "gmtModified": "response only",
    }


class ItemEditorTests(unittest.TestCase):
    def test_food_material_default_does_not_conflict_with_explicit_no(self):
        rules = [{'name': '与食品接触部位材质', 'pid': 2710, 'templatePid': 1851863,
                  'parentTemplatePid': 1850823, 'required': True,
                  'values': [{'vid': 540157, 'name': '不锈钢', 'parentVidList': [67313]}]}]
        item = sample_item()
        item['attributes'] = [{'name': '是否可用于食品接触', 'templatePid': 1850823,
                               'values': [{'vid': 67314, 'name': '否'}]}]
        before = copy.deepcopy(item)
        self.assertFalse(apply_food_contact_default(item, rules))
        self.assertEqual(item, before)
        item['attributes'][0]['values'] = [{'vid': 67313, 'name': '是'}]
        self.assertTrue(apply_food_contact_default(item, rules))

    def test_age_default_uses_six_plus_and_respects_adult_parent(self):
        rule = {'name': '适用年龄段', 'pid': 1141, 'templatePid': 1203216, 'required': True,
                'values': [{'vid': 73673, 'name': '6+'}, {'vid': 87187, 'name': '18+'}]}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [rule], '适用年龄段', '6+'))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 73673)
        item['attributes'][0]['values'][0].update(vid=87187, name='18+')
        self.assertFalse(apply_required_choice_default(item, [rule], '适用年龄段', '6+'))
        rule['parentTemplatePid'] = 55
        rule['values'][0]['parentVidList'] = [2]
        rule['values'][1]['parentVidList'] = [1]
        item = sample_item()
        item['attributes'] = [{'templatePid': 55, 'values': [{'vid': 1}]}]
        self.assertFalse(apply_required_choice_default(item, [rule], '适用年龄段', '6+'))

    def test_wood_default_uses_actual_raw_wood_choice_and_preserves_recycled(self):
        rule = {'name': '木材类型', 'pid': 2230, 'templatePid': 1420294, 'required': True,
                'parentTemplatePid': 1203213, 'values': [
                    {'vid': 213321, 'name': '原木', 'parentVidList': [410]},
                    {'vid': 213322, 'name': '再生木', 'parentVidList': [410]}]}
        item = sample_item()
        item['attributes'] = [{'templatePid': 1203213, 'name': '材质', 'values': [{'vid': 410, 'name': '木材'}]}]
        self.assertTrue(apply_required_choice_default(item, [rule], '木材类型', '原木'))
        self.assertEqual(item['attributes'][1]['values'][0]['vid'], 213321)
        item['attributes'][1]['values'][0].update(vid=213322, name='再生木')
        self.assertFalse(apply_required_choice_default(item, [rule], '木材类型', '原木'))

    def test_textile_composition_default_uses_human_made_fiber(self):
        rule = {'name': '织物材质成分', 'pid': 301, 'templatePid': 302, 'required': True,
                'valueType': 'SELECT', 'chooseMaxNum': 1,
                'values': [{'vid': 701, 'name': '人造纤维'}, {'vid': 702, 'name': '植物纤维'}]}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [rule], '织物材质成分', '人造纤维'))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 701)
        item['attributes'][0]['values'] = [{'vid': 702, 'name': '植物纤维'}]
        self.assertTrue(apply_required_choice_default(item, [rule], '织物材质成分', '人造纤维'))
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '植物纤维')

    def test_blade_material_default_uses_stainless_steel(self):
        rule = {'name': '刀片材质', 'pid': 401, 'templatePid': 402, 'required': True,
                'valueType': 'SELECT', 'chooseMaxNum': 1,
                'values': [{'vid': 801, 'name': '合金钢'}, {'vid': 802, 'name': '不锈钢'}]}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [rule], '刀片材质', '不锈钢'))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 802)

    def test_material_type_default_uses_non_textile_choice(self):
        rule = {'name': '材质类型', 'pid': 411, 'templatePid': 412, 'required': True,
                'valueType': 'SELECT', 'chooseMaxNum': 1,
                'values': [{'vid': 811, 'name': '纺织品材质'}, {'vid': 812, 'name': '非纺织品材质'}]}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [rule], '材质类型', '非纺织品材质'))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 812)

    def test_textile_material_type_is_selected_before_cotton_composition(self):
        material_type = {'name': '材质类型', 'pid': 411, 'templatePid': 412, 'required': False,
                         'values': [{'vid': 811, 'name': '纺织品材质'}, {'vid': 812, 'name': '非纺织品材质'}]}
        composition = {'name': '纺织纤维成分', 'pid': 96, 'templatePid': 97, 'valueType': 'TABLE',
                       'parentTemplatePid': 412, 'valueInputSum': 100, 'valueUnit': ['%'],
                       'values': [{'vid': 901, 'name': '棉Cotton', 'parentVidList': [811]}]}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [material_type, composition], '材质类型', '纺织品材质',
                                                      required_by_vendor=True))
        self.assertTrue(apply_textile_fiber_composition_default(item, [material_type, composition]))
        self.assertEqual([attribute['name'] for attribute in item['attributes']], ['材质类型', '纺织纤维成分'])
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '纺织品材质')

    def test_origin_default_uses_real_guangzhou_id_and_preserves_other_valid_origins(self):
        rule = {'name': '货源产地', 'pid': 1920, 'templatePid': 1186968, 'required': True,
                'valueType': 'SELECT', 'chooseMaxNum': 1,
                'values': [{'vid': 82815, 'name': '广州产区'}, {'vid': 82959, 'name': '义乌产区'}]}
        item = sample_item()
        self.assertTrue(apply_source_origin_default(item, [rule]))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 82815)
        self.assertFalse(apply_source_origin_default(item, [rule]))
        item['attributes'][0]['values'] = [{'vid': 82959, 'name': '义乌产区', 'numberValue': None, 'valueUnit': ''}]
        self.assertFalse(apply_source_origin_default(item, [rule]))
        item['attributes'][0]['values'] = [{'name': '义乌产区'}]
        self.assertTrue(apply_source_origin_default(item, [rule]))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 82959)
        rule['required'] = False
        self.assertFalse(apply_source_origin_default(sample_item(), [rule]))
        self.assertTrue(apply_source_origin_default(sample_item(), [rule], required_by_vendor=True))
        self.assertFalse(apply_source_origin_default(sample_item(), []))
        rule['values'] = [{'vid': 82959, 'name': '义乌产区'}]
        with self.assertRaisesRegex(ValueError, '没有“广州产区”'):
            apply_source_origin_default(sample_item(), [rule], required_by_vendor=True)

    def test_fabric_weight_default_and_recovery_preserve_valid_numbers(self):
        rules = [{'name': '平方克重（g/㎡）', 'templatePid': 987654, 'pid': 1776, 'refPid': 3980,
                  'valueType': 'STRING', 'propertyValueType': 1, 'valuePrecision': 255,
                  'minValue': '0', 'maxValue': '99999', 'valueUnit': ['']}]
        from attribute_ai import missing_attributes
        for name, numeric, expected in (('', None, '10'), ('NaN', 'bad', '10'),
                                         ('60', None, '60'), ('50', '85.5', '85.5'),
                                         ('', 'Infinity', '10'), ('', '-1', '10')):
            with self.subTest(name=name, numeric=numeric):
                item = sample_item()
                item['attributes'] = [{'pid': 1776, 'name': rules[0]['name'],
                                       'values': [{'vid': 0, 'name': name, 'numberValue': numeric}]}]
                self.assertTrue(apply_fabric_weight_default(item, rules))
                self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], expected)
                self.assertEqual(missing_attributes(item, rules), [])
                self.assertFalse(apply_fabric_weight_default(item, rules))
        item = sample_item()
        self.assertFalse(apply_fabric_weight_default(item, []))
        self.assertTrue(apply_fabric_weight_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '10')
        rules[0]['maxValue'] = '20'
        item['attributes'] = []
        self.assertTrue(apply_fabric_weight_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '10')

    def test_fabric_weight_one_defaults_to_five(self):
        rules = [{'name': '面料克重1（g/m²）', 'templatePid': 76, 'pid': 77,
                  'valueType': 'NUMBER', 'minValue': '0', 'maxValue': '99999', 'valueUnit': ['g/m²']}]
        item = sample_item()
        self.assertTrue(apply_fabric_weight_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '5')

    def test_fabric_texture_can_be_forced_before_its_dependent_weight(self):
        texture = {'name': '面料纹理1', 'pid': 70, 'templatePid': 71, 'required': False,
                   'values': [{'vid': 701, 'name': '光面'}, {'vid': 702, 'name': '磨砂'}]}
        weight = {'name': '面料克重1（g/m²）', 'templatePid': 76, 'pid': 77,
                  'valueType': 'NUMBER', 'minValue': '0', 'maxValue': '99999', 'valueUnit': ['g/m²']}
        item = sample_item()
        self.assertTrue(apply_required_choice_default(item, [texture, weight], '面料纹理1', '光面',
                                                      required_by_vendor=True))
        self.assertTrue(apply_fabric_weight_default(item, [texture, weight]))
        self.assertEqual([attribute['name'] for attribute in item['attributes']], ['面料纹理1', '面料克重1（g/m²）'])
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '光面')

    def test_thickness_defaults_to_three(self):
        rules = [{'name': '厚度（cm）', 'templatePid': 86, 'pid': 87,
                  'valueType': 'NUMBER', 'minValue': '0', 'maxValue': '100', 'valueUnit': ['cm']}]
        item = sample_item()
        self.assertTrue(apply_thickness_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '3')

    def test_textile_fiber_composition_defaults_to_cotton_one_hundred_percent(self):
        rules = [{'name': '纺织纤维成分', 'templatePid': 96, 'pid': 97, 'valueType': 'TABLE',
                  'valueInputSum': 100, 'valueUnit': ['%'],
                  'values': [{'vid': 901, 'name': '棉Cotton'}, {'vid': 902, 'name': '涤纶Polyester'}]}]
        item = sample_item()
        self.assertTrue(apply_textile_fiber_composition_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0], {
            'vid': 901, 'name': '棉Cotton', 'numberValue': '100', 'valueUnit': '%',
        })

    def test_food_contact_usage_defaults_to_category_yes_and_keeps_explicit_no(self):
        rules = [{'name': '是否可用于食品接触', 'pid': 1795, 'templatePid': 1261920,
                  'values': [{'vid': 67313, 'name': '是'}, {'vid': 67314, 'name': '否'}]}]
        item = sample_item()
        self.assertTrue(apply_food_contact_usage_default(item, rules))
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 67313)
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '是')
        item['attributes'][0]['values'] = [{'vid': 67314, 'name': '否'}]
        before = copy.deepcopy(item)
        self.assertFalse(apply_food_contact_usage_default(item, rules))
        self.assertEqual(item, before)
        self.assertFalse(apply_food_contact_usage_default(sample_item(), []))
        rules[0]['values'] = [{'vid': 67314, 'name': '否'}]
        with self.assertRaisesRegex(ValueError, '没有可用'):
            apply_food_contact_usage_default(sample_item(), rules)

    def test_blade_length_default_fills_numeric_field_and_preserves_existing_numbers(self):
        rules = [{'name': '刀刃长度', 'templatePid': 42, 'pid': 43, 'valueUnit': ['cm']}]
        for original in (None, '', 'five', 'NaN'):
            with self.subTest(original=original):
                item = sample_item()
                item['attributes'] = [{'templatePid': 42, 'values': [{'numberValue': original}]}]
                self.assertTrue(apply_blade_length_default(item, rules))
                self.assertEqual(item['attributes'][0]['values'], [
                    {'vid': 0, 'name': '5', 'numberValue': '5', 'valueUnit': 'cm'},
                ])
        item = sample_item()
        self.assertFalse(apply_blade_length_default(item, []))
        self.assertTrue(apply_blade_length_default(item, rules))
        self.assertEqual(item['attributes'][0]['templatePid'], 42)
        saved = normalize_item(item, 'shop', detail_id=777, shop_id=1234)
        self.assertEqual(saved['attributes'][0]['values'][0]['vid'], 0)
        item['attributes'][0]['values'][0]['numberValue'] = '8.2'
        before = copy.deepcopy(item)
        self.assertFalse(apply_blade_length_default(item, rules))
        self.assertEqual(item, before)

    def test_food_contact_default_uses_real_option_and_preserves_existing_material(self):
        rules = [{'name': '与食品接触部位材质', 'pid': 10, 'templatePid': 20,
                  'values': [{'vid': 30, 'name': '不锈钢'}]}]
        item = sample_item()
        item['attributes'] = [{'name': '颜色', 'values': [{'name': '银色'}]}]
        self.assertTrue(apply_food_contact_default(item, rules))
        self.assertEqual(item['attributes'][1]['values'][0]['vid'], 30)
        self.assertEqual(item['attributes'][0]['values'][0]['name'], '银色')
        item['attributes'][1]['values'] = [{'vid': 31, 'name': '铝'}]
        self.assertFalse(apply_food_contact_default(item, rules))
        self.assertEqual(item['attributes'][1]['values'][0]['name'], '铝')

    def test_food_contact_default_only_applies_to_matching_category_options(self):
        item = sample_item()
        self.assertFalse(apply_food_contact_default(item, []))
        self.assertEqual(item['attributes'], [])
        with self.assertRaisesRegex(ValueError, '没有可用'):
            apply_food_contact_default(item, [{'name': '与食品接触部位材质', 'values': []}])

    def test_create_payload_has_only_editable_fields(self):
        item = normalize_item(sample_item(), "site")
        self.assertNotIn("detailId", item)
        self.assertNotIn("gmtModified", item)
        self.assertNotIn("stock", item["skuMap"][";sample;"])
        self.assertNotIn("shopName", item["collectBoxDetailShopList"][0])
        self.assertEqual(item["collectBoxDetailShopList"][0]["shopId"], 1234)

    def test_source_is_not_modified(self):
        original = sample_item()
        before = copy.deepcopy(original)
        normalize_item(original, "site")
        self.assertEqual(original, before)

    def test_shop_save_fixes_ids(self):
        item = normalize_item(sample_item(), "shop", detail_id=55, shop_id=66)
        self.assertEqual((item["detailId"], item["shopId"], item["editModel"]), (55, 66, "shop"))
        self.assertNotIn("collectBoxDetailShopList", item)
        self.assertEqual(item['manufacturingLocationRegionShortNameList'], ['CN'])

    def test_manufacturing_location_uses_origin_country_when_old_item_omits_it(self):
        item = sample_item()
        item['manufacturingLocationRegionShortNameList'] = []
        self.assertEqual(normalize_item(item, 'site')['manufacturingLocationRegionShortNameList'], ['CN'])
        item['manufacturingLocationRegionShortNameList'] = ['us', 'CN', 'US']
        self.assertEqual(normalize_item(item, 'site')['manufacturingLocationRegionShortNameList'], ['US', 'CN'])
        item['manufacturingLocationRegionShortNameList'] = ['Guangdong']
        with self.assertRaisesRegex(ValueError, '制造地点'):
            normalize_item(item, 'site')

    def test_invalid_image_and_missing_sku_are_rejected(self):
        value = sample_item()
        value["imgUrls"] = ["javascript:alert(1)"]
        with self.assertRaises(ValueError):
            normalize_item(value, "site")
        value = sample_item()
        value["skuMap"] = {}
        with self.assertRaises(ValueError):
            normalize_item(value, "site")

    def test_image_count_has_no_local_ten_image_limit(self):
        for mode in ('site', 'shop'):
            for count in (14, 50):
                value = sample_item()
                value['imgUrls'] = [f'https://example.com/{i}.jpg' for i in range(count)]
                result = normalize_item(value, mode, detail_id=777, shop_id=1234)
                self.assertEqual(result['imgUrls'], value['imgUrls'])
        for images in ([], None, ['https://example.com/1.jpg', 'javascript:alert(1)']):
            value = sample_item()
            value['imgUrls'] = images
            with self.assertRaises(ValueError):
                normalize_item(value, 'site')

    def test_size_template_ids_keep_only_positive_integer_ids(self):
        item = sample_item()
        item['collectShowSizeTemplateIds'] = [12, '34', ' 034 ',
                                              '984e3e0d-1ff0-4686-8a11-16f5992213d7',
                                              0, -2, True, 5.0, '']
        self.assertTrue(normalize_show_size_template_ids(item))
        self.assertEqual(item['collectShowSizeTemplateIds'], [12, 34])
        self.assertFalse(normalize_show_size_template_ids(item))
        for mode in ('site', 'shop'):
            with self.subTest(mode=mode):
                value = sample_item()
                value['collectShowSizeTemplateIds'] = ['bad-template-uuid']
                result = normalize_item(value, mode, detail_id=777, shop_id=1234)
                self.assertEqual(result['collectShowSizeTemplateIds'], [])
                self.assertEqual(value['collectShowSizeTemplateIds'], ['bad-template-uuid'])

    def test_version_is_order_independent(self):
        value = sample_item()
        self.assertEqual(fingerprint(value), fingerprint(dict(reversed(list(value.items())))))


if __name__ == "__main__":
    unittest.main()
