import unittest

from shared_automation import template_preflight
from test_item_editor import sample_item


def valid_item():
    item = sample_item()
    item.update(
        multiLanguageTitleMap={'en': 'Sample product'},
        shipmentLimitDay=2,
        siteShipmentLimitDayMap={'US': 2},
        saleAttributes=[{'name': '颜色', 'values': [{'name': '红色', 'imgUrls': ['https://example.com/red.jpg']}]}],
    )
    item['skuMap'][';sample;']['imgUrl'] = 'https://example.com/sku.jpg'
    return item


class ImportTemplatePreflightTests(unittest.TestCase):
    def test_valid_common_template_fields_pass(self):
        self.assertEqual(template_preflight(valid_item(), 'US'), [])

    def test_reports_shipping_and_component_before_remote_create(self):
        item = valid_item()
        item['siteShipmentLimitDayMap']['US'] = 7
        item['skuMap'][';sample;'].update(skuClassification=2, productSkuAccessories=None)
        issues = template_preflight(item, 'US')
        self.assertTrue(any('承诺发货时效' in issue for issue in issues))
        self.assertTrue(any('包含的组件' in issue for issue in issues))

    def test_apparel_requires_two_specs_and_colour_images(self):
        item = valid_item()
        item['title'] = '女装连衣裙'
        item['saleAttributes'][0]['values'][0]['imgUrls'] = []
        issues = template_preflight(item, 'US')
        self.assertIn('服饰商品缺少第二组规格', issues)
        self.assertIn('服饰颜色规格缺少颜色图', issues)
