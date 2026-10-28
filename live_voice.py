"""Local-only LiveKit session credentials. Provider secrets never reach the browser."""
import asyncio
import importlib.util
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta

AGENT_NAME = os.environ.get('LIVEKIT_AGENT_NAME', 'banking-evidence-local')
EVENTS_TOPIC = 'banking.events'  # Backend -> agent only; the browser token cannot publish data.
REQUIRED = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET',
            'DEEPGRAM_API_KEY', 'OPENAI_API_KEY', 'ELEVENLABS_API_KEY', 'ELEVENLABS_VOICE_ID')
_lock = threading.Lock()
_last_token = 0.0

def cloud_url():
    value = os.environ.get('LIVEKIT_URL', '').strip()
    # A project URL, never an arbitrary browser-supplied destination or CSP fragment.
    return value if re.fullmatch(r'wss://[a-zA-Z0-9-]+\.livekit\.cloud', value) else ''

def status():
    missing = [name for name in REQUIRED if not os.environ.get(name, '').strip()]
    sdk = importlib.util.find_spec('livekit') is not None
    running = os.environ.get('LIVEKIT_LOCAL_AGENT_RUNNING') == '1'
    return {'configured': not missing and bool(cloud_url()) and sdk and running,
            'missing': missing, 'sdk_available': sdk, 'worker_started': running,
            'turn_detector': 'LiveKit Audio Turn Detector v1 (cloud)',
            'message': 'Configuration only; provider connections are verified when you start a live session.'}

def issue_token(body):
    global _last_token
    if body != {}: raise ValueError('Live sessions accept no client configuration.')
    if not status()['configured']:
        raise ValueError('Start scripts/start_live.py and configure LiveKit, Deepgram, OpenAI and ElevenLabs first.')
    from livekit import api
    with _lock:
        now = time.monotonic()
        if now - _last_token < 5: raise ValueError('Please wait five seconds before starting another session.')
        _last_token = now
    room = 'evidence-' + secrets.token_hex(12)
    identity = 'customer-' + secrets.token_hex(8)
    token = (api.AccessToken(os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET'])
             .with_identity(identity).with_ttl(timedelta(minutes=2))
             .with_grants(api.VideoGrants(room_join=True, room=room, can_subscribe=True,
                          can_publish=True, can_publish_data=False, can_publish_sources=['microphone']))
             .with_room_config(api.RoomConfiguration(name=room, empty_timeout=60,
                    departure_timeout=20, max_participants=2,
                    agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]))
             .to_jwt())
    return {'url': cloud_url(), 'token': token, 'max_session_seconds': 600}

def notify_room(room, payload, timeout=5):
    """Deliver a backend-confirmed event to the agent in one call's room. Returns True when LiveKit accepted it."""
    if not status()['configured'] or not re.fullmatch(r'evidence-[0-9a-f]{24}', room or ''): return False
    from livekit import api

    async def send():
        lk = api.LiveKitAPI(cloud_url().replace('wss://', 'https://'), os.environ['LIVEKIT_API_KEY'], os.environ['LIVEKIT_API_SECRET'])
        try:
            await lk.room.send_data(api.SendDataRequest(room=room, data=json.dumps(payload).encode(), topic=EVENTS_TOPIC,
                                                        kind=api.DataPacket.Kind.RELIABLE))
        finally:
            await lk.aclose()
    try:
        asyncio.run(asyncio.wait_for(send(), timeout))
        return True
    except Exception:
        return False
