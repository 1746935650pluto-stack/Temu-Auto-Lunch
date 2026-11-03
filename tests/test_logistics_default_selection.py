import unittest
from automation_pool import select_logistics_default


class LogisticsDefaultSelectionTests(unittest.TestCase):
    def test_general_is_supported_with_other_named_warehouses(self):
        options = [{'id':'west','name':'美西'}, {'id':'general','name':' 通用 '}]
        self.assertEqual(select_logistics_default(options,'warehouse')['id'],'general')

    def test_shipping_retains_priority_over_general(self):
        options = [{'id':'general','name':'通用'}, {'id':'shipping','name':'发货'}]
        self.assertEqual(select_logistics_default(options,'warehouse')['id'],'shipping')

    def test_ambiguous_shipping_does_not_fall_back_to_general(self):
        options = [{'id':'a','name':'发货'}, {'id':'b','name':'发货'}, {'id':'c','name':'通用'}]
        self.assertIsNone(select_logistics_default(options,'warehouse'))

    def test_ambiguous_general_and_single_custom_are_not_guessed(self):
        self.assertIsNone(select_logistics_default([{'id':'a','name':'通用'},{'id':'b','name':'通用'}],'warehouse'))
        self.assertIsNone(select_logistics_default([{'id':'a','name':'美西'}],'warehouse'))

    def test_repeated_same_id_is_unique_and_missing_ids_are_ignored(self):
        options = [{'id':'','name':'发货'}, {'id':'a','name':'通用'}, {'id':'a','name':'通用'}]
        self.assertEqual(select_logistics_default(options,'warehouse')['id'],'a')

    def test_freight_still_uses_general_only(self):
        self.assertIsNone(select_logistics_default([{'id':'a','name':'发货'}],'freight'))
        self.assertEqual(select_logistics_default([{'id':'a','name':'通用'}],'freight')['id'],'a')
