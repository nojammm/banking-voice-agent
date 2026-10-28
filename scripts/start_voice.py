#!/usr/bin/env python3
"""Deepgram STT + OpenAI answers + ElevenLabs TTS. Secrets only in process environment."""
import getpass
import os
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
args=sys.argv[1:]
# Keep the old explicit flag compatible; ElevenLabs is now the default provider.
if '--elevenlabs' in args:args.remove('--elevenlabs')
browser_voice='--browser-voice' in args
if browser_voice:args.remove('--browser-voice')

def require_key(name):
    if os.environ.get(name,'').strip():return
    if not sys.stdin.isatty():raise SystemExit('Configure '+name+' in the server environment or run setup in an interactive terminal.')
    try:key=getpass.getpass(name+' (hidden): ').strip()
    except (EOFError,KeyboardInterrupt):raise SystemExit('\nCancelled.')
    if not key:raise SystemExit('No key entered. Use scripts/start_local.py --voice for the no-key scripted demo.')
    os.environ[name]=key

print('Voice input: Send recording sends raw audio to Deepgram STT. Use synthetic details only.')
require_key('DEEPGRAM_API_KEY')
if not browser_voice:
    print('ElevenLabs receives only the checked agent reply. API quota and provider retention policies apply.')
    require_key('ELEVENLABS_API_KEY')
    if not os.environ.get('ELEVENLABS_VOICE_ID','').strip():
        if not sys.stdin.isatty():raise SystemExit('Configure ELEVENLABS_VOICE_ID in the server environment.')
        try:voice=input('ELEVENLABS_VOICE_ID (from your voice library): ').strip()
        except (EOFError,KeyboardInterrupt):raise SystemExit('\nCancelled.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',voice):raise SystemExit('Invalid voice ID.')
        os.environ['ELEVENLABS_VOICE_ID']=voice
else:
    print('Browser demo voice requested; select Browser voice in the simulator.')
print('OpenAI is used for text answers only, not speech recognition.')
os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/start_openai.py'),'--voice']+args)
