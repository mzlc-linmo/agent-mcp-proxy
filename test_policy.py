import unittest
from policy import compact, permitted

class PolicyTests(unittest.TestCase):
    def test_explicitly_authorized_actions_are_exported_but_paid_generation_is_not(self):
        self.assertTrue(permitted('ops-readonly', 'ops_decisions_first_page'))
        self.assertTrue(permitted('ops-ai', 'ops_custom_plan_status'))
        for domain, tool in [('ops-actions','ops_decision_adopt'), ('ops-actions','ops_decision_reject'), ('ops-collection','ops_collection_trigger')]:
            self.assertTrue(permitted(domain, tool))
        self.assertFalse(permitted('ops-ai','ops_custom_plan_generate'))

    def test_action_and_collection_receipts_are_bounded_and_not_fake_lists(self):
        import json
        action=json.loads(compact('ops_decision_adopt',{'code':0,'data':{'id':'12','status':'ADOPTED','remark':'private'}}))
        self.assertTrue(action['ok'])
        self.assertEqual(action['data']['id'],'12')
        self.assertNotIn('remark',action['data'])
        failed=json.loads(compact('ops_collection_trigger',{'code':0,'data':{'batchNo':'b1','status':'FAILED','response':'private'}}))
        self.assertFalse(failed['ok'])
        self.assertNotIn('private',json.dumps(failed))

    def test_result_is_valid_bounded_json_with_honest_truncation(self):
        import json
        value={'code':0,'data':{'records':[{'decisionNo':str(i),'title':'很长的中文标题'*90,'status':'ADOPTED','ownerName':'张维'} for i in range(20)],'total':100}}
        text=compact('ops_decisions_first_page', value)
        self.assertLessEqual(len(text.encode()),1024)
        result=json.loads(text)
        self.assertEqual(result['total'],100)
        self.assertTrue(result['truncated'])

    def test_field_shortening_is_explicit_even_if_all_rows_fit(self):
        import json
        value={'code':0,'data':{'records':[{'title':'长'*121}],'total':1}}
        result=json.loads(compact('ops_decisions_first_page',value))
        self.assertTrue(result['truncated'])

    def test_missing_ai_receipt_does_not_become_success(self):
        import json
        result=json.loads(compact('ops_custom_plan_status',{'code':0,'data':None}))
        self.assertFalse(result['ok'])

    def test_business_failure_is_not_an_empty_success(self):
        import json
        result=json.loads(compact('ops_decisions_first_page',{'code':1,'msg':'denied','data':None}))
        self.assertFalse(result['ok'])

if __name__=='__main__': unittest.main()
