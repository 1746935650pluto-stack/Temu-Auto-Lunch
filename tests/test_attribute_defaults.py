from copy import deepcopy
import unittest

from attribute_defaults import apply_attribute_defaults


def rule(name='面料克重1（g/m²）', **kwargs):
    return dict(name=name, pid=1, templatePid=1, valueType='NUMBER', required=True,
                minValue=0, maxValue=1000, valueUnit=['g/m²'], **kwargs)


class AttributeDefaultsTests(unittest.TestCase):
    def test_numeric_defaults_and_source_values_use_same_policy(self):
        for name, expected in [('面料克重1（g/m²）', '5'), ('里料克重', '5'), ('平方克重', '10'), ('厚度', '3'), ('刀刃长度', '5')]:
            item = {'attributes': []}
            apply_attribute_defaults(item, {}, [rule(name)])
            self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], expected)
            source = deepcopy(item)
            source['attributes'][0]['values'][0].update(numberValue='87', name='87')
            item = {'attributes': []}
            apply_attribute_defaults(item, source, [rule(name)])
            self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '87')
            self.assertEqual(item['attributes'][0]['values'][0]['valueUnit'], 'g/m²')

    def test_inactive_parent_and_out_of_range_do_not_invent_values(self):
        item = {'attributes': []}
        conditional = rule(parentTemplatePid=9, parentVidList=[3])
        apply_attribute_defaults(item, {}, [conditional])
        self.assertEqual(item['attributes'], [])
        bounded = rule(); bounded['minValue'] = 100
        with self.assertRaisesRegex(ValueError, '默认值 5'):
            apply_attribute_defaults(item, {}, [bounded])
        self.assertEqual(item['attributes'], [])

    def test_valid_texture_source_is_not_forced_to_default(self):
        choices = dict(name='面料纹理1', templatePid=1, pid=1, required=True, valueType='SELECT',
                       values=[{'vid': 1, 'name': '光面'}, {'vid': 2, 'name': '磨砂'}])
        source = {'attributes': [{'templatePid': 1, 'pid': 1, 'name': '面料纹理1', 'values': [{'vid': 2, 'name': '磨砂'}]}]}
        item = {'attributes': []}
        apply_attribute_defaults(item, source, [choices])
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 2)

    def test_incompatible_source_unit_is_not_silently_relabelled(self):
        item = {'attributes': []}
        source = {'attributes': [{'templatePid': 1, 'pid': 1, 'name': '面料克重1（g/m²）',
            'values': [{'vid': 0, 'name': '5', 'numberValue': '5', 'valueUnit': 'kg/m²'}]}]}
        with self.assertRaisesRegex(ValueError, '单位'):
            apply_attribute_defaults(item, source, [rule()])
        self.assertEqual(item['attributes'], [])


if __name__ == '__main__':
    unittest.main()
