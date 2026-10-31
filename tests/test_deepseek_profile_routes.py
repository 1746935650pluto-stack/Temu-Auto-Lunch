"""Named API lifecycle under isolated accounts; every key is synthetic."""
import unittest
from unittest.mock import patch

import server
import test_integration_settings as fixtures


class DeepSeekProfileRouteTests(unittest.TestCase):
    request = fixtures.IntegrationSettingsTests.request
    stop_server = fixtures.IntegrationSettingsTests.stop_server
    setUp = fixtures.IntegrationSettingsTests.setUp

    def profile(self, action, identifier='fixture-api', **fields):
        body = {'action': action, 'confirmation': {
            'create': 'CREATE_DEEPSEEK_PROFILE', 'replace': 'REPLACE_DEEPSEEK_PROFILE',
            'save': 'SAVE_DEEPSEEK_PROFILE', 'assign': 'ASSIGN_DEEPSEEK_PROFILE',
            'delete': 'DELETE_DEEPSEEK_PROFILE'}[action]}
        if action in ('create', 'replace', 'save'):
            body.update(id=identifier, name='Fixture', apiKey='synthetic-key')
        elif action == 'delete':
            body['id'] = identifier
        body.update(fields)
        return self.request('POST', '/api/settings/deepseek/profiles', body, self.headers)

    def test_create_collision_and_missing_replace_are_conflicts_without_mutation(self):
        status, data = self.profile('create')
        self.assertEqual(status, 200, data)
        before = server.DEEPSEEK_PROFILES.public()
        for action in ('create', 'save'):
            self.assertEqual(self.profile(action, apiKey='different-synthetic-key')[0], 409)
        self.assertEqual(self.profile('replace', identifier='missing')[0], 409)
        self.assertEqual(server.DEEPSEEK_PROFILES.public(), before)

    def test_create_is_allowed_during_ai_and_replace_protects_only_assigned_accounts(self):
        self.assertEqual(self.profile('create')[0], 200)
        self.assertEqual(self.profile('assign', accountId=1, profileId='fixture-api')[0], 200)
        with patch.object(server, 'DEEPSEEK_ACCOUNT_ACTIVE', {1: 1}):
            self.assertEqual(self.profile('create', identifier='spare-api')[0], 200)
            self.assertEqual(self.profile('replace', identifier='spare-api')[0], 200)
            self.assertEqual(self.profile('replace')[0], 409)
            self.assertEqual(self.profile('delete')[0], 409)
            self.assertEqual(self.profile('assign', accountId=2, profileId='spare-api')[0], 200)
        self.assertEqual(self.profile('replace', apiKey='replacement-synthetic')[0], 200)
        self.assertEqual(server.DEEPSEEK_PROFILES.resolve(1)['apiKey'], 'replacement-synthetic')

    def test_assignment_rejected_for_queued_editor_and_leaves_existing_choice(self):
        self.assertEqual(self.profile('create')[0], 200)
        pool = server.automation_pool(2)
        pool.put_item(123, 'site', 0, {'title': 'fixture'})
        pool.queue_jobs([123], 7, 'US')
        status, response = self.profile('assign', accountId=2, profileId='fixture-api')
        self.assertEqual(status, 409, response)
        self.assertNotIn('2', server.DEEPSEEK_PROFILES.public()['assignments'])
        self.assertEqual(self.profile('assign', accountId=1, profileId='fixture-api')[0], 200)

    def test_responses_have_revision_and_never_return_keys(self):
        status, result = self.profile('create')
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['revision'], 1)
        self.assertNotIn('synthetic-key', str(result))
        self.assertEqual(self.profile('assign', accountId=1, profileId='fixture-api')[0], 200)
        status, result = self.request('GET', '/api/settings/deepseek/profiles', headers=self.headers)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['revision'], 2)
        self.assertNotIn('synthetic-key', str(result))


if __name__ == '__main__':
    unittest.main()
