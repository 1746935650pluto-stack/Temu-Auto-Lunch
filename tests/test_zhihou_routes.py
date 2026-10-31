"""ZhiHou integration routes: all traffic is mocked and session-only."""
from unittest.mock import patch
import unittest

import server
import test_integration_settings as fixtures


class ZhiHouRouteTests(unittest.TestCase):
    request = fixtures.IntegrationSettingsTests.request
    stop_server = fixtures.IntegrationSettingsTests.stop_server

    def setUp(self):
        fixtures.IntegrationSettingsTests.setUp(self)
        session = patch.object(server, 'ZHIHOU_SESSION', {'token': '', 'expiresAt': 0.0, 'account': ''})
        session.start()
        self.addCleanup(session.stop)
        previews = patch.object(server, 'ZHIHOU_WRITE_PREVIEWS', {})
        previews.start()
        self.addCleanup(previews.stop)

    def login_body(self, **changes):
        result = {
            'account': 'fixture-user', 'password': 'fixture-password',
            'tokenLifetime': 'WEEKLY_EXPIRED', 'confirmation': 'LOGIN_ZHIHOU',
        }
        result.update(changes)
        return result

    def test_login_is_same_origin_session_only_and_never_returns_token(self):
        with patch.object(server.ZhiHouClient, 'login', return_value='fixture-token') as login:
            status, body = self.request('POST', '/api/zhihou/login', self.login_body(), self.headers)
        self.assertEqual(status, 200, body)
        login.assert_called_once_with('fixture-user', 'fixture-password', 'WEEKLY_EXPIRED')
        self.assertTrue(body['data']['connected'])
        self.assertNotIn('fixture-token', str(body))
        self.assertNotIn('fixture-password', str(body))
        self.assertEqual(server.ZHIHOU_SESSION['token'], 'fixture-token')
        self.assertEqual(self.request('GET', '/api/zhihou/status', headers=self.headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/zhihou/login', self.login_body(), {})[0], 403)
        self.assertEqual(self.request('POST', '/api/zhihou/login', self.login_body(extra='x'), self.headers)[0], 400)

    def test_read_routes_are_whitelisted_and_do_not_accept_arbitrary_fields(self):
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=10 ** 10, account='fixture-user')
        with patch.object(server.ZhiHouClient, 'read', return_value=[{'id': 7, 'name': 'fixture'}]) as read:
            status, body = self.request('POST', '/api/zhihou/read', {
                'confirmation': 'READ_ZHIHOU', 'action': 'warehouses'}, self.headers)
        self.assertEqual(status, 200, body)
        read.assert_called_once_with('/openApi/warehouse/cluster/list', 'fixture-token')
        self.assertNotIn('fixture-token', str(body))
        self.assertEqual(self.request('POST', '/api/zhihou/read', {
            'confirmation': 'READ_ZHIHOU', 'action': 'https://unexpected.invalid', 'path': '/anything'}, self.headers)[0], 400)

    def test_order_query_validates_bounds_and_only_posts_documented_payload(self):
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=10 ** 10, account='fixture-user')
        body = {'confirmation': 'READ_ZHIHOU', 'action': 'orders', 'storeId': '4633',
                'beginCreateTime': '2026-01-16 14:22:23', 'endCreateTime': '2026-02-26 14:22:23',
                'orderNos': ['PK12345678'], 'transferOrderNos': ['ZH9Y121234'], 'pageNo': 1, 'pageSize': 30}
        with patch.object(server.ZhiHouClient, 'read', return_value={'totalCount': 0, 'data': []}) as read:
            status, response = self.request('POST', '/api/zhihou/read', body, self.headers)
        self.assertEqual(status, 200, response)
        read.assert_called_once_with('/openApi/parcel/page', 'fixture-token', {
            'storeId': '4633', 'beginCreateTime': '2026-01-16 14:22:23',
            'endCreateTime': '2026-02-26 14:22:23', 'orderNos': ['PK12345678'],
            'transferOrderNos': ['ZH9Y121234'], 'pageNo': 1, 'pageSize': 30})
        self.assertEqual(self.request('POST', '/api/zhihou/read', {
            'confirmation': 'READ_ZHIHOU', 'action': 'orders', 'pageNo': 1, 'pageSize': 101}, self.headers)[0], 400)

    def test_disconnect_and_expired_session_clear_memory_without_remote_call(self):
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=1.0, account='fixture-user')
        status, state = self.request('GET', '/api/zhihou/status', headers=self.headers)
        self.assertEqual(status, 200)
        self.assertFalse(state['data']['connected'])
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=10 ** 10, account='fixture-user')
        status, body = self.request('POST', '/api/zhihou/disconnect', {'confirmation': 'DISCONNECT_ZHIHOU'}, self.headers)
        self.assertEqual(status, 200, body)
        self.assertFalse(body['data']['connected'])
        self.assertEqual(server.ZHIHOU_SESSION['token'], '')

    def test_write_preview_is_local_then_single_use_confirmed_submit(self):
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=10 ** 10, account='fixture-user')
        preview_body = {
            'confirmation': 'PREVIEW_ZHIHOU_WRITE', 'action': 'create', 'storeId': '4633',
            'orderNo': 'PK12345678', 'channelId': '5605494029019136', 'country': '美国',
            'countryCode': 'US', 'zipcode': '71221-6618', 'virPartTag': 'D', 'itemHsCode': '3924900000',
            'itemQuantity': '1', 'itemWeight': '50', 'itemAmount': '200',
        }
        with patch.object(server.ZhiHouClient, 'write', return_value={'result': [{'orderNo': 'PK12345678'}]}) as write:
            status, preview = self.request('POST', '/api/zhihou/write-preview', preview_body, self.headers)
            self.assertEqual(status, 200, preview)
            write.assert_not_called()
            self.assertNotIn('fixture-token', str(preview))
            self.assertEqual(preview['data']['endpoint'], '/openApi/parcel/add')
            self.assertEqual(preview['data']['payload'][0]['orderItem'][0]['hsCode'], '3924900000')
            submit = {'confirmation': 'SUBMIT_ZHIHOU_WRITE', 'previewId': preview['data']['previewId']}
            status, result = self.request('POST', '/api/zhihou/write-submit', submit, self.headers)
        self.assertEqual(status, 200, result)
        write.assert_called_once_with('/openApi/parcel/add', 'fixture-token', [{
            'storeId': '4633', 'orderNo': 'PK12345678', 'channelId': '5605494029019136',
            'country': '美国', 'countryCode': 'US', 'zipcode': '71221-6618', 'virPartTag': 'D',
            'orderItem': [{'hsCode': '3924900000', 'quantity': 1, 'weight': 50, 'amount': 200}],
        }])
        self.assertEqual(self.request('POST', '/api/zhihou/write-submit', submit, self.headers)[0], 400)

    def test_write_actions_are_whitelisted_and_cancel_uses_documented_array(self):
        server.ZHIHOU_SESSION.update(token='fixture-token', expiresAt=10 ** 10, account='fixture-user')
        self.assertEqual(self.request('POST', '/api/zhihou/write-preview', {
            'confirmation': 'PREVIEW_ZHIHOU_WRITE', 'action': 'https://unexpected.invalid'}, self.headers)[0], 400)
        body = {'confirmation': 'PREVIEW_ZHIHOU_WRITE', 'action': 'cancel', 'storeId': '333341792', 'orderNo': 'XM2266034'}
        status, preview = self.request('POST', '/api/zhihou/write-preview', body, self.headers)
        self.assertEqual(status, 200, preview)
        with patch.object(server.ZhiHouClient, 'write', return_value=[{'id': 'fixture', 'note': 'ok'}]) as write:
            status, result = self.request('POST', '/api/zhihou/write-submit', {
                'confirmation': 'SUBMIT_ZHIHOU_WRITE', 'previewId': preview['data']['previewId']}, self.headers)
        self.assertEqual(status, 200, result)
        write.assert_called_once_with('/openApi/parcel/cancel', 'fixture-token', [{'storeId': 333341792, 'orderNo': 'XM2266034'}])
        self.assertEqual(self.request('POST', '/api/zhihou/write-submit', {
            'confirmation': 'SUBMIT_ZHIHOU_WRITE', 'previewId': 'x' * 32}, self.headers)[0], 400)


if __name__ == '__main__':
    unittest.main()
