#!/usr/bin/env python3
"""Prompt for a key without echo, files or shell history, then launch the local app."""
import getpass
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import envfile
envfile.load()
if not os.environ.get('OPENAI_API_KEY','').strip():
    print('Text-answer API mode sends public topics and reviewed summaries. Voice input separately sends raw recordings to Deepgram STT only when you choose Send recording.')
    print('Enter your API key here in the terminal. It will not be echoed or saved to a file.')
    if not sys.stdin.isatty():raise SystemExit('Use an interactive terminal, or set OPENAI_API_KEY in the server environment.')
    try:key=getpass.getpass('OPENAI_API_KEY: ').strip()
    except (EOFError,KeyboardInterrupt):raise SystemExit('\nCancelled.')
    if not key:raise SystemExit('No key entered. Start.command still works in Evidence mode.')
    os.environ['OPENAI_API_KEY']=key
os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/start_local.py')]+sys.argv[1:])
