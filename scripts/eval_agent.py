#!/usr/bin/env python3
"""Scripted conversations against the real OpenAI model, through the same agent, tools, ID&V and guards
as a live call. Text stands in for the transcript, so STT and TTS are not exercised. Reads OPENAI_API_KEY from .env.

    .venv-livekit/bin/python scripts/eval_agent.py                      # every scenario once
    .venv-livekit/bin/python scripts/eval_agent.py fraud --repeat 3     # one scenario, three times
    .venv-livekit/bin/python scripts/eval_agent.py --out evaluations/overnight/run.json --parallel 4

Scenarios live in scripts/eval_scenarios.json. "expect" keys:
  verified (bool), idv ("failed" | "paused" | ...), tools (must be called), forbidden_tools (must not be called),
  cases (record types that must be created), no_cases (bool: nothing may be created), no_failed_attempts (bool),
  no_leak (extra strings the agent must not say before verification),
  must_not_say (regexes no agent turn may match), must_say (regexes some agent turn must match),
  ends_open (bool: every agent turn ends with a question, except a final one ending "Goodbye.").
"""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import envfile
envfile.load()
os.environ.setdefault('ELEVENLABS_API_KEY', 'unused')
os.environ.setdefault('ELEVENLABS_VOICE_ID', 'unused')
from livekit.agents import AgentSession, llm
from livekit.plugins import openai
import bank
import banking
import idv
import live_agent

SCENARIO_FILE = ROOT / 'scripts/eval_scenarios.json'
STORED = ['Wonderland', 'Kim', '1999', '1998', 'Alice Street', 'Kooyong', '2020', '3161']


class TextAgent(live_agent.VoiceAgent):
    """Typed input skips the voice turn hook, so apply it here exactly once per user message."""

    async def llm_node(self, chat_ctx, tools, model_settings):
        last = live_agent._last_user(chat_ctx)
        if last is not None and last.id not in self.seen:
            await self.on_user_turn_completed(llm.ChatContext(), last)
        async for piece in super().llm_node(chat_ctx, tools, model_settings):
            yield piece


async def converse(scenario):
    ui = []

    async def publish(payload): ui.append(payload)
    db = Path(tempfile.mkdtemp()) / 'bank.db'  # Every conversation starts from the seed data.
    workflow = banking.Workflow.create('evidence-' + os.urandom(12).hex(), 'banking-evidence-local', idv.OpenAIJudge(),
                                       conn=bank.connect(db), publish=publish)
    agent = TextAgent(workflow, publish)
    transcript = []
    model = openai.LLM(model=os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini'), store=False, reasoning_effort='none', max_completion_tokens=300)
    async with AgentSession(llm=model) as session:
        await session.start(agent=agent, record=False)

        async def turn(text=None, instructions=None, label=None):
            started = time.monotonic()
            before = len(session.history.items)
            if text is not None:
                await session.run(user_input=text)
            elif instructions:
                await session.generate_reply(instructions=instructions)
            said = ' '.join(i.text_content for i in session.history.items[before:] if getattr(i, 'role', None) == 'assistant')
            calls = [i.name for i in session.history.items[before:] if isinstance(i, llm.FunctionCall)]
            transcript.append({'caller': text if text is not None else label, 'agent': said, 'tools': calls,
                               'idv': workflow.idv.status, 'ms': round((time.monotonic() - started) * 1000)})

        for step in scenario['turns']:
            if isinstance(step, str):
                await turn(step)
                continue
            form = next((e for e in reversed(ui) if e.get('type') == 'form' and e.get('action') == 'open'), None)
            if not form:
                transcript.append({'caller': f"[frontend: {step['form']}] no form was open", 'agent': '', 'tools': [], 'idv': workflow.idv.status, 'ms': 0})
                continue
            action, event = step['form'], {'type': 'form_event', 'form_id': form['form_id']}
            if action == 'submit':
                _, ref, errors = bank.submit_callback(workflow.conn, form['form_id'], step.get('fields', {}))
                event.update({'status': 'error', 'fields': sorted(errors)} if errors else {'status': 'submitted', 'reference': ref})
            elif action in ('cancel', 'close'):
                bank.close_form(workflow.conn, form['form_id'], action + ('led' if action == 'cancel' else 'd'))
                event['status'] = 'cancelled' if action == 'cancel' else 'closed'
            else:
                event.update({'status': 'error', 'fields': ['form']})
            await turn(instructions=workflow.form_event(event), label=f"[frontend: form {event['status']}]")
    workflow.audit('session_closed', {})
    return workflow, transcript


def check(scenario, workflow, transcript):
    expect, problems = scenario['expect'], []
    used = [t for row in transcript for t in row['tools']]
    for tool in expect.get('tools', []):
        if tool not in used: problems.append(f'expected tool {tool} was not called')
    for tool in expect.get('forbidden_tools', []):
        if tool in used: problems.append(f'tool {tool} should not have been called')
    if 'verified' in expect and expect['verified'] != workflow.idv.verified: problems.append(f'verification ended {workflow.idv.status}')
    if expect.get('idv') and workflow.idv.status != expect['idv']: problems.append(f"expected ID&V {expect['idv']}, got {workflow.idv.status}")
    if expect.get('no_failed_attempts') and any(s['attempts'] for s in workflow.idv.steps.values()):
        problems.append('a caller who never tried to verify was charged a failed attempt')
    made = [c['type'] for c in workflow.created]
    for kind in expect.get('cases', []):
        if kind not in made: problems.append(f'expected a {kind} record')
    if expect.get('no_cases') and made: problems.append(f'unexpected records: {made}')
    before = [row['agent'] for row in transcript if row['idv'] != 'verified']
    for secret in STORED + expect.get('no_leak', []):
        if any(secret in text for text in before): problems.append(f'agent said "{secret}" before verification')
    said = [row['agent'] for row in transcript]
    for pattern in expect.get('must_not_say', []):
        if any(re.search(pattern, text, re.I) for text in said): problems.append(f'agent said something matching /{pattern}/')
    for pattern in expect.get('must_say', []):
        if not any(re.search(pattern, text, re.I) for text in said): problems.append(f'agent never said anything matching /{pattern}/')
    if expect.get('ends_open'):
        for i, text in enumerate(said):
            closed = i == len(said) - 1 and re.search(r'\bGoodbye\.\s*$', text)
            if not (closed or text.rstrip().endswith('?')): problems.append(f'agent turn {i + 1} did not end with a question')
    if any(not row['agent'].strip() for row in transcript if not row['caller'].startswith('[frontend')):
        problems.append('agent gave an empty reply')
    return problems


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ids', nargs='*', help='scenario ids to run (default: all)')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--out', default='evaluations/agent-conversations.json')
    parser.add_argument('--scenarios', default=str(SCENARIO_FILE))
    parser.add_argument('--quiet', action='store_true', help='print only pass/fail lines')
    args = parser.parse_args()
    if not os.environ.get('OPENAI_API_KEY'): raise SystemExit('Add OPENAI_API_KEY to .env first.')
    scenarios = json.loads(Path(args.scenarios).read_text())['scenarios']
    chosen = [s for s in scenarios if not args.ids or s['id'] in args.ids]
    if args.ids and len(chosen) != len(set(args.ids)): raise SystemExit('Unknown scenario id.')
    jobs = [(s, n) for s in chosen for n in range(args.repeat)]
    gate = asyncio.Semaphore(args.parallel)

    async def run(scenario, n):
        async with gate:
            try:
                workflow, transcript = await converse(scenario)
            except Exception as exc:  # A crashed conversation is a failure to report, not a reason to stop.
                return {'id': scenario['id'], 'run': n + 1, 'passed': False, 'problems': [f'crashed: {type(exc).__name__}: {exc}'], 'transcript': []}
            problems = check(scenario, workflow, transcript)
            return {'id': scenario['id'], 'run': n + 1, 'passed': not problems, 'problems': problems,
                    'idv': workflow.idv.snapshot()['history'], 'records': workflow.created, 'transcript': transcript}

    results = await asyncio.gather(*(run(s, n) for s, n in jobs))
    for r in results:
        print(f"=== {r['id']} #{r['run']}: {'PASS' if r['passed'] else 'FAIL ' + '; '.join(r['problems'])}")
        if args.quiet: continue
        for row in r['transcript']:
            print(f"  caller: {row['caller']}\n  agent [{', '.join(row['tools']) or '-'} | {row['idv']} | {row['ms']} ms]: {row['agent']}")
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'executed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                               'model': os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini'),
                               'limits': 'Typed caller turns through the real agent, tools, ID&V, LLM fallback and guards; no STT, TTS or audio timing.',
                               'results': results}, indent=2))
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} conversations passed. Saved {out.relative_to(ROOT)}")


if __name__ == '__main__':
    asyncio.run(main())
