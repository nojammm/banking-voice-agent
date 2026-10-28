"""Per-call banking workflow behind the voice agent's tools.

Every customer-data tool checks the Worker's verification state itself; the prompt is not the
control. Results carry only what the reply needs, with account numbers masked.
"""
import re
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
LOAN_PURPOSES = {'home_loan', 'refinance', 'personal_loan', 'car_loan', 'other'}
MOCK = 'Demo record only: no real bank staff will act on it. Say so plainly if the caller expects contact.'


def money(value):
    return ('-' if value < 0 else '') + f'${abs(value):,.2f}'


def spoken(ref):
    """A reference as it should be read aloud: one character at a time, no dash."""
    return ', '.join(ref.replace('-', ''))


def mask(number):
    return 'ending ' + number[-4:]


class Workflow:
    def __init__(self, session_id, conn, verification, publish=None):
        self.session_id, self.conn, self.idv = session_id, conn, verification
        self.publish = publish
        self.forms, self.created, self.facts, self.hardship_ref = {}, [], set(), None
        verification.on_event = self.audit
        bank.save_session(conn, session_id, self.snapshot())

    @classmethod
    def create(cls, session_id, assistant=None, judge=None, conn=None, publish=None, config=None, today=None):
        conn = conn or bank.connect()
        config = config or idv.load_config(assistant)
        return cls(session_id, conn, idv.Verification(bank.identity_records(conn), config, judge, today), publish)

    # ------------------------------------------------------------ state, audit and UI events

    def snapshot(self):
        return {'idv': self.idv.snapshot(), 'verified_customer': self.idv.customer_id if self.idv.verified else None,
                'forms': self.forms, 'cases_created': self.created}

    def audit(self, kind, data):
        bank.log(self.conn, self.session_id, kind, data)
        bank.save_session(self.conn, self.session_id, self.snapshot())

    async def emit(self, payload):
        """Customer-facing UI event. Carries no identity values or debug detail."""
        if self.publish: await self.publish(payload)

    async def auth_changed(self):
        await self.emit({'type': 'auth', 'state': self.idv.status,
                         'step': idv.STEP_LABEL.get(self.idv.step) if self.idv.step else None})

    def state_note(self, fresh=None):
        lines = [self.idv.note(fresh)]
        open_forms = [f for f, v in self.forms.items() if v['status'] == 'open']
        if open_forms:
            lines.append('A callback form is open on the caller\'s screen. Wait for its form event; never assume it was submitted.')
        if self.created:
            lines.append('Records created this call: ' + ', '.join(f"{c['type']} {c['ref']}" for c in self.created) + '.')
        return '\n\n'.join(lines)

    def _guard(self, tool):
        """Independent check for every protected tool. Starts ID&V rather than guessing."""
        status = self.idv.status
        if status == 'verified': return None
        self.audit('tool_blocked', {'tool': tool, 'idv': status})
        if status in ('not_started', 'paused'):
            self.idv.begin('tool')  # A paused check resumes where it stopped, attempts intact.
            return {'error': 'verification_required',
                    'next': f"Identity verification is needed for this. Ask the caller for their {idv.STEP_LABEL[self.idv.step]}, naturally."}
        if status == 'in_progress':
            return {'error': 'verification_in_progress', 'next': f"Finish verification first: ask for the caller's {idv.STEP_LABEL[self.idv.step]}."}
        return {'error': 'verification_failed', 'next': "Records can't be accessed on this call. Offer a human follow-up."}

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
        if self.idv.status == 'failed': return {'status': 'failed', 'next': "Verification can't be retried on this call. Offer a human follow-up."}
        self.idv.begin('tool')
        await self.auth_changed()
        return {'status': 'in_progress', 'next': f"Ask for the caller's {idv.STEP_LABEL[self.idv.step]} naturally."}

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
        return {'accounts': out}

    async def get_recent_transactions(self, account_id='', search='', limit=5):
        if (blocked := self._guard('get_recent_transactions')): await self.auth_changed(); return blocked
        limit = max(1, min(int(limit or 5), 10))
        rows = bank.transactions(self.conn, self.idv.customer_id, account_id or None, (search or '').strip()[:40] or None, limit)
        return {'transactions': [{'transaction_id': t['id'], 'date': t['posted'], 'description': t['description'],
                                  'amount': money(t['amount']), 'status': t['status'], 'account_id': t['account_id']} for t in rows]}

    async def get_transaction_details(self, transaction_id):
        if (blocked := self._guard('get_transaction_details')): await self.auth_changed(); return blocked
        t = bank.transaction(self.conn, self.idv.customer_id, transaction_id)
        if not t: return {'error': 'not_found', 'next': 'Check the transaction with get_recent_transactions.'}
        twins = [o for o in bank.transactions(self.conn, self.idv.customer_id, t['account_id'], limit=50)
                 if o['id'] != t['id'] and o['description'] == t['description'] and o['amount'] == t['amount'] and o['posted'] == t['posted']]
        disputes = [c['ref'] for c in bank.cases(self.conn, self.idv.customer_id) if c['details'].get('transaction_id') == t['id']]
        return {'transaction_id': t['id'], 'date': t['posted'], 'description': t['description'], 'amount': money(t['amount']),
                'status': t['status'], 'account_id': t['account_id'],
                'identical_charges_same_day': [o['id'] for o in twins], 'existing_cases': disputes}

    async def get_loans(self):
        if (blocked := self._guard('get_loans')): await self.auth_changed(); return blocked
        return {'loans': [{'loan_id': l['id'], 'product': l['product'], 'balance_owing': money(l['balance']),
                           'interest_rate': f"{l['rate']:.2f}% p.a. {l['rate_type']}", 'repayment': money(l['repayment']),
                           'frequency': l['frequency'], 'next_due': l['next_due'], 'term_ends': l['term_end'],
                           'offset_account': l['offset_account']} for l in bank.loans(self.conn, self.idv.customer_id)]}

    async def get_loan_applications(self):
        if (blocked := self._guard('get_loan_applications')): await self.auth_changed(); return blocked
        return {'applications': [{'application_id': a['id'], 'product': a['product'], 'amount': money(a['amount']),
                                  'status': a['status'], 'lodged': a['lodged'], 'last_updated': a['updated'], 'next_step': a['next_step']}
                                 for a in bank.applications(self.conn, self.idv.customer_id)]}

    async def get_case_status(self, reference=''):
        if (blocked := self._guard('get_case_status')): await self.auth_changed(); return blocked
        rows = bank.cases(self.conn, self.idv.customer_id)
        if reference: rows = [c for c in rows if c['ref'].replace('-', '').lower() == reference.replace('-', '').replace(' ', '').lower()]
        return {'cases': [{'reference': c['ref'], 'type': c['type'], 'status': c['status'], 'opened': c['created_at'][:10],
                           'summary': c['summary'], **{k: v for k, v in c['details'].items() if k.endswith('_due')}} for c in rows]}

    async def lodge_dispute(self, transaction_id, dispute_type, description):
        if (blocked := self._guard('lodge_dispute')): await self.auth_changed(); return blocked
        if dispute_type not in DISPUTE_TYPES: return {'error': 'invalid_type', 'allowed': sorted(DISPUTE_TYPES)}
        t = bank.transaction(self.conn, self.idv.customer_id, transaction_id)
        if not t: return {'error': 'not_found', 'next': 'Confirm the transaction with get_recent_transactions first.'}
        existing = [c['ref'] for c in bank.cases(self.conn, self.idv.customer_id)
                    if c['details'].get('transaction_id') == t['id'] and c['type'] in ('dispute', 'fraud')]
        if existing: return {'error': 'already_lodged', 'reference': existing[0], 'next': 'Give the existing reference and its status.'}
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
                    'next': 'Give the security guidance now, especially locking the card. Explain the fraud team reviews it; never promise a refund. ' + MOCK}
        ref, _ = await self._record('dispute', f"{dispute_type.replace('_', ' ').capitalize()}: {t['description']} {money(t['amount'])}", details,
                                    status='Investigating')
        return {'reference': ref, 'say_reference': spoken(ref), 'route': 'dispute', 'typical_investigation': 'up to 45 days',
                'next': 'Explain the investigation; never promise a refund or outcome. ' + MOCK}

    # ------------------------------------------------------------ available before verification

    async def search_banking_knowledge(self, query):
        results = kb.search((query or '')[:300])
        self.audit('rag_search', {'query': (query or '')[:120], 'results': [r['id'] for r in results]})
        await self.emit({'type': 'sources', 'sources': [{'title': r['title'], 'url': r['url'], 'label': r['type']}
                                                        for r in results if r['url']]})
        if not results:
            return {'results': [], 'next': "No guidance found. Say you don't have reviewed information on that; don't guess."}
        return {'results': [{'title': r['title'], 'label': r['type'], 'source': r['source'], 'text': r['text']} for r in results]}

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
        details = {'cause': cause, 'situation': situation[:500], 'affected_products': affected_products[:200],
                   'expected_duration': expected_duration[:100], 'support_wanted': support_wanted[:200],
                   'identity': 'verified' if self.idv.verified else 'not verified'}
        if self.hardship_ref:  # One notice per call: later details update it rather than creating another.
            bank.update_case(self.conn, self.hardship_ref, details)
            self.audit('case_updated', {'ref': self.hardship_ref, 'type': 'hardship'})
            return {'reference': self.hardship_ref, 'say_reference': spoken(self.hardship_ref), 'status': 'updated',
                    'next': 'The existing notice now has these details; the reference is unchanged.'}
        ref, customer = await self._record('hardship', f"Hardship notice: {cause.replace('_', ' ')}", details, priority='high',
                                           status='Received - assessment pending')
        self.hardship_ref = ref
        result = {'reference': ref, 'say_reference': spoken(ref), 'response_due_days': 21,
                  'next': 'Give the reference. A hardship specialist assesses options; nothing is approved on this call. Offer a specialist follow-up. ' + MOCK}
        if not customer:
            result['identity'] = 'Not verified, so the notice is not yet linked to accounts. The caller can verify now, or a specialist will confirm identity first.'
        return result

    async def lodge_complaint(self, category, summary, desired_outcome=''):
        if category not in COMPLAINT_CATEGORIES: return {'error': 'invalid_category', 'allowed': sorted(COMPLAINT_CATEGORIES)}
        details = {'category': category, 'desired_outcome': desired_outcome[:300], 'identity': 'verified' if self.idv.verified else 'not verified'}
        days = 21 if category == 'hardship' else 30
        ref, _ = await self._record('complaint', summary[:300], details, status='Received')
        return {'reference': ref, 'say_reference': spoken(ref), 'final_response_within_days': days,
                'next': 'Give the reference and the timeframe; mention AFCA only if they ask what happens if they stay unhappy. ' + MOCK}

    async def request_human_followup(self, reason, summary, urgency='normal'):
        if reason not in FOLLOWUP_REASONS: return {'error': 'invalid_reason', 'allowed': sorted(FOLLOWUP_REASONS)}
        ref, _ = await self._record('escalation', summary[:300], {'reason': reason, 'identity': self.idv.status},
                                    priority='urgent' if urgency == 'urgent' else 'normal', status='Queued')
        return {'reference': ref, 'say_reference': spoken(ref),
                'next': 'Tell the caller a follow-up request was recorded. No live transfer happened. ' + MOCK}

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

    def form_event(self, event):
        """Apply a backend-confirmed form event. Returns reply instructions, or None to ignore it."""
        form_id, status = event.get('form_id'), event.get('status')
        record = self.forms.get(form_id)
        if not record or status not in ('submitted', 'cancelled', 'closed', 'error'):
            self.audit('form_event_ignored', {'form_id': form_id, 'status': status, 'reason': 'unknown form or status'})
            return None
        if record['status'] != 'open':
            self.audit('form_event_ignored', {'form_id': form_id, 'status': status, 'reason': 'form already ' + record['status']})
            return None
        if status != 'error': record['status'] = status
        self.audit('form_event_received', {'form_id': form_id, 'status': status})
        if status == 'submitted':
            ref = event.get('reference', '')
            if not re.fullmatch(r'CBK-\d{4}', ref): return None
            self.created.append({'type': 'callback', 'ref': ref})
            self.facts.update(re.findall(r'\d+', ref) + list(ref[-4:]))  # Whole, or read digit by digit.
            return (f"Frontend event: the caller submitted the lending callback form and the backend saved request {ref}. "
                    f"Acknowledge it naturally and give the reference, read as: {spoken(ref)}. Make clear it's a demo request, so nobody will actually call. "
                    "Then continue the conversation where it left off.")
        if status in ('cancelled', 'closed'):
            return ("Frontend event: the caller closed the callback form without submitting it, so no request was created. "
                    "Acknowledge lightly and ask whether they'd like to carry on or need anything else.")
        fields = [f for f in event.get('fields', []) if f in ('name', 'phone', 'consent', 'form')]
        return ("Frontend event: the callback form couldn't be submitted" + (f" ({', '.join(fields)} needs attention)" if fields else '') +
                ". The form is still open. Briefly offer help; don't ask them to read details aloud.")
