#!/usr/bin/env python3
"""Start the localhost web app only. Never start or download a local model."""
import argparse
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
import urllib.request
import webbrowser
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import providers

parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');parser.add_argument('--voice',action='store_true');parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
children=[]
try:
    import openai_provider
    print(openai_provider.status()['message'],flush=True)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:raise SystemExit(f'Port {args.port} is already in use. Stop the old server or choose --port {args.port+1}.')
    child=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(args.port)],cwd=ROOT)
    children.append(child)
    url='http://127.0.0.1:'+str(args.port)
    for _ in range(30):
        if child.poll() is not None:raise SystemExit('Server did not start; try another --port.')
        try:
            with urllib.request.urlopen(url,timeout=1) as response:
                if response.status==200:break
        except OSError:time.sleep(.2)
    else:raise SystemExit('Local server was not ready.')
    if not args.no_browser:webbrowser.open(url+('#voice' if args.voice else ''))
    print('Keep this terminal open. Press Ctrl+C to stop the processes started here.',flush=True)
    child.wait()
except KeyboardInterrupt:print('\nStopping local prototype…',flush=True)
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
