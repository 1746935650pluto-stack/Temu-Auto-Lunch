"""Maintenance HTTP integration: loopback server and disposable databases only."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
import http.client
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import event_archive
from maintenance_service import MaintenanceError, MaintenanceService
import server
import test_event_archive as archive_fixtures
import test_order_accounts as http_fixtures


class MaintenanceRouteTests(unittest.TestCase):
    request = http_fixtures.OrderAccountTests.request
    stop_server = http_fixtures.OrderAccountTests.stop_server
    endpoint = '/api/maintenance/event-archive'

    def setUp(self):
        http_fixtures.OrderAccountTests.setUp(self)
        self.root = server.AUTOMATION_POOL.path.parent
        # AutomationPool initializes lazily; make only this disposable pool
        # physical so the production provider can enumerate its existing file.
        with closing(server.AUTOMATION_POOL._connect()):
            pass
        cache = patch.object(server, 'MAINTENANCE_SERVICES', {})
        cache.start()
        self.addCleanup(cache.stop)
        remote = patch.object(server.MiaoShouClient, 'post', side_effect=AssertionError('No remote API is permitted'))
        self.remote = remote.start()
        self.addCleanup(remote.stop)
        self.addCleanup(self.remote.assert_not_called)
        self.headers = {'Origin': f'http://127.0.0.1:{self.httpd.server_port}'}

    @staticmethod
    def body(preview, **updates):
        return {'previewToken': preview['token'], 'retentionDays': preview['retentionDays'],
                'confirmation': 'ARCHIVE_SUCCESS_EVENTS', **updates}

    def raw_request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        try:
            connection.request(method, path, body, {'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type'), response.read()
        finally:
            connection.close()

    def seed_success(self):
        pool = server.AUTOMATION_POOL
        job_id = 'a' * 32
        pool.put_item(123, 'site', 0, {'title': 'fixture'})
        pool.start_job(job_id, 123, 7, 'US')
        pool.update_job(job_id, 'complete', 'done', 'fixture complete')
        old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with closing(pool._connect()) as db, db:
            db.execute('UPDATE automation_jobs SET created_at=?,updated_at=?', (old, old))
            db.execute('UPDATE automation_job_events SET created_at=?', (old,))
        return job_id

    def test_same_origin_preview_token_execute_and_replay_use_temporary_pool(self):
        job_id = self.seed_success()
        before = server.AUTOMATION_POOL.get_job(job_id)
        status, response = self.request('GET', self.endpoint, headers=self.headers)
        self.assertEqual(status, 200, response)
        preview = response['data']
        self.assertEqual(preview['jobs'], 1)
        self.assertEqual(preview['events'], len(before['events']))
        self.assertTrue(preview['scopeDigest'])
        self.assertTrue(preview['token'])
        self.assertFalse(event_archive.archive_path(server.AUTOMATION_POOL.path).exists())
        status, response = self.request('POST', self.endpoint, self.body(preview), self.headers)
        self.assertEqual(status, 200, response)
        self.assertEqual(response['data']['deletedEvents'], preview['events'])
        self.assertEqual(server.AUTOMATION_POOL.get_job(job_id), before)
        self.assertEqual(self.request('POST', self.endpoint, self.body(preview), self.headers)[0], 409)

    def test_cross_origin_get_and_post_are_denied_before_service_access(self):
        with patch.object(server, 'maintenance_service') as service:
            for origin in ('https://example.com', f'http://127.0.0.1:{self.httpd.server_port + 1}',
                           f'https://127.0.0.1:{self.httpd.server_port}'):
                for method, body in (('GET', None), ('POST', {})):
                    with self.subTest(origin=origin, method=method):
                        self.assertEqual(self.request(method, self.endpoint, body, {'Origin': origin})[0], 403)
            service.assert_not_called()

    def test_missing_token_confirmation_bad_days_and_client_paths_cannot_execute(self):
        preview = self.request('GET', self.endpoint)[1]['data']
        invalid = [{}, {'retentionDays': 90, 'confirmation': 'ARCHIVE_SUCCESS_EVENTS'},
            self.body(preview, previewToken=''), self.body(preview, confirmation='wrong'),
            self.body(preview, retentionDays=89), self.body(preview, retentionDays=True),
            self.body(preview, retentionDays='90'), self.body(preview, retentionDays=180),
            self.body(preview, paths=[str(self.root / 'elsewhere.sqlite3')]),
            self.body(preview, poolPaths=[]), self.body(preview, archivePath='outside.sqlite3')]
        with patch.object(event_archive, 'archive_task_events') as archive:
            for body in invalid:
                with self.subTest(body=body):
                    self.assertEqual(self.request('POST', self.endpoint, body, self.headers)[0], 400)
            archive.assert_not_called()
        self.assertEqual(self.request('POST', self.endpoint, self.body(preview, previewToken='invalid'), self.headers)[0], 409)

    def test_get_rejects_bad_days_duplicate_parameters_and_client_paths(self):
        for query in ('retentionDays=6', 'retentionDays=-1', 'retentionDays=90.1', 'retentionDays=true',
                      'retentionDays=', 'retentionDays=90&retentionDays=180',
                      'path=outside.sqlite3', 'poolPaths=x', 'retentionDays=90&archive=x'):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', self.endpoint + '?' + query)[0], 400)
        for days in (7, 30, 90, 91, 180, 365):
            status, response = self.request('GET', self.endpoint + f'?retentionDays={days}')
            self.assertEqual(status, 200, response)
            self.assertEqual(response['data']['retentionDays'], days)

    def test_malformed_json_and_oversized_body_never_reach_execution(self):
        service = Mock(spec=MaintenanceService)
        with patch.object(server, 'maintenance_service', return_value=service):
            for data in ('{', '[]', 'null', '"string"', '{"padding":"' + 'x' * 9000 + '"}'):
                with self.subTest(data=data[:20]):
                    status, _, _ = self.raw_request('POST', self.endpoint, data, self.headers)
                    self.assertEqual(status, 400)
            service.execute.assert_not_called()

    def test_partial_scope_conflict_preserves_data_and_error_status_over_http(self):
        partial = {'scopeConflict': True, 'deletedEvents': 2, 'archivedEvents': 2,
                   'errors': [{'code': 'scope_conflict', 'error': 'fixture scope changed'}]}
        service = Mock(spec=MaintenanceService)
        service.execute.side_effect = MaintenanceError(HTTPStatus.CONFLICT, 'fixture conflict', data=partial)
        service.preview.side_effect = MaintenanceError(HTTPStatus.SERVICE_UNAVAILABLE, 'fixture busy')
        with patch.object(server, 'maintenance_service', return_value=service):
            status, response = self.request('POST', self.endpoint, {
                'previewToken': 'fixture', 'retentionDays': 90, 'confirmation': 'ARCHIVE_SUCCESS_EVENTS'}, self.headers)
            self.assertEqual(status, 409)
            self.assertEqual(response, {'error': 'fixture conflict', 'data': partial})
            status, response = self.request('GET', self.endpoint)
            self.assertEqual(status, 503)
            self.assertEqual(response, {'error': 'fixture busy', 'data': None})

    def test_expired_preview_maps_to_gone_without_archive(self):
        tick = [0.0]
        service = MaintenanceService(lambda: [server.AUTOMATION_POOL.path], monotonic=lambda: tick[0])
        with patch.object(server, 'maintenance_service', return_value=service):
            preview = self.request('GET', self.endpoint)[1]['data']
            tick[0] = 600
            with patch.object(event_archive, 'archive_task_events') as archive:
                self.assertEqual(self.request('POST', self.endpoint, self.body(preview))[0], 410)
                archive.assert_not_called()

    def test_diagnostics_script_is_public_but_python_and_databases_are_not(self):
        status, content_type, content = self.raw_request('GET', '/diagnostics-panel.js?v=fixture')
        self.assertEqual(status, 200)
        self.assertTrue('javascript' in content_type or 'text/plain' in content_type)
        self.assertEqual(content, (Path(server.__file__).parent / 'diagnostics-panel.js').read_bytes())
        for path in ('/server.py', '/maintenance_service.py', '/event_archive.py',
                     '/automation_pool.sqlite3', '/automation_pool.archive.sqlite3', '/.env'):
            with self.subTest(path=path):
                status, _, content = self.raw_request('GET', path)
                self.assertEqual(status, 404)
                self.assertIn('error', json.loads(content))

    def test_service_singleton_is_stable_for_same_root_and_isolated_across_roots(self):
        first = server.maintenance_service()
        self.assertIs(first, server.maintenance_service())
        preview = first.preview()
        with patch.object(server, 'AUTOMATION_POOL', SimpleNamespace(path=self.root / '.' / 'automation_pool.sqlite3')):
            self.assertIs(server.maintenance_service(), first)
        other_root = self.root / 'other-root'
        other_root.mkdir()
        other_path = other_root / 'automation_pool.sqlite3'
        archive_fixtures.EventArchiveTests.create_pool(other_path)
        with patch.object(server, 'AUTOMATION_POOL', SimpleNamespace(path=other_path)):
            second = server.maintenance_service()
            self.assertIsNot(second, first)
            self.assertIs(second, server.maintenance_service())
            self.assertEqual(self.request('POST', self.endpoint, self.body(preview))[0], 409)
        self.assertIs(server.maintenance_service(), first)
        self.assertEqual(self.request('POST', self.endpoint, self.body(preview))[0], 200)

    def test_provider_enumerates_three_pools_and_accounts_2_through_20_only(self):
        names = {'automation_pool.sqlite3', 'automation_pool.pool-2.sqlite3', 'automation_pool.pool-3.sqlite3'}
        for account in (2, 20):
            stem = f'automation_pool.account-{account}'
            names.update({stem + '.sqlite3', stem + '.pool-2.sqlite3', stem + '.pool-3.sqlite3'})
        for name in names - {'automation_pool.sqlite3'}:
            archive_fixtures.EventArchiveTests.create_pool(self.root / name)
        ignored = {'automation_pool.archive.sqlite3', 'automation_pool.pool-2.archive.sqlite3',
                   'automation_pool.account-2.archive.sqlite3', 'automation_pool.account-21.sqlite3',
                   'automation_pool.pool-4.sqlite3', 'automation_pool.account-1.sqlite3',
                   'automation_pool.unknown.sqlite3', 'unrelated.sqlite3'}
        for name in ignored:
            # Invalid schemas are intentional: enumerating one would fail the preview.
            with closing(sqlite3.connect(self.root / name)):
                pass
        status, response = self.request('GET', self.endpoint)
        self.assertEqual(status, 200, response)
        paths = {Path(row['pool']) for row in response['data']['pools']}
        self.assertEqual(paths, {self.root / name for name in names})
        self.assertTrue(all(path.parent == self.root for path in paths))


if __name__ == '__main__':
    unittest.main()
