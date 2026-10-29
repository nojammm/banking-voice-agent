#!/usr/bin/env python3
"""Loopback-only HTTP server: the app, its demo banking backend and the demonstrator inspector."""
import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import bank
import engine
import kb
import providers
import openai_provider
import speech
import live_voice
import base64
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
MODEL_GATE = threading.Lock()
EVAL_LOCK = threading.Lock()
EVAL_STATE = {'state': 'idle', 'result': None}

class Handler(BaseHTTPRequestHandler):
    server_version = 'EvidencePrototype/1'
    def log_message(self, *args): pass
    def setup(self):
        super().setup()
        self.connection.settimeout(8)
    def safe_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
    def send(self, code, data, content_type='application/json'):
        payload = json.dumps(data, ensure_ascii=False).encode() if content_type == 'application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        live_connect = " wss://*.livekit.cloud https://*.livekit.cloud" if live_voice.cloud_url() else ''
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'" + live_connect + "; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try: self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError): pass
    def do_GET(self):
        if not self.safe_host(): return self.send(403, {'error': 'Invalid host.'})
        if self.path == '/api/status':
            return self.send(200, {'csrf_token': TOKEN, 'provider': openai_provider.status(), 'speech': speech.status(), 'live_voice': live_voice.status(), 'date': engine.today(),
                'corpus_hash': engine.canonical_hash(engine.load_corpus()), 'prompt_version': providers.PROMPT_VERSION})
        if self.path == '/api/knowledge':
            return self.send(200, {'files': kb.files()})
        if self.path == '/api/evaluation':
            with EVAL_LOCK: state = dict(EVAL_STATE)
            return self.send(200, state)
        # Demonstrator inspector: local only, never reachable from the voice session.
        if self.path == '/api/demo/overview':
            with closing(bank.connect()) as conn: return self.send(200, bank.overview(conn))
        if self.path.startswith('/api/demo/session'):
            after = self.path.partition('after=')[2]
            with closing(bank.connect()) as conn:
                return self.send(200, {'session': bank.latest_session(conn, int(after) if after.isdigit() else 0)})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'application/javascript; charset=utf-8'), '/voice.js': ('voice.js', 'application/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        files.update({'/live-voice.js': ('live-voice.js', 'application/javascript; charset=utf-8'),
                      '/inspector.js': ('inspector.js', 'application/javascript; charset=utf-8'),
                      '/livekit-client.js': ('vendor/livekit-client.js', 'application/javascript; charset=utf-8')})
        if self.path not in files: return self.send(404, {'error': 'Not found.'})
        name, mime = files[self.path]
        return self.send(200, (ROOT/'static'/name).read_bytes(), mime)
    def do_POST(self):
        if not self.safe_host(): return self.send(403, {'error': 'Invalid host.'})
        origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
        if self.headers.get('Origin') not in origins or self.headers.get('X-CSRF-Token') != TOKEN:
            return self.send(403, {'error': 'Same-origin request and session token required.'})
        if self.headers.get('Content-Type') != 'application/json': return self.send(415, {'error': 'JSON required.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = speech.MAX_AUDIO_JSON if self.path == '/api/voice-chat' else 10000
            if size < 1 or size > limit: return self.send(413, {'error': 'Request too large or empty.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict): raise ValueError()
        except (ValueError, OSError): return self.send(400, {'error': 'Invalid JSON request.'})
        if self.path == '/api/live-token':
            try: return self.send(200, live_voice.issue_token(body))
            except ValueError as exc: return self.send(400, {'error': str(exc)})
            except Exception: return self.send(503, {'error': 'Live voice could not start. Use scripts/start_live.py.'})
        if self.path in ('/api/forms/submit', '/api/forms/close', '/api/forms/typing'):
            with closing(bank.connect()) as conn: return self.send(*form_action(conn, self.path, body))
        if self.path == '/api/demo/reset':
            if body != {'confirm': True}: return self.send(400, {'error': 'Confirmation required.'})
            with closing(bank.connect()) as conn: bank.reset(conn)
            return self.send(200, {'reset': True})
        if self.path == '/api/chat':
            if set(body) - {'question', 'mode', 'previous', 'fixture'}: return self.send(400, {'error': 'Unknown request field.'})
            if not MODEL_GATE.acquire(False): return self.send(429, {'error': 'A request or evaluation is already running.'})
            try:
                result = speech.attach_playback(engine.answer(**body))
                return self.send(200, result)
            except (ValueError, TypeError): return self.send(400, {'error': 'Invalid question or options.'})
            except Exception: return self.send(500, {'error': 'Internal failure. No answer produced.'})
            finally: MODEL_GATE.release()
        if self.path in ('/api/voice-chat', '/api/speak'):
            if not MODEL_GATE.acquire(False): return self.send(429, {'error': 'A request or evaluation is already running.'})
            try:
                if self.path == '/api/voice-chat':
                    result = speech.attach_playback(speech.answer_audio(body))
                    return self.send(200, result)
                if set(body) != {'playback_token'}: raise ValueError('Only a server-issued playback token is accepted.')
                started = time.monotonic()
                audio, cached = speech.eleven_audio(body['playback_token'])
                return self.send(200, {'audio': base64.b64encode(audio).decode(), 'mime': 'audio/mpeg',
                    'provider': 'ElevenLabs', 'cached': cached, 'tts_ms': round((time.monotonic()-started)*1000,1)})
            except (ValueError, TypeError) as exc: return self.send(400, {'error': str(exc) if isinstance(exc, ValueError) else 'Invalid voice request.'})
            except providers.ModelError as exc: return self.send(502, {'error': str(exc)})
            except Exception: return self.send(500, {'error': 'Voice processing failed. No automatic fallback.'})
            finally: MODEL_GATE.release()
        if self.path == '/api/evaluate':
            if set(body) != {'mode'} or body['mode'] not in ('evidence', 'ollama', 'openai'): return self.send(400, {'error': 'Invalid evaluation mode.'})
            if not MODEL_GATE.acquire(False): return self.send(429, {'error': 'Another request is running.'})
            with EVAL_LOCK: EVAL_STATE.update(state='running', result=None)
            def work():
                try:
                    import evaluation
                    report = evaluation.run(body['mode'])
                    with EVAL_LOCK: EVAL_STATE.update(state='complete', result=report)
                except Exception:
                    with EVAL_LOCK: EVAL_STATE.update(state='error', result={'error': 'Evaluation failed. Run CLI for diagnostics.'})
                finally: MODEL_GATE.release()
            threading.Thread(target=work, daemon=True).start()
            return self.send(202, {'state': 'running'})
        return self.send(404, {'error': 'Not found.'})

def form_action(conn, path, body):
    """Validate and save in the backend first, then tell the agent what actually happened."""
    if path == '/api/forms/typing':  # The caller started typing their customer number: the agent shouldn't talk over them.
        record = bank.form(conn, body.get('form_id'))
        if set(body) != {'form_id'} or not record or record['kind'] != 'customer_number' or record['status'] != 'open':
            return 400, {'error': 'Invalid request.'}
        return 200, {'agent_notified': live_voice.notify_room(record['session_id'], {'type': 'form_event', 'form_id': record['id'], 'status': 'typing'})}
    if path == '/api/forms/close':
        if set(body) != {'form_id', 'reason'} or body['reason'] not in ('cancelled', 'closed'): return 400, {'error': 'Invalid request.'}
        record = bank.close_form(conn, body['form_id'], body['reason'])
        if not record: return 404, {'error': 'This form is no longer open.'}
        event = {'type': 'form_event', 'form_id': record['id'], 'status': body['reason']}
        return 200, {'closed': True, 'agent_notified': deliver(conn, record['session_id'], event)}
    number_box = (bank.form(conn, body.get('form_id')) or {}).get('kind') == 'customer_number'
    if set(body) - ({'form_id', 'customer_number'} if number_box else {'form_id', 'name', 'phone', 'consent'}):
        return 400, {'error': 'Unknown request field.'}
    if number_box:
        record, number, errors = bank.submit_customer_number(conn, body['form_id'], body)
        if errors:
            notified = record['status'] == 'open' and deliver(conn, record['session_id'], {'type': 'form_event', 'form_id': record['id'], 'status': 'error', 'fields': sorted(errors)})
            return 400, {'errors': errors, 'agent_notified': notified}
        event = {'type': 'form_event', 'form_id': record['id'], 'status': 'submitted', 'customer_number': number}
        return 200, {'submitted': True, 'agent_notified': deliver(conn, record['session_id'], event)}
    record, ref, errors = bank.submit_callback(conn, body.get('form_id'), body)
    if record is None: return 404, {'errors': errors}
    if errors:
        notified = record['status'] == 'open' and deliver(conn, record['session_id'], {'type': 'form_event', 'form_id': record['id'], 'status': 'error', 'fields': sorted(errors)})
        return 400, {'errors': errors, 'agent_notified': notified}
    event = {'type': 'form_event', 'form_id': record['id'], 'status': 'submitted', 'reference': ref}
    return 200, {'reference': ref, 'agent_notified': deliver(conn, record['session_id'], event)}


def deliver(conn, session_id, event):
    delivered = live_voice.notify_room(session_id, event)
    bank.log(conn, session_id, 'form_event_sent', {**event, 'delivered': delivered})
    return delivered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Banking Voice Assistant: http://127.0.0.1:{server.server_port}', flush=True)
    print('Independent localhost prototype. OpenAI API is used only when explicitly selected. Ctrl+C to stop.', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__ == '__main__': main()
