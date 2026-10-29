"""Worker-side identity verification (ID&V).

The Worker, not the conversational model, owns verification. Each step runs a field-specific
normaliser, deterministic matching first, fuzzy scoring where the field allows it, and a
constrained LLM fallback only for ambiguous cases. The Worker validates every fallback answer
and makes the final decision. Stored identity values never enter the conversation.
"""
from __future__ import annotations

import calendar
import copy
import difflib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'config/assistants.yaml'
STEP_CONFIG = {'customer_number': 'customer_number_match', 'full_name': 'name_match', 'dob': 'dob_match', 'address': 'address_match'}
STEP_LABEL = {'customer_number': 'customer number', 'full_name': 'full name', 'dob': 'date of birth', 'address': 'residential address'}
IDENTIFY = ('customer_number', 'full_name')  # Steps that can pick the record; whichever comes first does.

DEFAULTS = {
    'steps': ['customer_number', 'full_name', 'dob', 'address'],
    'max_incomplete': 3,
    'free_uncertain_per_step': 1,
    'auto_pause_after_non_answers': 2,
    'debug': True,
    'customer_number_match': {'max_attempts': 3},
    'name_match': {'high_threshold': 0.55, 'low_threshold': 0.15, 'component_floor': 0.5,
                   'llm_fallback_enabled': True, 'max_attempts': 3},
    'dob_match': {'two_digit_year_pivot': 8, 'llm_fallback_enabled': True, 'max_attempts': 2},
    'address_match': {'high_threshold': 0.75, 'low_threshold': 0.4,
                      'llm_fallback_enabled': True, 'max_attempts': 2},
}


# ---------------------------------------------------------------- configuration

def _merge(base, override, where):
    for key, value in (override or {}).items():
        if key not in base: raise ValueError(f'Unknown ID&V setting {where}{key}')
        if isinstance(base[key], dict):
            if not isinstance(value, dict): raise ValueError(f'{where}{key} must be a mapping')
            _merge(base[key], value, f'{where}{key}.')
        else:
            base[key] = value


def validate_config(cfg):
    if not cfg['steps'] or any(s not in STEP_CONFIG for s in cfg['steps']) or len(set(cfg['steps'])) != len(cfg['steps']):
        raise ValueError('steps must list distinct values from ' + ', '.join(STEP_CONFIG))
    if cfg['steps'][0] not in IDENTIFY or (cfg['steps'][0] == 'full_name' and 'customer_number' in cfg['steps']):
        raise ValueError('customer_number (or full_name, without it) must come first: it identifies the record the other steps check')
    for key in ('name_match', 'address_match'):
        low, high = cfg[key]['low_threshold'], cfg[key]['high_threshold']
        if not 0 <= low <= high <= 1: raise ValueError(f'{key}: need 0 <= low_threshold <= high_threshold <= 1')
    for key in STEP_CONFIG.values():
        if not isinstance(cfg[key]['max_attempts'], int) or cfg[key]['max_attempts'] < 1:
            raise ValueError(f'{key}.max_attempts must be a positive integer')
    for key in ('max_incomplete', 'free_uncertain_per_step', 'auto_pause_after_non_answers'):
        if not isinstance(cfg[key], int) or cfg[key] < 0: raise ValueError(f'{key} must be a whole number')
    return cfg


def load_config(assistant, path=None):
    """Defaults <- file defaults <- this assistant's overrides. Missing keys keep their default."""
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path or os.environ.get('IDV_CONFIG') or CONFIG_PATH)
    if path.exists():
        import yaml  # The worker's virtualenv ships PyYAML; nothing else reads this file.
        data = yaml.safe_load(path.read_text()) or {}
        _merge(cfg, data.get('defaults'), '')
        _merge(cfg, (data.get('assistants') or {}).get(assistant), f'assistants.{assistant}.')
    return validate_config(cfg)


# ---------------------------------------------------------------- spoken numbers

UNITS = {'zero': 0, 'oh': 0, 'o': 0, 'nought': 0, 'naught': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
         'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9}
TEENS = {'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
         'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fourty': 40, 'fifty': 50, 'sixty': 60,
        'seventy': 70, 'eighty': 80, 'ninety': 90}
ORD_UNITS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'forth': 4, 'fifth': 5, 'sixth': 6,
             'seventh': 7, 'eighth': 8, 'ninth': 9}
ORDINALS = {**ORD_UNITS, 'tenth': 10, 'eleventh': 11, 'twelfth': 12, 'thirteenth': 13, 'fourteenth': 14,
            'fifteenth': 15, 'sixteenth': 16, 'seventeenth': 17, 'eighteenth': 18, 'nineteenth': 19,
            'twentieth': 20, 'thirtieth': 30}
MONTHS = {name: i for i, names in enumerate([
    (), ('january', 'jan'), ('february', 'feb', 'febuary', 'febru'), ('march', 'mar'), ('april', 'apr'),
    ('may',), ('june', 'jun'), ('july', 'jul'), ('august', 'aug'), ('september', 'sep', 'sept'),
    ('october', 'oct'), ('november', 'nov'), ('december', 'dec')]) for name in names}
NUMBER_WORDS = set(UNITS) | set(TEENS) | set(TENS) | set(ORDINALS) | {'hundred', 'thousand', 'double', 'triple'}


@dataclass
class Num:
    digits: str
    ordinal: bool
    pos: int


@dataclass
class Word:
    text: str
    pos: int


def tokens(text):
    text = text.lower().replace('’', "'").replace("'", '')
    return re.findall(r'\d+(?:st|nd|rd|th)?|[a-z]+', text)


def _numeric(toks, i):
    return 0 <= i < len(toks) and (toks[i][0].isdigit() or (toks[i] in NUMBER_WORDS and toks[i] not in ('o', 'oh')))


def _small(toks, i):
    """A number below 100 starting at toks[i]: (value, ordinal, tokens used)."""
    t = toks[i] if i < len(toks) else None
    if t in TENS:
        n = toks[i + 1] if i + 1 < len(toks) else None
        if n in UNITS and UNITS[n] and n not in ('o', 'oh'): return TENS[t] + UNITS[n], False, 2
        if n in ORD_UNITS: return TENS[t] + ORD_UNITS[n], True, 2
        return TENS[t], False, 1
    if t in TEENS: return TEENS[t], False, 1
    if t in ORDINALS: return ORDINALS[t], True, 1
    if t in ('o', 'oh') and not (_numeric(toks, i + 1) or _numeric(toks, i - 1)):
        return 0, False, 0  # "Oh, it's…" is a filler, not a zero.
    if t in UNITS: return UNITS[t], False, 1
    return 0, False, 0


def _compound(toks, i, allow_thousand=True):
    value, ordinal, used = _small(toks, i)
    if not used: return None
    j = i + used
    if not ordinal and toks[j:j + 1] == ['hundred']:
        value, j = value * 100, j + 1
        k = j + (toks[j:j + 1] == ['and'])
        rest, ordinal, more = _small(toks, k)
        if more: value, j = value + rest, k + more
    if allow_thousand and not ordinal and toks[j:j + 1] == ['thousand']:
        value, j = value * 1000, j + 1
        k = j + (toks[j:j + 1] == ['and'])
        rest = _compound(toks, k, allow_thousand=False)
        if rest: value, ordinal, j = value + int(rest[0]), rest[1], k + rest[2]
    return str(value), ordinal, j - i


def spoken_items(text):
    """Tokens with spoken numbers grouped the way people say them.

    "nineteen eighty five" -> 19, 85; "twenty third" -> 23rd; "two thousand and two" -> 2002;
    "oh three" -> 0, 3; "double four" -> 4, 4. Pieces keep their spoken boundaries so date and
    address parsers can decide how to join them.
    """
    toks, items, i = tokens(text), [], 0
    while i < len(toks):
        t = toks[i]
        if (m := re.fullmatch(r'(\d+)(st|nd|rd|th)?', t)):
            items.append(Num(m[1], bool(m[2]), i)); i += 1; continue
        if t in ('st', 'nd', 'rd', 'th') and items and isinstance(items[-1], Num) and items[-1].pos == i - 1:
            items[-1].ordinal = True; i += 1; continue
        if t in ('double', 'triple') and i + 1 < len(toks) and toks[i + 1] in UNITS:
            for _ in range(2 if t == 'double' else 3): items.append(Num(str(UNITS[toks[i + 1]]), False, i))
            i += 2; continue
        if (found := _compound(toks, i)):
            items.append(Num(found[0], found[1], i)); i += found[2]; continue
        items.append(Word(t, i)); i += 1
    return items


# ---------------------------------------------------------------- results and fallback

@dataclass
class FieldResult:
    field: str
    outcome: str  # pass | fail | incomplete | no_answer | uncertain (probably right, can't confirm)
    method: str  # exact | fuzzy | deterministic | llm | none
    score: float | None = None
    normalized: str | None = None
    missing: list = field(default_factory=list)
    components: dict = field(default_factory=dict)
    llm: dict | None = None
    customer_id: str | None = None
    reason: str | None = None  # Why an answer didn't count, e.g. previous_step: it finished the last answer.

    def public(self, debug=True):
        data = asdict(self)
        data.pop('customer_id')
        if not debug: data.pop('normalized'); data.pop('components')
        return data


LLM_SCHEMAS = {
    'name': {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'cause', 'evidence'], 'properties': {
        'verdict': {'type': 'string', 'enum': ['same_name', 'different_name', 'not_a_name', 'uncertain']},
        'cause': {'type': 'string', 'enum': ['stt_error', 'pronunciation', 'spelling_variant', 'short_form', 'different', 'not_a_name', 'unclear']},
        'evidence': {'type': 'string'}}},
    'dob': {'type': 'object', 'additionalProperties': False, 'required': ['status', 'date', 'missing'], 'properties': {
        'status': {'type': 'string', 'enum': ['complete', 'incomplete', 'unclear']},
        'date': {'type': 'string'},
        'missing': {'type': 'array', 'items': {'type': 'string', 'enum': ['day', 'month', 'year']}}}},
    'address': {'type': 'object', 'additionalProperties': False, 'required': ['verdict', 'evidence'], 'properties': {
        'verdict': {'type': 'string', 'enum': ['same', 'different', 'uncertain']},
        'evidence': {'type': 'string'}}},
}
LLM_INSTRUCTIONS = {
    'name': ("You are a constrained second-opinion check inside a bank's identity verification. A caller's spoken "
             "name was transcribed by speech-to-text and only partly matches the record. Decide whether the "
             "difference is plausibly caused by transcription error, accent or pronunciation, a spelling variant "
             "or a common short form of the same name. A different first name or surname is a different name. "
             "If the words aren't a person's name at all (a question, a request, banking words), answer not_a_name. "
             "If you cannot tell, answer uncertain. Give one short sentence of evidence."),
    'dob': ("Normalise a caller's spoken date of birth, transcribed by speech-to-text, to YYYY-MM-DD. It may be "
            "pieces said over several turns, and speech-to-text can write a spoken year as money ('$19.91' is 1991). "
            "Australian day-month-year order is the default. Treat 'oh' as zero and 'a' as 'of'. Two-digit years 00-08 are "
            "2000s, otherwise 1900s. Never invent a missing part: use status incomplete and list what is missing. "
            "If the words can't be read as a single date, use status unclear and an empty date."),
    'address': ("You are a constrained second-opinion check inside a bank's identity verification. Decide whether a "
                "spoken address component, transcribed by speech-to-text, is plausibly the same place name as the "
                "record despite transcription or pronunciation differences. Speech-to-text often turns a name into a "
                "similar-sounding word or name ('Alice' written as 'Ellis'), so compare how the words sound, not how "
                "they're spelled. The spoken text can include the street type and suburb; compare only the part that "
                "corresponds to the component. A name that sounds clearly different, or has an extra or missing syllable ('Alison' for 'Alice'), is different. If you cannot tell, "
                "answer uncertain. Give one short sentence of evidence."),
}


class OpenAIJudge:
    """LLM fallback for ambiguous ID&V inputs. It interprets; the Worker decides."""

    def __init__(self, model=None, timeout=4.0):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(timeout=timeout, max_retries=0)
        self.model = model or os.environ.get('IDV_LLM_MODEL') or os.environ.get('LIVE_LLM_MODEL', 'gpt-5.4-mini')

    async def __call__(self, task, payload):
        response = await self.client.chat.completions.create(
            model=self.model, store=False, reasoning_effort='none', max_completion_tokens=200,
            messages=[{'role': 'system', 'content': LLM_INSTRUCTIONS[task]},
                      {'role': 'user', 'content': json.dumps(payload)}],
            response_format={'type': 'json_schema', 'json_schema': {
                'name': f'idv_{task}', 'strict': True, 'schema': LLM_SCHEMAS[task]}})
        return json.loads(response.choices[0].message.content)


async def _ask(judge, task, payload):
    """Call the fallback; any exception or timeout becomes an invalid verdict, never a pass."""
    started = time.monotonic()
    info = {'invoked': True, 'task': task}
    try:
        info['response'] = await judge(task, payload)
    except Exception as exc:  # The fallback must fail closed.
        info['error'] = type(exc).__name__
    info['latency_ms'] = round((time.monotonic() - started) * 1000)
    return info


# ---------------------------------------------------------------- names

FILLER = re.compile(
    r"\b(?:is my (?:full )?name|my (?:full |legal )?name is|my (?:full )?names|the names?|names|full name is|"
    r"it is|its|i am|im|this is|thats|that is|spelled|spelt|um+|uh+|erm|er|ah+|hmm+|yeah|yep|yes|sure|okay|ok|"
    r"so|well|right|alright|hi|hello|thanks|thank you|please|speaking|here)\b")
COMMON = set('''i me my mine you your we us want wanted need would like to a an the of for about with and or but not
no can could do does did why what how who where when is are was be been have has had help account balance loan
card money pay payment complaint complain problem issue mortgage bank transaction call speak someone person human
wait sorry hang on just give tell know dont that this there again repeat already said told agent real fraud scam
hardship dispute charge charged check if will should get got go going interest rate rates current westpac
credit savings fees fee cost general information info question asking actually instead rather loans accounts
anything something nothing never mind forget sorry'''.split())


def soundex(word):
    codes = {**dict.fromkeys('bfpv', '1'), **dict.fromkeys('cgjkqsxz', '2'), **dict.fromkeys('dt', '3'),
             'l': '4', **dict.fromkeys('mn', '5'), 'r': '6'}
    out, last = word[0], codes.get(word[0], '')
    for ch in word[1:]:
        code = codes.get(ch, '')
        if code and code != last: out += code
        if ch not in 'hw': last = code
    return (out + '000')[:4]


def similarity(a, b):
    if a == b: return 1.0
    if not a or not b: return 0.0
    score = difflib.SequenceMatcher(None, a, b).ratio()
    if len(a) > 2 and len(b) > 2 and soundex(a) == soundex(b): score = max(score, 0.85)
    return score


def name_tokens(text):
    raw = text.lower().replace('’', "'")
    words = re.findall(r"[a-z]+", FILLER.sub(' ', raw.replace("'", '')))
    joined, run = [], []
    for w in words + ['']:  # Spelled-out letters ("c a r t e r") become one word.
        if len(w) == 1: run.append(w); continue
        joined.extend([''.join(run)] if len(run) >= 3 else run); run = []
        if w: joined.append(w)
    return joined


def score_name(spoken, record):
    first, middle, last = (record.get('first_name', '') or '').lower(), (record.get('middle_name') or '').lower(), record['last_name'].lower()
    grams = spoken + [a + b for a, b in zip(spoken, spoken[1:])]
    f = max(similarity(g, first) for g in grams)
    l = max(similarity(g, last) for g in grams)
    m = max(similarity(g, middle) for g in grams) if middle and len(spoken) >= 3 else None
    weighted = 0.45 * f + 0.55 * l if m is None else 0.4 * f + 0.15 * m + 0.45 * l
    whole = max(difflib.SequenceMatcher(None, ''.join(spoken), first + last).ratio(),
                difflib.SequenceMatcher(None, ''.join(spoken), first + middle + last).ratio())
    return round(max(weighted, whole), 3), {'first': round(f, 3), 'middle': None if m is None else round(m, 3),
                                             'last': round(l, 3), 'whole': round(whole, 3)}


def full_name(record):
    return ' '.join(p for p in (record['first_name'], record.get('middle_name'), record['last_name']) if p)


def name_decision(score, parts, cfg):
    """pass at or above high_threshold (if first and last name each clear the floor), fail below
    low_threshold, otherwise ask the LLM. The floor stops "right first name, wrong surname" passing."""
    if score >= cfg['high_threshold'] and min(parts['first'], parts['last']) >= cfg['component_floor']: return 'pass'
    return 'llm' if score >= cfg['low_threshold'] else 'fail'


# Someone else's name ("my wife, Emily Carter, wants…") must never be picked out of a longer turn.
THIRD_PARTY = re.compile(r"\b(?:wife|husband|partner|mum|mom|mother|dad|father|son|daughter|brother|sister|friend|"
                         r"customer|client|she|shes|he|hes|her|his|their|behalf)\b")


def _name_like(text):
    spoken = name_tokens(text)
    common = [t for t in spoken if t in COMMON]
    return bool(spoken) and len(common) < 2 and len(common) * 2 <= len(spoken) <= 6


async def match_name(text, customers, cfg, judge=None):
    if '?' in text or re.search(r'\d', text) or not _name_like(text):
        # A name can sit in one clause of a longer turn ("I just told you, it's Emily Carter."). A clause counts
        # only on a clear match without the fallback, so a stray clause never costs an attempt.
        clauses = [c for c in re.split(r'[.,;:!?]+', text) if c.strip() and c.strip() != text.strip()]
        if not THIRD_PARTY.search(text.lower().replace('’', "'").replace("'", '')):
            for clause in clauses:
                if re.search(r'\d', clause) or not _name_like(clause): continue
                result = await match_name(clause, customers, cfg)
                if result.outcome == 'pass': return result
        return FieldResult('full_name', 'no_answer', 'none')  # A question or a different request, not a name.
    spoken = [t for t in name_tokens(text) if t not in COMMON]
    normalized = ' '.join(spoken)
    for c in customers:
        exact = {f"{c['first_name']} {c['last_name']}".lower(), full_name(c).lower()}
        if normalized in exact:
            return FieldResult('full_name', 'pass', 'exact', 1.0, normalized, customer_id=c['id'])
    if len(spoken) == 1:
        for c in customers:
            whole = difflib.SequenceMatcher(None, spoken[0], (c['first_name'] + c['last_name']).lower()).ratio()
            if whole >= 0.8: break  # One run-together token ("emilycarter") is still a full name.
            if similarity(spoken[0], c['first_name'].lower()) >= 0.8:
                return FieldResult('full_name', 'incomplete', 'deterministic', normalized=normalized, missing=['last name'])
            if similarity(spoken[0], c['last_name'].lower()) >= 0.8:
                return FieldResult('full_name', 'incomplete', 'deterministic', normalized=normalized, missing=['first name'])
    scored = sorted(((score_name(spoken, c), c) for c in customers), key=lambda x: -x[0][0])
    (score, parts), best = scored[0]
    result = FieldResult('full_name', 'fail', 'fuzzy', score, normalized, components=parts, customer_id=best['id'])
    decision = name_decision(score, parts, cfg)
    if decision == 'pass':
        result.outcome = 'pass'
    elif decision == 'llm':
        if not (cfg['llm_fallback_enabled'] and judge):
            return result  # Without a second opinion the ambiguous band fails closed.
        info = await _ask(judge, 'name', {'spoken': normalized, 'record': full_name(best), 'fuzzy_score': score})
        result.method, result.llm = 'llm', info
        verdict = _valid_name_verdict(info.get('response'))
        info['valid'] = verdict is not None
        # The fallback can also say the words weren't a name at all: that's not an attempt.
        result.outcome = {'same_name': 'pass', 'different_name': 'fail', 'not_a_name': 'no_answer'}.get(verdict, 'uncertain')
    if result.outcome != 'pass': result.customer_id = None
    return result


def _valid_name_verdict(response):
    if not isinstance(response, dict) or set(response) != {'verdict', 'cause', 'evidence'}: return None
    verdict, cause, evidence = response['verdict'], response['cause'], response['evidence']
    if verdict not in ('same_name', 'different_name', 'not_a_name', 'uncertain') or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 400:
        return None
    if verdict == 'same_name' and cause not in ('stt_error', 'pronunciation', 'spelling_variant', 'short_form'):
        return None  # A pass must name a plausible cause.
    return None if verdict == 'uncertain' else verdict


# ---------------------------------------------------------------- dates of birth

# A question about their own details ("is my birthday in March?") or about something else ("quick one, are
# your branches open?") isn't an answer, even when it contains a month or a stray number word.
QUESTION = re.compile(r"(?:^|[.,;:!]\s*|\b(?:and|so|but|oh|um|also|sorry|actually|one)\s+)"
                      r"(?:is|are|was|were|do|does|did|can|could|will|would|should|what|why|how)(?:['’]s)?\s+"
                      r"(?:my|your|you|it|its|there|the|this|that|i|we|they)\b", re.I)


CUSTOMER_NUMBER = re.compile(r'(?:CUS)?-?(\d{5})')


def normalize_customer_number(text):
    """'cus 10482', 'CUS-10482' and '10482' are the same number; anything else isn't one."""
    m = CUSTOMER_NUMBER.fullmatch(re.sub(r'\s+', '', text).upper()) if isinstance(text, str) else None
    return f'CUS-{m.group(1)}' if m else None


def match_customer_number(text, customers):
    """Typed into the box on screen, so it's exact: one record or none."""
    number = normalize_customer_number(text)
    if not number: return FieldResult('customer_number', 'no_answer', 'none', reason='format')
    found = next((c for c in customers if c['id'] == number), None)
    return FieldResult('customer_number', 'pass' if found else 'fail', 'exact', 1.0 if found else 0.0, number,
                       customer_id=found['id'] if found else None)


def is_question(text):
    return '?' in text or bool(QUESTION.search(text))


@dataclass
class DobParse:
    status: str  # ok | ambiguous | incomplete | no_answer | unparsed
    value: str | None = None
    candidates: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    parts: dict = field(default_factory=dict)  # For an incomplete date: the parts that were clear.
    month_first: bool = False  # The only reading puts the month first, which Australian callers don't do.
    assembled: bool = False  # Joined from parts said over several turns.


def _year(text, today, pivot):
    if len(text) == 2:
        year = int(text) + (2000 if int(text) <= pivot else 1900)
    elif len(text) == 4:
        year = int(text)
    else:
        return None
    return year if 1900 <= year <= today.year else None


def make_date(d, m, y, today, pivot):
    if not (1 <= len(d) <= 2 and 1 <= len(m) <= 2): return None
    year, month, day = _year(y, today, pivot), int(m), int(d)
    if year is None or not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]: return None
    value = date(year, month, day)
    return value.isoformat() if value <= today else None


def _best(found):
    if not found: return None
    top = max(score for score, _ in found)
    return sorted({value for score, value in found if score == top})


def _partitions(pieces, today, pivot):
    """Split spoken number pieces into day, month and year; DD/MM/YYYY first, as in Australia.
    Returns the readings and the order that produced them."""
    n, orders = len(pieces), {'dmy': [], 'mdy': [], 'ymd': []}
    for i in range(1, n - 1):
        for j in range(i + 1, n):
            groups = (pieces[:i], pieces[i:j], pieces[j:])
            text = [''.join(p.digits for p in g) for g in groups]
            natural = sum(len(g) == 1 for g in groups)
            for order, (d, m, y) in (('dmy', (0, 1, 2)), ('mdy', (1, 0, 2)), ('ymd', (2, 1, 0))):
                value = make_date(text[d], text[m], text[y], today, pivot)
                if value:
                    bonus = 0.5 if any(p.ordinal for p in groups[d]) else 0
                    orders[order].append((natural + bonus, value))
    for order in ('dmy', 'ymd', 'mdy'):
        if (best := _best(orders[order])): return best, order
    return [], None


def parse_dob(text, today, pivot=8):
    items = spoken_items(text)
    nums = [x for x in items if isinstance(x, Num)]
    months = {MONTHS[x.text]: x.pos for x in items if isinstance(x, Word) and x.text in MONTHS}
    if not nums and not months: return DobParse('no_answer')
    if len(months) > 1: return DobParse('unparsed')
    if months:
        (month, pos), = months.items()
        before, after = [n for n in nums if n.pos < pos], [n for n in nums if n.pos > pos]
        joined = lambda group: ''.join(p.digits for p in group)
        found = []
        if before and after:
            found += [(2, v) for v in [make_date(joined(before), str(month), joined(after), today, pivot),
                                       make_date(joined(after), str(month), joined(before), today, pivot)] if v]
        else:
            group = before or after
            for k in range(1, len(group)):
                value = make_date(joined(group[:k]), str(month), joined(group[k:]), today, pivot)
                if value: found.append(((k == 1) + (len(group) - k == 1) + 0.5 * group[0].ordinal, value))
        if (best := _best(found)): return DobParse('ok' if len(best) == 1 else 'ambiguous', best[0] if len(best) == 1 else None, best)
        group = before or after
        if not nums: return DobParse('incomplete', missing=['day', 'year'], parts={'month': month})
        if len(nums) == 1:
            value = nums[0].digits
            if len(value) <= 2 and int(value) <= 31: return DobParse('incomplete', missing=['year'], parts={'month': month, 'day': int(value)})
            year = _year(value, today, pivot)
            return DobParse('incomplete', missing=['day'], parts={'month': month, 'year': year} if year else {})
        return DobParse('unparsed')
    candidates, order = _partitions(nums, today, pivot)
    if not candidates and any(len(n.digits) > 4 for n in nums):
        singles = [Num(ch, False, n.pos) for n in nums for ch in n.digits]
        candidates, order = _partitions(singles, today, pivot)
    # Australians don't say dates month-first: a reading that only works that way ("3 19 91") is a guess, not an answer.
    if len(candidates) == 1 and order != 'mdy': return DobParse('ok', candidates[0], candidates)
    if candidates: return DobParse('ambiguous', candidates=candidates, month_first=order == 'mdy')
    if len(nums) == 2:  # "the 2nd, 1978": a day said as an ordinal and a year, no month.
        day, year = sorted(nums, key=lambda n: len(n.digits))
        if day.ordinal and int(day.digits) <= 31 and len(year.digits) == 4 and _year(year.digits, today, pivot):
            return DobParse('incomplete', missing=['month'], parts={'day': int(day.digits), 'year': int(year.digits)})
    digits = sum(len(n.digits) for n in nums)
    if digits <= 4 and len(nums) <= 2:
        if len(nums) == 1 and len(nums[0].digits) == 4 and _year(nums[0].digits, today, pivot):
            return DobParse('incomplete', missing=['day', 'month'], parts={'year': int(nums[0].digits)})
        clear = len(nums) == 1 and 1 <= int(nums[0].digits) <= 31  # A lone small number is the day ("the 2nd").
        return DobParse('incomplete', missing=['year'] if len(nums) == 2 else ['month', 'year'],
                        parts={'day': int(nums[0].digits)} if clear else {})
    return DobParse('unparsed')


def _valid_llm_date(response, today, candidates):
    if not isinstance(response, dict) or set(response) != {'status', 'date', 'missing'}: return None
    if response['status'] == 'incomplete':
        missing = response['missing']
        if isinstance(missing, list) and missing and set(missing) <= {'day', 'month', 'year'}: return ('incomplete', missing)
        return None
    if response['status'] != 'complete' or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(response['date'])): return None
    y, m, d = response['date'].split('-')
    value = make_date(d, m, y, today, 0)
    if value is None or (candidates and value not in candidates):
        return None  # The fallback may only choose between readings the Worker already found.
    return ('complete', value)


def _year_pair(a, b, today, pivot):
    """"nineteen ninety one", or "$19.91" when speech-to-text formats a spoken year as money: 19 and 91 are 1991."""
    if a.ordinal or b.ordinal or a.digits not in ('19', '20') or len(b.digits) != 2: return None
    return _year(a.digits + b.digits, today, pivot)


def _pieces(text, today, pivot):
    """The parts of a date of birth said on their own ("The fourteenth.", "I said three.", "$19.91."): day, month,
    year, and 'either' for a bare number up to 12 that could be the day or the month. None if it can't be split."""
    items = spoken_items(re.sub(r'(?<=\d),(?=\d{3}\b)', '', text))  # "$1,991"
    nums = [x for x in items if isinstance(x, Num)]
    months = {MONTHS[x.text] for x in items if isinstance(x, Word) and x.text in MONTHS}
    if len(months) > 1 or not (nums or months): return None
    parts = {'month': months.pop()} if months else {}
    for i, n in enumerate(nums):
        if len(n.digits) == 4 and not n.ordinal and _year(n.digits, today, pivot):
            parts['year'], nums = int(n.digits), nums[:i] + nums[i + 1:]
            break
        if i + 1 < len(nums) and (year := _year_pair(n, nums[i + 1], today, pivot)):
            parts['year'], nums = year, nums[:i] + nums[i + 2:]
            break
    if len(nums) == 2 and 'month' not in parts:  # "fourteen three", "the 3rd of the 14th"
        a, b = nums if not (nums[1].ordinal and not nums[0].ordinal) else nums[::-1]
        if not (1 <= int(a.digits) <= 31 and 1 <= int(b.digits) <= 12): return None
        return {**parts, 'day': int(a.digits), 'month': int(b.digits)}
    if len(nums) > 1: return None
    if nums:
        n, value = nums[0], int(nums[0].digits)
        if 'month' in parts or n.ordinal or 12 < value <= 31:
            if not 1 <= value <= 31: return None
            parts['day'] = value
        elif 1 <= value <= 12:
            parts['either'] = [value]
        elif 'year' not in parts and len(n.digits) == 2 and _year(n.digits, today, pivot):
            parts['year'] = _year(n.digits, today, pivot)  # "91"
        else:
            return None
    return parts


def _assemble(carry, new, today, pivot):
    """Join the parts of this turn with those carried from earlier turns. A bare number fills whichever of day or
    month is still open ("The fourteenth." then "I said three." is 14 March); a later part replaces an earlier one."""
    parts = {k: v for k, v in {**carry, **new}.items() if k in ('day', 'month', 'year')}
    either = list(dict.fromkeys(carry.get('either', []) + new.get('either', [])))
    if 'month' in parts: either = [e for e in either if e != parts['month']]  # "three", then "March": the same month.
    while either and ('day' in parts) != ('month' in parts):
        parts['month' if 'day' in parts else 'day'] = either.pop()
    either = either[-2:] if not ('day' in parts or 'month' in parts) else []
    assembled = bool(carry)
    if {'day', 'month', 'year'} <= set(parts):
        value = make_date(str(parts['day']), str(parts['month']), str(parts['year']), today, pivot)
        return DobParse('ok', value, [value], assembled=assembled) if value else DobParse('unparsed', assembled=assembled)
    if 'year' in parts and len(either) == 2:  # Two bare numbers and a year: which is the day is a guess.
        found = [make_date(str(d), str(m), str(parts['year']), today, pivot) for d, m in (either, either[::-1])]
        found = sorted({v for v in found if v})
        if found: return DobParse('ambiguous', candidates=found, assembled=True)
    missing = [p for p in ('day', 'month', 'year') if p not in parts]
    if either and 'year' not in parts: missing = ['year'] if len(either) == 2 else ['month', 'year']
    return DobParse('incomplete', missing=missing, parts={**parts, 'either': either} if either else parts, assembled=assembled)


def _swapped(text, value, today):
    """An all-number date read the other way round ("09/01/1999" said month-first), when both numbers could be the
    month. Only someone who knows the date can say it either way, so accepting both readings costs almost nothing."""
    items = spoken_items(text)
    if any(isinstance(x, Word) and x.text in MONTHS for x in items) or any(isinstance(x, Num) and x.ordinal for x in items): return None
    y, m, d = map(int, value.split('-'))
    if d > 12 or d == m: return None
    other = date(y, d, m)
    return other.isoformat() if other <= today else None


def _dob_known(parts):
    return {p for p in ('day', 'month', 'year') if p in parts} | {('either', e) for e in parts.get('either', [])}


async def match_dob(text, stored, cfg, judge=None, today=None, carry=None):
    """Parts of an incomplete answer carry over in components['_carry'], so the next turn can finish the date."""
    today, pivot, carry = today or date.today(), cfg['two_digit_year_pivot'], carry or {}
    parsed = parse_dob(text, today, pivot)
    question = is_question(text)
    # A question counts only if it holds a whole date ("is it 14 March 1991?"); otherwise it's not an answer.
    if parsed.status == 'no_answer' or (parsed.status not in ('ok', 'ambiguous') and question):
        return FieldResult('dob', 'no_answer', 'none')
    spoken = ' '.join(filter(None, [carry.get('said'), text]))  # The whole answer so far, for the fallback.
    if parsed.status != 'ok' and not question and (parsed.status == 'incomplete' or carry):
        pieces = _pieces(text, today, pivot)
        if pieces is None and parsed.status == 'incomplete': pieces = parsed.parts
        if pieces is not None:
            joined = _assemble(carry, pieces, today, pivot)
            if parsed.status != 'ambiguous' or (joined.status == 'ok' and parsed.month_first):
                parsed = joined  # Month-first is a guess; the parts they gave earlier settle it.
            elif joined.status == 'ok':
                parsed = DobParse('ambiguous', candidates=sorted(set(parsed.candidates) | {joined.value}), assembled=True)
    if parsed.status == 'incomplete':
        return FieldResult('dob', 'incomplete', 'deterministic', missing=parsed.missing,
                           components={'_carry': {**parsed.parts, 'said': spoken}})
    if parsed.status == 'ok':
        match = stored in (parsed.value, _swapped(text, parsed.value, today))
        return FieldResult('dob', 'pass' if match else 'fail', 'exact', normalized=parsed.value)
    result = FieldResult('dob', 'uncertain', 'llm', components={'candidates': parsed.candidates})
    if not (cfg['llm_fallback_enabled'] and judge): result.method = 'deterministic'; return result
    result.llm = info = await _ask(judge, 'dob', {'spoken': spoken})
    checked = _valid_llm_date(info.get('response'), today, parsed.candidates)
    info['valid'] = checked is not None
    if checked and checked[0] == 'incomplete':
        result.outcome, result.missing = 'incomplete', checked[1]
        if carry: result.components['_carry'] = carry  # Keep what they've said; this turn added nothing certain.
    elif checked:
        result.normalized = checked[1]
        # A month-first guess ("$19.91"), a date joined from pieces or words that don't read as one date ("ninth September
        # first") that doesn't match is more likely a mishearing.
        misheard = parsed.month_first or parsed.assembled or parsed.status == 'unparsed'
        result.outcome = 'pass' if checked[1] == stored else 'uncertain' if misheard else 'fail'
    return result


# ---------------------------------------------------------------- addresses

STREET_TYPES = {'st': 'street', 'street': 'street', 'rd': 'road', 'road': 'road', 'ave': 'avenue', 'av': 'avenue',
                'avenue': 'avenue', 'dr': 'drive', 'drive': 'drive', 'cres': 'crescent', 'crescent': 'crescent',
                'ct': 'court', 'court': 'court', 'pl': 'place', 'place': 'place', 'hwy': 'highway',
                'highway': 'highway', 'pde': 'parade', 'parade': 'parade', 'tce': 'terrace', 'terrace': 'terrace',
                'blvd': 'boulevard', 'bvd': 'boulevard', 'boulevard': 'boulevard', 'ln': 'lane', 'lane': 'lane',
                'cl': 'close', 'close': 'close', 'way': 'way', 'cct': 'circuit', 'circuit': 'circuit',
                'gr': 'grove', 'grove': 'grove', 'sq': 'square', 'square': 'square', 'esp': 'esplanade',
                'esplanade': 'esplanade'}
STATES = [('new south wales', 'NSW'), ('n s w', 'NSW'), ('nsw', 'NSW'), ('victoria', 'VIC'), ('vic', 'VIC'),
          ('queensland', 'QLD'), ('qld', 'QLD'), ('western australia', 'WA'), ('w a', 'WA'), ('wa', 'WA'),
          ('south australia', 'SA'), ('s a', 'SA'), ('sa', 'SA'), ('tasmania', 'TAS'), ('tas', 'TAS'),
          ('northern territory', 'NT'), ('n t', 'NT'), ('nt', 'NT'),
          ('australian capital territory', 'ACT'), ('a c t', 'ACT'), ('act', 'ACT')]
ADDRESS_FILLER = set('''my address is its it the at in live i um uh er yeah so slash number unit apartment apt flat
suite level postcode post code state of and its thats a we our on yes sure okay ok well'''.split())
EXACT_PARTS, FUZZY_PARTS = ('unit', 'street_number', 'postcode', 'state'), ('street_name', 'suburb')


def _address_parts(text):
    lowered = ' ' + ' '.join(tokens(text.replace('/', ' slash '))) + ' '
    state = None
    for phrase, code in STATES:
        if f' {phrase} ' in lowered:
            state = state or code
            lowered = lowered.replace(f' {phrase} ', ' ')
    groups, current, words = [], [], []
    for item in spoken_items(lowered):
        if isinstance(item, Num): current.append(item.digits); continue
        if item.text == 'slash' and current: continue
        if current: groups.append(current); current = []
        words.append(item.text)
    if current: groups.append(current)
    return groups, words, state


ADDRESS_CUES = {'unit', 'apartment', 'apt', 'flat', 'level', 'suite', 'slash', 'postcode', 'lot'}


def _dob_fragment(text):
    """The tail of a date of birth heard at the address step ("March fourteenth", "the 14th", "1991"): a month word, an
    ordinal day or only a 19xx year, with no address cue (street type, state, unit, slash, level, postcode)."""
    items = spoken_items(text.replace('/', ' slash '))
    nums = [x for x in items if isinstance(x, Num)]
    words = [x.text for x in items if isinstance(x, Word)]
    if _address_parts(text)[2] or any(w in STREET_TYPES or w in ADDRESS_CUES for w in words): return False
    month = any(w in MONTHS and (w != 'may' or nums) for w in words)
    pair = len(nums) == 2 and _year_pair(nums[0], nums[1], date.today(), 0)  # "$19.91": a year heard as money.
    year_only = bool(nums) and (pair or all(len(n.digits) == 4 and n.digits.startswith('19') for n in nums)) and \
        not [w for w in words if w not in ADDRESS_FILLER]
    return month or any(n.ordinal for n in nums) or year_only


def _candidates(group):
    return {''.join(group[i:j]) for i in range(len(group)) for j in range(i + 1, min(len(group), i + 4) + 1)}


SOUNDS_ALIKE = 0.6  # Inside the address band: a place name that sounds right gets the LLM's second opinion, never a pass.


def sounds_alike(a, b):
    """Soundex ignoring the opening vowel, which speech-to-text often swaps ("Alice" heard as "Ellis")."""
    def key(word): return ('a' if word[0] in 'aeiouy' else word[0]) + soundex(word)[1:]
    return len(a) > 2 and len(b) > 2 and key(a) == key(b)


def _gram_score(words, target):
    target = target.lower().split()
    if not words: return 0.0
    best = 0.0
    for size in {max(1, len(target) - 1), len(target), len(target) + 1}:
        for i in range(len(words) - size + 1):
            spoken, stored = ''.join(words[i:i + size]), ''.join(target)
            best = max(best, similarity(spoken, stored), SOUNDS_ALIKE if sounds_alike(spoken, stored) else 0.0)
    return round(best, 3)


def score_address(text, stored):
    """Per-component status for one spoken answer: match, mismatch, missing, or a fuzzy score."""
    groups, words, state = _address_parts(text)
    unit, number, postcode = stored.get('unit') or '', stored['number'], stored['postcode']
    parts = {}
    street = next((g for g in groups if number in _candidates(g) or (unit and unit + number in _candidates(g))), None)
    if street:
        parts['street_number'] = 'match'
        if unit:
            cands = _candidates(street)
            parts['unit'] = ('match' if unit + number in cands or unit in cands - {number} else
                             'missing' if street == [number] else 'mismatch')
    rest = [g for g in groups if g is not street]
    if any(postcode in _candidates(g) for g in rest):
        parts['postcode'] = 'match'
    else:
        parts['postcode'] = 'mismatch' if any(len(''.join(g)) == 4 for g in rest) else 'missing'
    if not street:
        guesses = [g for g in rest if len(''.join(g)) != 4]
        parts['street_number'] = 'mismatch' if guesses else 'missing'
        if unit: parts['unit'] = parts['street_number']
    parts['state'] = 'missing' if state is None else ('match' if state == stored['state'] else 'mismatch')
    content = [w for w in words if w not in ADDRESS_FILLER]
    type_at = next((i for i, w in enumerate(content) if w in STREET_TYPES), None)
    street_words = content[:type_at] if type_at is not None else content
    suburb_words = content[type_at + 1:] if type_at is not None else content
    parts['street_name'] = _gram_score(street_words, stored['street']) if street_words else 'missing'
    parts['suburb'] = _gram_score(suburb_words, stored['suburb']) if suburb_words else 'missing'
    for part in FUZZY_PARTS:  # Without a street type, unrelated words aren't evidence of a wrong name.
        if type_at is None and isinstance(parts[part], float) and parts[part] < 0.5: parts[part] = 'missing'
    return parts, {'numbers': [''.join(g) for g in groups], 'state': state, 'words': content}


def _address_verdict(info):
    response = info.get('response')
    ok = (isinstance(response, dict) and set(response) == {'verdict', 'evidence'} and
          response['verdict'] in ('same', 'different', 'uncertain') and
          isinstance(response['evidence'], str) and bool(response['evidence'].strip()))
    info['valid'] = ok
    return response['verdict'] if ok else 'uncertain'


async def match_address(text, stored, cfg, judge=None, carry=None):
    """Exact street number, unit, postcode and state; fuzzy street and suburb names.

    Components confirmed in an earlier turn carry over, so a retry only needs the missing part.
    """
    carry = dict(carry or {})
    if _dob_fragment(text):  # Speech-to-text split "1991. March fourteenth": the tail isn't an address attempt.
        return FieldResult('address', 'no_answer', 'none', reason='previous_step')
    parts, normalized = score_address(text, stored)
    required = [p for p in EXACT_PARTS + FUZZY_PARTS if p != 'unit' or stored.get('unit')]
    if all(v == 'missing' for v in parts.values()) or (is_question(text) and any(parts[p] == 'missing' for p in required)):
        return FieldResult('address', 'no_answer', 'none')  # Nothing given, or a question without a whole address.
    readable = f"numbers {' '.join(normalized['numbers']) or '-'} · state {normalized['state'] or '-'} · words {' '.join(normalized['words']) or '-'}"
    result = FieldResult('address', 'fail', 'deterministic', normalized=readable)
    status = {}
    for part in required:
        if carry.get(part) == 'match':
            status[part], result.components[part] = 'match', 'match (earlier turn)'
            continue
        value = parts[part]
        if isinstance(value, float):
            result.method = 'fuzzy'
            status[part] = ('match' if value >= cfg['high_threshold'] else
                            'ambiguous' if value >= cfg['low_threshold'] else 'mismatch')
            result.components[part] = f'{status[part]} ({value})'
        else:
            status[part] = result.components[part] = value
    ambiguous = [p for p in required if status[p] == 'ambiguous']
    if 'mismatch' not in status.values() and ambiguous:
        if cfg['llm_fallback_enabled'] and judge:
            result.method, result.llm = 'llm', {'invoked': True, 'calls': []}
            for part in ambiguous:
                info = await _ask(judge, 'address', {'component': part.replace('_', ' '), 'spoken': ' '.join(normalized['words']),
                                                     'record': stored['street' if part == 'street_name' else 'suburb']})
                verdict = _address_verdict(info)
                result.llm['calls'].append({'component': part, **info})
                status[part] = {'same': 'match', 'different': 'mismatch'}[verdict] if verdict != 'uncertain' else 'uncertain'
        else:
            status.update({p: 'mismatch' for p in ambiguous})  # No second opinion: fail closed.
    for part in required:
        if status[part] == 'match': carry[part] = 'match'
    missing = [p for p in required if status[p] == 'missing']
    if 'mismatch' in status.values(): result.outcome = 'fail'
    elif 'uncertain' in status.values(): result.outcome = 'uncertain'
    elif missing: result.outcome, result.missing = 'incomplete', [p.replace('_', ' ') for p in missing]
    else: result.outcome = 'pass'
    result.components['_carry'] = carry
    return result


# ---------------------------------------------------------------- state machine

# Volunteering one of these while paused resumes verification. Names have a strong "is this an answer?"
# gate; stray numbers could look like a date or street number, so those steps resume only on request.
RESUME_BY_ANSWER = {'full_name'}
CLARIFY = {'customer_number': 'check it and type it in again',
           'full_name': 'spell their surname', 'dob': 'say it again with the month as a word',
           'address': 'repeat the street and suburb slowly'}
ASK_PATTERNS = {
    'customer_number': re.compile(r"\bcustomer (?:number|id)\b", re.I),
    'full_name': re.compile(r"\b(?:full name|your name|first and last name|surname|last name|legal name)\b", re.I),
    'dob': re.compile(r"\b(?:date of birth|birth ?date|d\.?o\.?b|when (?:were|was) you born|birthday)\b", re.I),
    'address': re.compile(r"\b(?:residential address|home address|your address|street address|address (?:we|on file)|postcode)\b", re.I),
}
VERIFY_CONTEXT = re.compile(r"\b(?:verify|verification|confirm|identity|security|secure|make sure|check (?:that )?(?:it'?s|its) you)\b", re.I)


class Verification:
    """Worker-owned ID&V state. The model can only observe it."""

    def __init__(self, customers, config=None, judge=None, today=None, on_event=None):
        self.customers = customers
        self.cfg = config or validate_config(copy.deepcopy(DEFAULTS))
        self.judge, self.today = judge, today
        self.on_event = on_event or (lambda kind, data: None)
        self.status, self.step, self.customer_id, self.last = 'not_started', None, None, None
        self.steps = {s: {'status': 'pending', 'attempts': 0, 'incomplete': 0, 'uncertain': 0, 'asked': False, 'activated_by': None}
                      for s in self.cfg['steps']}
        self.address_carry, self.dob_carry, self.history, self.non_answers, self.paused_by = {}, {}, [], 0, None
        self.box_turns = 0  # Caller turns spoken while the customer number box waited for them.

    @property
    def verified(self):
        return self.status == 'verified'

    def customer(self):
        return next((c for c in self.customers if c['id'] == self.customer_id), None)

    def pool(self):
        """The records a name is checked against: the one the customer number found, or (name first) all of them."""
        return [self.customer()] if self.customer_id else self.customers

    def _event(self, kind, **data):
        self.on_event(kind, data)

    def _activate(self, step, source):
        self.step = step
        self.steps[step].update(status='active', activated_by=source)
        self._event('idv_step_active', step=step, source=source)

    def begin(self, source='tool'):
        if self.status in ('verified', 'failed'):
            self._event('idv_transition_blocked', reason=f'already {self.status}', source=source)
            return False
        if self.status == 'in_progress': return True
        if self.status == 'paused':  # Resume where it stopped; attempts are never reset.
            self.status, self.paused_by, self.non_answers = 'in_progress', None, 0
            self._event('idv_resumed', source=source, step=self.step)
            return True
        self.status = 'in_progress'
        self._event('idv_started', source=source)
        self._activate(self.cfg['steps'][0], source)
        return True

    def pause(self, source, reason=''):
        """Stop matching because the caller moved on. Only removes access, so the model may ask for it."""
        if self.status != 'in_progress':
            self._event('idv_transition_blocked', reason=f'cannot pause when {self.status}', source=source)
            return False
        self.status, self.paused_by, self.non_answers = 'paused', source, 0
        self._event('idv_paused', source=source, step=self.step, reason=reason[:120])
        return True

    def observe_agent(self, text):
        """Compatibility path: detect identity questions in the agent's own words."""
        hits = [s for s, p in ASK_PATTERNS.items() if s in self.steps and p.search(text)]
        if not hits: return
        if self.status == 'not_started':
            if self.cfg['steps'][0] in hits and VERIFY_CONTEXT.search(text):
                self.begin('regex')
                self.steps[self.step]['asked'] = True
            else:
                self._event('idv_regex_ignored', detected=hits, reason='no verification context or out of order')
        elif self.status == 'in_progress':
            if self.step in hits:
                if not self.steps[self.step]['asked']: self._event('idv_step_asked', step=self.step, source='regex')
                self.steps[self.step]['asked'] = True
            else:
                self._event('idv_regex_ignored', detected=hits, expected=self.step, reason='out of order')
        elif self.status == 'paused' and self.step in hits:
            self.begin('regex')  # The agent went back to the paused question.
            self.steps[self.step]['asked'] = True
        else:
            self._event('idv_regex_ignored', detected=hits, reason=f'verification {self.status}')

    def might_answer(self, text):
        """Cheap check: could this paused-step turn be a volunteered answer?"""
        if self.status != 'paused' or self.step not in RESUME_BY_ANSWER: return False
        spoken = name_tokens(text)
        common = [t for t in spoken if t in COMMON]
        return bool(spoken) and '?' not in text and len(common) < 2 and len(common) * 2 <= len(spoken) <= 12

    async def take_volunteered_name(self, text):
        """A name given in the turn that started verification counts, but only if it clearly matches."""
        if self.status != 'in_progress' or self.step != 'full_name' or self.history: return None
        if (await match_name(text, self.pool(), self.cfg['name_match'])).outcome != 'pass': return None
        return await self.answer(text)

    def enter_customer_number(self, text):
        """What the caller typed into the customer number box on their screen."""
        if self.status != 'in_progress' or self.step != 'customer_number': return None
        result = match_customer_number(text, self.customers)
        if result.outcome == 'no_answer': return None  # The backend checks the format first; this never counts.
        return self._settle('customer_number', result)

    async def answer(self, text):
        resuming = self.status == 'paused' and self.step in RESUME_BY_ANSWER
        if self.status != 'in_progress' and not resuming: return None
        if self.step == 'customer_number':  # It comes only from the box on screen, never from speech.
            if self.status == 'in_progress': self.box_turns += 1
            return None
        step, today = self.step, self.today or date.today()
        cfg = self.cfg[STEP_CONFIG[step]]
        added = False
        if step == 'full_name':
            result = await match_name(text, self.pool(), cfg, self.judge)
        elif step == 'dob':
            before = self.dob_carry
            result = await match_dob(text, self.customer()['dob'], cfg, self.judge, today, self.dob_carry)
            if result.outcome != 'no_answer': self.dob_carry = result.components.pop('_carry', {})
            # A turn that adds a part to an answer already under way ("I said three.") is progress, not a miss.
            added = bool(before) and bool(_dob_known(self.dob_carry) - _dob_known(before))
        else:
            result = await match_address(text, self.customer()['address'], cfg, self.judge, self.address_carry)
            self.address_carry = result.components.pop('_carry', self.address_carry)
        if resuming:
            if result.outcome == 'no_answer': return None  # Still moved on: nothing is recorded.
            self.begin('caller')  # They volunteered the detail; it counts like any other attempt.
        return self._settle(step, result, added)

    def _settle(self, step, result, added=False):
        """Record an answer's outcome and move the state machine on."""
        cfg, state = self.cfg[STEP_CONFIG[step]], self.steps[step]
        if result.outcome == 'pass':
            state['status'] = 'passed'
            if step in IDENTIFY: self.customer_id = result.customer_id
            remaining = [s for s in self.cfg['steps'] if self.steps[s]['status'] == 'pending']
            if remaining:
                self._activate(remaining[0], 'progression')
            else:
                self.status, self.step = 'verified', None
        elif result.outcome == 'incomplete' and not (step == 'dob' and added):
            state['incomplete'] += 1
            if state['incomplete'] > self.cfg['max_incomplete']:
                state['incomplete'] = 0
                self._fail_attempt(step, cfg)
        elif result.outcome == 'uncertain':
            state['uncertain'] += 1  # Probably a mishearing: the first ones only ask for a clarification.
            if state['uncertain'] > self.cfg['free_uncertain_per_step']: self._fail_attempt(step, cfg)
        elif result.outcome == 'fail':
            self._fail_attempt(step, cfg)
        self.non_answers = self.non_answers + 1 if result.outcome == 'no_answer' else 0
        self.last = {'step': step, **result.public(self.cfg['debug'])}
        self.history.append(self.last)
        self._event('idv_attempt', step=step, attempts=state['attempts'], max_attempts=cfg['max_attempts'],
                    status=self.status, **result.public(self.cfg['debug']))
        if self.status in ('verified', 'failed'): self._event('idv_complete', status=self.status)
        limit = self.cfg['auto_pause_after_non_answers']
        if limit and self.non_answers >= limit: self.pause('worker', 'consecutive turns without an answer')
        return result

    def _fail_attempt(self, step, cfg):
        state = self.steps[step]
        state['attempts'] += 1
        if state['attempts'] >= cfg['max_attempts']:
            state['status'] = 'failed'
            self.status, self.step = 'failed', None

    def note(self, fresh=None):
        """What the agent needs to know, with no stored values and no detail about what was wrong."""
        lines = ['IDENTITY VERIFICATION (set by the Worker; authoritative)']
        if self.status == 'not_started':
            lines.append("Not started. Customer-specific tools will start it when needed; you can also call "
                         "begin_identity_verification once the caller wants something that needs their records.")
        elif self.status == 'verified':
            lines.append(f"VERIFIED for this call. Address the caller as {self.customer()['preferred_name']} if natural. "
                         "Account tools are unlocked. Don't ask for identity details again.")
            if fresh and fresh['outcome'] == 'pass':
                lines.append("Verification just finished. Let them know warmly, in your own words, that the checks are all done, then get on with what they wanted, as they last put it (a correction replaces what it corrects), "
                             "without repeating back what they told you.")
        elif self.status == 'paused':
            by = {'worker': "the Worker, after turns that weren't answers", 'caller': 'the caller closing the customer number box'}.get(self.paused_by, 'you')
            lines.append(f"PAUSED by {by}: the caller moved on from what needed verification, so don't push for identity "
                         "details. As soon as they want to continue, want something from their own records, or start giving "
                         f"identity details, call begin_identity_verification: it resumes at the {STEP_LABEL[self.step]} step.")
        elif self.status == 'failed':
            lines.append("FAILED: the attempt limit was reached. Don't retry verification, don't say which detail failed, and "
                         "don't discuss or note anything against account records. Tell them kindly you weren't able to verify them, "
                         "and that the team can call them back: in that same reply, call open_contact_form, which puts a short form on "
                         "their screen for their name and number, without asking first. General questions and complaints are still fine.")
        else:
            done = [STEP_LABEL[s] for s in self.cfg['steps'] if self.steps[s]['status'] == 'passed']
            cfg = self.cfg[STEP_CONFIG[self.step]]
            left = cfg['max_attempts'] - self.steps[self.step]['attempts']
            lines.append('In progress. ' + (f"Done: {', '.join(done)}. " if done else '') +
                         f"Current step: the caller's {STEP_LABEL[self.step]}. Attempts left: {left}.")
            if fresh:
                outcome = fresh['outcome']
                if fresh['step'] == 'customer_number' and outcome == 'pass':
                    count = {1: 'one', 2: 'two', 3: 'three'}.get(len(self.cfg['steps']) - 1, 'a few')
                    lines.append("The customer number they typed in found their record. Thank them briefly, then say that for their "
                                 f"security you just need {count} quick details to confirm it's them, and ask for their {STEP_LABEL[self.step]}.")
                elif fresh['step'] == 'customer_number' and outcome == 'fail':
                    lines.append("The customer number they typed in didn't match a record. Kindly let them know, as a likely typo, "
                                 "without reading anything back. The Worker then asks them to type it again, so don't ask yourself.")
                elif fresh['step'] != self.step and outcome == 'pass':
                    lines.append(f"Their {STEP_LABEL[fresh['step']]} just checked out. React warmly in your own words, "
                                 f"then ask for their {STEP_LABEL[self.step]} in the same turn.")
                elif outcome == 'incomplete':
                    lines.append("Their last answer was incomplete. Ask only for the missing part (" + ', '.join(fresh['missing']) +
                                 "), without saying which part they gave or that it was right.")
                elif outcome == 'uncertain':
                    lines.append("Their last answer was close but couldn't be confirmed, probably a mishearing. "
                                 "Ask them to " + CLARIFY[self.step] + ", lightly, without saying what was wrong.")
                elif outcome == 'fail':
                    hint = ' You may ask them to spell it.' if self.step == 'full_name' and left == 1 else ''
                    lines.append("Their last answer didn't match. Kindly ask them to try again, as if it was probably "
                                 "a mishearing, without saying what was wrong." + hint)
                elif outcome == 'no_answer' and fresh.get('reason') == 'previous_step':
                    lines.append("Their last words were the end of their previous answer, not this one, so they don't count. "
                                 f"Don't react to them or say you didn't catch anything; just ask for their {STEP_LABEL[self.step]}.")
                elif outcome == 'no_answer':
                    lines.append("Their last turn wasn't an answer to the verification question, so don't say you didn't "
                                 "catch it. Respond to what they actually said. A side question doesn't mean they've moved on: "
                                 "answer it, then come back to the question. Only if they no longer want what needed "
                                 "verification, call pause_identity_verification and help with the new request.")
            elif self.step == 'customer_number':
                lines.append("The Worker has asked them to type their customer number (it starts with CUS) into a box on their "
                             "screen; wait for them to type it in. Don't ask them to say it aloud.")
                if self.box_turns:  # Once they've had a say: the first ask shouldn't sound like a refusal.
                    lines.append("If they read it out, ask them to type it into the box instead.")
                    lines.append("If they don't have one, can't find it, or still haven't typed it after you've asked twice, stop "
                                 "asking: call pause_identity_verification, say you can't get into their records without it, and "
                                 "offer general help or a call back from the team (open_contact_form).")
            else:
                lines.append(f"Ask for their {STEP_LABEL[self.step]} naturally, one item at a time.")
        lines.append("Never say, hint at, or confirm any stored identity detail, and never claim the caller is "
                     "verified unless this note says VERIFIED.")
        return '\n'.join(lines)

    def snapshot(self):
        return {'status': self.status, 'step': self.step, 'paused_by': self.paused_by,
                'customer_id': self.customer_id if self.status == 'verified' else None,
                'steps': copy.deepcopy(self.steps), 'history': copy.deepcopy(self.history), 'debug': self.cfg['debug'],
                'thresholds': {k: self.cfg[k] for k in STEP_CONFIG.values()}}
