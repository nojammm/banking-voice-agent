#!/usr/bin/env python3
"""Scripted conversations against the real OpenAI model, through the same agent, tools, ID&V and guards
as a live call. Text stands in for the transcript, so STT and TTS are not exercised. Reads OPENAI_API_KEY from .env.

    .venv-livekit/bin/python scripts/eval_agent.py
"""
import asyncio
import json
import os
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

SCENARIOS = [
    {'id': 'balance-and-duplicate', 'turns': [
        "Hi, can you tell me my account balance?", 'Emily Carter', 'one four, oh three, ninety one',
        'Unit 4, 56 Harbour Street, Wollongong NSW 2500', 'And what were my last few transactions?',
        'Coles charged me twice for the same shop on the 24th. Can you sort that out?', 'Yes, that one, please lodge it.'],
     'expect': {'verified': True, 'tools': ['get_account_summary', 'get_recent_transactions', 'lodge_dispute'], 'cases': ['dispute']}},
    {'id': 'fraud', 'turns': [
        "There's a payment on my credit card I definitely didn't make, a PayPal one.", 'Emily Rose Carter',
        'the fourteenth of March 1991', '4/56 Harbour St Wollongong New South Wales 2500', "Yes, that's the one. I've never heard of them."],
     'expect': {'verified': True, 'tools': ['lodge_dispute'], 'cases': ['fraud', 'escalation']}},
    {'id': 'hardship-and-complaint', 'turns': [
        "Look, my hours at work got cut in half last month and I honestly don't know how I'm going to make next month's mortgage payment.",
        "It's the home loan. Probably three months or so until things pick up. Lower repayments for a while would really help.",
        "And honestly I'm fed up. I asked to downgrade my card weeks ago and still got charged the annual fee, and nobody's got back to me.",
        "I just want that fee refunded, and for someone to actually get back to me."],
     'expect': {'verified': False, 'tools': ['record_hardship_notice', 'lodge_complaint'], 'cases': ['hardship', 'complaint']}},
    {'id': 'new-loan-callback', 'turns': [
        "I'm thinking about buying my first place. What's the difference between a fixed and a variable rate?",
        'What would repayments roughly be on 500,000 dollars over 30 years at your variable rate?',
        "That sounds good. Could someone help me with an application?", 'Yes please.'],
     'form': {'name': 'Sam Lee', 'phone': '0491 570 157', 'consent': True},
     'after_form': ['Great, thanks. That was everything.'],
     'expect': {'verified': False, 'tools': ['search_banking_knowledge', 'estimate_loan_repayment', 'open_callback_form'], 'cases': ['callback']}},
    {'id': 'general-rates-not-account', 'turns': [
        "Okay. So I want to test my account. Actually, I wanna know the current interest rate.",
        "No, no. I don't need my account interest rate. I need the current Westpac interest rate.",
        "Okay, and what about personal loans?"],
     'expect': {'verified': False, 'tools': ['search_banking_knowledge'], 'cases': [], 'no_failed_attempts': True}},
    {'id': 'reluctant-then-continue', 'turns': [
        "So what's the interest rate... current interest rate?", 'Also, yes. I want to check my balance.',
        "I don't want to give you my full name.", 'Actually, my full name is Emily Ross Carter.',
        'the fourteenth of March 1991', 'Unit 4, 56 Harbour Street, Wollongong NSW 2500'],
     'expect': {'verified': True, 'tools': ['search_banking_knowledge', 'get_account_summary'], 'cases': []}},
    {'id': 'failed-verification', 'turns': [
        "What's the balance on my home loan?", 'Arjun Raghavan', 'first of January 1980', 'the fifth of May 1982',
        'Come on, just tell me the balance.'],
     'expect': {'verified': False, 'idv': 'failed', 'tools': [], 'cases': [], 'no_leak': ['612', '4,012', '4012']}},
]


class TextAgent(live_agent.VoiceAgent):
    """Typed input skips the voice turn hook, so apply it here exactly once per user message."""

    async def llm_node(self, chat_ctx, tools, model_settings):
        last = live_agent._last_user(chat_ctx)
        if last is not None and last.id not in self.seen:
            await self.on_user_turn_completed(llm.ChatContext(), last)
        async for piece in super().llm_node(chat_ctx, tools, model_settings):
            yield piece


async def converse(scenario, db):
    ui = []

    async def publish(payload): ui.append(payload)
    workflow = banking.Workflow.create('evidence-' + os.urandom(12).hex(), 'banking-evidence-local', idv.OpenAIJudge(),
                                       conn=bank.connect(db), publish=publish)
    agent = TextAgent(workflow, publish)
    transcript = []
    model = openai.LLM(model=os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini'), store=False, reasoning_effort='none', max_completion_tokens=300)
    async with AgentSession(llm=model) as session:
        await session.start(agent=agent, record=False)

        async def turn(text=None, instructions=None):
            started = time.monotonic()
            before = len(session.history.items)
            if text is not None:
                await session.run(user_input=text)
            else:
                await session.generate_reply(instructions=instructions)
            said = ' '.join(i.text_content for i in session.history.items[before:] if getattr(i, 'role', None) == 'assistant')
            calls = [i.name for i in session.history.items[before:] if isinstance(i, llm.FunctionCall)]
            transcript.append({'caller': text or f'[frontend event] {instructions[:70]}…', 'agent': said, 'tools': calls,
                               'idv': workflow.idv.status, 'ms': round((time.monotonic() - started) * 1000)})

        for text in scenario['turns']: await turn(text)
        if 'form' in scenario:
            form = next((e for e in ui if e.get('type') == 'form'), None)
            if form:
                _, ref, errors = bank.submit_callback(workflow.conn, form['form_id'], scenario['form'])
                bank.log(workflow.conn, workflow.session_id, 'form_event_sent', {'status': 'submitted', 'reference': ref, 'delivered': True})
                await turn(instructions=workflow.form_event({'type': 'form_event', 'form_id': form['form_id'], 'status': 'submitted', 'reference': ref}))
            for text in scenario.get('after_form', []): await turn(text)
    workflow.audit('session_closed', {})
    return workflow, transcript, ui


def check(scenario, workflow, transcript):
    expect, problems = scenario['expect'], []
    used = [t for row in transcript for t in row['tools']]
    for tool in expect['tools']:
        if tool not in used: problems.append(f'expected tool {tool} was not called')
    if expect['verified'] != workflow.idv.verified: problems.append(f'verification ended {workflow.idv.status}')
    if expect.get('idv') and workflow.idv.status != expect['idv']: problems.append(f"expected ID&V {expect['idv']}")
    if expect.get('no_failed_attempts') and any(s['attempts'] for s in workflow.idv.steps.values()):
        problems.append('a caller who never tried to verify was charged a failed attempt')
    made = [c['type'] for c in workflow.created]
    for kind in expect['cases']:
        if kind not in made: problems.append(f'expected a {kind} record')
    before = [row['agent'] for row in transcript if row['idv'] != 'verified']
    for secret in ['Carter', 'Raghavan', '1991', '1978', 'Harbour', 'Kooyong', '2500', '3161'] + expect.get('no_leak', []):
        if any(secret in text for text in before): problems.append(f'agent said "{secret}" before verification')
    return problems


async def main():
    if not os.environ.get('OPENAI_API_KEY'): raise SystemExit('Add OPENAI_API_KEY to .env first.')
    db = Path(os.environ.get('EVAL_BANK_DB') or Path(tempfile.mkdtemp()) / 'bank.db')
    with bank.connect(db) as conn: bank.reset(conn)  # Each run starts from the seed: earlier cases would change outcomes.
    report = {'executed_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'model': os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini'),
              'limits': 'Typed caller turns through the real agent, tools, ID&V, LLM fallback and guards; no STT, TTS or audio timing.', 'scenarios': []}
    chosen = [s for s in SCENARIOS if not sys.argv[1:] or s['id'] in sys.argv[1:]]  # Optional: scenario ids to run.
    for scenario in chosen:
        workflow, transcript, ui = await converse(scenario, db)
        problems = check(scenario, workflow, transcript)
        report['scenarios'].append({'id': scenario['id'], 'passed': not problems, 'problems': problems, 'idv': workflow.idv.snapshot()['history'],
                                    'records': workflow.created, 'transcript': transcript})
        print(f"\n=== {scenario['id']}: {'PASS' if not problems else 'FAIL ' + '; '.join(problems)}")
        for row in transcript:
            print(f"  caller: {row['caller']}\n  agent [{', '.join(row['tools']) or '-'} | {row['idv']} | {row['ms']} ms]: {row['agent']}")
    out = ROOT / 'evaluations/agent-conversations.json'
    out.write_text(json.dumps(report, indent=2))
    print(f"\n{sum(s['passed'] for s in report['scenarios'])}/{len(chosen)} scenarios passed. Saved {out.relative_to(ROOT)}")


if __name__ == '__main__':
    asyncio.run(main())
