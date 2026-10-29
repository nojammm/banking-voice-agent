"""Per-call banking workflow behind the voice agent's tools.

Every customer-data tool checks the Worker's verification state itself; the prompt is not the
control. Results carry only what the reply needs, with account numbers masked.
"""
import asyncio
import re
from datetime import date
import bank
import idv
import kb

PROTECTED = 'protected'
DISPUTE_TYPES = {'duplicate', 'incorrect_amount', 'not_received', 'unauthorised', 'scam', 'other'}
FRAUD_TYPES = {'unauthorised', 'scam'}  # Customer didn't make it, or was tricked into making it.
HARDSHIP_CAUSES = {'job_loss', 'reduced_income', 'illness_or_injury', 'relationship_breakdown', 'natural_disaster',
                   'family_or_carer', 'other'}
COMPLAINT_CATEGORIES = {'fees_and_charges', 'service', 'delay', 'product', 'decision', 'hardship', 'dispute_handling', 'privacy', 'other'}
FOLLOWUP_REASONS = {'lending_decision', 'complex_dispute', 'hardship_assessment', 'fraud_or_security', 'formal_complaint',
                    'unsupported_request', 'verification_failed', 'other'}
CONTACT_REASONS = {'hardship', 'dispute', 'complaint', 'account', 'other'}
LOAN_PURPOSES = {'home_loan', 'refinance', 'personal_loan', 'car_loan', 'other'}
FAILED_NEXT = ("Verification can't be retried and records can't be accessed or noted against an account on this call. Tell them "
               "kindly that you weren't able to verify them, and that the team can call them back; in that same reply, without asking "
               "first, call open_contact_form to put a short form on their screen for their name and number.")
BOX_LEAD_IN = ("Right after your words, the Worker asks them to type their customer number into a box on their screen, with a "
               "fixed line. So don't ask for it, mention the box, or explain why you need to check who they are; if nothing else "
               "needs saying, say nothing.")
MOCK = "Don't promise when or how anyone will be in touch."
MOCK_GIVEN = "Don't repeat what happens next unless they ask."
OTHER_ASKS = ("If they asked for other things too that you haven't mentioned yet, touch on each in a few words first: the answer if a "
              "search found it, otherwise that you'll come back to it, never that it's done or started unless a tool result shows it. Then ")


def short_reason(reason):
    """A model-written reason worth carrying: a few plain words."""
    return isinstance(reason, str) and 0 < len(reason) <= 80 and '\n' not in reason


def money(value):
    return ('-' if value < 0 else '') + f'${abs(value):,.2f}'


def spoken(ref):
    """A reference as it should be read aloud: one character at a time, no dash."""
    return ', '.join(ref.replace('-', ''))


MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')


def say_date(value, today=None):
    """An ISO date as it should be said: "6 November", with the year only when it isn't this year."""
    try: d = date.fromisoformat(str(value)[:10])
    except ValueError: return value
    return f'{d.day} {MONTHS[d.month - 1]}' + ('' if d.year == (today or date.today()).year else f' {d.year}')


ABBREVIATIONS = {'Mgmt': 'Management', 'Pty': '', 'Ltd': ''}


def say_description(raw):
    """A transaction description as it should be said: merchant words in normal case, without long reference
    numbers, internal IDs, symbols, web suffixes or a "Pending:" prefix (the status says that).
    "PAYPAL *GAMEVAULT 4029357733" -> "Paypal Gamevault"."""
    text = re.sub(r'^\s*pending:\s*', '', str(raw or ''), flags=re.I)
    text = re.sub(r'\b[A-Z]{2,3}-\d+\b|\b\d{6,}\b|[*#]', ' ', text)  # LN-2001, 4029357733, PAYPAL *
    text = re.sub(r'\.com(?:\.au)?\b', '', text, flags=re.I).replace(' - ', ', ')
    words = [ABBREVIATIONS.get(w, w.capitalize() if len(w) > 3 and w.isupper() else w) for w in text.split()]
    return re.sub(r'\s+([,:])', r'\1', ' '.join(w for w in words if w)).strip(' ,:') or str(raw)


def mask(number):
    return 'ending ' + number[-4:]


class Workflow:
    def __init__(self, session_id, conn, verification, publish=None):
        self.session_id, self.conn, self.idv = session_id, conn, verification
        self.publish = publish
        self.forms, self.created, self.facts, self.hardship_ref, self.complaint_ref = {}, [], set(), None, None
        self.followup_ref, self.mock_given = None, False
        self.acknowledged = False  # Set by the agent before a tool call: it already responded to how the caller feels this turn.
        self.turn, self.shown = None, {}  # Caller turns (set by the voice agent) and when each transaction was first shown.
        self.verified_turn = None  # The caller turn in which verification passed.
        # Set by the voice worker: the customer number box waits until the agent has finished asking for it, so the caller
        # isn't typing while it's still talking. show_box() puts it up; a timer does if no reply ends.
        self.hold_box, self.held_box, self.box_timer = False, None, None
        self.waiting = []  # What the caller asked for that needed verification, so it isn't lost on the way.
        verification.on_event = self.audit
        bank.save_session(conn, session_id, self.snapshot())

    @classmethod
    def create(cls, session_id, assistant=None, judge=None, conn=None, publish=None, config=None, today=None):
        conn = conn or bank.connect()
        config = config or idv.load_config(assistant)
        return cls(session_id, conn, idv.Verification(bank.identity_records(conn), config, judge, today), publish)

    # ------------------------------------------------------------ state, audit and UI events

    def new_turn(self):
        """The caller finished a turn. A transaction found in this turn hasn't been confirmed by them yet."""
        self.turn = (self.turn or 0) + 1

    def _shown(self, *ids):
        for t in ids: self.shown.setdefault(t, self.turn)

    def snapshot(self):
        return {'idv': self.idv.snapshot(), 'verified_customer': self.idv.customer_id if self.idv.verified else None,
                'forms': self.forms, 'cases_created': self.created}

    def audit(self, kind, data):
        if kind == 'idv_complete' and data.get('status') == 'verified': self.verified_turn = self.turn
        bank.log(self.conn, self.session_id, kind, data)
        bank.save_session(self.conn, self.session_id, self.snapshot())

    async def emit(self, payload):
        """Customer-facing UI event. Carries no identity values or debug detail."""
        if self.publish: await self.publish(payload)

    async def auth_changed(self):
        await self.emit({'type': 'auth', 'state': self.idv.status,
                         'step': idv.STEP_LABEL.get(self.idv.step) if self.idv.step else None})
        await self.sync_number_form()

    async def sync_number_form(self):
        """The customer number box is on screen exactly while verification waits for it."""
        waiting = self.idv.status == 'in_progress' and self.idv.step == 'customer_number'
        open_ = [f for f, v in self.forms.items() if v['status'] == 'open' and v.get('kind') == 'customer_number']
        if waiting and not open_:
            missed = bool(self.idv.last) and self.idv.last['step'] == 'customer_number' and self.idv.last['outcome'] == 'fail'
            notice = 'not_found' if missed else None
            form_id = bank.open_form(self.conn, self.session_id, 'customer_number', 'identification')
            self.forms[form_id] = {'status': 'open', 'purpose': 'identification', 'kind': 'customer_number', 'notice': notice}
            self.audit('form_opened', {'form_id': form_id, 'kind': 'customer_number'})
            payload = {'type': 'form', 'action': 'open', 'form_id': form_id, 'kind': 'customer_number', 'notice': notice}
            if not self.hold_box: return await self.emit(payload)
            self.held_box = payload
            self.box_timer = asyncio.get_running_loop().call_later(12, lambda: asyncio.ensure_future(self.show_box()))
        elif open_ and not waiting:  # Paused or finished some other way: take the box away.
            self._drop_held()
            for form_id in open_:
                bank.close_form(self.conn, form_id, 'withdrawn')
                self.forms[form_id]['status'] = 'withdrawn'
                self.audit('form_withdrawn', {'form_id': form_id, 'idv': self.idv.status})
                await self.emit({'type': 'form', 'action': 'close', 'form_id': form_id})

    async def show_box(self):
        """The agent has finished speaking: put up the customer number box if it's waiting."""
        payload = self._drop_held()
        if payload: await self.emit(payload)

    def _drop_held(self):
        payload, self.held_box = self.held_box, None
        if self.box_timer: self.box_timer.cancel()
        self.box_timer = None
        return payload

    def typing(self):
        """The open customer number box, if the caller has started typing into it."""
        return next((f for f, v in self.forms.items() if v['status'] == 'open' and v.get('kind') == 'customer_number' and v.get('typing')), None)

    def state_note(self, fresh=None):
        contact = [f['status'] for f in self.forms.values() if f.get('kind') == 'contact_callback']
        if self.idv.status == 'failed' and contact:  # Told once and the form is up or done: don't start it over.
            lines = ["IDENTITY VERIFICATION (set by the Worker; authoritative)\nFAILED: don't retry it, and don't discuss or note "
                     f"anything against account records. They've been told, and the call-back form is {contact[-1]}: don't open it "
                     "again or repeat that you couldn't verify them. General questions and complaints are still fine."]
        else:
            lines = [self.idv.note(fresh)]
        if self.idv.verified and fresh and fresh.get('outcome') == 'pass' and self.waiting:
            lines.append("Before verification they asked for: " + '; '.join(w.replace('_', ' ') for w in self.waiting) +
                         ". Do each now that's still wanted, calling its tool, as well as anything they've raised since.")
        if self.typing():
            lines.append("They're typing their customer number into the box right now. Don't ask for it again or explain why it's "
                         "needed: if they say something, answer in a few words and let them finish.")
        open_forms = [f for f, v in self.forms.items() if v['status'] == 'open' and v.get('kind') != 'customer_number']
        if open_forms:
            lines.append('A callback form is open on the caller\'s screen. Wait for its form event; never assume it was submitted.')
        if self.created:
            lines.append('Records created this call: ' + ', '.join(f"{c['type']} {c['ref']}" for c in self.created) + '.')
        return '\n\n'.join(lines)

    def _mock(self):
        """What happens next goes with the first record of the call only; it isn't repeated every turn."""
        if self.mock_given: return MOCK_GIVEN
        self.mock_given = True
        return MOCK

    def _date(self, value):
        """Dates go to the model ready to say. The current year may still be said, though it's left out."""
        if not value: return value
        today = self.idv.today or date.today()
        self.facts.add(str(today.year))
        return say_date(value, today)

    def _guard(self, tool):
        """Independent check for every protected tool. Starts ID&V rather than guessing."""
        status = self.idv.status
        if status == 'verified': return None
        self.audit('tool_blocked', {'tool': tool, 'idv': status})
        if tool not in self.waiting: self.waiting.append(tool)
        if status in ('not_started', 'paused'):
            fresh = status == 'not_started'
            self.idv.begin('tool')  # A paused check resumes where it stopped, attempts intact.
            return {'error': 'verification_required', 'next': f"Identity verification is needed for this. {self.ask_step(fresh)}"}
        if status == 'in_progress':
            return {'error': 'verification_in_progress', 'next': f"Finish verification first: ask for the caller's {idv.STEP_LABEL[self.idv.step]}."}
        return {'error': 'verification_failed', 'next': FAILED_NEXT}

    async def _record(self, kind, summary, details, priority='normal', status='Open'):
        customer = self.idv.customer_id if self.idv.verified else None
        ref = bank.create_case(self.conn, kind, summary, details, customer, self.session_id, priority, status)
        self.created.append({'type': kind, 'ref': ref})
        self.facts.update(re.findall(r'\d+', ref) + list(ref[-4:]))  # Whole, or read digit by digit.
        self.audit('case_created', {'ref': ref, 'type': kind, 'priority': priority, 'linked_customer': bool(customer)})
        await self.emit({'type': 'case', 'kind': kind, 'ref': ref})
        return ref, customer

    async def call(self, tool, **args):
        """Run a tool with an audit trail. Tool errors become structured results, never crashes."""
        self.audit('tool_call', {'tool': tool, 'args': {k: v for k, v in args.items() if k not in ('description',)}})
        try:
            result = await getattr(self, tool)(**args)
        except Exception as exc:
            self.audit('tool_error', {'tool': tool, 'error': type(exc).__name__})
            return {'error': 'backend_error', 'next': "The banking system didn't respond. Apologise and offer to try again or arrange a follow-up."}
        self.audit('tool_result', {'tool': tool, 'ok': 'error' not in result, 'error': result.get('error')})
        return result

    # ------------------------------------------------------------ verification

    async def begin_identity_verification(self, reason=''):
        if self.idv.status == 'verified': return {'status': 'already_verified'}
        if self.idv.status == 'failed': return {'status': 'failed', 'next': FAILED_NEXT}
        fresh = self.idv.status == 'not_started'
        if reason and short_reason(reason) and reason not in self.waiting: self.waiting.append(reason)
        self.idv.begin('tool')
        await self.auth_changed()
        return {'status': 'in_progress', 'next': self.ask_step(fresh)}

    def ask_step(self, fresh):
        """What the reply that starts or resumes verification says, as one reply. If the agent already responded to how the
        caller feels this turn (acknowledged), it goes straight to the request."""
        if self.idv.step == 'customer_number':  # The Worker asks for it itself, with the box: the reply is only the lead-in.
            feel = ("You've already responded to how they feel this turn, so don't again. " if self.acknowledged or not fresh else
                    "Respond to what they've told you as a caring person would: real empathy if they're struggling, upset or worried, "
                    "a few friendly words otherwise. ")
            return f"{feel}{OTHER_ASKS.replace(' Then ', ' ')}{BOX_LEAD_IN}"
        if fresh and self.idv.step == self.idv.cfg['steps'][0]:
            feel = ("You've already responded to how they feel this turn, so go straight on: " if self.acknowledged else
                    "respond to what they've told you as a caring person would: real empathy if they're struggling, upset or worried, "
                    "a few friendly words otherwise. Then ")
            return (f"{OTHER_ASKS}in this one reply, {feel}say briefly why you need to confirm who they are (for their security, and "
                    "so you can help with their accounts), that it's three quick details, and ask for their full name as a question.")
        return f"{OTHER_ASKS}ask for the caller's {idv.STEP_LABEL[self.idv.step]} naturally."

    async def pause_identity_verification(self, reason=''):
        if not self.idv.pause('model', reason or ''):
            return {'status': self.idv.status, 'next': 'Nothing to pause.'}
        await self.auth_changed()
        return {'status': 'paused', 'next': "Help with what the caller wants now and don't ask for identity details. "
                                            "If they later need their own records, the account tools resume verification."}

    # ------------------------------------------------------------ protected records

    async def get_account_summary(self):
        if (blocked := self._guard('get_account_summary')): await self.auth_changed(); return blocked
        out = []
        for a in bank.accounts(self.conn, self.idv.customer_id):
            item = {'account_id': a['id'], 'name': a['name'], 'number': mask(a['number']), 'type': a['type']}
            if a['type'] == 'credit_card':
                item.update(owing=money(-a['balance']), available_credit=money(a['available']), limit=money(a['credit_limit']))
            else:
                item.update(balance=money(a['balance']), available=money(a['available']))
            out.append(item)
        return {'accounts': out, 'next': 'Keep each figure with its own label: balance and available, '
                                         'or owing and available credit, are different amounts.'}

    async def get_recent_transactions(self, account_id='', search='', limit=5):
        if (blocked := self._guard('get_recent_transactions')): await self.auth_changed(); return blocked
        limit = max(1, min(int(limit or 5), 10))
        rows = bank.transactions(self.conn, self.idv.customer_id, account_id or None, (search or '').strip()[:40] or None, limit)
        self._shown(*(t['id'] for t in rows))
        return {'transactions': [{'transaction_id': t['id'], 'date': self._date(t['posted']), 'description': say_description(t['description']),
                                  'amount': money(t['amount']), 'status': t['status'], 'account_id': t['account_id']} for t in rows]}

    async def get_transaction_details(self, transaction_id):
        if (blocked := self._guard('get_transaction_details')): await self.auth_changed(); return blocked
        t = bank.transaction(self.conn, self.idv.customer_id, transaction_id)
        if not t: return {'error': 'not_found', 'next': 'Check the transaction with get_recent_transactions.'}
        self._shown(t['id'])
        twins = [o for o in bank.transactions(self.conn, self.idv.customer_id, t['account_id'], limit=50)
                 if o['id'] != t['id'] and o['description'] == t['description'] and o['amount'] == t['amount'] and o['posted'] == t['posted']]
        disputes = [c['ref'] for c in bank.cases(self.conn, self.idv.customer_id) if c['details'].get('transaction_id') == t['id']]
        return {'transaction_id': t['id'], 'date': self._date(t['posted']), 'description': say_description(t['description']), 'amount': money(t['amount']),
                'status': t['status'], 'account_id': t['account_id'],
                'identical_charges_same_day': [o['id'] for o in twins], 'existing_cases': disputes}

    async def get_loans(self):
        if (blocked := self._guard('get_loans')): await self.auth_changed(); return blocked
        return {'loans': [{'loan_id': l['id'], 'product': l['product'], 'balance_owing': money(l['balance']),
                           'interest_rate': f"{l['rate']:.2f}% p.a. {l['rate_type']}", 'repayment': money(l['repayment']),
                           'frequency': l['frequency'], 'next_due': self._date(l['next_due']), 'term_ends': self._date(l['term_end']),
                           'offset_account': l['offset_account']} for l in bank.loans(self.conn, self.idv.customer_id)]}

    async def get_loan_applications(self):
        if (blocked := self._guard('get_loan_applications')): await self.auth_changed(); return blocked
        return {'applications': [{'application_id': a['id'], 'product': a['product'], 'amount': money(a['amount']),
                                  'status': a['status'], 'lodged': self._date(a['lodged']), 'last_updated': self._date(a['updated']), 'next_step': a['next_step']}
                                 for a in bank.applications(self.conn, self.idv.customer_id)]}

    async def get_case_status(self, reference=''):
        if (blocked := self._guard('get_case_status')): await self.auth_changed(); return blocked
        rows = bank.cases(self.conn, self.idv.customer_id)
        if reference: rows = [c for c in rows if c['ref'].replace('-', '').lower() == reference.replace('-', '').replace(' ', '').lower()]
        return {'cases': [{'reference': c['ref'], 'say_reference': spoken(c['ref']), 'type': c['type'], 'status': c['status'], 'opened': self._date(c['created_at']),
                           'summary': say_description(c['summary']), **{k: self._date(v) for k, v in c['details'].items() if k.endswith('_due')}} for c in rows]}

    async def lodge_dispute(self, transaction_id, dispute_type, description):
        if (blocked := self._guard('lodge_dispute')): await self.auth_changed(); return blocked
        if dispute_type not in DISPUTE_TYPES: return {'error': 'invalid_type', 'allowed': sorted(DISPUTE_TYPES)}
        t = bank.transaction(self.conn, self.idv.customer_id, transaction_id)
        if not t: return {'error': 'not_found', 'next': 'Confirm the transaction with get_recent_transactions first.'}
        existing = [c['ref'] for c in bank.cases(self.conn, self.idv.customer_id)
                    if c['details'].get('transaction_id') == t['id'] and c['type'] in ('dispute', 'fraud')]
        if existing: return {'error': 'already_lodged', 'reference': existing[0], 'say_reference': spoken(existing[0]), 'next': "Nothing new was lodged, and details can't be added to an existing case on this call. Give the existing reference and its status."}
        if self.turn is not None and self.shown.get(t['id'], self.turn) >= self.turn:
            return {'error': 'not_confirmed', 'next': "The caller hasn't confirmed this transaction yet. Describe it briefly (merchant, "
                                                      "amount, date) and ask if it's the one; lodge it once they say yes."}
        details = {'transaction_id': t['id'], 'merchant': t['description'], 'amount': money(t['amount']),
                   'dispute_type': dispute_type, 'customer_account': (description or '')[:500]}
        if dispute_type in FRAUD_TYPES:
            ref, _ = await self._record('fraud', f"Suspected {dispute_type}: {t['description']} {money(t['amount'])}", details,
                                        priority='urgent', status='Referred to fraud team')
            esc, _ = await self._record('escalation', f'Fraud team review for {ref}', {'reason': 'fraud_or_security', 'case': ref},
                                        priority='urgent', status='Queued')
            guidance = kb.document('scams-security')
            return {'reference': ref, 'say_reference': spoken(ref), 'route': 'fraud', 'escalation_reference': esc,
                    'security_guidance': guidance['text'],
                    'next': 'Give the security guidance now, especially locking the card. Explain the fraud team reviews it; never promise a refund. ' + self._mock()}
        ref, _ = await self._record('dispute', f"{dispute_type.replace('_', ' ').capitalize()}: {t['description']} {money(t['amount'])}", details,
                                    status='Investigating')
        return {'reference': ref, 'say_reference': spoken(ref), 'route': 'dispute', 'typical_investigation': 'up to 45 days',
                'next': 'Explain the investigation; never promise a refund or outcome. ' + self._mock()}

    # ------------------------------------------------------------ available before verification

    async def search_banking_knowledge(self, query):
        results = kb.search((query or '')[:300])
        self.audit('rag_search', {'query': (query or '')[:120], 'results': [r['id'] for r in results]})
        await self.emit({'type': 'sources', 'sources': [{'title': r['title'], 'url': r['url'], 'label': r['type']}
                                                        for r in results if r['url']]})
        if not results:
            return {'results': [], 'next': "No guidance found. Say once that you don't have reviewed information on that; don't guess, and don't offer to search again."}
        return {'results': [{'title': r['title'], 'label': r['type'], 'source': r['source'], 'text': r['text']} for r in results],
                'next': "These are the closest matches, not necessarily answers. Say only what they directly state about the question; "
                        "if none of them covers it, say you don't have that information. Never fill the gap from general knowledge, and don't offer to search again for the same thing."}

    async def estimate_loan_repayment(self, amount, annual_rate_percent, years, frequency='monthly'):
        per_year = {'weekly': 52, 'fortnightly': 26, 'monthly': 12}.get(frequency)
        if per_year is None: return {'error': 'frequency must be weekly, fortnightly or monthly'}
        if not (1000 <= amount <= 5_000_000 and 0.1 <= annual_rate_percent <= 25 and 1 <= years <= 30):
            return {'error': 'out_of_range', 'next': 'Amount 1,000 to 5,000,000, rate 0.1 to 25 percent, term 1 to 30 years.'}
        r, n = annual_rate_percent / 100 / per_year, round(years * per_year)
        repayment = amount * r / (1 - (1 + r) ** -n)
        result = {'repayment': money(round(repayment, 2)), 'frequency': frequency, 'total_interest': money(round(repayment * n - amount, 2)),
                  'inputs': {'amount': money(amount), 'rate': f'{annual_rate_percent}% p.a.', 'years': years},
                  'label': 'Estimate only, principal and interest, rate unchanged for the whole term. Not an offer or approval.'}
        self.facts.update(re.findall(r'\d+(?:\.\d+)?', f"{amount} {annual_rate_percent} {years}"))
        return result

    async def record_hardship_notice(self, cause, situation, affected_products, expected_duration, support_wanted):
        if cause not in HARDSHIP_CAUSES: return {'error': 'invalid_cause', 'allowed': sorted(HARDSHIP_CAUSES)}
        if self.idv.status == 'failed':  # Not against an account they couldn't prove is theirs: the team calls them back instead.
            return {'error': 'verification_failed', 'next': "Don't record it: they couldn't be verified. The call-back form "
                                                            "(open_contact_form) is how the team reaches them."}
        # With no reason given, the turn verification passes in asks what's happened instead; the next turn records whatever they say.
        if cause == 'other' and not self.hardship_ref and self.turn is not None and self.turn == self.verified_turn:
            return {'error': 'not_recorded_yet', 'next': "They haven't said what's behind it, so ask before recording. Without repeating "
                                                         "what they told you, ask one gentle, caring question about what's happened, "
                                                         "so you can help them properly. Record it once they answer, whatever they say, "
                                                         "and don't ask again."}
        details = {'cause': cause, 'situation': situation[:500], 'affected_products': affected_products[:200],
                   'expected_duration': expected_duration[:100], 'support_wanted': support_wanted[:200],
                   'identity': 'verified' if self.idv.verified else 'not verified'}
        if self.hardship_ref:  # One notice per call: later details update it rather than creating another.
            bank.update_case(self.conn, self.hardship_ref, details)
            self.audit('case_updated', {'ref': self.hardship_ref, 'type': 'hardship'})
            return {'reference': self.hardship_ref, 'say_reference': spoken(self.hardship_ref), 'status': 'updated',
                    'next': 'The existing notice now has these details. Say so in a few words; the reference is unchanged, so don\'t read it again.'}
        ref, customer = await self._record('hardship', f"Hardship notice: {cause.replace('_', ' ')}", details, priority='high',
                                           status='Received - assessment pending')
        self.hardship_ref = ref
        counselling = kb.document('hardship-help')
        result = {'reference': ref, 'say_reference': spoken(ref), 'response_due_days': 21, 'free_counselling': counselling and counselling['text'],
                  'next': 'This reply is only: a warm, personal sentence of your own, in the spirit of having noted it down and passing it '
                          'to the team who can help them (not a flat statement that a notice was recorded); the reference; that we\'ll respond '
                          'within 21 days (you speak as the bank, so we, not the bank); and making clear that no change to their repayments has been agreed yet. Only if the '
                          'caller sounds distressed or overwhelmed, add the free counselling in a sentence. Then link naturally to the closing '
                          'question, which ends your part of this; ask nothing about their situation. ' + self._mock()}
        if not customer:
            result['identity'] = 'Not verified, so the notice is not yet linked to accounts. The caller can verify now, or a specialist will confirm identity first.'
        return result

    async def lodge_complaint(self, category, summary, desired_outcome=''):
        if category not in COMPLAINT_CATEGORIES: return {'error': 'invalid_category', 'allowed': sorted(COMPLAINT_CATEGORIES)}
        details = {'category': category, 'desired_outcome': desired_outcome[:300], 'identity': 'verified' if self.idv.verified else 'not verified'}
        days = 21 if category == 'hardship' else 30
        if self.complaint_ref:  # One complaint per call: details the caller adds later update it.
            bank.update_case(self.conn, self.complaint_ref, details, summary[:300])
            self.audit('case_updated', {'ref': self.complaint_ref, 'type': 'complaint'})
            return {'reference': self.complaint_ref, 'say_reference': spoken(self.complaint_ref), 'status': 'updated',
                    'next': "The existing complaint now has these details. Say so in a few words; the reference and timeframe are unchanged, so don't read them again."}
        ref, _ = await self._record('complaint', summary[:300], details, status='Received')
        self.complaint_ref = ref
        afca = next((x for x in re.split(r'(?<=\.)\s+', (kb.document('complaints') or {}).get('text', '')) if 'AFCA' in x), None)
        return {'reference': ref, 'say_reference': spoken(ref), 'final_response_within_days': days, 'if_still_unhappy': afca,
                'next': 'Give the reference and the timeframe. Mention AFCA only if they want to go there or ask what happens if they stay unhappy, '
                        'with one contact route. ' + self._mock()}

    async def request_human_followup(self, reason, summary, urgency='normal'):
        if reason not in FOLLOWUP_REASONS: return {'error': 'invalid_reason', 'allowed': sorted(FOLLOWUP_REASONS)}
        details = {'reason': reason, 'identity': self.idv.status}
        if self.followup_ref:  # One follow-up request per call: later details update it.
            bank.update_case(self.conn, self.followup_ref, details, summary[:300])
            self.audit('case_updated', {'ref': self.followup_ref, 'type': 'escalation'})
            return {'reference': self.followup_ref, 'say_reference': spoken(self.followup_ref), 'status': 'updated',
                    'next': "The existing follow-up request now has these details. Say so in a few words; the reference is unchanged, so don't read it again."}
        ref, _ = await self._record('escalation', summary[:300], details,
                                    priority='urgent' if urgency == 'urgent' else 'normal', status='Queued')
        self.followup_ref = ref
        return {'reference': ref, 'say_reference': spoken(ref),
                'next': 'Tell the caller a follow-up request was recorded. No live transfer happened. ' + self._mock()}

    async def open_callback_form(self, purpose):
        if purpose not in LOAN_PURPOSES: return {'error': 'invalid_purpose', 'allowed': sorted(LOAN_PURPOSES)}
        if any(f['status'] == 'open' for f in self.forms.values()):
            return {'status': 'already_open', 'next': 'A form is already on screen; wait for the caller to finish it.'}
        form_id = bank.open_form(self.conn, self.session_id, 'loan_callback', purpose)
        self.forms[form_id] = {'status': 'open', 'purpose': purpose}
        self.audit('form_opened', {'form_id': form_id, 'purpose': purpose})
        await self.emit({'type': 'form', 'action': 'open', 'form_id': form_id, 'kind': 'loan_callback', 'purpose': purpose})
        return {'status': 'opened', 'next': "Tell the caller the form is on their screen for their name, number and consent. "
                                            "Don't say it's submitted until a form event arrives."}

    async def open_contact_form(self, reason):
        """A call back for a caller who couldn't be verified: a form for their name and number only."""
        if reason not in CONTACT_REASONS: return {'error': 'invalid_reason', 'allowed': sorted(CONTACT_REASONS)}
        if self.idv.verified: return {'error': 'already_verified', 'next': "They're verified; use the usual tools instead."}
        if any(f['status'] == 'open' for f in self.forms.values()):
            return {'status': 'already_open', 'next': 'A form is already on screen; wait for the caller to finish it.'}
        form_id = bank.open_form(self.conn, self.session_id, 'contact_callback', reason)
        self.forms[form_id] = {'status': 'open', 'purpose': reason, 'kind': 'contact_callback'}
        self.audit('form_opened', {'form_id': form_id, 'purpose': reason, 'kind': 'contact_callback'})
        await self.emit({'type': 'form', 'action': 'open', 'form_id': form_id, 'kind': 'contact_callback', 'purpose': reason})
        return {'status': 'opened', 'next': "If you haven't yet told them kindly that you weren't able to verify them, do that first. "
                                            "Then tell them a short form is on their screen for their name and phone number. Save what "
                                            "happens next for after they've sent it, and don't say it's submitted until a form event "
                                            "arrives."}

    def _number_event(self, status, event):
        """The caller used the customer number box. The Worker, not the backend, decides whether it found anyone."""
        if status == 'submitted':
            if self.idv.enter_customer_number(event.get('customer_number')) is None: return None
            return 'Frontend event: the caller typed a customer number into the box on their screen.\n\n' + self.idv.note(self.idv.last)
        if status in ('cancelled', 'closed'):
            self.idv.pause('caller', 'closed the customer number box')
            return ("Frontend event: the caller closed the customer number box without entering a number, so verification is paused. "
                    "Ask lightly whether they'd like to carry on; if they do, call begin_identity_verification and the box comes back. "
                    "Otherwise help with what they want now.")
        return ("Frontend event: what the caller typed isn't a customer number (those look like CUS and five digits). The box is still "
                "open and shows the error. Briefly let them know, without reading anything back.")

    def form_event(self, event):
        """Apply a backend-confirmed form event. Returns reply instructions, or None to ignore it."""
        form_id, status = event.get('form_id'), event.get('status')
        record = self.forms.get(form_id)
        if status == 'typing':  # Not an outcome: the agent just shouldn't talk over it.
            if record and record['status'] == 'open' and record.get('kind') == 'customer_number' and not record.get('typing'):
                record['typing'] = True
                self.audit('caller_typing', {'form_id': form_id})
            return None
        if not record or status not in ('submitted', 'cancelled', 'closed', 'error'):
            self.audit('form_event_ignored', {'form_id': form_id, 'status': status, 'reason': 'unknown form or status'})
            return None
        if record['status'] != 'open':
            self.audit('form_event_ignored', {'form_id': form_id, 'status': status, 'reason': 'form already ' + record['status']})
            return None
        if status != 'error': record['status'] = status
        self.audit('form_event_received', {'form_id': form_id, 'status': status})
        if record.get('kind') == 'customer_number': return self._number_event(status, event)
        if status == 'submitted':
            ref = event.get('reference', '')
            if not re.fullmatch(r'CBK-\d{4}', ref): return None
            self.created.append({'type': 'callback', 'ref': ref})
            self.facts.update(re.findall(r'\d+', ref) + list(ref[-4:]))  # Whole, or read digit by digit.
            if record.get('kind') == 'contact_callback':
                return (f"Frontend event: the caller submitted their name and number and the backend saved request {ref}. Tell them, "
                        f"warmly and in your own words, that the team will contact them on that number, and give the reference, read as: "
                        f"{spoken(ref)}. Don't read the number back or promise when. That's your part done: ask nothing about their "
                        "situation, since the team will, and end with the closing question.")
            return (f"Frontend event: the caller submitted the lending callback form and the backend saved request {ref}. "
                    f"Acknowledge it naturally and give the reference, read as: {spoken(ref)}. Don't promise when they'll be called. "
                    "Then continue the conversation where it left off.")
        if status in ('cancelled', 'closed'):
            return ("Frontend event: the caller closed the callback form without submitting it, so no request was created. "
                    "Acknowledge lightly and ask whether they'd like to carry on or need anything else.")
        fields = [f for f in event.get('fields', []) if f in ('name', 'phone', 'consent', 'form')]
        return ("Frontend event: the callback form couldn't be submitted" + (f" ({', '.join(fields)} needs attention)" if fields else '') +
                ". The form is still open. Briefly offer help; don't ask them to read details aloud.")
