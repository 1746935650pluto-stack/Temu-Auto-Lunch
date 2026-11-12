from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from size_reference import (SizeReferenceTemplates, chart_key, fill_bound_references, generate_reference,
                            infer_reference, merge_reference, plan_reference)


def sample():
    return {'cid': '123', 'title': '男士短裤', 'goodsModelReqs': [],
            'saleAttributes': [{'refPid': 65, 'values': [{'name': 'S'}, {'name': 'M'}]}],
            'sizeCharts': [{'sizeTemplateSource': 'erp', 'sizeTemplateId': '77', 'elementIds': [1, 2],
                            'manualEntryIds': [], 'sizeTemplateUniqueId': 'opaque-existing-id',
                            'sizeItems': [{'sizeName': 'S', 'elementValueList': [
                                {'elementId': 1, 'elementValue': '56', 'retain': True},
                                {'elementId': 2, 'elementValue': ''}], 'groupValueList': [], 'groupIdAndValueMap': None}]}]}


COLS = [{'elementId': 1, 'name': '腰围全围', 'unit': 'cm'}, {'elementId': 2, 'name': '裤长', 'unit': 'cm'}]
ANSWER = {'values': [{'sizeName': 'S', 'elementId': 2, 'value': '42'},
                     {'sizeName': 'M', 'elementId': 1, 'value': '60'},
                     {'sizeName': 'M', 'elementId': 2, 'value': '43'}]}


class SizeReferenceTests(unittest.TestCase):
    def test_only_missing_cells_requested_existing_zero_and_metadata_kept(self):
        item = sample(); original = deepcopy(item); infer = Mock(return_value=ANSWER)
        result = generate_reference(item, 0, COLS, infer)
        self.assertEqual(item, original)
        self.assertEqual(result['generated'], 3)
        plan = infer.call_args.args[0]
        self.assertEqual(plan['missing'], [{k: v for k, v in r.items() if k != 'value'} for r in ANSWER['values']])
        chart = result['sizeCharts'][0]
        self.assertEqual(chart['sizeTemplateId'], '77')
        self.assertEqual(chart['sizeTemplateUniqueId'], 'opaque-existing-id')
        self.assertEqual(chart['sizeItems'][0]['elementValueList'][0], original['sizeCharts'][0]['sizeItems'][0]['elementValueList'][0])
        self.assertIn('非实测', result['source'])
        item['sizeCharts'] = result['sizeCharts']
        infer.reset_mock()
        result = generate_reference(item, 0, COLS, infer)
        self.assertEqual(result['generated'], 0); infer.assert_not_called()
        item['sizeCharts'][0]['sizeItems'][0]['elementValueList'][0]['elementValue'] = 0
        self.assertEqual(generate_reference(item, 0, COLS, infer)['generated'], 0)

    def test_no_entire_template_or_sku_size_is_invented(self):
        for mutate in [lambda i: i.update(sizeCharts=[]),
                       lambda i: i['sizeCharts'][0].update(sizeTemplateId='made-up'),
                       lambda i: i['sizeCharts'][0].update(elementIds=[]),
                       lambda i: (i.update(saleAttributes=[]), i['sizeCharts'][0].update(sizeItems=[]))]:
            item = sample();mutate(item);infer = Mock()
            with self.assertRaises(ValueError): generate_reference(item, 0, COLS, infer)
            infer.assert_not_called()

    def test_reject_bad_ai_response_atomically(self):
        bad = [None, [], {}, {'values': []}, {'values': ANSWER['values'] + [ANSWER['values'][0]]}]
        for value in ['', 'NaN', 'Infinity', '-1', '0', '1001', '1.234', True, {}, '12cm']:
            answer = deepcopy(ANSWER);answer['values'][0]['value'] = value;bad.append(answer)
        for field, value in [('elementId', 9), ('elementId', True), ('sizeName', 'XL')]:
            answer = deepcopy(ANSWER);answer['values'][0][field] = value;bad.append(answer)
        answer = deepcopy(ANSWER);answer['values'][0] = {'sizeName': 'S', 'elementId': 1, 'value': '999'};bad.append(answer)
        answer = deepcopy(ANSWER);answer['values'][0] = deepcopy(answer['values'][1]);bad.append(answer)
        for answer in bad:
            with self.subTest(answer=answer):
                item=sample();before=deepcopy(item)
                with self.assertRaises(ValueError): merge_reference(item, 0, COLS, answer)
                self.assertEqual(item,before)

    def test_grouped_or_duplicate_rows_and_wrong_labels_are_rejected(self):
        item=sample();item['sizeCharts'][0]['sizeItems'][0]['groupIdAndValueMap']={'1':'2'}
        with self.assertRaisesRegex(ValueError,'分组'):plan_reference(item,0,COLS)
        item=sample();item['sizeCharts'][0]['sizeItems']*=2
        with self.assertRaisesRegex(ValueError,'重复'):plan_reference(item,0,COLS)
        for cols in [[], COLS[::-1], [{'elementId':1,'name':'','unit':'cm'},COLS[1]],
                     [{**COLS[0],'unit':'inch'},COLS[1]]]:
            with self.assertRaises(ValueError):plan_reference(sample(),0,cols)

    def test_prompt_uses_authorized_reference_policy_without_model_or_id_generation(self):
        complete=Mock(return_value=json.dumps(ANSWER));context=plan_reference(sample(),0,COLS)
        self.assertEqual(infer_reference(complete,context),ANSWER)
        self.assertEqual(complete.call_args.kwargs,{'structured':True,'purpose':'size_reference'})
        prompt=complete.call_args.args[0][0]['content']
        self.assertIn('通用参考',prompt);self.assertIn('非实测',prompt);self.assertIn('只补缺失',prompt)
        self.assertIn('腰围全围不能当成平铺半围',prompt)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=SizeReferenceTemplates(Path(temp.name)/'size_reference_templates.sqlite3')

    def save(self, enabled=True, revision=0):
        return self.store.save('scope-one',9,sample(),0,COLS,'短裤参考',enabled,revision)

    def test_local_store_revision_and_scope_shop_category_template_isolation(self):
        self.assertIsNone(self.store.get('scope-one',9,chart_key(sample(),0)))
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.save()['revision'],1)
        with self.assertRaisesRegex(ValueError,'其他页面'):self.save()
        self.assertEqual(self.save(False,1)['revision'],2)
        key=chart_key(sample(),0)
        for scope,shop,k in [('scope-two',9,key),('scope-one',10,key),('scope-one',9,('124',*key[1:])),
                             ('scope-one',9,(*key[:2],'78'))]:
            self.assertIsNone(self.store.get(scope,shop,k))

    def test_automatic_fill_only_for_enabled_exact_binding_and_logs_reference_source(self):
        infer=Mock(return_value=ANSWER);log=Mock();item=sample()
        self.assertEqual(fill_bound_references(item,self.store,'scope-one',9,infer,log),0)
        infer.assert_not_called()
        self.save(False)
        self.assertEqual(fill_bound_references(item,self.store,'scope-one',9,infer,log),0)
        self.save(True,1)
        self.assertEqual(fill_bound_references(item,self.store,'scope-one',9,infer,log),3)
        self.assertIn('非实测',log.call_args.args[0]);self.assertEqual(item['goodsModelReqs'],[])
        self.assertEqual(fill_bound_references(item,self.store,'scope-one',9,infer,log),0)
        infer.assert_called_once()


if __name__=='__main__':unittest.main()
