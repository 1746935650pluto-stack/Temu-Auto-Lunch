"""Static coverage for the ZhiHou store selector lifecycle."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ZhiHouUiTests(unittest.TestCase):
    def test_store_selector_auto_loads_and_accepts_common_list_shapes(self):
        javascript = (ROOT / 'zhihou.js').read_text(encoding='utf-8')
        self.assertIn('async function ensureStores(force = false, throwOnError = false)', javascript)
        self.assertIn("action: 'stores'", javascript)
        self.assertIn("['stores', 'storeList', 'shopList']", javascript)
        self.assertIn('void ensureStores();', javascript)
        self.assertIn('await ensureStores(true, true);', javascript)

    def test_disconnect_clears_stale_store_options(self):
        javascript = (ROOT / 'zhihou.js').read_text(encoding='utf-8')
        self.assertIn('storesLoaded = false;', javascript)
        self.assertIn('populateStores([]);', javascript)

    def test_login_and_manual_action_sync_stores_with_the_current_order_page(self):
        javascript = (ROOT / 'zhihou.js').read_text(encoding='utf-8')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('async function syncStoresAndOrders()', javascript)
        self.assertIn('function orderQueryBody()', javascript)
        self.assertIn("$('#zhihouSyncStoresAndOrders')?.addEventListener('click', syncStoresAndOrders);", javascript)
        self.assertIn('await readOrders();', javascript)
        self.assertIn('id="zhihouSyncStoresAndOrders"', html)


if __name__ == '__main__':
    unittest.main()
