import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from contracts import AccountingNarrative
from services.llm import ask

def response(text):
    return {'output':{'message':{'role':'assistant','content':[{'text':text}]}}}

class LLMTests(unittest.TestCase):
    def modules(self,client):
        boto=types.ModuleType('boto3');boto.client=MagicMock(return_value=client)
        botocore=types.ModuleType('botocore');config=types.ModuleType('botocore.config')
        config.Config=MagicMock()
        return {'boto3':boto,'botocore':botocore,'botocore.config':config}

    def test_json_repair_is_bounded_and_schema_validated(self):
        client=MagicMock()
        client.converse.side_effect=[response('not JSON'),response(json.dumps({'summary':'ok','review_questions':[]}))]
        with patch.dict(sys.modules,self.modules(client)),patch.dict(os.environ,{'AWS_REGION':'test','BEDROCK_MODEL_ID':'test'}):
            output=ask('Test role',{'known':'evidence'},AccountingNarrative)
        self.assertEqual(output['summary'],'ok')
        self.assertEqual(client.converse.call_count,2)

    def test_no_silent_simulation_on_invalid_model_output(self):
        client=MagicMock();client.converse.return_value=response('{"wrong":"schema"}')
        with patch.dict(sys.modules,self.modules(client)),patch.dict(os.environ,{'AWS_REGION':'test','BEDROCK_MODEL_ID':'test'}):
            with self.assertRaises(ValueError):ask('Test role',{},AccountingNarrative)
        self.assertEqual(client.converse.call_count,2)

    def test_model_service_error_propagates(self):
        client=MagicMock();client.converse.side_effect=RuntimeError('model access unavailable')
        with patch.dict(sys.modules,self.modules(client)),patch.dict(os.environ,{'AWS_REGION':'test','BEDROCK_MODEL_ID':'test'}):
            with self.assertRaises(RuntimeError):ask('Test role',{},AccountingNarrative)
        self.assertEqual(client.converse.call_count,1)

if __name__=='__main__':unittest.main()
