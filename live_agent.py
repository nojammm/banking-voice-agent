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
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, cli, function_tool, inference, llm, room_io, stt
from livekit.plugins import deepgram, elevenlabs, openai
import banking
import engine
import idv
from live_voice import AGENT_NAME, EVENTS_TOPIC

GREETING = ("Hi there, you're speaking with an AI assistant. Just so you know, this is a demo bank with "
            "fictional accounts. So… what can I help you with today?")
# Spoken by the knowledge tool itself the moment a search starts, so the wait is never silent.
LOOKUP_LINES = ("Let me look that up for you.", "Sure, let me just check that.", "Good question… let me check.",
                "One sec, I'll find that for you.")

TTS_MODEL = os.environ.get('ELEVENLABS_LIVE_MODEL', 'eleven_v4_turbo')
# ElevenLabs v3/v4 perform bracketed audio tags; Flash and older models read them aloud.
AUDIO_TAGS = TTS_MODEL.startswith(('eleven_v3', 'eleven_v4'))
PROMPTS = Path(__file__).resolve().parent / 'prompts'
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
- Use one when the feeling is real: warmth when something checks out or gets sorted, sympathy when they're struggling, a chuckle when they joke. Most turns need none, and never use more than two in a turn.
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
RECORD_TOOLS = {'record_hardship_notice', 'lodge_complaint', 'request_human_followup', 'lodge_dispute'}
UNBACKED = ("Nothing was recorded or updated in this turn: no record tool has been called since the caller last spoke. "
            "If what they just said needs recording, call the tool now, saying nothing before the call: a new complaint or hardship "
            "notice, or details for a record made earlier in this call (call the tool that made it again with everything so far). "
            "Otherwise reply without saying you recorded, noted, added or updated anything.")


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


async def guarded(stream, allowed, publish, audio_tags=None, said=None, seen=None, backed=True, flags=None):
    """Pass tool calls through untouched; stop speech that states a number nobody supplied.

    Speech goes out a sentence at a time. A question, or an announcement that a record is being made, waits for the
    reply to end; if the reply calls a tool, it and anything after it are dropped, so the agent never asks something
    and then answers it from the result, or says "I'm lodging it now" before a result that says otherwise. A clause of
    an announcement that isn't one ("…, and I can see why that's frustrating") is still said. A sentence
    already said this turn (seen), or one close to a line said before the tool call, isn't said again. After an
    unsupported number the reply goes quiet, but its tool calls still go through; the fallback line is said only if
    there were none (the reply after the tool result speaks instead).

    Unless a record tool ran this turn (backed), a claim that a record was updated waits too. If the reply ends without
    a tool call, the claim is never said: with flags, nothing held is said and flags['unbacked'] asks the caller to
    regenerate; without, only the claim sentences are left out."""
    audio_tags = AUDIO_TAGS if audio_tags is None else audio_tags
    pending, held, tool_called, spoken, blocked = '', [], False, False, False
    said = [] if said is None else said
    seen = set() if seen is None else seen
    earlier = list(seen)  # Said in this turn before a tool call.

    def take(text):
        ready = []
        for sentence in sentences(text):
            if (held or sentence.rstrip().endswith('?') or ANNOUNCE.search(sentence)
                    or (not backed and claims_record(sentence))): held.append(sentence)
            else: ready.append(sentence)
        return ready

    def salvage(pieces):
        """With a tool call: keep the parts of a held announcement that aren't one
        ("I can log that now, and I can see why it's frustrating." -> "I can see why it's frustrating.")."""
        out = []
        for piece in pieces:
            if piece.rstrip().endswith('?') or not ANNOUNCE.search(piece): break  # Later lines may depend on the result.
            for clause in re.split(r",\s*(?:and|but|so)\s+|;\s*|\s+[—–]\s+|\s+but\s+", piece.strip()):
                clause = clause.strip(' ,.!…')
                if len(clause.split()) >= 3 and not ANNOUNCE.search(clause) and not claims_record(clause):
                    out.append(clause[0].upper() + clause[1:] + '. ')
        return out

    def unclaimed(pieces):
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
                if chunk.delta and chunk.delta.tool_calls: tool_called = True
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


def recorded_this_turn(chat_ctx):
    """Whether a record tool has been called since the caller last spoke."""
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == 'user': return False
        if isinstance(item, llm.FunctionCall) and item.name in RECORD_TOOLS: return True
    return False


class VoiceAgent(Agent):
    def __init__(self, workflow, publish):
        super().__init__(instructions=build_prompt())
        self.workflow, self.publish = workflow, publish
        self.raw_finals, self.seen, self.fresh, self.pending = [], set(), {}, set()
        self.current_said, self.last_lookup, self.opening, self.turn_seen = [], None, None, (None, set())

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if isinstance(event, stt.SpeechEvent):
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                    # The ID&V matcher needs the words as spoken; everything downstream sees the redacted copy.
                    self.raw_finals.append(event.alternatives[0].text)
                for alternative in event.alternatives:
                    alternative.text = redact(alternative.text)
            yield event

    async def transcription_node(self, text, model_settings):
        async for chunk in display_text(Agent.default.transcription_node(self, text, model_settings)):
            yield chunk

    async def on_user_turn_completed(self, turn_ctx, new_message):
        raw = ' '.join(self.raw_finals).strip() or new_message.text_content or ''
        self.raw_finals.clear()
        self.seen.add(new_message.id)
        self.workflow.new_turn()
        verification = self.workflow.idv
        # A name said before verification starts counts if a tool starts verification in this same turn.
        self.opening = (new_message.id, raw) if verification.status == 'not_started' else None
        if verification.status not in ('in_progress', 'paused'): return
        step = verification.step
        result = await verification.answer(raw)
        if result is None: return  # Paused and not an answer: an ordinary turn.
        self.fresh[new_message.id] = verification.last
        if result.outcome != 'no_answer':
            # The model never sees identity answers; it only learns the Worker's outcome.
            new_message.content = [f'[The caller answered the {idv.STEP_LABEL[step]} question. The Worker withheld the answer.]']
        # Always changing this turn's context stops a preemptive draft from being reused.
        turn_ctx.add_message(role='system', content='The Worker processed this turn for identity verification.')
        await self.workflow.auth_changed()

    async def llm_node(self, chat_ctx, tools, model_settings):
        last = _last_user(chat_ctx)
        verification = self.workflow.idv
        pending = last is not None and last.id not in self.seen
        if pending and (verification.status == 'in_progress' or verification.might_answer(last.text_content or '')):
            return  # A preemptive draft during ID&V would send the raw answer before the Worker has handled it.
        text = (last.text_content or '') if last is not None else ''
        if last is not None and last.id not in self.fresh and engine.ATTACK.search(text):
            await self.publish({'type': 'guard', 'reason': 'blocked_injection'})
            yield DECLINE['blocked_injection']
            return
        ctx = chat_ctx.copy()
        ctx.add_message(role='system', content=self.workflow.state_note(self.fresh.get(last.id) if last is not None else None))
        allowed = allowed_numbers(ctx, self.workflow.facts)
        said = self.current_said = []
        turn_id = last.id if last is not None else None
        if self.turn_seen[0] != turn_id: self.turn_seen = (turn_id, set())  # Sentences said so far this turn.
        seen = self.turn_seen[1]
        backed, flags = recorded_this_turn(chat_ctx), {}
        try:
            async for piece in guarded(Agent.default.llm_node(self, ctx, tools, model_settings), allowed, self.publish, said=said, seen=seen,
                                       backed=backed, flags=flags):
                yield piece
            if flags.get('unbacked'):  # Claimed a record changed with no tool: one retry, told to call the tool or not claim it.
                already = ' '.join(AUDIO_TAG.sub('', ''.join(said)).split())
                ctx.add_message(role='system', content=UNBACKED + (f' You already said this turn: "{already}" Carry on from there '
                                                                   "without repeating or rephrasing it." if already else ''))
                async for piece in guarded(Agent.default.llm_node(self, ctx, tools, model_settings), allowed, self.publish, said=said, seen=seen,
                                           backed=backed):
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
            if self.workflow.idv.status != before:
                task = asyncio.ensure_future(self.workflow.auth_changed())
                self.pending.add(task)
                task.add_done_callback(self.pending.discard)

    async def _run(self, tool, **args):
        verification, opening = self.workflow.idv, self.opening
        result = await self.workflow.call(tool, **args)
        if opening and verification.status == 'in_progress' and await verification.take_volunteered_name(opening[1]):
            self.opening = None
            self.fresh[opening[0]] = verification.last
            await self.workflow.auth_changed()
            result['next'] = (f"The full name the caller already gave checked out, so don't ask for it. "
                              f"Ask for their {idv.STEP_LABEL[verification.step]}.")
        if tool == 'lodge_complaint' and 'final_response_within_days' in result:  # A new complaint: acknowledge once.
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
        A short "let me look that up" line is spoken automatically while it runs.

        Args:
            query: The question in plain words.
        """
        self.announce_lookup(context.session)
        return await self._run('search_banking_knowledge', query=query)

    def announce_lookup(self, session):
        """Fill the retrieval wait at once, unless the model already spoke this turn."""
        if ''.join(self.current_said).strip(): return
        self.last_lookup = random.choice([line for line in LOOKUP_LINES if line != self.last_lookup])
        self.current_said.append(self.last_lookup)
        session.say(self.last_lookup, allow_interruptions=True, add_to_chat_ctx=True)

    @function_tool()
    async def estimate_loan_repayment(self, context: RunContext, amount: float, annual_rate_percent: float, years: int,
                                      frequency: Literal['weekly', 'fortnightly', 'monthly'] = 'monthly') -> str:
        """Estimate principal-and-interest repayments. Estimates only, never an offer.

        Args:
            amount: Loan amount in dollars.
            annual_rate_percent: Interest rate, e.g. 6.09, from the caller or the knowledge base.
            years: Loan term in years.
            frequency: Repayment frequency.
        """
        return await self._run('estimate_loan_repayment', amount=amount, annual_rate_percent=annual_rate_percent,
                               years=years, frequency=frequency)

    @function_tool()
    async def record_hardship_notice(self, context: RunContext,
                                     cause: Literal['job_loss', 'reduced_income', 'illness_or_injury', 'relationship_breakdown',
                                                    'natural_disaster', 'family_or_carer', 'other'],
                                     situation: str, affected_products: str, expected_duration: str, support_wanted: str) -> str:
        """Record a financial hardship notice. Calling it again updates this call's notice. Works before verification.

        Args:
            cause: The main cause.
            situation: A short summary of what happened.
            affected_products: Which repayments are affected, e.g. "home loan".
            expected_duration: How long the caller expects it to last, or "unknown".
            support_wanted: What the caller says would help, or "unsure".
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
    async def open_callback_form(self, context: RunContext,
                                 purpose: Literal['home_loan', 'refinance', 'personal_loan', 'car_loan', 'other']) -> str:
        """Show the lending-specialist callback form on the caller's screen, only after they agree.

        Args:
            purpose: What the callback is about.
        """
        return await self._run('open_callback_form', purpose=purpose)


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
    return elevenlabs.VoiceSettings(stability=float(os.environ.get('ELEVENLABS_STABILITY', '0.4')), similarity_boost=0.75,
                                    speed=float(speed) if speed else elevenlabs.tts.NOT_GIVEN)


def make_session():
    return AgentSession(
        stt=deepgram.STT(model=os.environ.get('DEEPGRAM_MODEL', 'nova-3'), language='en', smart_format=True,
                         keyterm=['Westpac', 'PartPay', 'offset', 'redraw'], mip_opt_out=True),
        llm=openai.LLM(model=os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini'), store=False,
                       reasoning_effort='none', max_completion_tokens=300),
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

    async def publish(payload):
        await ctx.room.local_participant.publish_data(json.dumps(payload).encode(), reliable=True, topic='evidence')

    def send(payload):
        task = asyncio.create_task(publish(payload))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    safe_publish = send_async(publish)
    workflow = banking.Workflow.create(ctx.room.name, AGENT_NAME, idv.OpenAIJudge(), publish=safe_publish)

    @session.on('user_state_changed')
    def user_state(event):
        send({'type': 'user_state', 'state': event.new_state})
        if event.old_state == 'speaking' and event.new_state == 'listening':
            turn['stopped'] = time.monotonic()
        elif event.new_state == 'speaking':
            turn['stopped'] = None

    @session.on('agent_state_changed')
    def agent_state(event):
        send({'type': 'agent_state', 'state': event.new_state})
        # End of the customer's speech to the agent's first audio, measured in this process.
        if event.new_state == 'speaking' and turn['stopped'] is not None:
            send({'type': 'latency', 'ms': round((time.monotonic() - turn['stopped']) * 1000)})
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
        if instructions: session.generate_reply(instructions=instructions)

    async def cleanup():
        workflow.audit('session_closed', {})
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
