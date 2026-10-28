"""OpenAI Responses API adapter. Credentials only from server environment."""
import json
import os
import re
import urllib.error
import urllib.request
from providers import ModelError, NoRedirect, SCHEMA, SYSTEM

ENDPOINT = 'https://api.openai.com/v1/responses'
DEFAULT_MODEL = 'gpt-5.4-mini-2026-03-17'

class Selection(dict):
    def __init__(self, claims, metadata):
        super().__init__(claims)
        self.model_metadata = metadata

def status():
    configured = bool(os.environ.get('OPENAI_API_KEY', '').strip())
    model = os.environ.get('OPENAI_MODEL', DEFAULT_MODEL).strip()
    valid_model = bool(re.fullmatch(r'[A-Za-z0-9._:-]{1,120}', model))
    return {'available': configured and valid_model, 'configured': configured, 'connection_verified': False,
            'selected': {'name': model, 'provider': 'openai'} if valid_model else None,
            'message': ('OpenAI API key configured; connection and model access are not yet verified.' if configured else
                        'OPENAI_API_KEY is not configured. Add it in the server terminal, never in the browser or chat.') if valid_model else 'Invalid OPENAI_MODEL setting.',
            'transfer': 'Only canonical public topic/facets and reviewed evidence summaries are sent to OpenAI; never raw questions or chat history.'}

def build_payload(task, evidence, model):
    return {'model': model, 'store': False, 'stream': False, 'max_output_tokens': 1200,
            'instructions': SYSTEM,
            'input': [{'role': 'user', 'content': json.dumps({
                'public_task': task,
                'evidence': [{'source_id': c['parent'], 'chunk_id': c['id'], 'text': c['text'], 'conditions': c['conditions']} for c in evidence]
            })}],
            'text': {'format': {'type': 'json_schema', 'name': 'evidence_selection', 'strict': True, 'schema': SCHEMA}}}

def request(payload):
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key: raise ModelError('OPENAI_API_KEY is not configured on the server.')
    try:
        timeout = min(45, max(1, float(os.environ.get('OPENAI_TIMEOUT', '30'))))
        req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key})
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(262145)
        if len(raw) > 262144: raise ModelError('OpenAI response exceeds the size limit.')
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Do not parse/echo external error bodies, headers, credentials or request payloads.
        messages = {400:'OpenAI rejected the request or model configuration.',
                    401:'OpenAI authentication failed. Check the server API key.',
                    403:'OpenAI denied access. Check account and model permissions.',
                    404:'The configured OpenAI model or endpoint is unavailable.',
                    429:'OpenAI rate limit or quota reached. Check API billing and retry later.'}
        raise ModelError(messages.get(exc.code, 'OpenAI API returned an error. No answer produced.')) from None
    except ModelError: raise
    except Exception:
        raise ModelError('OpenAI connection failed, timed out, or returned invalid JSON. No automatic retry or fallback.') from None

def select(task, evidence, model):
    data = request(build_payload(task, evidence, model))
    try:
        if not isinstance(data, dict) or data.get('status') != 'completed':
            raise ModelError('OpenAI response was incomplete or failed; no answer produced.')
        texts = []
        for item in data.get('output', []):
            if item.get('type') != 'message': continue
            if item.get('role') != 'assistant' or item.get('status') != 'completed':
                raise ModelError('OpenAI returned an unexpected message state.')
            for part in item.get('content', []):
                if part.get('type') == 'refusal': raise ModelError('OpenAI declined this request.')
                if part.get('type') == 'output_text': texts.append(part['text'])
        if len(texts) != 1 or not isinstance(texts[0], str) or len(texts[0]) > 20000:
            raise ModelError('OpenAI did not return one bounded structured answer.')
        parsed = json.loads(texts[0])
        if not isinstance(parsed, dict): raise ModelError('OpenAI output must be a JSON object.')
        reported = data.get('model')
        if not isinstance(reported, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,120}', reported):
            raise ModelError('OpenAI response is missing valid model metadata.')
        usage = data.get('usage') or {}
        counts = {k: v for k, v in usage.items() if k in ('input_tokens','output_tokens','total_tokens') and isinstance(v,int) and not isinstance(v,bool) and v >= 0}
        return Selection(parsed, {'provider':'openai','reported_model':reported,'usage':counts})
    except ModelError: raise
    except (AttributeError,KeyError,TypeError,ValueError):
        raise ModelError('OpenAI output failed structured response parsing.') from None
