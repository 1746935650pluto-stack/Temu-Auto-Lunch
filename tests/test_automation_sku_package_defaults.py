import copy
import unittest
import server
from test_item_editor import sample_item


class PackagingDefaultsTests(unittest.TestCase):
    def prepare(self, raw):
        return server._automation_item_for_save(raw, 777, 1234, 'US', '中文标题', 'English title', '11', '22')

    def test_missing_null_empty_and_whitespace_fields_receive_defaults_without_snapshot_changes(self):
        for missing in (None, '', '  '):
            with self.subTest(missing=missing):
                raw = sample_item()
                raw['skuMap'][';sample;'].update(length=missing, width=missing, height=missing, weight=missing)
                del raw['skuMap'][';sample;']['width']
                original = copy.deepcopy(raw)
                sku = self.prepare(raw)['skuMap'][';sample;']
                self.assertEqual({k: sku[k] for k in ('length','width','height','weight')},
                                 {'length':'10','width':'10','height':'3','weight':200})
                self.assertEqual(raw, original)

    def test_existing_values_are_preserved_per_field_and_per_sku(self):
        raw = sample_item()
        raw['skuMap'][';sample;'].update(length='12', width='', height='8', weight=321)
        raw['skuMap'][';second;'] = dict(raw['skuMap'][';sample;'], length=None, width='6', height=None, weight=None)
        skus = self.prepare(raw)['skuMap']
        self.assertEqual([skus[';sample;'][k] for k in ('length','width','height','weight')],['12','10','8',321])
        self.assertEqual([skus[';second;'][k] for k in ('length','width','height','weight')],['10','6','3',200])
