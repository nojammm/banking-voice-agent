import asyncio
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import bank
import banking
import idv
import kb
import server

TODAY = date(2026, 10, 28)
EMILY = ['Emily Carter', 'fourteenth of March 1991', 'unit 4 56 Harbour Street Wollongong NSW 2500']
ARJUN = ['Arjun Raghavan', 'two eleven seventy eight', '17 Kooyong Road Caulfield North Victoria 3161']


def run(coro):
    return asyncio.run(coro)


class TempDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'bank.db'
        patcher = patch.object(bank, 'DB_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)
        self.conn = bank.connect()
        self.addCleanup(self.conn.close)
        self.ui = []

    def workflow(self, session='evidence-' + 'a' * 24, judge=None):
        async def publish(payload): self.ui.append(payload)
        return banking.Workflow.create(session, conn=self.conn, judge=judge, publish=publish,
                                       config=idv.validate_config(json.loads(json.dumps(idv.DEFAULTS))), today=TODAY)

    def verify(self, wf, answers=EMILY):
        wf.idv.begin('tool')
        for text in answers: run(wf.idv.answer(text))
        self.assertTrue(wf.idv.verified)


class AccessControlTests(TempDB):
    PROTECTED = [('get_account_summary', {}), ('get_recent_transactions', {}), ('get_transaction_details', {'transaction_id': 'TXN-1001-03'}),
                 ('get_loans', {}), ('get_loan_applications', {}), ('get_case_status', {}),
                 ('lodge_dispute', {'transaction_id': 'TXN-1001-03', 'dispute_type': 'duplicate', 'description': 'x'})]

    def test_every_protected_tool_refuses_and_starts_verification(self):
        wf = self.workflow()
        for tool, args in self.PROTECTED:
            with self.subTest(tool):
                result = run(wf.call(tool, **args))
                self.assertIn(result['error'], ('verification_required', 'verification_in_progress'))
                self.assertNotIn('2,418', json.dumps(result))
        self.assertEqual((wf.idv.status, wf.idv.step, wf.idv.steps['full_name']['activated_by']), ('in_progress', 'full_name', 'tool'))
        self.assertEqual(self.ui[0], {'type': 'auth', 'state': 'in_progress', 'step': 'full name'})

    def test_data_is_returned_only_for_the_verified_customer(self):
        wf = self.workflow()
        self.verify(wf)
        summary = run(wf.call('get_account_summary'))
        self.assertEqual(summary['accounts'][0], {'account_id': 'ACC-1001', 'name': 'Everyday Choice', 'number': 'ending 3307',
                                                  'type': 'transaction', 'balance': '$2,418.76', 'available': '$2,368.76'})
        self.assertEqual(summary['accounts'][2]['owing'], '$1,284.60')
        self.assertNotIn('48213307', json.dumps(summary))  # Full account numbers never reach the model.
        other = run(wf.call('get_transaction_details', transaction_id='TXN-2001-01'))
        self.assertEqual(other['error'], 'not_found')  # Arjun's transaction isn't visible to Emily.

    def test_failed_verification_keeps_records_locked(self):
        wf = self.workflow()
        wf.idv.begin()
        run(wf.idv.answer('Emily Carter'))
        run(wf.idv.answer('1 January 1990'))
        run(wf.idv.answer('2 January 1990'))
        self.assertEqual(run(wf.call('get_loans'))['error'], 'verification_failed')
        self.assertEqual(run(wf.call('begin_identity_verification', reason='retry'))['status'], 'failed')

    def test_pausing_releases_nothing_and_account_tools_resume(self):
        wf = self.workflow()
        self.assertEqual(run(wf.call('pause_identity_verification', reason='x'))['status'], 'not_started')
        run(wf.call('get_loans'))
        self.assertEqual(run(wf.call('pause_identity_verification', reason='only wants general rates'))['status'], 'paused')
        self.assertEqual(self.ui[-1]['state'], 'paused')
        self.assertIn('rates', json.dumps(run(wf.call('search_banking_knowledge', query='current home loan rates'))))
        blocked = run(wf.call('get_loans'))
        self.assertEqual(blocked['error'], 'verification_required')
        self.assertIn('full name', blocked['next'])
        self.assertEqual(wf.idv.status, 'in_progress')

    def test_loans_applications_and_cases(self):
        wf = self.workflow()
        self.verify(wf, ARJUN)
        loan = run(wf.call('get_loans'))['loans'][0]
        self.assertEqual((loan['balance_owing'], loan['interest_rate'], loan['repayment'], loan['next_due']),
                         ('$612,304.88', '6.24% p.a. variable', '$4,012.55', '1 November'))
        self.assertEqual(run(wf.call('get_loan_applications'))['applications'], [])
        cases = run(wf.call('get_case_status', reference='cmp 2741'))['cases']
        self.assertEqual((cases[0]['status'], cases[0]['response_due']), ('Under review', '11 November'))
        self.assertEqual(cases[0]['opened'], '12 October')
        self.assertEqual(cases[0]['say_reference'], 'C, M, P, 2, 7, 4, 1')  # Read one character at a time, no dash.

    def test_dates_are_ready_to_say(self):
        self.assertEqual(banking.say_date('2026-11-06', TODAY), '6 November')
        self.assertEqual(banking.say_date('2027-01-02', TODAY), '2 January 2027')
        self.assertEqual(banking.say_date('2026-10-12T09:30:00+11:00', TODAY), '12 October')
        self.assertEqual(banking.say_date('soon', TODAY), 'soon')
        wf = self.workflow()
        self.verify(wf)
        dates = [t['date'] for t in run(wf.call('get_recent_transactions', limit=10))['transactions']]
        self.assertTrue(dates and all(not re.search(r'\d{4}-\d', d) for d in dates), dates)
        self.assertIn('2026', wf.facts)  # The year left out may still be said.

    def test_transaction_descriptions_are_ready_to_say(self):
        for raw, said in [('PAYPAL *GAMEVAULT 4029357733', 'Paypal Gamevault'), ('AMAZON AU Marketplace', 'Amazon AU Marketplace'),
                          ('Pending: Shell Coles Express Fairy Meadow', 'Shell Coles Express Fairy Meadow'),
                          ('Rent - Harbour Property Mgmt', 'Rent, Harbour Property Management'), ('Netflix.com', 'Netflix'),
                          ('Car loan repayment LN-2001', 'Car loan repayment'), ('JB Hi-Fi Wollongong', 'JB Hi-Fi Wollongong'),
                          ('AGL Energy', 'AGL Energy'), ("St Mary's Primary School fees", "St Mary's Primary School fees"),
                          ('AMAZON AU Marketplace $129.00: goods not received', 'Amazon AU Marketplace $129.00: goods not received')]:
            with self.subTest(raw): self.assertEqual(banking.say_description(raw), said)
        wf = self.workflow()
        self.verify(wf)
        listed = run(wf.call('get_recent_transactions', search='paypal'))['transactions'][0]
        self.assertEqual(listed['description'], 'Paypal Gamevault')
        self.assertEqual(run(wf.call('get_transaction_details', transaction_id=listed['transaction_id']))['description'], 'Paypal Gamevault')
        lodged = run(wf.call('lodge_dispute', transaction_id=listed['transaction_id'], dispute_type='unauthorised', description='x'))
        merchant = next(c for c in bank.cases(self.conn, 'CUS-10482') if c['type'] == 'fraud')['details']['merchant']
        self.assertEqual((lodged['route'], merchant), ('fraud', 'PAYPAL *GAMEVAULT 4029357733'))  # The record keeps the raw text.

    def test_transactions_filter_and_duplicate_detection(self):
        wf = self.workflow()
        self.verify(wf)
        coles = run(wf.call('get_recent_transactions', search='coles'))['transactions']
        self.assertEqual([t['transaction_id'] for t in coles], ['TXN-1001-02', 'TXN-1001-03', 'TXN-1001-04'])
        detail = run(wf.call('get_transaction_details', transaction_id='TXN-1001-03'))
        self.assertEqual(detail['identical_charges_same_day'], ['TXN-1001-04'])

    def test_backend_failure_is_a_structured_result(self):
        wf = self.workflow()
        self.verify(wf)
        with patch.object(bank, 'accounts', side_effect=RuntimeError('db down')):
            result = run(wf.call('get_account_summary'))
        self.assertEqual(result['error'], 'backend_error')
        kinds = [r['kind'] for r in bank.latest_session(self.conn)['events']]
        self.assertIn('tool_error', kinds)


class CaseWorkflowTests(TempDB):
    def test_a_dispute_waits_for_the_caller_to_confirm_the_transaction(self):
        wf = self.workflow()
        self.verify(wf)
        wf.new_turn()  # The turn that finished verification: the agent finds the payment.
        run(wf.call('get_recent_transactions', search='paypal'))
        early = run(wf.call('lodge_dispute', transaction_id='TXN-1003-04', dispute_type='unauthorised', description='Not me'))
        self.assertEqual(early['error'], 'not_confirmed')
        self.assertIn('ask', early['next'])
        self.assertEqual(wf.created, [])
        self.assertEqual(run(wf.call('lodge_dispute', transaction_id='TXN-1001-04', dispute_type='duplicate', description='x'))['error'],
                         'not_confirmed')  # Never shown to the caller at all.
        wf.new_turn()  # "Yes, that's the one."
        self.assertEqual(run(wf.call('lodge_dispute', transaction_id='TXN-1003-04', dispute_type='unauthorised', description='Not me'))['route'],
                         'fraud')

    def test_dispute_versus_fraud_routes(self):
        wf = self.workflow()
        self.verify(wf)
        dispute = run(wf.call('lodge_dispute', transaction_id='TXN-1001-04', dispute_type='duplicate', description='Charged twice'))
        self.assertEqual(dispute['route'], 'dispute')
        self.assertRegex(dispute['reference'], r'^DSP-\d{4}$')
        again = run(wf.call('lodge_dispute', transaction_id='TXN-1001-04', dispute_type='duplicate', description='again'))
        self.assertEqual((again['error'], again['reference']), ('already_lodged', dispute['reference']))
        self.assertEqual(again['say_reference'], dispute['say_reference'])
        self.assertIn("can't be added", again['next'])  # Nothing to add the caller's update to.
        fraud = run(wf.call('lodge_dispute', transaction_id='TXN-1003-04', dispute_type='unauthorised', description="Didn't make it"))
        self.assertEqual(fraud['route'], 'fraud')
        self.assertIn('lock it straight away', fraud['security_guidance'])
        self.assertNotIn('refund', fraud['next'].replace('never promise a refund', ''))
        kinds = {c['type']: c for c in bank.cases(self.conn, 'CUS-10482')}
        self.assertEqual((kinds['fraud']['priority'], kinds['escalation']['priority']), ('urgent', 'urgent'))
        self.assertEqual([e['kind'] for e in self.ui if e['type'] == 'case'], ['dispute', 'fraud', 'escalation'])

    def test_references_are_speakable_and_hardship_is_one_notice_per_call(self):
        wf = self.workflow()
        first = run(wf.call('record_hardship_notice', cause='reduced_income', situation='Hours cut', affected_products='unknown',
                            expected_duration='unknown', support_wanted='unsure'))
        self.assertEqual(first['say_reference'], ', '.join(first['reference'].replace('-', '')))
        second = run(wf.call('record_hardship_notice', cause='reduced_income', situation='Hours cut in half', affected_products='home loan',
                             expected_duration='3 months', support_wanted='lower repayments'))
        self.assertEqual((second['reference'], second['status']), (first['reference'], 'updated'))
        rows = [c for c in bank.cases(self.conn, session_id=wf.session_id) if c['type'] == 'hardship']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['details']['affected_products'], 'home loan')

    def test_a_complaint_is_one_record_per_call_and_later_details_update_it(self):
        wf = self.workflow()
        first = run(wf.call('lodge_complaint', category='fees_and_charges', summary='Overdrawn fee'))
        second = run(wf.call('lodge_complaint', category='fees_and_charges', summary='Overdrawn fee of 15 dollars for a few cents',
                             desired_outcome='Fee refunded'))
        self.assertEqual((second['reference'], second['status']), (first['reference'], 'updated'))
        self.assertNotIn('final_response_within_days', second)  # Unchanged, so not read again.
        rows = [c for c in bank.cases(self.conn, session_id=wf.session_id) if c['type'] == 'complaint']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['summary'], rows[0]['details']['desired_outcome']),
                         ('Overdrawn fee of 15 dollars for a few cents', 'Fee refunded'))
        self.assertEqual([e['kind'] for e in self.ui if e['type'] == 'case'], ['complaint'])

    def test_one_follow_up_per_call_and_the_demo_disclaimer_once(self):
        wf = self.workflow()
        first = run(wf.call('request_human_followup', reason='other', summary='Wants a person'))
        self.assertIn(banking.MOCK, first['next'])
        second = run(wf.call('request_human_followup', reason='other', summary='Wants a person about a complicated home loan'))
        self.assertEqual((second['reference'], second['status']), (first['reference'], 'updated'))
        rows = [c for c in bank.cases(self.conn, session_id=wf.session_id) if c['type'] == 'escalation']
        self.assertEqual((len(rows), rows[0]['summary']), (1, 'Wants a person about a complicated home loan'))
        complaint = run(wf.call('lodge_complaint', category='service', summary='Nobody called back'))
        self.assertNotIn(banking.MOCK, complaint['next'])
        self.assertIn(banking.MOCK_GIVEN, complaint['next'])

    def test_hardship_and_complaints_work_before_verification(self):
        wf = self.workflow()
        hardship = run(wf.call('record_hardship_notice', cause='reduced_income', situation='Hours cut in half',
                               affected_products='home loan', expected_duration='3 months', support_wanted='lower repayments'))
        self.assertRegex(hardship['reference'], r'^HDS-\d{4}$')
        self.assertIn('not yet linked', hardship['identity'])
        self.assertEqual(wf.idv.status, 'not_started')  # Recording hardship doesn't force verification.
        complaint = run(wf.call('lodge_complaint', category='delay', summary='Still waiting on a fee complaint'))
        self.assertEqual(complaint['final_response_within_days'], 30)
        rows = bank.cases(self.conn, session_id=wf.session_id)
        self.assertTrue(all(r['customer_id'] is None for r in rows))
        self.assertEqual(run(wf.call('lodge_complaint', category='rude', summary='x'))['error'], 'invalid_category')

    def test_multiple_intents_in_one_call(self):
        wf = self.workflow()
        run(wf.call('record_hardship_notice', cause='reduced_income', situation='Hours cut', affected_products='home loan',
                    expected_duration='unknown', support_wanted='unsure'))
        self.verify(wf, ARJUN)
        self.assertIn('$4,012.55', json.dumps(run(wf.call('get_loans'))))
        run(wf.call('lodge_complaint', category='fees_and_charges', summary='Card fee after downgrade, third time chasing'))
        run(wf.call('request_human_followup', reason='hardship_assessment', summary='Hardship and complaint follow-up'))
        note = wf.state_note()
        for kind in ('hardship', 'complaint', 'escalation'): self.assertIn(kind, note)
        self.assertIn('VERIFIED', note)
        linked = bank.cases(self.conn, 'CUS-20917')
        self.assertEqual({c['type'] for c in linked if not c['seeded']}, {'complaint', 'escalation'})  # Hardship came before ID&V.

    def test_repayment_estimate_is_labelled(self):
        wf = self.workflow()
        result = run(wf.call('estimate_loan_repayment', amount=500000, annual_rate_percent=6.09, years=30))
        self.assertEqual(result['repayment'], '$3,026.75')
        self.assertIn('Not an offer', result['label'])
        self.assertEqual(run(wf.call('estimate_loan_repayment', amount=10, annual_rate_percent=6, years=30))['error'], 'out_of_range')


class CallbackFormTests(TempDB):
    def open(self, wf):
        result = run(wf.call('open_callback_form', purpose='home_loan'))
        self.assertEqual(result['status'], 'opened')
        return next(e for e in self.ui if e['type'] == 'form')['form_id']

    def test_submission_is_validated_saved_and_reported_to_agent(self):
        wf = self.workflow()
        form_id = self.open(wf)
        self.assertEqual(run(wf.call('open_callback_form', purpose='home_loan'))['status'], 'already_open')
        self.assertIn('never assume', wf.state_note())
        _, ref, errors = bank.submit_callback(self.conn, form_id, {'name': 'E', 'phone': '123', 'consent': False})
        self.assertEqual((ref, set(errors)), (None, {'name', 'phone', 'consent'}))
        error_reply = wf.form_event({'type': 'form_event', 'form_id': form_id, 'status': 'error', 'fields': ['phone']})
        self.assertIn('phone', error_reply)
        _, ref, errors = bank.submit_callback(self.conn, form_id, {'name': 'Emily Carter', 'phone': '+61 491 570 006', 'consent': True})
        self.assertEqual(errors, {})
        saved = bank.cases(self.conn, session_id=wf.session_id)[0]
        self.assertEqual((saved['ref'], saved['details']['phone'], saved['details']['mock']), (ref, '0491570006', True))
        reply = wf.form_event({'type': 'form_event', 'form_id': form_id, 'status': 'submitted', 'reference': ref})
        self.assertIn(ref, reply)
        self.assertIn("nobody will actually call", reply)
        self.assertIsNone(wf.form_event({'type': 'form_event', 'form_id': form_id, 'status': 'submitted', 'reference': ref}))
        self.assertEqual(bank.submit_callback(self.conn, form_id, {'name': 'Emily', 'phone': '0491570006', 'consent': True})[2],
                         {'form': 'This form was already submitted.'})

    def test_cancel_close_and_forged_events(self):
        wf = self.workflow()
        form_id = self.open(wf)
        self.assertIsNone(wf.form_event({'type': 'form_event', 'form_id': 'F' + '0' * 16, 'status': 'submitted', 'reference': 'CBK-1234'}))
        self.assertIsNone(wf.form_event({'type': 'form_event', 'form_id': form_id, 'status': 'approved'}))
        self.assertIsNotNone(bank.close_form(self.conn, form_id, 'cancelled'))
        reply = wf.form_event({'type': 'form_event', 'form_id': form_id, 'status': 'cancelled'})
        self.assertIn('without submitting', reply)
        self.assertIsNone(bank.close_form(self.conn, form_id, 'closed'))
        self.assertEqual(bank.cases(self.conn, session_id=wf.session_id), [])


class KnowledgeTests(unittest.TestCase):
    def test_retrieval_finds_the_right_document(self):
        cases = {"what's the difference between fixed and variable": 'fixed-vs-variable', 'what documents do I need': 'documents',
                 "my hours got cut and I can't pay the mortgage": 'hardship-rights', 'I was charged twice': 'disputes',
                 "I'm not happy and want to complain": 'complaints', 'how long does a card lock last': 'lock.duration.v1',
                 'how much could I borrow': 'serviceability-buffer', 'what are your current rates': 'indicative-rates'}
        for query, expected in cases.items():
            # Any document on the topic counts: the policy corpus in knowledge/ grows independently.
            with self.subTest(query): self.assertTrue(any(expected.split('.')[0].rstrip('s') in r['id'] for r in kb.search(query)))

    def test_unrelated_questions_return_nothing_and_mock_policy_is_labelled(self):
        self.assertEqual(kb.search("what's the weather in Perth"), [])
        self.assertEqual(kb.LABELS[kb.document('indicative-rates')['type']], 'Demo policy (mock)')
        self.assertTrue(all(r['type'] in kb.LABELS.values() for r in kb.search('interest rates')))
        self.assertTrue(all(d['url'] is None or d['url'].startswith('https://') for d in kb._documents()))

    def test_tool_reports_sources_and_empty_results(self):
        events = []
        async def publish(payload): events.append(payload)
        with tempfile.TemporaryDirectory() as d, closing(bank.connect(Path(d) / 'b.db')) as conn:
            wf = banking.Workflow.create('evidence-' + 'b' * 24, conn=conn, publish=publish, config=idv.validate_config(json.loads(json.dumps(idv.DEFAULTS))))
            self.assertEqual(run(wf.call('search_banking_knowledge', query='weather in Perth'))['results'], [])
            found = run(wf.call('search_banking_knowledge', query='how do I make a complaint'))
        self.assertIn('complaint', found['results'][0]['title'].lower())
        self.assertTrue(all(s['url'].startswith('https://') for s in events[-1]['sources']))


class EndpointTests(TempDB):
    def setUp(self):
        super().setUp()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.url = f'http://127.0.0.1:{self.http.server_port}'
        self.sent = []
        patcher = patch.object(server.live_voice, 'notify_room', side_effect=lambda room, event: self.sent.append((room, event)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, body, token=server.TOKEN):
        headers = {'Content-Type': 'application/json', 'Origin': self.url, 'X-CSRF-Token': token}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, json.dumps(body).encode(), headers)) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as r: return json.loads(r.read())

    def test_form_submit_saves_then_notifies_the_calls_room(self):
        wf = self.workflow()
        run(wf.call('open_callback_form', purpose='refinance'))
        form_id = self.ui[-1]['form_id']
        self.assertEqual(self.post('/api/forms/submit', {'form_id': form_id, 'name': 'A', 'phone': 'x', 'consent': True}, token='bad')[0], 403)
        code, body = self.post('/api/forms/submit', {'form_id': form_id, 'name': 'A', 'phone': '12', 'consent': True})
        self.assertEqual((code, set(body['errors'])), (400, {'name', 'phone'}))
        code, body = self.post('/api/forms/submit', {'form_id': form_id, 'name': 'Arjun R', 'phone': '03 9555 0100', 'consent': True})
        self.assertEqual((code, body['agent_notified']), (200, True))
        room, event = self.sent[-1]
        self.assertEqual((room, event['status'], event['reference']), (wf.session_id, 'submitted', body['reference']))
        self.assertNotIn('phone', json.dumps(event))  # The agent learns the outcome, not the number.
        log = [e for e in bank.latest_session(self.conn)['events'] if e['kind'] == 'form_event_sent']
        self.assertEqual([e['data']['status'] for e in log], ['error', 'submitted'])
        self.assertEqual(self.post('/api/forms/submit', {'form_id': form_id, 'name': 'Arjun R', 'phone': '0395550100', 'consent': True})[0], 400)

    def test_form_close_and_unknown_forms(self):
        wf = self.workflow()
        run(wf.call('open_callback_form', purpose='home_loan'))
        form_id = self.ui[-1]['form_id']
        self.assertEqual(self.post('/api/forms/close', {'form_id': form_id, 'reason': 'deleted'})[0], 400)
        self.assertEqual(self.post('/api/forms/close', {'form_id': form_id, 'reason': 'closed'})[1]['agent_notified'], True)
        self.assertEqual(self.sent[-1][1]['status'], 'closed')
        self.assertEqual(self.post('/api/forms/close', {'form_id': form_id, 'reason': 'closed'})[0], 404)
        self.assertEqual(self.post('/api/forms/submit', {'form_id': '../etc', 'name': 'A B', 'phone': '0491570006', 'consent': True})[0], 404)

    def test_inspector_endpoints_and_reset(self):
        overview = self.get('/api/demo/overview')
        self.assertEqual([c['first_name'] for c in overview['customers']], ['Emily', 'Arjun'])
        self.assertTrue(overview['scenarios'])
        wf = self.workflow()
        self.verify(wf)
        session = self.get('/api/demo/session')['session']
        self.assertEqual((session['id'], session['state']['idv']['status']), (wf.session_id, 'verified'))
        attempts = [e for e in session['events'] if e['kind'] == 'idv_attempt']
        self.assertEqual([a['data']['step'] for a in attempts], ['full_name', 'dob', 'address'])
        later = self.get('/api/demo/session?after=' + str(session['events'][-1]['seq']))['session']
        self.assertEqual(later['events'], [])
        run(wf.call('lodge_complaint', category='service', summary='Rude'))
        self.assertEqual(self.post('/api/demo/reset', {})[0], 400)
        self.assertEqual(self.post('/api/demo/reset', {'confirm': True})[0], 200)
        self.assertTrue(all(c['seeded'] for c in self.get('/api/demo/overview')['cases']))


if __name__ == '__main__':
    unittest.main()
