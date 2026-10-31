import unittest
from unittest.mock import patch

import server
import test_shared_automation as fixtures


class RemovedAccountVisibilityTests(unittest.TestCase):
    setUp = fixtures.SharedAutomationTests.setUp
    request = fixtures.SharedAutomationTests.request
    stop_server = fixtures.SharedAutomationTests.stop_server

    def test_cleared_retired_account_is_absent_but_uncleared_history_remains_visible(self):
        slots = server.miaoshou_account_slots(include_removed=True)
        removed = {'id': 3, 'name': '妙手采集箱 3', 'configured': False,
                   'removed': True, 'historyCleared': False}
        with patch.object(server, 'miaoshou_account_slots', return_value=[*slots, removed]):
            self.assertIn(3, [a['id'] for a in self.service.accounts()])
        removed['historyCleared'] = True
        with patch.object(server, 'miaoshou_account_slots', return_value=[*slots, removed]):
            self.assertEqual([a['id'] for a in self.service.accounts()], [a['id'] for a in slots])
            self.assertNotIn(3, [p['accountId'] for p in self.service.activity()['pools']])
            with self.assertRaisesRegex(ValueError, '未配置 API'):
                self.service.check_account(3)


if __name__ == '__main__':
    unittest.main()
