"""Live audio worker: Deepgram -> OpenAI banking agent with Worker-owned ID&V and tools -> ElevenLabs."""
import asyncio
import difflib
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Literal

os.environ['LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE'] = '0'
from livekit import rtc
from livekit.agents import (Agent, AgentServer, AgentSession, AudioConfig, BackgroundAudioPlayer, BuiltinAudioClip, JobContext,
                            MetricsCollectedEvent, RunContext, StopResponse, cli, function_tool, inference, llm, room_io, stt)
import anthropic as anthropic_sdk
from livekit.plugins import anthropic, deepgram, elevenlabs, openai
import banking
import engine
import idv
from live_voice import AGENT_NAME, EVENTS_TOPIC

GREETING = "Hi, I'm Red, Westpac's AI assistant. How can I help you today?"
# Spoken by the knowledge tool itself the moment a search starts, so the wait is never silent.
LOOKUP_LINES = ("Let me look that up for you.", "Sure, let me just check that.", "Good question… let me check.",
                "One sec, I'll find that for you.")

# The customer number box comes with its own line, said by the Worker, so the ask is always the same and never
# hallucinated. The model's reply is only the lead-in; any ask or ID&V explanation in it is dropped (BOX_TALK).
ASK_NUMBER = "Could you type your customer number into the box on your screen for me?"
ASK_NUMBER_AGAIN = "Could you check it and type it in again for me?"
BOX_TALK = re.compile(r"\bcustomer (?:number|id)\b|\bbox\b|\bverif|\bidentity\b|\bsecurity\b|"
                      r"\bconfirm (?:who|it['’]?s|your)\b|\bcheck (?:who|it['’]?s you)\b|\b(?:full|your) name\b|\bdate of birth\b", re.I)

TTS_MODEL = os.environ.get('ELEVENLABS_LIVE_MODEL', 'eleven_v4_turbo')
# ElevenLabs v3/v4 perform bracketed audio tags; Flash and older models read them aloud.
AUDIO_TAGS = TTS_MODEL.startswith(('eleven_v3', 'eleven_v4'))
PROMPTS = Path(__file__).resolve().parent / 'prompts'
LOGS = Path(__file__).resolve().parent / 'logs'
ROLE = (PROMPTS / 'banking-agent.md').read_text()
STYLE = (PROMPTS / 'natural-conversation.md').read_text()
INSTRUCTIONS_ID = 'lk.agent_task.instructions'

PACING = """# Pacing
Your voice can't be slowed down by a setting; it follows your punctuation. Write the rhythm you want heard.
- Short sentences, one idea each. A full stop lets a point land. A comma is a small breath.
- An ellipsis (…) gives a thinking pause or a softer, slower moment, like "Okay… let me think." Use one now and then, not in every sentence.
- A dash gives a quick aside or a self-correction, like "It's fixed — well, for the first two years."
- Slow down for what matters. Give figures, dates and references their own short sentence. When a tool gives a say_reference, read the reference exactly that way.
- Never put a pause inside a number. Don't use capitals for emphasis."""

TAGGED_DELIVERY = PACING + """

# Voice delivery tags
Your voice engine performs short bracketed audio tags instead of reading them aloud, for example [warm], [sympathetic], [reassuring], [relieved], [chuckles], [thoughtful] or [exhales]. They're the one exception to the style guide's rule against stage directions and descriptions of how to speak.
- Save them for moments with real feeling: sympathy when they're struggling or upset, reassurance when they're worried about fraud, a chuckle when they joke. A [warm] when you greet them by name, something gets sorted or they thank you also fits. Aim for about one tag every three or four replies, never more than one in a turn, and none on bare figures or identity questions.
- Put a tag right before the words it colours. Never put one inside a number or a name.
- Never sigh at the customer or their question. No tags in refusals, security warnings or identity questions.
- No sound effects, accents, singing, whispering, or playful tags such as [giggles] or [mischievously]."""

PLAIN_DELIVERY = PACING + """

# Voice delivery
Your voice engine reads everything aloud, including brackets, so never write bracketed tags or stage directions."""


def build_prompt():
    return '\n\n'.join([ROLE.strip(), STYLE.strip(), TAGGED_DELIVERY if AUDIO_TAGS else PLAIN_DELIVERY])


DECLINE = {'blocked_injection': "I can't change how I work or what I can access. I'm happy to help with a banking question, though."}
UNSUPPORTED = "Sorry, I can't confirm that detail right now."
AUDIO_TAG = re.compile(r'\[[^\[\]{}\n]{1,40}\]\s*')  # A tag and the space after it.
WORDS = {w: str(n) for n, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
    'sixteen seventeen eighteen nineteen twenty'.split())}
WORDS.update(thirty='30', forty='40', fifty='50', sixty='60', ninety='90', hundred='100', thousand='1000')
REFERENCE = re.compile(r'\b[A-Z]{3}-(\d{4})\b')
SMALL = {'0', '1', '2'}  # Too common in ordinary speech ("one moment") to treat as claims.
SPOKEN_NUMBER = (r'(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|'
                 r'fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|'
                 r'ninety|hundred|thousand|double|triple|\d+)')
# Card numbers and codes read aloud may be transcribed as words; five or more number words in a row are masked.
SPOKEN_DIGITS = re.compile(rf'\b{SPOKEN_NUMBER}(?:[\s,.-]+(?:and[\s,.-]+)?{SPOKEN_NUMBER}){{4,}}\b', re.I)


def numbers(text, expand=False):
    """Numbers in text, normalised ("$4,012.50" -> 4012.50). expand also allows the parts people say aloud."""
    text = re.sub(r'(?<=\d),(?=\d{3}\b)', '', text)
    found = set()
    for n in re.findall(r'\d+(?:\.\d+)?', text):
        whole, _, frac = n.partition('.')
        whole = whole.lstrip('0') or '0'
        found.add(whole + ('.' + frac if frac else ''))
        if expand:
            found.add(whole)
            if frac: found.update({frac, frac.lstrip('0') or '0', whole + '.' + frac.rstrip('0') if frac.rstrip('0') else whole})
    # Number words count both ways: the caller says "three months", the agent writes "3 months".
    found.update(WORDS[w] for w in re.findall(r'[a-z]+', text.lower()) if w in WORDS)
    return found


def allowed_numbers(chat_ctx, extra=()):
    """Numbers the reply may use: from the caller, tool results, Worker notes and frontend events."""
    allowed = set(SMALL) | set(extra)
    for item in chat_ctx.items:
        if isinstance(item, llm.FunctionCallOutput):
            text = item.output
        elif isinstance(item, llm.ChatMessage) and item.role != 'assistant' and item.id != INSTRUCTIONS_ID:
            text = item.text_content or ''
        else:
            continue
        allowed |= numbers(text, expand=True)
        for digits in REFERENCE.findall(text): allowed.update(digits)  # References are read out digit by digit.
        if isinstance(item, llm.FunctionCallOutput): allowed |= list_counts(text)
    return allowed


def list_counts(output):
    """How many items each list in a tool result holds ("here are the last 5"): a count the result supports."""
    try: data = json.loads(output)
    except ValueError: return set()
    counts, stack = set(), [data]
    while stack:
        value = stack.pop()
        if isinstance(value, list): counts.add(str(len(value))); stack.extend(value)
        elif isinstance(value, dict): stack.extend(value.values())
    return counts


def redact(text):
    text = SPOKEN_DIGITS.sub('[REDACTED]', engine.redact(text)[0])
    return re.sub(r'\[REDACTED\](?:[\s,.-]*\[REDACTED\])+', '[REDACTED]', text)


SENTENCE_END = re.compile(r'[.?!…]+["’”)]*\s+')
# "I'm lodging it now", "I can record that for you", "let's get this recorded", "I'll need a few details": says a record is
# about to be made, or what's needed first, before the tool has made it.
ANNOUNCE = re.compile(r"\b(?:I['’]m|I am|I['’]ll|I will|I can|we can|let me|let['’]s|then|so I can|before I)\s+"
                      r"(?:just\s+|now\s+|also\s+|going to\s+|go ahead and\s+|get (?:this|that|it|the [\w -]{1,25}?|a [\w -]{1,25}?)\s+)*"
                      r"(?:lodg|record|log(?:ging)?\b|add|updat|not(?:e|ing)\b|fil(?:e|ing)\b|rais|submit|pass|flag|escalat|creat|sort|arrang|set (?:\w+ )?up)"
                      r"|\bneed (?:a (?:couple|few) (?:of )?|some |a bit more |more )?(?:details|information|info)\b", re.I)
# "Let me search our loan policies", "I'll just check that": the knowledge search speaks its own line, so the model's is dropped.
SEARCHING = re.compile(r"\b(?:let me|I['’]ll|I will|I['’]m|I am|while I)\s+(?:just\s+|quickly\s+|going to\s+|go ahead and\s+)*"
                       r"(?:search|check|find|look(?:ing)? (?:\w+ ){0,2}(?:up|into)|have a (?:quick )?look)", re.I)
# The caller asking for their own records in plain words: the Worker starts verification itself, so the reply can explain and
# ask straight away instead of after a tool call. Someone else's records ("my wife's balance"), fraud reports and general
# questions don't match and take the usual path.
OWN_RECORDS = re.compile(r"\b(?:my (?:account |card |credit card |savings |everyday )?balances?|balances? (?:on|of|in) my|"
                         r"my (?:recent |last (?:few )?|latest )?transactions|"
                         r"(?:check|see|know|tell me|look at|access) my (?:accounts?|savings|credit card|card|loans?|home loan|car loan|mortgage|statements?)\b|"
                         r"how much (?:do I owe|is (?:left )?(?:owing )?on my)|my next (?:\w+ )?(?:\w+ )?repayment|"
                         r"where(?:['’]s| is) my (?:home loan |loan |car loan )?application)", re.I)
# "I just need to check your identity first": verification is about to start. The reply after the tool explains it and asks
# in one go, so said before the tool call it would be heard twice, split by the wait.
VERIFY_FIRST = re.compile(r"\b(?:need to (?:verify|check|confirm|start|understand)|(?:confirm|verify|check) who you are|verif(?:y|ication)(?: \w+){0,2} first|identity (?:checks?|first)|"
                          r"security checks?|protect(?:s)? your account)\b", re.I)
# "I've updated the notice", "I've got it down as unsure", "that's noted", "the refund is part of it now": says a record holds
# something. True only if a record tool ran this turn. An offer or condition before it ("I can get that recorded") isn't a claim.
FIRST_PERSON = re.compile(r"\b(?:I['’]ve|I have|we['’]ve|we have)\b", re.I)
RECORDED = re.compile(r"\b(?:updated|added|noted|captured|included|amended|recorded|logged|lodged)\b"
                      r"|\b(?:got|put|have|marked|left) (?:it|that|this|you|the [\w ’']{1,30}?) (?:down|as|in|on)\b", re.I)
NOW_RECORDED = re.compile(r"\b(?:been|now|is|are|['’]s)\s+(?:also\s+|now\s+|already\s+)*(?:updated|added|noted|captured|included|amended)\b"
                          r"|\bpart of (?:it|that|the (?:complaint|notice|request|case))\b", re.I)
NOT_CLAIM = re.compile(r"\b(?:can|could|will|would|if|want|need|going to|once|when|until|before|not|nothing|was|were)\b|['’](?:ll|d)\b|n['’]t\b", re.I)


def claims_record(sentence):
    """Whether a sentence says a record now holds something ("I've added that", "it's been updated")."""
    found = NOW_RECORDED.search(sentence)
    if not found and (first := FIRST_PERSON.search(sentence)): found = RECORDED.search(sentence, first.end())
    clause = found and re.split(r"[,;:—–]|\b(?:but|and|so)\b", sentence[:found.start()])[-1]  # "I can't…, but I've added it" claims.
    return bool(found) and not NOT_CLAIM.search(clause)


EMPATHY = re.compile(r"sorry|frustrat|hear you|understand|annoy|not good|rough|a pain|get (?:it|why)|makes sense|fair enough|"
                     r"disappoint|upset|tough|no wonder|see why|not on\b|unfair", re.I)  # An acknowledgement already said.
FOLLOWUP_OFFER = re.compile(r"\b(?:would you like|do you want|want me to|if you(?:['’]d| would)? (?:like|want)|I can (?:also )?(?:record|arrange|ask|set|get|put|request))\b"
                            r"[^.?!]*\b(?:follow[- ]?up|specialist|a (?:person|human)|someone)\b", re.I)
# The form opens only once an earlier reply has explained the lending specialist or the callback, so the caller knows why
# it's there before agreeing to it.
CALLBACK_OFFER = re.compile(r"\b(?:call ?back|lending specialist)\b", re.I)
JUST_VERIFIED_HARDSHIP = ("If they came about hardship, don't repeat back what they told you. If they've said anything at all about why "
                          "(lost work, bills, the economy, a split, illness), that's enough, so record it now without asking more. If "
                          "they've said nothing about why beyond not being able to pay (when or which payment isn't a reason), ask one "
                          "gentle, caring question about what's happened, and record it once they've answered, whatever "
                          "they say. Never ask for more detail, which loan, how long or what would help, and don't look up their "
                          "accounts for it.")
LEAVING = ("The caller is leaving before verification is finished. If they raised hardship, a complaint or anything else that must "
           "be recorded, record it now without verification and give the reference: the team will confirm who they are. Then ask "
           "the closing question.")
NOT_OFFERED = ("Don't open the form yet. Explain briefly that a lending specialist works through their situation and handles the "
               "application, so you can arrange a callback, and ask if they'd like one; a short form will come up on their screen "
               "for their name, number and consent. Open it only after they say yes.")
LOAN_KIND_ASKED = re.compile(r"\bhome loan\b[^.?!]*\bcar loan\b[^.?!]*\?", re.I)  # "…a home loan, a car loan or a personal loan?"
OFFERED = ("You've already offered a human or specialist follow-up in this call. Don't offer it again unless the caller asks "
           "for one or brings it up.")
VERIFIED_NOW = ("Verification has just finished, but no tool was called. Call the tool for what they asked, as they last put it, "
                "now, saying nothing before the call. The one exception is hardship where they've said nothing at all about why: then "
                "let them know the checks are done and, without repeating back what they said, ask one gentle, caring question "
                "about what's happened.")
RECORD_TOOLS = {'record_hardship_notice', 'lodge_complaint', 'request_human_followup', 'lodge_dispute'}
UNBACKED = ("Nothing was recorded or updated in this turn: no record tool has been called since the caller last spoke. "
            "If what they just said needs recording, call the tool now, saying nothing before the call: a new complaint or hardship "
            "notice, or details for a record made earlier in this call (call the tool that made it again with everything so far). "
            "Otherwise reply without saying you recorded, noted, added or updated anything.")
# A finished reply leaves the caller something to respond to. When one ends flat, a short check-in is written by a second
# call and said after it; the call runs while the reply is being spoken, so the caller doesn't wait for it.
INVITATION = re.compile(r"\?|\b(?:let me know|feel free|just (?:say|ask|tell me)|tell me|go ahead|take your time|no rush|"
                        r"whenever you['’]re ready|if you(?:['’]d| would)? (?:like|want)|if (?:that|it)(?: would)? helps?|happy to|"
                        r"I['’]m here|anything else|I can (?:also |still )?(?:tell you|give you|explain|walk you|go through|run through|talk you))\b", re.I)
WRAP_UP = re.compile(r"\b(?:that['’]?s (?:all|it|everything)|that(?:['’]ll| will) be all|nothing else|(?:good)?bye|see ya|"
                     r"(?:got|have|gotta) (?:to )?go|I['’]m (?:all )?(?:good|done|sorted)|all good|have a good)\b", re.I)
FAREWELL = re.compile(r"\b(?:take care|(?:good)?bye|have a (?:good|great|lovely|nice)|all the best|enjoy the rest)\b", re.I)
INTERNAL = re.compile(r"\b(?:worker|tools?|function|system|prompt|instructions?|json|note|check-?in)\b|_", re.I)
CHECK_IN = ("Your reply above finished what they asked, but it leaves the caller nothing to respond to. Write one short sentence "
            "to say straight after it, in your own words, that fits what you just said: check in with them, or offer more on it "
            "that you can actually give. "
            "Don't repeat anything, add facts or numbers, or offer to record, arrange or follow up anything. Make it a question "
            "or an open invitation, worded differently from any check-in earlier in this call. Write only that sentence.")


def split_ready(pending):
    """Split after the last finished sentence that isn't inside an unclosed [audio tag].
    Speech is synthesised a sentence at a time anyway, so this costs no audio latency."""
    limit = len(pending)
    opened = pending.rfind('[')
    if opened > pending.rfind(']') and len(pending) - opened < 60:
        limit = opened
    ends = [m.end() for m in SENTENCE_END.finditer(pending, 0, limit)]
    return (pending[:ends[-1]], pending[ends[-1]:]) if ends else ('', pending)


def sentences(text):
    out, start = [], 0
    for m in SENTENCE_END.finditer(text):
        out.append(text[start:m.end()]); start = m.end()
    return out + ([text[start:]] if text[start:] else [])


async def guarded(stream, allowed, publish, audio_tags=None, said=None, seen=None, backed=True, flags=None, drop=None):
    """Pass tool calls through untouched; stop speech that states a number nobody supplied.

    Speech goes out a sentence at a time. A question, or an announcement that a record is being made or verification is
    about to start, waits for the
    reply to end; if the reply calls a tool, it and anything after it are dropped, so the agent never asks something
    and then answers it from the result, or says "I'm lodging it now" before a result that says otherwise. A clause of
    an announcement that isn't one ("…, and I can see why that's frustrating") is still said. A sentence
    already said this turn (seen), or one close to a line said before the tool call, isn't said again. After an
    unsupported number the reply goes quiet, but its tool calls still go through; the fallback line is said only if
    there were none (the reply after the tool result speaks instead).

    Unless a record tool ran this turn (backed), a claim that a record was updated waits too. If the reply ends without
    a tool call, the claim is never said: with flags, nothing held is said and flags['unbacked'] asks the caller to
    regenerate; without, only the claim sentences are left out. A sentence matching drop is never said."""
    audio_tags = AUDIO_TAGS if audio_tags is None else audio_tags
    pending, held, tool_called, searched, spoken, blocked = '', [], False, False, False, False
    said = [] if said is None else said
    seen = set() if seen is None else seen
    earlier = list(seen)  # Said in this turn before a tool call.

    def take(text):
        ready = []
        for sentence in sentences(text):
            if (held or sentence.rstrip().endswith('?') or ANNOUNCE.search(sentence) or VERIFY_FIRST.search(sentence) or SEARCHING.search(sentence)
                    or (not backed and claims_record(sentence))): held.append(sentence)
            else: ready.append(sentence)
        return ready

    def salvage(pieces):
        """With a tool call: keep the parts of a held announcement that aren't one
        ("I can log that now, and I can see why it's frustrating." -> "I can see why it's frustrating.")."""
        out = []
        for piece in pieces:
            if not searched and SEARCHING.search(piece) and not ANNOUNCE.search(piece) and not piece.rstrip().endswith('?'):
                out.append(piece); continue  # "Let me check your transactions" before another tool is still said.
            if piece.rstrip().endswith('?') or not ANNOUNCE.search(piece): break  # Later lines may depend on the result.
            for clause in re.split(r",\s*(?:and|but|so)\s+|;\s*|\s+[—–]\s+|\s+but\s+", piece.strip()):
                clause = clause.strip(' ,.!…')
                if (len(clause.split()) >= 3 and not ANNOUNCE.search(clause) and not claims_record(clause)
                        and not (searched and SEARCHING.search(clause))):
                    out.append(clause[0].upper() + clause[1:] + '. ')
        return out

    def unclaimed(pieces):
        if flags is not None and flags.get('verified_now'): return []  # The retry acts instead of asking first.
        if backed or not any(claims_record(p) for p in pieces): return pieces
        if flags is not None:
            flags['unbacked'] = True
            return []
        return [p for p in pieces if not claims_record(p)]

    def close(key, before):
        """A paraphrase ("I'm sorry you're going through that.") or a short part ("I'm sorry.") of an earlier line."""
        words = re.findall(r"[a-z'’]+", key)
        return (difflib.SequenceMatcher(None, key, before).ratio() >= 0.7 or
                (len(words) <= 5 and set(words) <= set(re.findall(r"[a-z'’]+", before))))

    def speak(pieces):
        """The text to yield for these sentences, minus repeats, up to any number nobody supplied (then True)."""
        nonlocal spoken
        out = []
        for piece in pieces:
            key = ' '.join(AUDIO_TAG.sub('', piece).lower().split())
            if drop and drop.search(key): continue
            if len(key) >= 8 and key in seen: continue
            if not re.search(r'\d', key) and any(close(key, e) for e in earlier): continue
            seen.add(key)
            if numbers(piece) - allowed: return out, True
            spoken = True
            said.append(piece)
            out.append(piece if audio_tags else AUDIO_TAG.sub('', piece))
        return out, False

    async for chunk in stream:
        if isinstance(chunk, str):
            text = chunk
        else:
            text = chunk.delta.content if chunk.delta else None
            if chunk.delta is None or chunk.delta.tool_calls:
                if chunk.delta and chunk.delta.tool_calls:
                    tool_called = True
                    searched |= any(c.name == 'search_banking_knowledge' for c in chunk.delta.tool_calls)
                if chunk.delta: chunk.delta.content = None
                yield chunk  # Tool calls and usage records go straight to the framework.
            if not text: continue
        if blocked: continue
        pending += text
        complete, pending = split_ready(pending)
        out, blocked = speak(take(complete))
        if blocked: await publish({'type': 'guard', 'reason': 'unsupported_number'})
        for piece in out: yield piece
    if not blocked:
        out, blocked = speak(take(pending) + (salvage(held) if tool_called else unclaimed(held)))
        if blocked: await publish({'type': 'guard', 'reason': 'unsupported_number'})
        for piece in out: yield piece
    if flags is not None: flags['no_tool'] = not tool_called
    if blocked and not tool_called: yield (' ' if spoken else '') + UNSUPPORTED


async def display_text(text):
    """Remove audio tags from the on-screen transcript; the voice still performs them."""
    held = ''
    async for chunk in text:
        value = held + str(chunk)
        opened = value.rfind('[')
        if opened > value.rfind(']') and len(value) - opened < 60:
            value, held = value[:opened], value[opened:]
        else:
            held = ''
        clean = AUDIO_TAG.sub('', value)
        if clean == str(chunk): yield chunk  # Unchanged chunks keep their audio timing.
        elif clean: yield clean
    if held: yield AUDIO_TAG.sub('', held)


def _last_user(chat_ctx):
    return next((item for item in reversed(chat_ctx.items) if isinstance(item, llm.ChatMessage) and item.role == 'user'), None)


def recorded_this_turn(chat_ctx, names=RECORD_TOOLS):
    """Whether a record tool (or, with names=None, any tool) has been called since the caller last spoke."""
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == 'user': return False
        if isinstance(item, llm.FunctionCall) and (names is None or item.name in names): return True
    return False


def verification_only(chat_ctx):
    """Whether this is the reply straight after tool results that only started or resumed verification (nothing to say
    about them). A later reply in the same turn, like one after a form event, isn't."""
    outputs = []
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == 'user': break
        if isinstance(item, llm.ChatMessage) and item.role == 'assistant': return False
        if isinstance(item, llm.FunctionCallOutput): outputs.append(item.output)
    def started(output):
        try: result = json.loads(output)
        except ValueError: return False
        return result.get('error') in ('verification_required', 'verification_in_progress') or result.get('status') == 'in_progress'
    return bool(outputs) and all(started(o) for o in outputs)


def ends_flat(reply, caller=''):
    """Whether a finished reply leaves the caller nothing to respond to: it asks and invites nothing, and it isn't
    a goodbye or a reply to a caller who is wrapping up ("that's all, thanks", "bye")."""
    reply = AUDIO_TAG.sub('', reply).strip()
    wrapping_up = WRAP_UP.search(caller) and '?' not in caller
    offer = reply and re.search(r"\bI can (?:also |still )?help\b|\bchange your mind\b", sentences(reply)[-1], re.I)  # Ends on its own offer.
    return bool(reply) and not (INVITATION.search(reply) or FAREWELL.search(reply) or offer or wrapping_up)


def usable_check_in(text, earlier=(), allowed=SMALL):
    """The check-in as one short spoken line, or None if it isn't one: too long, not a question or invitation,
    internal words, a number nobody supplied, an offer to record something, or a repeat of an earlier check-in."""
    text = ' '.join(AUDIO_TAG.sub('', text).split()).strip('"“”‘’\' ')
    if not text or len(text.split()) > 25 or len(sentences(text)) > 2 or not INVITATION.search(text): return None
    if INTERNAL.search(text) or ANNOUNCE.search(text) or FOLLOWUP_OFFER.search(text) or claims_record(text): return None
    if numbers(text) - set(allowed): return None
    key = re.sub(r"[^a-z' ]", '', text.lower())
    if any(difflib.SequenceMatcher(None, key, re.sub(r"[^a-z' ]", '', e.lower())).ratio() >= 0.8 for e in earlier): return None
    return text


class VoiceAgent(Agent):
    def __init__(self, workflow, publish):
        super().__init__(instructions=build_prompt())
        self.workflow, self.publish = workflow, publish
        self.raw_finals, self.seen, self.fresh, self.pending = [], set(), {}, set()
        self.current_said, self.last_lookup, self.opening, self.turn_seen = [], None, None, (None, set())
        self.followup_offered = False  # Once offered, a human or specialist follow-up isn't offered again unasked.
        self.callback_offered = None  # The caller turn in which the agent explained the lending callback.
        self.check_ins = []  # Check-ins added after flat replies, so the next one is worded differently.
        self.started = set()  # Caller turns in which the Worker started verification from their words.
        self.asked_loan_kind = False  # The agent's last reply asked which kind of loan.
        self.heard = False  # Speech-to-text is in use, so every real caller turn brings new final transcripts.
        self.last_turn = None  # (message id, content the model saw, raw words, when) for the caller's previous turn.

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if isinstance(event, stt.SpeechEvent):
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    # The ID&V matcher needs the words as spoken; everything downstream sees the redacted copy.
                    self.raw_finals.append(event.alternatives[0].text)
                    self.heard = True
                for alternative in event.alternatives:
                    alternative.text = redact(alternative.text)
            yield event

    async def transcription_node(self, text, model_settings):
        async for chunk in display_text(Agent.default.transcription_node(self, text, model_settings)):
            yield chunk

    async def on_user_turn_completed(self, turn_ctx, new_message):
        raw = ' '.join(self.raw_finals).strip() or new_message.text_content or ''
        if self.leftover(raw):
            self._same_turn(new_message, turn_ctx)
            return
        self.raw_finals.clear()
        self.seen.add(new_message.id)
        self.workflow.new_turn()
        self.last_turn = (new_message.id, new_message.content, raw, time.monotonic())
        if self.workflow.typing() and not (idv.is_question(raw) or '?' in (new_message.text_content or '')):
            raise StopResponse()  # They're typing the number and talking to themselves (or reading it out): let them finish.
        verification = self.workflow.idv
        kind = banking.loan_kind(raw)
        if (kind and verification.status != 'in_progress' and self.workflow.loan_stage is None
                and (self.asked_loan_kind or banking.loan_request(raw))):
            # They named the kind of loan they want: start finding out what it's for, so the first question is the Worker's.
            await self.workflow.call('note_loan_needs', loan_type=kind, **banking.loan_details(redact(raw)))
            turn_ctx.add_message(role='system', content='The Worker started the loan enquiry for this turn.')
            return
        if verification.status != 'in_progress' and self.workflow.loan_answer(redact(raw)):
            # Their answer about the loan they want. Changing the turn's context stops a draft written before it was recorded.
            turn_ctx.add_message(role='system', content='The Worker recorded this turn for the loan enquiry.')
            return
        # A name said before verification starts counts if a tool starts verification in this same turn.
        self.opening = (new_message.id, raw) if verification.status == 'not_started' else None
        if verification.status == 'not_started' and OWN_RECORDS.search(raw):
            await self._start_verification(new_message, raw, turn_ctx)
            return
        if verification.status not in ('in_progress', 'paused'): return
        step = verification.step
        result = await verification.answer(raw)
        if result is None: return  # Paused and not an answer: an ordinary turn.
        self.fresh[new_message.id] = verification.last
        if result.outcome != 'no_answer':
            # The model never sees identity answers; it only learns the Worker's outcome.
            new_message.content = [f'[The caller answered the {idv.STEP_LABEL[step]} question. The Worker withheld the answer.]']
        self.last_turn = (new_message.id, new_message.content, raw, self.last_turn[3])
        # Always changing this turn's context stops a preemptive draft from being reused.
        turn_ctx.add_message(role='system', content='The Worker processed this turn for identity verification.')
        await self.workflow.auth_changed()

    def leftover(self, raw):
        """A turn made only of words the previous turn already used: speech-to-text sent the end of an answer ("…ninety" +
        "nine") after the turn was taken, and the framework made it a turn of its own. No new final transcript, or the
        same words again within a few seconds."""
        if not self.heard or self.last_turn is None: return False
        if not self.raw_finals: return True
        words, before = ' '.join(re.findall(r'\w+', raw.lower())), ' '.join(re.findall(r'\w+', self.last_turn[2].lower()))
        return bool(words) and time.monotonic() - self.last_turn[3] < 3 and f' {words} ' in f' {before} '

    def _same_turn(self, message, turn_ctx):
        """Treat a leftover turn as the previous one: nothing is matched again, and the reply is the one that turn needed."""
        self.raw_finals.clear()
        self.seen.add(message.id)
        before, content = self.last_turn[0], self.last_turn[1]
        if before in self.fresh:
            self.fresh[message.id] = self.fresh[before]
            message.content = list(content)  # An identity answer stays withheld.
        if before in self.started: self.started.add(message.id)
        self.workflow.audit('caller_turn_leftover', {'words': len(message.text_content.split()) if message.text_content else 0})
        turn_ctx.add_message(role='system', content='This turn only repeated the end of the previous one; the Worker had already processed it.')

    async def _start_verification(self, message, raw, turn_ctx):
        """The caller wants their own records: start verification now, so the reply explains and asks in one go.
        A name said in the same turn counts if it clearly matches."""
        verification = self.workflow.idv
        verification.begin('worker')
        self.workflow.waiting.append('what they asked for in the message that started verification')
        self.opening = None
        if await verification.take_volunteered_name(raw): self.fresh[message.id] = verification.last
        else: self.started.add(message.id)
        await self.workflow.auth_changed()
        turn_ctx.add_message(role='system', content='The Worker started identity verification for this turn.')

    def lookup_line(self, piece, said):
        """A ready-made line the moment the model asks for a knowledge search, to fill the wait; once per reply. The model's own
        "let me search…" is held as an announcement and dropped with the tool call, so the search always sounds the same."""
        calls = (not isinstance(piece, str) and piece.delta and piece.delta.tool_calls) or []
        if not any(c.name == 'search_banking_knowledge' for c in calls) or set(said) & set(LOOKUP_LINES): return None
        self.last_lookup = random.choice([line for line in LOOKUP_LINES if line != self.last_lookup])
        said.append(self.last_lookup)
        return self.last_lookup

    async def llm_node(self, chat_ctx, tools, model_settings):
        last = _last_user(chat_ctx)
        verification = self.workflow.idv
        # A reused preemptive draft keeps its own copy of the caller's message, so the reply after a tool sees an id the Worker
        # never got. Tools only run once the turn is complete, so a tool call since the caller spoke means this isn't a draft.
        pending = last is not None and last.id not in self.seen and not recorded_this_turn(chat_ctx, None)
        if pending and (verification.status == 'in_progress' or verification.might_answer(last.text_content or '')):
            return  # A preemptive draft during ID&V would send the raw answer before the Worker has handled it.
        text = (last.text_content or '') if last is not None else ''
        if last is not None and last.id not in self.fresh and engine.ATTACK.search(text):
            await self.publish({'type': 'guard', 'reason': 'blocked_injection'})
            yield DECLINE['blocked_injection']
            return
        ctx = chat_ctx.copy()
        fresh_pass = last is not None and (self.fresh.get(last.id) or {}).get('outcome') == 'pass'
        note = self.workflow.state_note(self.fresh.get(last.id) if last is not None else None)
        if self.followup_offered and not self.workflow.followup_ref:
            note += '\n\n' + OFFERED
        if fresh_pass and verification.status == 'verified':
            note += '\n\n' + JUST_VERIFIED_HARDSHIP
        if verification.status == 'in_progress' and WRAP_UP.search(text) and '?' not in text:
            note += '\n\n' + LEAVING
        if last is not None and last.id in self.started and not verification.history and verification.step == verification.cfg['steps'][0]:
            note += '\n\nVerification has just started for what they asked. ' + self.workflow.ask_step(True)
        box = self.box_to_ask()
        if box and banking.BOX_LEAD_IN not in note: note += '\n\n' + banking.BOX_LEAD_IN
        ctx.add_message(role='system', content=note)
        allowed = allowed_numbers(ctx, self.workflow.facts)
        said = self.current_said = []
        turn_id = last.id if last is not None else None
        if self.turn_seen[0] != turn_id: self.turn_seen = (turn_id, set())  # Sentences said so far this turn.
        seen = self.turn_seen[1]
        fresh = self.fresh.get(last.id) if last is not None else None
        just_verified = (bool(fresh) and fresh.get('outcome') == 'pass' and self.workflow.idv.status == 'verified'
                         and not recorded_this_turn(chat_ctx, None))
        backed, flags = recorded_this_turn(chat_ctx), {'verified_now': just_verified}
        try:
            if box and seen and verification_only(chat_ctx):
                tool_called = False  # They've been answered before the tool call: the Worker's line is the whole reply.
            else:
                async for piece in guarded(Agent.default.llm_node(self, ctx, tools, model_settings), allowed, self.publish, said=said,
                                           seen=seen, backed=backed, flags=flags, drop=BOX_TALK if box else None):
                    if (line := self.lookup_line(piece, said)): yield line
                    yield piece
                tool_called = not flags.get('no_tool')
            if box and not tool_called:  # A tool call means another reply follows: the ask goes at the end of that one.
                self.workflow.forms[box]['asked'] = True
                line = ASK_NUMBER_AGAIN if self.workflow.forms[box].get('notice') == 'not_found' else ASK_NUMBER
                lead = '' if not said or said[-1][-1:].isspace() else ' '
                async for piece in guarded(self._one(lead + line), allowed, self.publish, said=said, seen=seen):
                    yield piece
            # One retry: a record change claimed with no tool, or verification just finished and nothing was looked up.
            retry = UNBACKED if flags.get('unbacked') else VERIFIED_NOW if just_verified and flags.get('no_tool') else None
            if retry:
                already = ' '.join(AUDIO_TAG.sub('', ''.join(said)).split())
                ctx.add_message(role='system', content=retry + (f' You already said this turn: "{already}" Carry on from there '
                                                                   "without repeating or rephrasing it." if already else ''))
                tool_called = False
                async for piece in guarded(Agent.default.llm_node(self, ctx, tools, model_settings), allowed, self.publish, said=said, seen=seen,
                                           backed=backed):
                    if not isinstance(piece, str) and piece.delta and piece.delta.tool_calls: tool_called = True
                    yield piece
            # The reply is final and ends flat: add a short check-in. Not while a form or verification waits on the caller.
            if (not tool_called and ends_flat(''.join(said), text) and verification.status != 'in_progress'
                    and not any(f['status'] == 'open' for f in self.workflow.forms.values())):
                async for piece in self._check_in(ctx, said, allowed, seen, backed):
                    yield piece
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.publish({'type': 'error', 'message': 'The language model failed. Check your OpenAI key and quota.'})
            yield "Sorry, I'm having trouble right now. Please try again in a moment."
        finally:
            # Compatibility path: regex on what the agent actually said, even if it was interrupted.
            before = self.workflow.idv.status
            self.workflow.idv.observe_agent(''.join(said))
            if any(FOLLOWUP_OFFER.search(line) for line in said): self.followup_offered = True
            if CALLBACK_OFFER.search(AUDIO_TAG.sub('', ''.join(said))):
                self.callback_offered = turn_id
                if self.workflow.loan_stage == 'confirmed' and self.workflow.loan_offered is None:
                    self.workflow.loan_offered = self.workflow.turn
            self.asked_loan_kind = bool(LOAN_KIND_ASKED.search(''.join(said)))
            if self.workflow.idv.status != before:
                task = asyncio.ensure_future(self.workflow.auth_changed())
                self.pending.add(task)
                task.add_done_callback(self.pending.discard)

    def box_to_ask(self):
        """The customer number box, if it's open and the Worker hasn't asked for it yet."""
        return next((f for f, v in self.workflow.forms.items() if v['status'] == 'open' and v.get('kind') == 'customer_number'
                     and not v.get('asked')), None)

    @staticmethod
    async def _one(text):
        yield text

    async def _check_in(self, ctx, said, allowed, seen, backed):
        """One short check-in in the agent's own words, written by a second call without tools and spoken after a flat
        reply. It goes through the same guard; anything that isn't a short, new question or invitation is dropped."""
        ctx = ctx.copy()
        ctx.add_message(role='assistant', content=AUDIO_TAG.sub('', ''.join(said)).strip())
        earlier = ' '.join(f'"{line}"' for line in self.check_ins[-4:])
        ctx.add_message(role='system', content=CHECK_IN + (f' Earlier check-ins: {earlier}' if earlier else ''))
        try:
            text = await self.write_check_in(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            return  # The reply has been said; a missing check-in is better than an error line.
        line = usable_check_in(text, self.check_ins, allowed)
        if not line: return
        self.check_ins.append(line)
        lead = '' if not said or said[-1][-1:].isspace() else ' '

        async def one():
            yield lead + line
        async for piece in guarded(one(), allowed, self.publish, said=said, seen=seen, backed=backed):
            yield piece

    async def write_check_in(self, ctx):
        """The session's model, called without tools."""
        activity = self._get_activity_or_raise()
        text = ''
        async with activity.llm.chat(chat_ctx=ctx, conn_options=activity.session.conn_options.llm_conn_options) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.content: text += chunk.delta.content
        return text

    async def _run(self, tool, **args):
        verification, opening = self.workflow.idv, self.opening
        self.workflow.acknowledged = any(EMPATHY.search(line) for line in self.turn_seen[1])
        result = await self.workflow.call(tool, **args)
        if opening and verification.status == 'in_progress' and await verification.take_volunteered_name(opening[1]):
            self.opening = None
            self.fresh[opening[0]] = verification.last
            await self.workflow.auth_changed()
            result['next'] = (f"The full name the caller already gave checked out, so don't ask for it. "
                              f"Ask for their {idv.STEP_LABEL[verification.step]}.")
        if 'final_response_within_days' in result or 'response_due_days' in result:  # A new complaint or hardship notice: acknowledge once.
            felt = any(EMPATHY.search(line) for line in self.turn_seen[1])
            result['next'] = ("You've already acknowledged them this turn, so don't again. " if felt else
                              "Start with a brief, genuine acknowledgement of how they feel. ") + result['next']
        return json.dumps(result)

    @function_tool()
    async def begin_identity_verification(self, context: RunContext, reason: str) -> str:
        """Start identity verification when the caller wants something from their own records: their balances,
        transactions, loans, applications or cases. Never for someone else's account, or for general rates, products, fees or how things work.

        Args:
            reason: What the caller wants, in a few words.
        """
        return await self._run('begin_identity_verification', reason=reason)

    @function_tool()
    async def pause_identity_verification(self, context: RunContext, reason: str) -> str:
        """Pause identity verification when the caller firmly declines or no longer wants the thing that needed it,
        e.g. they only wanted general information. Attempts are kept. If they later want to continue,
        call begin_identity_verification to resume.

        Args:
            reason: What the caller wants instead, in a few words.
        """
        return await self._run('pause_identity_verification', reason=reason)

    @function_tool()
    async def get_account_summary(self, context: RunContext) -> str:
        """Balances and available funds for the verified caller's accounts and cards."""
        return await self._run('get_account_summary')

    @function_tool()
    async def get_recent_transactions(self, context: RunContext, search: str = '', account_id: str = '', limit: int = 5) -> str:
        """Recent transactions for the verified caller, newest first.

        Args:
            search: Optional merchant or description text to filter by, e.g. "Coles".
            account_id: Optional account ID from get_account_summary.
            limit: How many to return, 1 to 10.
        """
        return await self._run('get_recent_transactions', search=search, account_id=account_id, limit=limit)

    @function_tool()
    async def get_transaction_details(self, context: RunContext, transaction_id: str) -> str:
        """Details of one transaction, including identical same-day charges and existing cases.

        Args:
            transaction_id: The transaction ID from get_recent_transactions.
        """
        return await self._run('get_transaction_details', transaction_id=transaction_id)

    @function_tool()
    async def get_loans(self, context: RunContext) -> str:
        """The verified caller's existing loans: balance, rate, repayment and next due date."""
        return await self._run('get_loans')

    @function_tool()
    async def get_loan_applications(self, context: RunContext) -> str:
        """Status and next step of the verified caller's loan applications."""
        return await self._run('get_loan_applications')

    @function_tool()
    async def get_case_status(self, context: RunContext, reference: str = '') -> str:
        """The verified caller's complaints, disputes, hardship and other cases.

        Args:
            reference: Optional case reference to look up; leave empty for all.
        """
        return await self._run('get_case_status', reference=reference)

    @function_tool()
    async def lodge_dispute(self, context: RunContext, transaction_id: str,
                            dispute_type: Literal['duplicate', 'incorrect_amount', 'not_received', 'unauthorised', 'scam', 'other'],
                            description: str) -> str:
        """Lodge a dispute or suspected fraud on a transaction the caller has confirmed.

        Args:
            transaction_id: The confirmed transaction ID.
            dispute_type: unauthorised means the caller didn't make it; scam means they were tricked into making it.
            description: What happened, in the caller's account, without card numbers or codes.
        """
        return await self._run('lodge_dispute', transaction_id=transaction_id, dispute_type=dispute_type, description=description)

    @function_tool()
    async def search_banking_knowledge(self, context: RunContext, query: str) -> str:
        """Search reviewed banking knowledge: products, rates, fees, processes, rights, regulation and policies.
        A short "let me look that up" line is spoken automatically the moment you call it.

        Args:
            query: The question in plain words.
        """
        return await self._run('search_banking_knowledge', query=query)

    @function_tool()
    async def estimate_loan_repayment(self, context: RunContext, amount: float, annual_rate_percent: float, years: int,
                                      frequency: Literal['weekly', 'fortnightly', 'monthly'] = 'monthly') -> str:
        """Estimate principal-and-interest repayments. Estimates only, never an offer.

        Args:
            amount: Loan amount in dollars.
            annual_rate_percent: Interest rate, e.g. 6.09, from the caller or the knowledge base.
            years: Loan term in years.
            frequency: Repayment frequency. Monthly unless they asked for another; don't ask first.
        """
        return await self._run('estimate_loan_repayment', amount=amount, annual_rate_percent=annual_rate_percent,
                               years=years, frequency=frequency)

    @function_tool()
    async def record_hardship_notice(self, context: RunContext,
                                     cause: Literal['job_loss', 'reduced_income', 'illness_or_injury', 'relationship_breakdown',
                                                    'natural_disaster', 'family_or_carer', 'other'],
                                     situation: str, affected_products: str, expected_duration: str, support_wanted: str) -> str:
        """Record a financial hardship notice from what the caller has already said. For anything they haven't mentioned,
        pass "unknown" and never ask for it: the hardship team follows up. Calling it again updates this call's notice.
        Record once they're verified, or without verification only if they decline it or need to go.

        Args:
            cause: The main cause, or "other" if they haven't said why.
            situation: A short summary of what they told you.
            affected_products: The loan or repayments they mentioned, or "unknown".
            expected_duration: How long they said it might last, or "unknown".
            support_wanted: What they said would help, or "unknown".
        """
        return await self._run('record_hardship_notice', cause=cause, situation=situation, affected_products=affected_products,
                               expected_duration=expected_duration, support_wanted=support_wanted)

    @function_tool()
    async def lodge_complaint(self, context: RunContext,
                              category: Literal['fees_and_charges', 'service', 'delay', 'product', 'decision', 'hardship',
                                                'dispute_handling', 'privacy', 'other'],
                              summary: str, desired_outcome: str = '') -> str:
        """Record a complaint. Calling it again updates this call's complaint. Works before verification.

        Args:
            category: The closest category.
            summary: What the caller is unhappy about, in a sentence or two; on an update, everything so far.
            desired_outcome: What they want to happen, if they said.
        """
        return await self._run('lodge_complaint', category=category, summary=summary, desired_outcome=desired_outcome)

    @function_tool()
    async def request_human_followup(self, context: RunContext,
                                     reason: Literal['lending_decision', 'complex_dispute', 'hardship_assessment', 'fraud_or_security',
                                                     'formal_complaint', 'unsupported_request', 'verification_failed', 'other'],
                                     summary: str, urgency: Literal['normal', 'urgent'] = 'normal') -> str:
        """Record a request for a human to follow up, after the caller agrees. Calling it again updates this call's request. No live transfer happens.

        Args:
            reason: Why a person is needed.
            summary: A short case summary without card numbers, codes or identity details.
            urgency: urgent only for suspected fraud or safety.
        """
        return await self._run('request_human_followup', reason=reason, summary=summary, urgency=urgency)

    @function_tool()
    async def note_loan_needs(self, context: RunContext, loan_type: Literal['home_loan', 'car_loan', 'personal_loan', 'other'],
                              purpose: str = '', amount: str = '', timeframe: str = '') -> str:
        """Start finding out what a new loan is for, so a lending specialist knows before calling back. Call it once you know
        the loan type, with anything else they've actually said; the Worker records their answers to the questions after that
        itself. Call it again only if they correct something. It says what to ask next.

        Args:
            loan_type: The kind of loan. Refinancing an existing loan is other, with purpose refinance.
            purpose: What it's for, in a few words, e.g. first home, next home, investment property, used car.
            amount: Roughly how much, as they said it. Leave empty if they haven't said.
            timeframe: When, as they said it. Leave empty if they haven't said.
        """
        return await self._run('note_loan_needs', loan_type=loan_type, purpose=purpose, amount=amount, timeframe=timeframe)

    @function_tool()
    async def open_callback_form(self, context: RunContext,
                                 purpose: Literal['home_loan', 'refinance', 'personal_loan', 'car_loan', 'other']) -> str:
        """Show the lending-specialist callback form on the caller's screen, only after they agree.

        Args:
            purpose: What the callback is about.
        """
        if self.callback_offered in (None, self.turn_seen[0]):  # Explained in an earlier turn, so the caller could say yes.
            return json.dumps({'error': 'not_offered', 'next': NOT_OFFERED})
        return await self._run('open_callback_form', purpose=purpose)


    @function_tool()
    async def open_contact_form(self, context: RunContext,
                                reason: Literal['hardship', 'dispute', 'complaint', 'account', 'other']) -> str:
        """Show a short form on the caller's screen for their name and phone number, so the team can call back a caller
        who couldn't be verified.

        Args:
            reason: What the call back is about.
        """
        return await self._run('open_contact_form', reason=reason)


TAKE_YOUR_TIME = 'No rush, take your time.'


def backend_instructions(packet, workflow):
    """Only the web backend (a server SDK call, so no participant) may report form outcomes.
    The browser token can't publish data, so the caller can't forge these."""
    if packet.participant is not None or packet.topic != EVENTS_TOPIC: return None
    try: event = json.loads(packet.data)
    except ValueError: return None
    if not isinstance(event, dict) or event.get('type') != 'form_event': return None
    return event, workflow.form_event(event)


def voice_settings():
    """Eleven v4 only takes stability: lower is more expressive, higher steadier but flatter.
    Speed applies to older models only; v4 pacing comes from punctuation."""
    speed = os.environ.get('ELEVENLABS_SPEED')
    return elevenlabs.VoiceSettings(stability=float(os.environ.get('ELEVENLABS_STABILITY', '0.5')), similarity_boost=0.75,
                                    speed=float(speed) if speed else elevenlabs.tts.NOT_GIVEN)


# Nova-3 keyterm prompting: bank, product and process words callers say. Customer details are left out, since boosting them
# biases identity answers towards a match; "Alice" is the one demo exception, as speech-to-text kept hearing it as "Ellis".
KEYTERMS = ['Westpac', 'Alice', 'Westpac Protect', 'SafeCall', 'eSaver', 'Everyday Choice', 'Altitude Visa', 'Low Rate Visa', 'PartPay',
            'home loan', 'car loan', 'personal loan', 'offset', 'redraw', 'comparison rate', 'repayment', 'hardship',
            'AFCA', 'chargeback', 'dispute', 'scam', 'BPAY', 'PayID', 'Osko', 'BSB', 'direct debit']


def fixed_prompt_cached(system):
    """Every system message reaches Claude as part of the system prompt, and the plugin marks the last for caching. That's
    the Worker's note, new every turn, so the mark moves to the first: the fixed instructions, which then stay cached."""
    if isinstance(system, list) and len(system) > 1 and 'cache_control' in system[-1]:
        system[0]['cache_control'] = system[-1].pop('cache_control')
    return system


def claude_llm(model):
    client = anthropic_sdk.AsyncAnthropic()
    create = client.messages.create

    async def cached_create(**kwargs):
        fixed_prompt_cached(kwargs.get('system'))
        return await create(**kwargs)

    client.messages.create = cached_create
    # Strict schemas make a choice with a default ("frequency": monthly) nullable but keep its string-only enum, which Claude
    # rejects; plain optional parameters work.
    return anthropic.LLM(model=model, client=client, caching='ephemeral', max_tokens=300, _strict_tool_schema=False)


def make_llm():
    """The conversation model from LIVE_LLM_MODEL: an OpenAI model, or a Claude model such as claude-haiku-4-5."""
    model = os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini')
    if model.startswith('claude'): return claude_llm(model)
    return openai.LLM(model=model, store=False, reasoning_effort='none', max_completion_tokens=300)


def make_session():
    return AgentSession(
        stt=deepgram.STT(model=os.environ.get('DEEPGRAM_MODEL', 'nova-3'), language='en', smart_format=True,
                         keyterm=KEYTERMS, mip_opt_out=True),
        llm=make_llm(),
        tts=elevenlabs.TTS(api_key=os.environ['ELEVENLABS_API_KEY'], voice_id=os.environ['ELEVENLABS_VOICE_ID'],
                          model=TTS_MODEL, voice_settings=voice_settings()),
        turn_handling={
            'turn_detection': inference.TurnDetector(version='v1', local_fallback=False),
            'endpointing': {'min_delay': 0.3, 'max_delay': 2.5},
            'interruption': {'enabled': True, 'mode': 'vad', 'min_duration': 0.5,
                             'resume_false_interruption': True},
            'preemptive_generation': {'enabled': True},
        },
    )


def background_audio():
    """A faint office room, and keyboard typing while the agent works out a reply. Volumes from .env; 0 turns one off."""
    ambient, typing = float(os.environ.get('AMBIENT_VOLUME', '0.05')), float(os.environ.get('TYPING_VOLUME', '0.12'))
    if not ambient and not typing: return None
    return BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=ambient, fade_in=2) if ambient else None,
        thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=typing, probability=.5, fade_in=.3, fade_out=.4),
                        AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=typing, probability=.5, fade_in=.3, fade_out=.4)]
        if typing else None)


class CallLog:
    """Per-call timings and the agent's side of the conversation, for diagnosing latency and repeated replies.
    The caller's words are never written: only that they spoke, and how long the pipeline took."""

    def __init__(self, room):
        LOGS.mkdir(exist_ok=True)
        self.path, self.started = LOGS / f"call-{time.strftime('%Y%m%d-%H%M%S')}-{room[-6:]}.jsonl", time.monotonic()

    def write(self, kind, **data):
        try:
            with self.path.open('a') as f: f.write(json.dumps({'t': round(time.monotonic() - self.started, 3), 'kind': kind, **data}) + '\n')
        except OSError: pass


server = AgentServer(host='127.0.0.1', port=0, num_idle_processes=0, log_level='WARN')


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    # SDK debug records may include transcript text. Suppress SDK logs for this demo;
    # sanitized errors and state are delivered explicitly below.
    logging.getLogger('livekit').setLevel(logging.CRITICAL)
    await ctx.connect()
    session = make_session()
    tasks = set()
    turn = {'stopped': None}
    log = CallLog(ctx.room.name)
    background = None

    @session.on('metrics_collected')
    def metrics(event: MetricsCollectedEvent):
        m = event.metrics
        if m.type == 'eou_metrics':
            log.write('eou', end_of_utterance=round(m.end_of_utterance_delay, 3), transcription=round(m.transcription_delay, 3),
                      turn_completed=round(m.on_user_turn_completed_delay, 3), speech=m.speech_id)
        elif m.type == 'llm_metrics':
            log.write('llm', ttft=round(m.ttft, 3), duration=round(m.duration, 3), prompt_tokens=m.prompt_tokens,
                      cached=m.prompt_cached_tokens, cancelled=m.cancelled, speech=m.speech_id)
        elif m.type == 'tts_metrics':
            log.write('tts', ttfb=round(m.ttfb, 3), cancelled=m.cancelled, speech=m.speech_id)

    @session.on('speech_created')
    def speech_created(event):
        log.write('speech_created', source=event.source, user_initiated=event.user_initiated, speech=event.speech_handle.id)

    @session.on('conversation_item_added')
    def item_added(event):
        item = event.item
        if getattr(item, 'role', None) == 'assistant':
            log.write('agent_said', text=item.text_content, interrupted=item.interrupted)
        elif getattr(item, 'role', None) == 'user':
            log.write('caller_turn', words=len((item.text_content or '').split()))

    @session.on('agent_false_interruption')
    def false_interruption(event):
        log.write('false_interruption', resumed=event.resumed)

    async def publish(payload):
        await ctx.room.local_participant.publish_data(json.dumps(payload).encode(), reliable=True, topic='evidence')

    def send(payload):
        task = asyncio.create_task(publish(payload))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    safe_publish = send_async(publish)
    workflow = banking.Workflow.create(ctx.room.name, AGENT_NAME, idv.OpenAIJudge(), publish=safe_publish)
    workflow.hold_box = True  # The customer number box comes up once the agent has finished asking for it.

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    @session.on('user_state_changed')
    def user_state(event):
        send({'type': 'user_state', 'state': event.new_state})
        log.write('caller', state=event.new_state)
        if event.old_state == 'speaking' and event.new_state == 'listening':
            turn['stopped'] = time.monotonic()
        elif event.new_state == 'speaking':
            turn['stopped'] = None

    @session.on('agent_state_changed')
    def agent_state(event):
        send({'type': 'agent_state', 'state': event.new_state})
        if event.new_state == 'listening' and workflow.held_box: spawn(workflow.show_box())  # The whole reply has finished.
        # End of the customer's speech to the agent's first audio, measured in this process.
        if event.new_state == 'speaking' and turn['stopped'] is not None:
            send({'type': 'latency', 'ms': round((time.monotonic() - turn['stopped']) * 1000)})
            log.write('first_audio', after_caller_stopped=round(time.monotonic() - turn['stopped'], 3))
            turn['stopped'] = None

    @session.on('error')
    def error(event):
        send({'type': 'error', 'message': 'A voice provider failed. Check your API configuration and quota, then reconnect.'})

    @ctx.room.on('data_received')
    def backend_event(packet: rtc.DataPacket):
        handled = backend_instructions(packet, workflow)
        if handled is None: return
        event, instructions = handled
        send({'type': 'form', 'action': 'ack', 'form_id': event.get('form_id'), 'status': event.get('status'), 'handled': bool(instructions)})
        if event.get('status') == 'typing':
            if workflow.typing() and session.agent_state == 'speaking':  # Still talking as they start: stop and let them type.
                session.interrupt()
                session.say(TAKE_YOUR_TIME, add_to_chat_ctx=True)
            return
        spawn(workflow.auth_changed())  # A typed customer number moves verification on, or brings the box back.
        if instructions: session.generate_reply(instructions=instructions)

    async def cleanup():
        workflow.audit('session_closed', {})
        if background: await background.aclose()
        for task in list(tasks): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    ctx.add_shutdown_callback(cleanup)
    customer_audio = asyncio.Event()

    @ctx.room.on('track_subscribed')
    def subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO: customer_audio.set()

    await session.start(agent=VoiceAgent(workflow, safe_publish), room=ctx.room, record=False,
                        room_options=room_io.RoomOptions(text_input=False, video_input=False,
                            text_output=room_io.TextOutputOptions(sync_transcription=True),
                            audio_input=room_io.AudioInputOptions(pre_connect_audio=False),
                            delete_room_on_close=True))
    background = background_audio()
    if background: await background.start(room=ctx.room, agent_session=session)
    await ctx.room.local_participant.set_attributes({'evidence.ready': 'true'})
    workflow.audit('session_started', {'assistant': AGENT_NAME})
    # The browser publishes its microphone once it sees the ready flag; greet after that,
    # so the customer hears the whole greeting.
    try:
        await asyncio.wait_for(customer_audio.wait(), timeout=30)
        session.say(GREETING, allow_interruptions=False)  # The AI disclosure is always heard in full.
    except asyncio.TimeoutError:
        pass

    async def expire():
        await asyncio.sleep(600)
        await session.aclose()
        ctx.shutdown(reason='Demo session time limit')

    task = asyncio.create_task(expire())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def send_async(publish):
    async def safe(payload):
        try: await publish(payload)
        except Exception: pass  # A dropped UI update must never interrupt speech.
    return safe


if __name__ == '__main__':
    cli.run_app(server)
