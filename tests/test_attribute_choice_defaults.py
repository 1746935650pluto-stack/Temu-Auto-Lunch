import copy
import unittest
from unittest.mock import Mock

from attribute_ai import AttributeInferenceError, complete_attributes, user_choice_values
from item_editor import apply_textile_fiber_composition_default


def composition_rule(**kwargs):
    return {'name': '成分', 'templatePid': 1, 'valueType': 'TABLE', 'required': True,
            'chooseMaxNum': 3, 'valueInputSum': 100, 'valueUnit': ['%'],
            'minValue': 0, 'maxValue': 100, 'valuePrecision': 2,
            'values': [{'vid': 10, 'name': '棉'}, {'vid': 20, 'name': '涤纶'}], **kwargs}


class UserChoiceDefaultTests(unittest.TestCase):
    def complete(self, item, rules, source=None):
        infer = Mock(return_value={'uncertain': True})
        complete_attributes(item, source or copy.deepcopy(item), rules, '服饰', infer, lambda _: None,
                            allow_user_choice_defaults=True)
        return infer

    def test_missing_composition_chooses_one_real_option_at_100_without_ai(self):
        item = {'attributes': []}
        infer = self.complete(item, [composition_rule()])
        infer.assert_not_called()
        values = item['attributes'][0]['values']
        self.assertEqual(len(values), 1)
        self.assertIn(values[0]['vid'], (10, 20))
        self.assertEqual(values[0]['numberValue'], '100')

    def test_empty_placeholder_counts_as_missing_composition(self):
        item = {'attributes': [{'name': '成分', 'values': [{'vid': 0, 'name': '', 'numberValue': None}]}]}
        self.complete(item, [composition_rule()])
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '100')

    def test_existing_ratios_are_topped_up_and_complete_ratios_unchanged(self):
        for amounts, expected in ((['60', '30'], ['70', '30']), (['60.5', '30.25'], ['69.75', '30.25']),
                                  (['60', '40'], ['60', '40']), ([''], ['100'])):
            item = {'attributes': [{'templatePid': 1, 'name': '成分', 'values': [
                {'vid': vid, 'name': name, 'numberValue': amount}
                for vid, name, amount in zip((10, 20), ('棉', '涤纶'), amounts)]}]}
            self.complete(item, [composition_rule()])
            self.assertEqual([v['numberValue'] for v in item['attributes'][0]['values']], expected)
            self.assertEqual([v['vid'] for v in item['attributes'][0]['values']], [10, 20][:len(amounts)])

    def test_partial_source_composition_is_used_when_target_empty(self):
        item = {'attributes': []}
        source = {'attributes': [{'name': '成分', 'values': [{'vid': 20, 'numberValue': 80}]}]}
        self.complete(item, [composition_rule()], source)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 20)
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '100')

    def test_bounds_and_non_composition_numbers_are_not_randomized(self):
        for rule in (composition_rule(maxValue=90, chooseMaxNum=1), composition_rule(valueInputSum=50),
                     composition_rule(name='重量', valueType='NUMBER')):
            self.assertIsNone(user_choice_values({'attributes': []}, {}, rule))
        item = {'attributes': [{'name': '成分', 'values': [{'vid': 10, 'numberValue': 110}]}]}
        before = copy.deepcopy(item)
        with self.assertRaises(AttributeInferenceError):
            self.complete(item, [composition_rule()])
        self.assertEqual(item, before)

    def test_parent_limits_choices_and_existing_select_is_preserved(self):
        parent = {'name': '父属性', 'templatePid': 1, 'valueType': 'SELECT', 'required': True,
                  'values': [{'vid': 10, 'name': '父选项'}]}
        child = {'name': '里料纹理', 'templatePid': 2, 'parentTemplatePid': 1,
                 'valueType': 'SELECT', 'required': True, 'values': [
                     {'vid': 20, 'name': '允许', 'parentVidList': [10]},
                     {'vid': 30, 'name': '不允许', 'parentVidList': [99]}]}
        item = {'attributes': []}
        infer = self.complete(item, [child, parent])
        self.assertEqual(item['attributes'][1]['values'][0]['vid'], 20)
        self.assertEqual(infer.call_count, 0)
        before = copy.deepcopy(item)
        self.complete(item, [child, parent]).assert_not_called()
        self.assertEqual(item, before)

    def test_vendor_rejection_does_not_repeatedly_randomize(self):
        rule = composition_rule(valueType='SELECT')
        item = {'attributes': []}
        attempted = set()
        kwargs = dict(allow_user_choice_defaults=True, attempted=attempted)
        infer = Mock(return_value={'uncertain': True})
        complete_attributes(item, {}, [rule], '', infer, lambda _: None, **kwargs)
        with self.assertRaises(AttributeInferenceError):
            complete_attributes(item, {}, [rule], '', infer, lambda _: None, targets=['成分'], **kwargs)
        self.assertEqual(infer.call_count, 1)

    def test_cotton_default_keeps_existing_component_without_percentage(self):
        rule = composition_rule(name='纺织纤维成分')
        rule['values'][0]['name'] = '棉Cotton'
        item = {'attributes': [{'name': rule['name'], 'values': [{'vid': 20, 'name': '涤纶'}]}]}
        self.assertFalse(apply_textile_fiber_composition_default(item, [rule]))
        self.complete(item, [rule])
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 20)
        self.assertEqual(item['attributes'][0]['values'][0]['numberValue'], '100')
