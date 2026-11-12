import unittest
from unittest.mock import patch

import server
import test_integration_settings as fixtures
from test_size_reference import sample, COLS, ANSWER


class SizeReferenceRouteTests(unittest.TestCase):
    request=fixtures.IntegrationSettingsTests.request
    stop_server=fixtures.IntegrationSettingsTests.stop_server
    setUp=fixtures.IntegrationSettingsTests.setUp

    def reference_body(self):
        return {'item':sample(),'chartIndex':0,'columns':COLS}

    def test_generation_is_preview_only_and_does_not_call_miaoshou(self):
        with patch.object(server.DeepSeekClient,'infer_size_reference',return_value=ANSWER) as ai:
            status,result=self.request('POST','/api/ai/size-reference',self.reference_body(),self.headers)
        self.assertEqual(status,200,result);self.assertEqual(result['data']['generated'],3)
        self.assertEqual(self.calls,[]);ai.assert_called_once()

    def test_origin_and_schema_validation_happen_before_paid_call(self):
        with patch.object(server.DeepSeekClient,'infer_size_reference') as ai:
            self.assertEqual(self.request('POST','/api/ai/size-reference',self.reference_body(),{})[0],403)
            body=self.reference_body();body['columns']=[]
            self.assertEqual(self.request('POST','/api/ai/size-reference',body,self.headers)[0],400)
            ai.assert_not_called()

    def test_local_template_save_conflict_and_account_isolation(self):
        body={**self.reference_body(),'shopId':9,'name':'参考模板','enabled':True,'revision':0,'confirmation':'SAVE_SIZE_REFERENCE_TEMPLATE'}
        status,result=self.request('POST','/api/size-reference/template',body,self.headers)
        self.assertEqual(status,200,result);self.assertEqual(result['data']['template']['revision'],1)
        self.assertEqual(self.request('POST','/api/size-reference/template',body,self.headers)[0],400)
        path='/api/size-reference/template?shopId=9&cid=123&source=erp&templateId=77'
        self.assertTrue(self.request('GET',path,headers=self.headers)[1]['data']['template']['enabled'])
        headers={**self.headers,'X-Miaoshou-Account-Id':'2'}
        self.assertIsNone(self.request('GET',path,headers=headers)[1]['data']['template'])
        self.assertEqual(self.calls,[])

    def test_complete_chart_skips_ai_and_failure_never_saves_to_miaoshou(self):
        from size_reference import merge_reference
        body=self.reference_body();body['item']['sizeCharts']=merge_reference(sample(),0,COLS,ANSWER)['sizeCharts']
        with patch.object(server.DeepSeekClient,'infer_size_reference') as ai:
            status,result=self.request('POST','/api/ai/size-reference',body,self.headers)
        self.assertEqual(status,200,result);self.assertEqual(result['data']['generated'],0);ai.assert_not_called()
        with patch.object(server.DeepSeekClient,'infer_size_reference',side_effect=RuntimeError('AI unavailable')):
            self.assertEqual(self.request('POST','/api/ai/size-reference',self.reference_body(),self.headers)[0],502)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
