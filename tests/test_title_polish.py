import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from server import ComfyUIClient, DeepSeekClient, LocalImageJobStore, OpenAIImageClient


class TitlePolishTests(unittest.TestCase):
    def test_weighted_length_counts_chinese_as_two(self):
        self.assertEqual(DeepSeekClient.weighted_length("手套ABC 9"), 9)
        self.assertEqual(DeepSeekClient.weighted_length("Winter gloves"), 13)

    def test_ai_result_stays_within_weighted_limit(self):
        client = DeepSeekClient()
        client.api_key = "test-key"
        responses = iter(["手" * 130, "手" * 117])
        client._completion = lambda _messages: next(responses)

        title = client.polish_title("冬季手套", "zh")

        self.assertEqual(DeepSeekClient.weighted_length(title), 234)


    def test_short_truthful_title_is_not_rejected_by_a_hard_minimum(self):
        client = DeepSeekClient(); client.api_key = 'test-key'
        client._completion = MagicMock(return_value='保暖手套，适合冬季户外使用')
        result = client.polish_title('保暖手套', 'zh')
        self.assertEqual(result, '保暖手套，适合冬季户外使用')
        self.assertEqual(client._completion.call_count, 3)
        self.assertLess(client.weighted_length(result), 220)

    def test_repeated_249_weight_drafts_are_fitted_instead_of_pausing(self):
        client = DeepSeekClient(); client.api_key = 'test-key'
        draft = '手' * 124 + 'a'
        self.assertEqual(client.weighted_length(draft), 249)
        client._completion = MagicMock(return_value=draft)
        result = client.polish_title('手套', 'zh')
        self.assertTrue(result)
        self.assertLessEqual(client.weighted_length(result), 240)
        self.assertEqual(client._completion.call_count, 3)

    def test_v150_target_repairs_225_and_uses_original_default_prompt(self):
        client = DeepSeekClient()
        client.api_key = 'test-key'
        client._completion = MagicMock(side_effect=['A' * 225, 'B' * 234])
        result = client.polish_title('Outdoor gloves', 'en')
        self.assertEqual(result, 'B' * 234)
        self.assertEqual(client._completion.call_count, 2)
        prompt = client._completion.call_args_list[0].args[0][0]['content']
        self.assertIn('230-240字符', prompt)
        self.assertNotIn('220-240', prompt)

    def test_oversized_draft_is_trimmed_by_weight(self):
        self.assertEqual(DeepSeekClient.weighted_length(DeepSeekClient.fit_limit("手" * 130)), 240)

    def test_deepseek_connection_failure_retries_once_before_succeeding(self):
        client = DeepSeekClient()
        client.api_key = 'test-key'
        response = MagicMock()
        response.read.return_value = json.dumps({'choices': [{'message': {'content': '标题'}}]}).encode()
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.side_effect = [URLError('temporary network error'), response]
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key', 'DEEPSEEK_DISABLED': '0'}), \
             patch('server.build_opener', return_value=opener), patch('server.time.sleep') as sleep:
            self.assertEqual(client._completion([{'role': 'user', 'content': 'test'}]), '标题')
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(0.75)

    def test_image_editor_rejects_non_https_source(self):
        with self.assertRaises(ValueError):
            OpenAIImageClient._validate_source_url("http://example.com/product.png")

    def test_image_editor_multipart_includes_image_and_prompt(self):
        body, boundary = OpenAIImageClient._multipart([
            ("prompt", "replace background", None),
            ("image", b"image-data", "image/png"),
        ])
        self.assertIn(boundary.encode(), body)
        self.assertIn(b"replace background", body)
        self.assertIn(b"image-data", body)

    def test_local_qwen_workflow_uses_uploaded_source_and_instruction(self):
        workflow = ComfyUIClient._workflow("local-product.png", "替换为纯白背景")

        self.assertEqual(workflow["1"]["inputs"]["image"], "local-product.png")
        self.assertIn("严格图生图编辑", workflow["5"]["inputs"]["prompt"])
        self.assertIn("<image1>", workflow["5"]["inputs"]["prompt"])
        self.assertTrue(workflow["5"]["inputs"]["prompt"].endswith("替换为纯白背景"))
        self.assertEqual(workflow["5"]["class_type"], "TextEncodeQwenImage21")
        self.assertEqual(workflow["5"]["inputs"]["images.image_1"], ["1", 0])
        self.assertEqual(workflow["5"]["inputs"]["resolution"], 992)
        self.assertEqual(workflow["6"]["class_type"], "QwenImage21Cache")
        self.assertEqual(workflow["9"]["class_type"], "SaveImage")

    def test_local_comfyui_endpoint_must_be_loopback(self):
        client = ComfyUIClient()
        client.base_url = "https://comfy.example.com"

        with self.assertRaises(RuntimeError):
            client._base()

    def test_local_job_status_never_exposes_source_or_instruction(self):
        job_id = LocalImageJobStore.create()
        LocalImageJobStore.update(job_id, "正在上传原图")

        status = LocalImageJobStore.public(job_id)

        self.assertEqual(status["status"], "running")
        self.assertIn("正在上传原图", status["events"])
        self.assertNotIn("sourceUrl", status)
        self.assertNotIn("instruction", status)


if __name__ == "__main__":
    unittest.main()
