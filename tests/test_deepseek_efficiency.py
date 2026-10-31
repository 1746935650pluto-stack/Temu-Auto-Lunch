import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from deepseek_efficiency import DeepSeekUsage, generate_titles, validate_before_ai, weighted_length
from attribute_ai import complete_attributes


class EfficientTitleTests(unittest.TestCase):
    zh = '中' * 117
    en = 'a' * 235

    def test_bilingual_first_pass_is_one_request(self):
        complete = Mock(return_value=json.dumps({'zh': self.zh, 'en': self.en}))
        result = generate_titles(complete, {'zh': '原题', 'en': 'Original'})
        self.assertEqual(result, {'zh': self.zh, 'en': self.en})
        complete.assert_called_once()
        self.assertEqual(complete.call_args.kwargs['purpose'], 'title_initial')

    def test_repair_only_invalid_language_retains_accepted_title(self):
        complete = Mock(side_effect=[json.dumps({'zh': self.zh, 'en': 'short'}),
                                    json.dumps({'zh': 'must not replace', 'en': self.en})])
        result = generate_titles(complete, {'zh': '原题', 'en': 'Original'})
        repair = json.loads(complete.call_args.args[0][1]['content'])
        self.assertEqual(list(repair), ['en'])
        self.assertEqual(repair['en']['weightedLength'], 5)
        self.assertEqual(result['zh'], self.zh)
        self.assertEqual(complete.call_args.kwargs['purpose'], 'title_repair')

    def test_short_or_invalid_json_stops_after_bounded_repairs(self):
        for value in ('not json', '[]', '{"zh":"短"}', '{"zh":null}'):
            with self.subTest(value=value):
                complete = Mock(return_value=value)
                with self.assertRaisesRegex(ValueError, '220–240'):
                    generate_titles(complete, {'zh': '原题'})
                self.assertEqual(complete.call_count, 3)

    def test_safe_local_boundary_avoids_paid_repair(self):
        complete = Mock(return_value=json.dumps({'en': self.en + ', additional phrase'}))
        result = generate_titles(complete, {'en': 'Original'})
        self.assertEqual(result['en'], self.en)
        complete.assert_called_once()

    def test_overlong_word_is_not_cut_midword(self):
        complete = Mock(side_effect=[json.dumps({'en': 'a' * 250}), json.dumps({'en': self.en})])
        self.assertEqual(generate_titles(complete, {'en': 'Original'})['en'], self.en)
        self.assertEqual(complete.call_count, 2)

    def test_boundary_220_and_240_inclusive(self):
        for length in (220, 240):
            complete = Mock(return_value=json.dumps({'en': 'a' * length}))
            self.assertEqual(weighted_length(generate_titles(complete, {'en': 'Original'})['en']), length)
            complete.assert_called_once()


class UsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.usage = DeepSeekUsage(self.directory)

    def record(self, result=None):
        self.usage.record(account=2, profile='profile-1', purpose='title_initial', model='model',
                          attempt=1, outcome='response', elapsed=.1, result=result)

    def test_persistent_actual_usage_and_secret_exclusion(self):
        self.record({'usage': {'total_tokens': 12, 'prompt_tokens': 10, 'completion_tokens': 2,
                              'prompt_cache_hit_tokens': 8, 'prompt_cache_miss_tokens': 2},
                     'choices': [{'message': {'content': 'PRIVATE PRODUCT'}}], 'api_key': 'SECRET'})
        result = DeepSeekUsage(self.directory).today()
        self.assertEqual(result['requests'], 1)
        self.assertEqual(result['knownUsageRequests'], 1)
        self.assertEqual(result['totalTokens'], 12)
        self.assertEqual(result['cacheHitTokens'], 8)
        text = next(self.directory.glob('*.jsonl')).read_text()
        self.assertNotIn('PRIVATE', text)
        self.assertNotIn('SECRET', text)

    def test_concurrent_records_are_complete_and_unknown_usage_is_not_estimated(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: self.record(), range(40)))
        result = self.usage.today()
        self.assertEqual(result['requests'], 40)
        self.assertEqual(result['totalTokens'], 0)
        self.assertEqual(result['knownUsageRequests'], 0)
        self.assertEqual(result['byPurpose'], {'title_initial': 40})

    def test_logging_failure_does_not_repeat_paid_request(self):
        with patch.object(Path, 'open', side_effect=OSError('disk unavailable')):
            self.record()
        self.assertEqual(self.usage.today()['recordErrors'], 1)


class PreflightTests(unittest.TestCase):
    def events(self, message, cid='123'):
        return [{'step': 'save', 'status': 'running', 'message': f'正在提交妙手商品保存请求；类目ID={cid}'},
                {'step': 'save', 'status': 'needs_review', 'message': message}]

    def test_empty_model_or_size_chart_is_not_assumed_required(self):
        validate_before_ai({'cid': 123, 'goodsModelReqs': None, 'sizeCharts': []})

    def test_known_model_rejection_blocks_only_same_category_and_missing_data(self):
        events = self.events('请设置模特信息后再发布。')
        with self.assertRaisesRegex(ValueError, '模特信息'):
            validate_before_ai({'cid': 123}, events)
        validate_before_ai({'cid': 456}, events)
        validate_before_ai({'cid': 123, 'goodsModelReqs': [{'id': 1}]}, events)

    def test_old_unscoped_error_and_unknown_write_are_not_assumed_requirements(self):
        validate_before_ai({'cid': 123}, [{'step': 'save', 'status': 'needs_review',
                                         'message': '请设置模特信息后再发布。'}])
        validate_before_ai({'cid': 123}, self.events('HTTP 504 timeout'))

    def test_success_clears_old_requirement(self):
        validate_before_ai({'cid': 123}, self.events('【尺码表】不能为空') +
                          [{'step': 'save', 'status': 'done', 'message': '已保存'}])

    def test_incomplete_size_values_block_but_zero_is_preserved(self):
        for value in (None, '', ' '):
            with self.assertRaisesRegex(ValueError, '尺码表'):
                validate_before_ai({'sizeCharts': [{'sizeItems': [{'elementValueList': [{'elementValue': value}]}]}]})
        validate_before_ai({'sizeCharts': [{'sizeItems': [{'elementValueList': [{'elementValue': 0}]}]}]})


class AttributeEfficiencyTests(unittest.TestCase):
    def rule(self, choices):
        return {'pid': 1, 'templatePid': 1, 'name': '纹理', 'required': True,
                'valueType': 'SELECT', 'values': choices}

    def test_unique_allowed_option_uses_existing_default_without_ai(self):
        infer = Mock(side_effect=AssertionError('must not call AI'))
        item = {'attributes': []}
        complete_attributes(item, {}, [self.rule([{'vid': 1, 'name': '光面'}])], '类目', infer,
                            lambda _: None, allow_user_choice_defaults=True)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 1)
        infer.assert_not_called()

    def test_multiple_options_still_prefer_evidence_inference(self):
        infer = Mock(return_value={'uncertain': False, 'values': [{'vid': 2, 'name': '磨砂'}], 'evidence': '磨砂'})
        item = {'attributes': []}
        complete_attributes(item, {'title': '磨砂'}, [self.rule([{'vid': 1, 'name': '光面'}, {'vid': 2, 'name': '磨砂'}])],
                            '类目', infer, lambda _: None, allow_user_choice_defaults=True)
        self.assertEqual(item['attributes'][0]['values'][0]['vid'], 2)
        infer.assert_called_once()


if __name__ == '__main__':
    unittest.main()
