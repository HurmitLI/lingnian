import json

import httpx
import pytest
from openai import InternalServerError, OpenAI

from app.services.llm.provider import QwenLLMProvider


@pytest.mark.parametrize('status', [200, 500])
def test_paid_completion_has_output_ceiling_and_never_retries(status):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if status == 500:
            return httpx.Response(500, json={'error': {'message': 'temporary failure'}})
        return httpx.Response(200, json={
            'id': 'bounded-test', 'object': 'chat.completion', 'created': 0,
            'model': 'test-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '{}'}, 'finish_reason': 'stop'}],
        })

    provider = QwenLLMProvider.__new__(QwenLLMProvider)
    provider.model_name = 'test-model'
    provider.client = OpenAI(api_key='test-only', max_retries=2,
                             http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    try:
        if status == 500:
            with pytest.raises(InternalServerError):
                provider._complete('system', 'fictional input')
        else:
            assert provider._complete('system', 'fictional input') == '{}'
        assert len(requests) == 1
        assert requests[0]['max_tokens'] == 4096
        assert requests[0]['enable_thinking'] is False
    finally:
        provider.client.close()
