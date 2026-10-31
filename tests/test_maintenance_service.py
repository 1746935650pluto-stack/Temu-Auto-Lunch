"""Preview approval and execution use disposable physical pools only."""
from contextlib import closing
from datetime import timedelta
from http import HTTPStatus
import sqlite3
from threading import Event, Thread
import unittest
from unittest.mock import patch

import event_archive
import test_event_archive as fixtures
from maintenance_service import MaintenanceError, MaintenanceService


class MaintenanceServiceTests(unittest.TestCase):
    create_pool = staticmethod(fixtures.EventArchiveTests.create_pool)
    add_job = fixtures.EventArchiveTests.add_job
    rows = fixtures.EventArchiveTests.rows

    def setUp(self):
        fixtures.EventArchiveTests.setUp(self)
        self.add_job()
        self.paths = [self.path]
        self.now = fixtures.NOW
        self.tick = 100.0
        self.service = MaintenanceService(lambda: self.paths,
            clock=lambda: self.now, monotonic=lambda: self.tick)

    @staticmethod
    def body(preview, **updates):
        return {'previewToken': preview['token'], 'retentionDays': preview['retentionDays'],
                'confirmation': 'ARCHIVE_SUCCESS_EVENTS', **updates}

    def assert_status(self, status, action):
        with self.assertRaises(MaintenanceError) as caught:
            action()
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(caught.exception.status_code, int(status))

    def test_preview_is_read_only_and_execution_uses_the_exact_approval(self):
        before = self.rows()
        preview = self.service.preview()
        self.assertTrue(preview['previewOnly'])
        self.assertEqual((preview['jobs'], preview['events'], preview['retentionDays']), (1, 2, 90))
        self.assertEqual(preview['expiresInSeconds'], 600)
        self.assertEqual(self.rows(), before)
        self.assertFalse(event_archive.archive_path(self.path).exists())
        # The client cannot alter the approval stored in the service.
        preview['pools'][0]['events'] = 999
        result = self.service.execute(self.body(preview))
        self.assertEqual(result['deletedEvents'], 2)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows(event_archive.archive_path(self.path)), before)
        self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))

    def test_missing_token_confirmation_and_client_paths_are_rejected(self):
        preview = self.service.preview()
        invalid = [{}, [], {'retentionDays': 90, 'confirmation': 'ARCHIVE_SUCCESS_EVENTS'},
            self.body(preview, previewToken=''), self.body(preview, previewToken=1),
            self.body(preview, confirmation='archive'), self.body(preview, paths=[str(self.path)]),
            self.body(preview, poolPaths=[]), self.body(preview, archive='/outside.sqlite3')]
        with patch.object(event_archive, 'archive_task_events') as archive:
            for body in invalid:
                with self.subTest(body=body):
                    self.assert_status(HTTPStatus.BAD_REQUEST, lambda: self.service.execute(body))
            archive.assert_not_called()
        self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview, previewToken='invalid-token')))

    def test_retention_accepts_presets_and_custom_integer_but_never_under_7(self):
        for days in (7, 30, 90, 91, 180, 365, 1000):
            with self.subTest(days=days):
                self.assertEqual(self.service.preview(days)['retentionDays'], days)
        for days in (6, 0, -1, True, 90.0, '90', None):
            with self.subTest(days=days):
                self.assert_status(HTTPStatus.BAD_REQUEST, lambda: self.service.preview(days))
        preview = self.service.preview(180)
        self.assert_status(HTTPStatus.BAD_REQUEST, lambda: self.service.execute(self.body(preview, retentionDays=90)))
        self.assertEqual(self.service.execute(self.body(preview))['deletedEvents'], 0)

    def test_expiration_is_monotonic_and_expired_token_never_executes(self):
        preview = self.service.preview()
        self.now -= timedelta(days=10)
        self.tick += 600
        with patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.GONE, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()
        self.assertEqual(len(self.rows()), 2)

    def test_token_cache_is_bounded_and_expired_tokens_are_removed(self):
        service = MaintenanceService(lambda: self.paths, clock=lambda: self.now,
                                     monotonic=lambda: self.tick, token_limit=2)
        first, second, third = (service.preview() for _ in range(3))
        self.assertEqual(len(service._tokens), 2)
        self.assert_status(HTTPStatus.CONFLICT, lambda: service.execute(self.body(first)))
        self.tick += 601
        service.preview()
        self.assertEqual(len(service._tokens), 1)

    def test_provider_scope_change_conflicts_even_when_added_pool_has_no_events(self):
        preview = self.service.preview()
        other = self.root / 'automation_pool.account-2.sqlite3'
        self.create_pool(other)
        self.paths.append(other)
        with patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()
        self.assertEqual(len(self.rows()), 2)

    def test_repreview_detects_changed_counts_and_consumes_old_token(self):
        preview = self.service.preview()
        self.add_job('another-success')
        with patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
            self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()
        self.assertEqual(len(self.rows()), 4)

    def test_repreview_detects_equal_count_replacement_of_approved_job(self):
        preview = self.service.preview()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE automation_jobs SET job_id='replacement' WHERE job_id='success'")
            db.execute("UPDATE automation_job_events SET job_id='replacement' WHERE job_id='success'")
        with patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()

    def test_preview_cutoff_does_not_age_new_candidates_into_the_execution(self):
        boundary = (self.now - timedelta(days=90) + timedelta(seconds=60)).isoformat()
        self.add_job('not-yet-old', updated_at=boundary)
        preview = self.service.preview()
        self.now += timedelta(minutes=5)
        self.tick += 300
        result = self.service.execute(self.body(preview))
        self.assertEqual(result['cutoff'], preview['cutoff'])
        self.assertEqual(result['deletedEvents'], 2)
        self.assertEqual({row[1] for row in self.rows()}, {'not-yet-old'})

    def test_scope_change_after_repreview_is_still_refused_by_archive_engine(self):
        preview = self.service.preview()
        original = event_archive.archive_task_events
        def archive(*args, **kwargs):
            self.add_job('added-after-repreview')
            return original(*args, **kwargs)
        with patch.object(event_archive, 'archive_task_events', side_effect=archive):
            with self.assertRaises(MaintenanceError) as caught:
                self.service.execute(self.body(preview))
        self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
        self.assertTrue(caught.exception.data['scopeConflict'])
        self.assertEqual(caught.exception.data['deletedEvents'], 0)
        self.assertEqual(len(self.rows()), 4)

    def test_equal_count_event_content_change_invalidates_preview(self):
        preview = self.service.preview()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE automation_job_events SET message='modified' WHERE event_id=1")
        with patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()

    def test_expiry_during_revalidation_stops_before_archive(self):
        preview = self.service.preview()
        original = event_archive.preview_event_archives
        def repreview(*args, **kwargs):
            self.tick += 601
            return original(*args, **kwargs)
        with patch.object(event_archive, 'preview_event_archives', side_effect=repreview), \
                patch.object(event_archive, 'archive_task_events') as archive:
            self.assert_status(HTTPStatus.GONE, lambda: self.service.execute(self.body(preview)))
            archive.assert_not_called()

    def test_concurrent_execute_is_single_flight_and_does_not_consume_other_token(self):
        first, second = self.service.preview(), self.service.preview()
        entered, release = Event(), Event()
        results, errors = [], []
        def archive(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test did not release archive')
            return {'previewOnly': False, 'deletedEvents': 0, 'errors': []}
        def execute():
            try:
                results.append(self.service.execute(self.body(first)))
            except Exception as error:
                errors.append(error)
        with patch.object(event_archive, 'archive_task_events', side_effect=archive) as operation:
            worker = Thread(target=execute)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(first)))
                self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(second)))
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(operation.call_count, 1)
        self.assertEqual(self.service.execute(self.body(second))['deletedEvents'], 2)

    def test_operation_error_consumes_token_and_releases_single_flight_lock(self):
        preview = self.service.preview()
        with patch.object(event_archive, 'archive_task_events', side_effect=sqlite3.OperationalError('test busy')):
            self.assert_status(HTTPStatus.SERVICE_UNAVAILABLE, lambda: self.service.execute(self.body(preview)))
        self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))
        fresh = self.service.preview()
        self.assertEqual(self.service.execute(self.body(fresh))['deletedEvents'], 2)

    def test_partial_archive_report_is_returned_and_cannot_reuse_token(self):
        preview = self.service.preview()
        result = {'previewOnly': False, 'deletedEvents': 0, 'errors': [{'error': 'fixture failure'}]}
        with patch.object(event_archive, 'archive_task_events', return_value=result):
            self.assertEqual(self.service.execute(self.body(preview)), result)
        self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))

    def test_late_scope_conflict_preserves_partial_completion_report(self):
        preview = self.service.preview()
        report = {'scopeConflict': True, 'deletedEvents': 2,
                  'errors': [{'code': 'scope_conflict', 'error': 'fixture scope change'}]}
        with patch.object(event_archive, 'archive_task_events', return_value=report):
            with self.assertRaises(MaintenanceError) as caught:
                self.service.execute(self.body(preview))
        self.assertEqual(caught.exception.status, HTTPStatus.CONFLICT)
        self.assertEqual(caught.exception.data, report)
        self.assert_status(HTTPStatus.CONFLICT, lambda: self.service.execute(self.body(preview)))


if __name__ == '__main__':
    unittest.main()
