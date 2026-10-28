#!/usr/bin/env python3
"""Start the local browser app and its LiveKit worker; secrets stay in process memory."""
import argparse
import getpass
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / '.venv-livekit/bin/python'
if Path(sys.prefix).resolve() != (ROOT / '.venv-livekit').resolve():
    if not VENV.exists():
        raise SystemExit('First install requirements-livekit.txt into .venv-livekit with Python 3.10+. See README.md.')
    os.execv(str(VENV), [str(VENV), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(ROOT))
import envfile
import live_voice

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8766)
parser.add_argument('--no-browser', action='store_true')
args = parser.parse_args()
with socket.socket() as probe:
    try: probe.bind(('127.0.0.1', args.port))
    except OSError: raise SystemExit(f'Port {args.port} is occupied. Choose another --port.')
print('Live microphone audio streams through LiveKit to Deepgram and LiveKit audio turn detection.')
print('OpenAI receives the redacted conversation and tool results (identity answers are withheld); ElevenLabs speaks the checked reply.')
if (loaded := envfile.load()): print('Loaded from .env: ' + ', '.join(loaded))
for name in live_voice.REQUIRED:
    if os.environ.get(name, '').strip(): continue
    if not sys.stdin.isatty(): raise SystemExit('Missing '+name+'. Add it to .env, or run in an interactive terminal.')
    try:
        value = (input(name+': ') if name in ('LIVEKIT_URL', 'ELEVENLABS_VOICE_ID')
                 else getpass.getpass(name+' (hidden): ')).strip()
    except (EOFError, KeyboardInterrupt): raise SystemExit('\nCancelled.')
    if not value: raise SystemExit('No value entered for '+name)
    os.environ[name] = value
if not live_voice.cloud_url(): raise SystemExit('LIVEKIT_URL must be wss://YOUR-PROJECT.livekit.cloud')
os.environ['LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE'] = '0'
children = []
# Treat a terminate signal like Ctrl+C so the worker and server below are always stopped.
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
try:
    worker = subprocess.Popen([sys.executable, str(ROOT/'live_agent.py'), 'start'], cwd=ROOT, start_new_session=True)
    children.append(worker)
    os.environ['LIVEKIT_LOCAL_AGENT_RUNNING'] = '1'
    http = subprocess.Popen([sys.executable, str(ROOT/'server.py'), '--port', str(args.port)], cwd=ROOT, start_new_session=True)
    children.append(http)
    url = f'http://127.0.0.1:{args.port}/#voice'
    for _ in range(50):
        if any(p.poll() is not None for p in children): raise SystemExit('A local process exited; check configuration and connection.')
        try:
            with urllib.request.urlopen(url.split('#')[0], timeout=1) as response:
                if response.status == 200: break
        except OSError: time.sleep(.2)
    else: raise SystemExit('Local server did not become ready.')
    print(url+' — choose Start live conversation. Ctrl+C stops both local processes.', flush=True)
    if not args.no_browser: webbrowser.open(url)
    while all(p.poll() is None for p in children): time.sleep(.5)
    raise SystemExit('A local process stopped. Restart this command to reconnect.')
except KeyboardInterrupt:
    print('\nStopping this launcher’s local server and agent…')
finally:
    for process in reversed(children):
        # Each child owns its own process group; never touch an existing user server.
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: continue
        try: process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
