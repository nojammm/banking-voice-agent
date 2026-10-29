import asyncio
import copy
import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path
import idv

TODAY = date(2026, 10, 28)
EMILY = {'id': 'CUS-1', 'first_name': 'Emily', 'middle_name': 'Rose', 'last_name': 'Carter', 'preferred_name': 'Emily',
         'dob': '1991-03-14', 'address': {'unit': '4', 'number': '56', 'street': 'Harbour', 'street_type': 'Street',
                                          'suburb': 'Wollongong', 'state': 'NSW', 'postcode': '2500'}}
ARJUN = {'id': 'CUS-2', 'first_name': 'Arjun', 'middle_name': None, 'last_name': 'Raghavan', 'preferred_name': 'Arjun',
         'dob': '1978-11-02', 'address': {'unit': None, 'number': '17', 'street': 'Kooyong', 'street_type': 'Road',
                                          'suburb': 'Caulfield North', 'state': 'VIC', 'postcode': '3161'}}
CUSTOMERS = [EMILY, ARJUN]


def config(**overrides):
    """Name first: these tests check spoken answers. CustomerNumberTests covers the default, number-first flow."""
    cfg = copy.deepcopy(idv.DEFAULTS)
    cfg['steps'] = ['full_name', 'dob', 'address']
    for key, value in overrides.items(): cfg[key].update(value)
    return idv.validate_config(cfg)


class Judge:
    """Scripted LLM fallback that records what it was asked."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def __call__(self, task, payload):
        self.calls.append((task, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response


def run(coro):
    return asyncio.run(coro)


class DobNormaliserTests(unittest.TestCase):
    def parse(self, text):
        return idv.parse_dob(text, TODAY)

    def test_spoken_forms_normalise_to_one_canonical_date(self):
        for spoken in ['fourteenth of March nineteen ninety one', '14/03/1991', 'March 14, 1991', 'the 14th of March 91',
                       'fourteen March nineteen ninety-one', 'the fourteenth a march, nineteen ninety one',
                       'march the fourteenth ninety one', '1991-03-14', 'fourteen three ninety one']:
            with self.subTest(spoken): self.assertEqual(self.parse(spoken).value, '1991-03-14')

    def test_split_digits_spoken_zeros_and_ordinal_months(self):
        cases = {'one four zero three one nine nine one': '1991-03-14', 'oh three oh four eighty nine': '1989-04-03',
                 '14031991': '1991-03-14', 'first of eleventh 88': '1988-11-01', 'thirty first three one nine nine eight': '1998-03-31',
                 'five four nineteen eighty': '1980-04-05', 'nineteenth of may two thousand and two': '2002-05-19',
                 'second of the twelfth double oh': '2000-12-02', 'twenty twenty three': None}
        for spoken, expected in cases.items():
            with self.subTest(spoken): self.assertEqual(self.parse(spoken).value, expected)

    def test_two_digit_year_pivot_and_date_order(self):
        self.assertEqual(self.parse('1 2 08').value, '2008-02-01')
        self.assertEqual(self.parse('1 2 09').value, '1909-02-01')
        self.assertEqual(self.parse('1 11 1988').value, '1988-11-01')  # DD/MM first, as in Australia.
        month_first = self.parse('12 25 1990')  # Only month-first is valid, which Australian callers don't say.
        self.assertEqual((month_first.status, month_first.candidates, month_first.month_first), ('ambiguous', ['1990-12-25'], True))

    def test_a_month_first_only_reading_is_a_guess_not_a_wrong_answer(self):
        cfg = config()['dob_match']
        spoken = 'I said three. Okay. $19.91.'  # "nineteen ninety one" heard as money.
        self.assertEqual(self.parse(spoken).status, 'ambiguous')
        self.assertEqual(run(idv.match_dob(spoken, '1991-03-14', cfg, today=TODAY)).outcome, 'uncertain')  # No fallback: clarify.
        judge = Judge({'status': 'complete', 'date': '1991-03-19', 'missing': []})
        self.assertEqual(run(idv.match_dob(spoken, '1991-03-14', cfg, judge, TODAY)).outcome, 'uncertain')
        judge = Judge({'status': 'complete', 'date': '1990-12-25', 'missing': []})
        self.assertEqual(run(idv.match_dob('12 25 1990', '1990-12-25', cfg, judge, TODAY)).outcome, 'pass')
        judge = Judge({'status': 'unclear', 'date': '', 'missing': []})
        self.assertEqual(run(idv.match_dob('12 25 1990', '1990-12-25', cfg, judge, TODAY)).outcome, 'uncertain')

    def test_an_all_number_date_said_month_first_still_matches(self):
        cfg = config()['dob_match']
        for spoken in ['09/01/1999.', '9/1/1999', 'oh nine oh one nineteen ninety nine', '01/09/1999']:
            with self.subTest(spoken): self.assertEqual(run(idv.match_dob(spoken, '1999-09-01', cfg, today=TODAY)).outcome, 'pass')
        for spoken in ['02/09/1999', '9th of January 1999', '09/01/1998']:  # A wrong day, a month said in words, a wrong year.
            with self.subTest(spoken): self.assertEqual(run(idv.match_dob(spoken, '1999-09-01', cfg, today=TODAY)).outcome, 'fail')

    def test_words_that_dont_read_as_one_date_are_a_mishearing(self):
        judge = Judge({'status': 'complete', 'date': '1999-09-09', 'missing': []})
        result = run(idv.match_dob('ninth September first nineteen ninety nine.', '1999-09-01', config()['dob_match'], judge, TODAY))
        self.assertEqual(result.outcome, 'uncertain')

    def test_partial_dates_are_incomplete_not_wrong(self):
        self.assertEqual(self.parse('14th of March').missing, ['year'])
        self.assertEqual(self.parse('March 1991').missing, ['day'])
        self.assertEqual(self.parse('fourteen three').status, 'incomplete')
        self.assertEqual(self.parse('why do you need that').status, 'no_answer')

    def test_impossible_or_future_dates_never_normalise(self):
        for spoken in ['31st of February 1990', '14 March 2030', '45 13 1990']:
            with self.subTest(spoken): self.assertIsNone(self.parse(spoken).value)

    def test_ambiguous_readings_are_reported_not_guessed(self):
        result = self.parse('1 1 1 1988')
        self.assertEqual(result.status, 'ambiguous')
        self.assertEqual(result.candidates, ['1988-01-11', '1988-11-01'])

    def test_exact_match_and_mismatch_are_deterministic(self):
        cfg = config()['dob_match']
        self.assertEqual(run(idv.match_dob('14 March 1991', '1991-03-14', cfg, today=TODAY)).outcome, 'pass')
        wrong = run(idv.match_dob('15 March 1991', '1991-03-14', cfg, today=TODAY))
        self.assertEqual((wrong.outcome, wrong.method), ('fail', 'exact'))

    def test_llm_normalises_ambiguous_input_and_worker_compares(self):
        cfg = config()['dob_match']
        judge = Judge({'status': 'complete', 'date': '1988-11-01', 'missing': []})
        result = run(idv.match_dob('1 1 1 1988', '1988-11-01', cfg, judge, TODAY))
        self.assertEqual((result.outcome, result.method, result.normalized), ('pass', 'llm', '1988-11-01'))
        self.assertNotIn('1988-11-01', str(judge.calls))  # The stored DOB is never sent to the fallback.

    def test_llm_date_outside_the_workers_readings_is_rejected(self):
        cfg = config()['dob_match']
        for response in [{'status': 'complete', 'date': '1991-03-14', 'missing': []},  # Not a reading of the words.
                         {'status': 'complete', 'date': '14/03/1991', 'missing': []},
                         {'status': 'unclear', 'date': '', 'missing': []}, {'verdict': 'yes'}, TimeoutError()]:
            with self.subTest(response):
                result = run(idv.match_dob('1 1 1 1988', '1991-03-14', cfg, Judge(response), TODAY))
                self.assertEqual(result.outcome, 'uncertain')
        self.assertEqual(run(idv.match_dob('1 1 1 1988', '1988-11-01', config(dob_match={'llm_fallback_enabled': False})['dob_match'],
                                           Judge(), TODAY)).outcome, 'uncertain')


class NameMatchingTests(unittest.TestCase):
    def match(self, text, judge=None, **cfg):
        return run(idv.match_name(text, CUSTOMERS, config(name_match=cfg)['name_match'], judge))

    def test_deterministic_matches_ignore_fillers_case_and_spelling_out(self):
        for spoken in ['Emily Carter', "yeah it's emily rose carter", 'My name is Emily Carter.', 'Emily, C A R T E R', 'EMILY CARTER']:
            with self.subTest(spoken):
                result = self.match(spoken)
                self.assertEqual((result.outcome, result.method, result.customer_id), ('pass', 'exact', 'CUS-1'))

    def test_stt_variants_pass_on_fuzzy_score(self):
        for spoken, customer in [('Emily Karter', 'CUS-1'), ('Arjun Ragavan', 'CUS-2'), ('Arjun Ragha van', 'CUS-2'), ('Arjoon Raghavan', 'CUS-2')]:
            with self.subTest(spoken):
                result = self.match(spoken)
                self.assertEqual((result.outcome, result.method, result.customer_id), ('pass', 'fuzzy', customer))
                self.assertGreaterEqual(result.score, 0.55)

    def test_threshold_boundaries(self):
        cfg = config()['name_match']
        clear = {'first': 0.9, 'last': 0.9}
        self.assertEqual(idv.name_decision(0.55, clear, cfg), 'pass')
        self.assertEqual(idv.name_decision(0.5499, clear, cfg), 'llm')
        self.assertEqual(idv.name_decision(0.15, clear, cfg), 'llm')
        self.assertEqual(idv.name_decision(0.1499, clear, cfg), 'fail')
        # A high overall score with a wrong surname goes to the fallback, not an automatic pass.
        self.assertEqual(idv.name_decision(0.9, {'first': 1.0, 'last': 0.2}, cfg), 'llm')

    def test_scores_below_low_threshold_fail_without_llm(self):
        judge = Judge()
        result = self.match('Bo Xu', judge)
        self.assertLess(result.score, 0.15)
        self.assertEqual((result.outcome, judge.calls), ('fail', []))

    def test_middle_band_asks_llm_and_worker_applies_verdict(self):
        judge = Judge({'verdict': 'same_name', 'cause': 'stt_error', 'evidence': 'Win is a common STT rendering of Nguyen.'})
        result = self.match('Emily Smith', judge)
        self.assertTrue(0.15 <= result.score)
        self.assertEqual((result.outcome, result.method, result.customer_id), ('pass', 'llm', 'CUS-1'))
        self.assertEqual(judge.calls[0][0], 'name')
        judge = Judge({'verdict': 'different_name', 'cause': 'different', 'evidence': 'Smith and Carter are different surnames.'})
        self.assertEqual(self.match('Emily Smith', judge).outcome, 'fail')

    def test_invalid_uncertain_or_failed_fallback_never_passes(self):
        for response in [{'verdict': 'uncertain', 'cause': 'unclear', 'evidence': 'Could be either.'},
                         {'verdict': 'same_name', 'cause': 'different', 'evidence': 'contradiction'},
                         {'verdict': 'same_name', 'cause': 'stt_error', 'evidence': ''},
                         {'verdict': 'approve', 'cause': 'stt_error', 'evidence': 'x'}, 'yes', RuntimeError('down')]:
            with self.subTest(response):
                result = self.match('Emily Smith', Judge(response))
                self.assertEqual(result.outcome, 'uncertain')
                self.assertIsNone(result.customer_id)
        self.assertEqual(self.match('Emily Smith', None).outcome, 'fail')  # No fallback: fail closed.

    def test_thresholds_are_configurable(self):
        self.assertEqual(self.match('Emily Karter', high_threshold=0.95).outcome, 'fail')  # Middle band, no judge.

    def test_partial_names_and_other_requests(self):
        self.assertEqual(self.match('Emily').missing, ['last name'])
        for spoken in ['I want to make a complaint', 'why do you need that?', 'can I just speak to someone']:
            with self.subTest(spoken): self.assertEqual(self.match(spoken).outcome, 'no_answer')


class AddressMatchingTests(unittest.TestCase):
    def match(self, text, stored=EMILY, judge=None, carry=None):
        return run(idv.match_address(text, stored['address'], config()['address_match'], judge, carry))

    def test_spoken_numbers_abbreviations_and_states(self):
        for spoken in ['unit 4 56 Harbour Street Wollongong NSW 2500', '4/56 Harbour St, Wollongong, New South Wales 2500',
                       'four fifty six harbour street wollongong n s w two five zero zero', '456 Harbor Street Wolongong NSW 2500']:
            with self.subTest(spoken): self.assertEqual(self.match(spoken).outcome, 'pass')
        self.assertEqual(self.match('seventeen Kooyong Rd Caulfield North Victoria three one six one', ARJUN).outcome, 'pass')

    def test_a_street_name_that_sounds_right_gets_a_second_opinion(self):
        alice = {'address': {'unit': None, 'number': '50', 'street': 'Alice', 'street_type': 'Street', 'suburb': 'Newtown',
                             'state': 'NSW', 'postcode': '2020'}}
        for verdict, outcome in [('same', 'pass'), ('different', 'fail')]:
            judge = Judge({'verdict': verdict, 'evidence': 'Ellis and Alice sound alike.'})
            result = self.match('Fifty. Ellis Street Newtown New South Wales twenty twenty.', alice, judge)
            with self.subTest(verdict): self.assertEqual((result.outcome, result.method), (outcome, 'llm'))
        self.assertEqual(self.match('50 Ellis Street Newtown NSW 2020', alice).outcome, 'fail')  # No second opinion: fail closed.
        self.assertEqual(self.match('50 Ellis Street Newtown NSW 2042', alice, Judge({'verdict': 'same', 'evidence': 'x'})).outcome, 'fail')

    def test_exact_components_must_match(self):
        for spoken in ['unit 5 56 Harbour Street Wollongong NSW 2500', 'unit 4 58 Harbour Street Wollongong NSW 2500',
                       'unit 4 56 Harbour Street Wollongong VIC 2500', 'unit 4 56 Harbour Street Wollongong NSW 2000']:
            with self.subTest(spoken): self.assertEqual(self.match(spoken).outcome, 'fail')

    def test_missing_parts_are_requested_and_carried_forward(self):
        first = self.match('unit 4 56 Harbour Street Wollongong')
        self.assertEqual((first.outcome, first.missing), ('incomplete', ['postcode', 'state']))
        carry = first.components['_carry']
        second = self.match('New South Wales, 2500', carry=carry)
        self.assertEqual(second.outcome, 'pass')

    def test_ambiguous_place_names_use_constrained_fallback(self):
        judge = Judge({'verdict': 'same', 'evidence': '"wool gone" is an STT split of Wollongong.'})
        result = self.match('unit 4 56 Harbour Street wool and gone NSW 2500', judge=judge)
        self.assertEqual((result.outcome, result.method), ('pass', 'llm'))
        self.assertEqual(judge.calls[0][1]['component'], 'suburb')
        self.assertEqual(self.match('unit 4 56 Harbour Street wool and gone NSW 2500', judge=Judge({'verdict': 'maybe'})).outcome, 'uncertain')
        self.assertEqual(self.match('unit 4 56 Harbour Street wool and gone NSW 2500').outcome, 'fail')


class VerificationStateTests(unittest.TestCase):
    def make(self, judge=None, **cfg):
        events = []
        v = idv.Verification(CUSTOMERS, config(**cfg), judge, TODAY, lambda kind, data: events.append((kind, data)))
        return v, events

    def answer(self, v, text):
        return run(v.answer(text))

    def test_structured_activation_and_full_pipeline(self):
        v, events = self.make()
        self.assertIsNone(self.answer(v, 'Emily Carter'))  # Nothing is matched before ID&V starts.
        self.assertTrue(v.begin('tool'))
        self.assertEqual((v.status, v.step, v.steps['full_name']['activated_by']), ('in_progress', 'full_name', 'tool'))
        self.answer(v, 'Emily Carter')
        self.assertEqual((v.step, v.steps['dob']['activated_by']), ('dob', 'progression'))
        self.answer(v, 'fourteenth of march ninety one')
        self.answer(v, 'unit 4, 56 Harbour Street, Wollongong NSW 2500')
        self.assertTrue(v.verified)
        self.assertEqual(v.customer_id, 'CUS-1')
        self.assertIn(('idv_complete', {'status': 'verified'}), events)

    def test_regex_activation_needs_verification_context_and_order(self):
        v, events = self.make()
        v.observe_agent('What name should the specialist ask for?')
        self.assertEqual(v.status, 'not_started')
        v.observe_agent("Sure. To keep your account secure, could I get your date of birth?")
        self.assertEqual(v.status, 'not_started')  # Out of order: name comes first.
        v.observe_agent("Sure, I just need to verify a few details first. What's your full name?")
        self.assertEqual((v.status, v.step, v.steps['full_name']['activated_by']), ('in_progress', 'full_name', 'regex'))
        self.assertTrue(v.steps['full_name']['asked'])
        v.observe_agent("And what's your residential address?")
        self.assertEqual(v.step, 'full_name')
        self.assertEqual(events[-1][0], 'idv_regex_ignored')

    def test_completed_verification_cannot_restart(self):
        v, events = self.make()
        v.begin()
        for text in ['Arjun Raghavan', 'second of November 1978', '17 Kooyong Road Caulfield North VIC 3161']: self.answer(v, text)
        self.assertTrue(v.verified)
        self.assertFalse(v.begin('tool'))
        v.observe_agent("Just to confirm your identity, what's your full name?")
        self.assertTrue(v.verified)
        self.assertIsNone(self.answer(v, 'Emily Carter'))
        self.assertEqual(v.customer_id, 'CUS-2')

    def test_retry_limits_fail_the_whole_verification(self):
        v, _ = self.make()
        v.begin()
        self.answer(v, 'Emily Carter')
        self.answer(v, '1 January 1990')
        self.assertEqual((v.status, v.steps['dob']['attempts']), ('in_progress', 1))
        self.answer(v, '2 January 1990')
        self.assertEqual((v.status, v.step), ('failed', None))
        self.assertFalse(v.begin('tool'))
        self.assertIn('FAILED', v.note())

    def test_incomplete_and_non_answers_do_not_use_attempts(self):
        v, _ = self.make()
        v.begin()
        self.answer(v, 'Emily Carter')
        self.answer(v, 'March 1991')
        self.answer(v, 'sorry, why do you need that?')
        self.assertEqual(v.steps['dob'], {**v.steps['dob'], 'attempts': 0, 'incomplete': 1})
        self.assertEqual(v.step, 'dob')

    def test_repeated_incomplete_answers_eventually_count(self):
        v, _ = self.make()
        v.cfg['max_incomplete'] = 1
        v.begin()
        self.answer(v, 'Emily Carter')
        self.answer(v, 'March 1991')
        self.answer(v, 'March 1991')
        self.assertEqual(v.steps['dob']['attempts'], 1)

    def test_name_identifies_record_that_later_steps_check(self):
        v, _ = self.make()
        v.begin()
        self.answer(v, 'Emily Carter')
        self.answer(v, 'second of November 1978')  # Arjun's DOB does not verify Emily.
        self.assertEqual(v.steps['dob']['attempts'], 1)

    def test_notes_and_snapshots_never_contain_stored_values(self):
        v, _ = self.make()
        v.begin()
        for text in ['Emily', 'Emily Carter', '15 March 1991', 'March 1991']:
            self.answer(v, text)
            note = v.note(v.last)
            for secret in ['Carter', '1991', 'Harbour', '2500', 'Wollongong']:
                self.assertNotIn(secret, note)
        self.assertIn('date of birth', v.note())
        self.assertNotIn('1991-03-14', str(v.snapshot()))


class OffTrackTests(unittest.TestCase):
    """Callers change their minds mid-verification; that must never cost them attempts."""

    def make(self, judge=None):
        events = []
        v = idv.Verification(CUSTOMERS, config(), judge, TODAY, lambda kind, data: events.append((kind, data)))
        v.begin('tool')
        return v, events

    def test_replay_of_a_caller_who_only_wanted_general_rates(self):
        # A real call: verification started, then the caller explained they only wanted the bank's rates.
        # Speech-to-text split the sentence into fragments; "interest rate" used to be scored as a name.
        judge = Judge()
        v, events = self.make(judge)
        for fragment in ["No. No. No. No. You don't need my account.", "I don't need my account interest rate.",
                         'interest rate', "I need what's the current Westpac interest rate."]:
            run(v.answer(fragment))
        self.assertEqual(v.steps['full_name']['attempts'], 0)
        self.assertEqual((v.status, v.paused_by), ('paused', 'worker'))
        self.assertEqual(judge.calls, [])
        self.assertIn('PAUSED', v.note())
        self.assertIn('begin_identity_verification', v.note())
        self.assertIn(('idv_paused', {'source': 'worker', 'step': 'full_name', 'reason': 'consecutive turns without an answer'}), events)

    def test_fallback_can_say_the_words_were_not_a_name(self):
        judge = Judge({'verdict': 'not_a_name', 'cause': 'not_a_name', 'evidence': 'These are banking words, not a name.'})
        result = run(idv.match_name('Emily Smith', CUSTOMERS, config()['name_match'], judge))
        self.assertEqual((result.outcome, result.customer_id), ('no_answer', None))

    def test_model_pause_keeps_attempts_and_account_need_resumes(self):
        v, events = self.make()
        run(v.answer('Bo Xu'))
        self.assertEqual(v.steps['full_name']['attempts'], 1)
        self.assertTrue(v.pause('model', 'wants general rates'))
        self.assertFalse(v.pause('model'))  # Only an active check can be paused.
        v.observe_agent('Our home loan rates start from 6.09 percent.')
        self.assertEqual(v.status, 'paused')
        self.assertTrue(v.begin('tool'))
        self.assertEqual((v.status, v.step, v.steps['full_name']['attempts']), ('in_progress', 'full_name', 1))
        self.assertEqual(events[-1], ('idv_resumed', {'source': 'tool', 'step': 'full_name'}))
        v.pause('model')
        v.observe_agent("Sure, can I grab your full name?")
        self.assertEqual(v.status, 'in_progress')  # The agent went back to the question.

    def test_volunteered_name_while_paused_resumes_and_counts(self):
        v, events = self.make()
        v.pause('model', 'caller declined')
        self.assertIsNone(run(v.answer('What about personal loans?')))
        self.assertTrue(v.might_answer('Actually, my full name is Emily Ross Carter.'))
        self.assertFalse(v.might_answer('Okay, what about personal loans?'))
        run(v.answer('Actually, my full name is Emily Ross Carter.'))
        self.assertEqual((v.status, v.step), ('in_progress', 'dob'))
        self.assertIn(('idv_resumed', {'source': 'caller', 'step': 'full_name'}), events)
        v.pause('model')
        run(v.answer('Bo Xu'))  # A wrong name volunteered while paused still costs an attempt: no free guesses.
        self.assertEqual(v.steps['full_name']['attempts'], 0)  # Already passed; now paused at the date of birth.
        self.assertIsNone(run(v.answer('I could borrow 500000 maybe')))  # Numbers never resume the date step.
        self.assertEqual((v.status, v.steps['dob']['attempts']), ('paused', 0))

    def test_paused_wrong_name_costs_an_attempt(self):
        v, _ = self.make()
        v.pause('model')
        run(v.answer('Bo Xu'))
        self.assertEqual((v.status, v.steps['full_name']['attempts']), ('in_progress', 1))

    def test_finished_verification_cannot_be_paused(self):
        v, _ = self.make()
        for text in EMILY_ANSWERS: run(v.answer(text))
        self.assertFalse(v.pause('model'))
        self.assertTrue(v.verified)

    def test_one_uncertain_answer_is_free_then_they_count(self):
        judge = Judge(*[{'verdict': 'uncertain', 'cause': 'unclear', 'evidence': 'Could be a mishearing.'}] * 3)
        v, _ = self.make(judge)
        run(v.answer('Emily Smith'))
        self.assertEqual((v.steps['full_name']['uncertain'], v.steps['full_name']['attempts']), (1, 0))
        self.assertIn('spell their surname', v.note(v.last))
        run(v.answer('Emily Smith'))
        self.assertEqual(v.steps['full_name']['attempts'], 1)

    def test_non_answer_note_never_claims_a_mishearing(self):
        v, _ = self.make()
        run(v.answer('Why do you need that?'))
        note = v.note(v.last)
        self.assertIn("don't say you didn't catch it", note)
        self.assertIn('pause_identity_verification', note)



class QuestionsAreNotAnswersTests(unittest.TestCase):
    """A question about the caller's own details, or about something else, is never a partial answer."""
    def dob(self, text):
        return run(idv.match_dob(text, EMILY['dob'], config()['dob_match'], None, TODAY))

    def address(self, text, carry=None):
        return run(idv.match_address(text, EMILY['address'], config()['address_match'], None, carry))

    def test_questions_at_the_dob_step_are_not_answers(self):
        for text in ["Is my birthday in March? Just tell me if it's March.", 'is my birthday in March',
                     'Oh sorry, quick one before I forget, are your branches open on Saturdays?',
                     'Oh sorry, quick one before I forget, are your branches open on Saturdays',
                     "What's the year on file?", 'Was I born in 1991?']:
            with self.subTest(text): self.assertEqual(self.dob(text).outcome, 'no_answer')

    def test_a_whole_date_still_counts_even_with_a_rising_question_mark(self):
        self.assertEqual(self.dob('14 March 1991?').outcome, 'pass')
        self.assertEqual(self.dob('Is it the 14th of March 1992?').outcome, 'fail')
        self.assertEqual(self.dob('March 1991').outcome, 'incomplete')  # Plain partial answers are unchanged.

    def test_questions_at_the_address_step_are_not_answers(self):
        for text in ['Is my postcode 2500?', 'Do you have me in Wollongong', 'quick one, are your branches open on Saturdays?',
                     'Is it Harbour Street?']:
            with self.subTest(text): self.assertEqual(self.address(text).outcome, 'no_answer')
        carry = self.address('unit 4 56 Harbour Street Wollongong').components['_carry']
        self.assertEqual(self.address('Is it New South Wales?', carry).outcome, 'no_answer')
        self.assertEqual(self.address('Unit 4, 56 Harbour Street, Wollongong NSW 2500?').outcome, 'pass')

    def test_question_turns_cost_nothing_and_the_note_never_acknowledges_a_part(self):
        v = idv.Verification(CUSTOMERS, config(), None, TODAY)
        v.begin()
        run(v.answer('Emily Carter'))
        result = run(v.answer("Is my birthday in March? Just tell me if it's March."))
        self.assertEqual(result.outcome, 'no_answer')
        self.assertEqual((v.step, v.steps['dob']['attempts'], v.steps['dob']['incomplete']), ('dob', 0, 0))
        run(v.answer('March 1991'))
        self.assertIn("without saying which part they gave", v.note(v.last))
        self.assertEqual(run(v.answer('the 14th of March 1991')).outcome, 'pass')

class VolunteeredNameTests(unittest.TestCase):
    """A caller who already said their name isn't asked to repeat it, and "I just told you" is still an answer."""
    def match(self, text):
        return run(idv.match_name(text, CUSTOMERS, config()['name_match'], Judge()))

    def test_a_name_inside_a_longer_turn_counts_only_on_a_clear_match(self):
        for text in ["I just told you, it's Emily Carter.", "Hi, it's Emily Carter here. Can you check my account balance for me?",
                     'Emily Carter, sorry, can I also ask about my loan?']:
            with self.subTest(text): self.assertEqual((self.match(text).outcome, self.match(text).customer_id), ('pass', 'CUS-1'))
        for text in ['I just told you, it was Bo Xu.', 'My wife, Emily Carter, wants to know her balance.',
                     "I'm calling for Emily Carter, she's my wife. Can I check her balance?", 'Why do you need that?']:
            with self.subTest(text): self.assertEqual(self.match(text).outcome, 'no_answer')

    def test_the_turn_that_started_verification_is_matched_once(self):
        v = idv.Verification(CUSTOMERS, config(), Judge(), TODAY)
        self.assertIsNone(run(v.take_volunteered_name('Hi, it\'s Emily Carter here.')))  # Not started yet.
        v.begin('tool')
        self.assertIsNone(run(v.take_volunteered_name('Hi, this is Bo Xu, can you check my balance?')))
        self.assertEqual((v.step, v.steps['full_name']['attempts'], v.history), ('full_name', 0, []))  # A miss costs nothing.
        self.assertEqual(run(v.take_volunteered_name("Hi, it's Emily Carter here. Can you check my balance?")).outcome, 'pass')
        self.assertEqual(v.step, 'dob')
        self.assertIsNone(run(v.take_volunteered_name("It's Emily Carter.")))  # Only before any answer.


class DobAcrossTurnsTests(unittest.TestCase):
    """A date of birth given in two turns ("November 1978", then "the 2nd") is joined, like the address."""
    def make(self):
        v = idv.Verification(CUSTOMERS, config(), None, TODAY)
        v.begin('tool')
        run(v.answer('Arjun Raghavan'))
        return v

    def test_parts_from_two_turns_are_joined(self):
        for first, second in [('November 1978', 'the 2nd'), ('the 2nd of November', '1978'), ('1978', 'the second of November'),
                              ('November', 'the 2nd, 1978')]:
            with self.subTest(first=first, second=second):
                v = self.make()
                self.assertEqual(run(v.answer(first)).outcome, 'incomplete')
                self.assertEqual(run(v.answer(second)).outcome, 'pass')
                self.assertEqual((v.step, v.steps['dob']['attempts']), ('address', 0))

    def test_joined_wrong_date_fails_and_the_carry_is_cleared(self):
        v = self.make()
        run(v.answer('November 1978'))
        self.assertEqual(v.note(v.last).count('day'), 1)
        self.assertEqual(run(v.answer('the 3rd')).outcome, 'fail')
        self.assertEqual(v.dob_carry, {})
        self.assertEqual(run(v.answer('the 2nd')).outcome, 'incomplete')  # Nothing left to join with.

    def test_a_question_neither_joins_nor_clears_the_parts(self):
        v = self.make()
        run(v.answer('November 1978'))
        self.assertEqual(run(v.answer('Is it the 2nd you have?')).outcome, 'no_answer')
        self.assertEqual(run(v.answer('the 2nd')).outcome, 'pass')



class DobPiecesWithSttArtifactsTests(unittest.TestCase):
    """The call that found this: "The fourteenth.", "I said three.", "$19.91.", "March 14." never joined, and the
    run of incomplete turns cost a failed attempt. Speech-to-text writes a spoken year as money ("$19.91" = 1991)."""
    ADDRESS = 'Unit 4, 56 Harbour Street, Wollongong NSW 2500'

    def make(self, judge=None, **cfg):
        v = idv.Verification(CUSTOMERS, config(), judge, TODAY)
        v.cfg.update(cfg)
        v.begin('tool')
        run(v.answer('Emily Carter'))
        return v

    def test_the_calls_sequence_verifies_without_using_an_attempt(self):
        v = self.make()
        outcomes = [run(v.answer(t)).outcome for t in ['The fourteenth.', 'I said three.', '$19.91.']]
        self.assertEqual(outcomes, ['incomplete', 'incomplete', 'pass'])
        tail = run(v.answer('March 14.'))  # Said again after it already passed: the end of the last answer.
        self.assertEqual((tail.outcome, tail.reason), ('no_answer', 'previous_step'))
        self.assertEqual(run(v.answer(self.ADDRESS)).outcome, 'pass')
        self.assertEqual((v.status, v.steps['dob']['attempts'], v.steps['address']['attempts']), ('verified', 0, 0))

    def test_pieces_in_other_orders_and_forms_are_joined(self):
        for turns in [['I said three.', '$19.91.', 'March 14.'], ['March 14.', '$19.91.'], ['$19.91.', '14', '3'],
                      ['The fourteenth.', 'nineteen ninety one', 'three'], ['three', 'March', 'the 14th', '91'],
                      ['the fourteenth', 'I said three. Okay. $19.91.'], ['The 14th.', '3', '$1,991.'],
                      ['fourteen three', '$19.91']]:
            with self.subTest(turns=turns):
                v = self.make()
                for text in turns[:-1]: self.assertEqual(run(v.answer(text)).outcome, 'incomplete')
                self.assertEqual(run(v.answer(turns[-1])).outcome, 'pass')
                self.assertEqual((v.step, v.steps['dob']['attempts']), ('address', 0))

    def test_a_bare_number_fills_the_open_part(self):
        v = self.make()
        run(v.answer('The fourteenth.'))
        result = run(v.answer('I said three.'))
        self.assertEqual(result.missing, ['year'])  # Three is the month, not a new day.
        self.assertIn('missing part (year)', v.note(v.last))

    def test_a_joined_wrong_date_still_fails(self):
        v = self.make()
        for text in ['The fourteenth.', 'I said three.']: run(v.answer(text))
        self.assertEqual(run(v.answer('$19.92.')).outcome, 'fail')
        self.assertEqual(v.steps['dob']['attempts'], 1)

    def test_an_ambiguous_joined_date_goes_to_the_fallback_with_everything_said(self):
        cfg = config()['dob_match']
        judge = Judge({'status': 'complete', 'date': '1991-04-03', 'missing': []})
        v = self.make(judge)
        run(v.answer('three'))
        run(v.answer('4'))
        result = run(v.answer('$19.91.'))
        self.assertEqual(result.outcome, 'uncertain')  # A joined guess that doesn't match is a mishearing, not a miss.
        self.assertEqual(v.steps['dob']['attempts'], 0)
        task, payload = judge.calls[0]
        self.assertEqual(task, 'dob')
        self.assertEqual(payload['spoken'], 'three 4 $19.91.')
        self.assertNotIn('1991-03-14', str(judge.calls))
        judge = Judge({'status': 'complete', 'date': '1991-03-04', 'missing': []})
        result = run(idv.match_dob('$19.91.', '1991-03-04', cfg, judge, TODAY, {'either': [3, 4], 'said': 'three 4'}))
        self.assertEqual((result.outcome, result.components['candidates']), ('pass', ['1991-03-04', '1991-04-03']))

    def test_turns_that_add_a_part_are_not_counted_as_misses(self):
        v = self.make(max_incomplete=1)
        for text in ['The fourteenth.', 'I said three.']: run(v.answer(text))
        self.assertEqual((v.steps['dob']['incomplete'], v.steps['dob']['attempts']), (1, 0))
        run(v.answer('I said three.'))  # Adds nothing: repeated misses still count.
        self.assertEqual(v.steps['dob']['attempts'], 1)

    def test_a_year_heard_as_money_at_the_address_step_is_the_previous_answer(self):
        self.assertTrue(idv._dob_fragment('$19.91.'))
        self.assertFalse(idv._dob_fragment('19 91 Harbour Street'))
        v = self.make()
        run(v.answer('14 March 1991'))
        tail = run(v.answer('$19.91.'))
        self.assertEqual((tail.outcome, tail.reason, v.steps['address']['attempts']), ('no_answer', 'previous_step', 0))

class SplitUtteranceTests(unittest.TestCase):
    """Speech-to-text can cut one answer into two turns; the tail belongs to the step that just passed."""
    def make(self):
        v = idv.Verification(CUSTOMERS, config(), None, TODAY)
        v.begin('tool')
        run(v.answer('Emily Carter'))
        return v

    def test_the_tail_of_a_date_of_birth_never_counts_as_an_address(self):
        # The call that found this: "1991. March fourteenth" arrived as two turns about a second apart.
        for turns in [['fourteenth of March', '1991.', 'March fourteenth'], ['14 March 1991', 'March fourteenth'],
                      ['14 March 1991', 'the 14th'], ['14th of March 1991', '1991']]:
            with self.subTest(turns=turns):
                v = self.make()
                for text in turns[:-1]: run(v.answer(text))
                self.assertEqual(v.step, 'address')
                tail = run(v.answer(turns[-1]))
                self.assertEqual((tail.outcome, tail.reason), ('no_answer', 'previous_step'))
                self.assertEqual(v.steps['address']['attempts'], 0)
                note = v.note(v.last)
                self.assertIn('end of their previous answer', note)
                self.assertNotIn("didn't catch", note.replace("Don't react to them or say you didn't catch", ''))
                self.assertEqual(run(v.answer('Unit 4, 56 Harbour Street, Wollongong NSW 2500')).outcome, 'pass')
                self.assertEqual(v.status, 'verified')

    def test_addresses_with_dates_or_months_in_them_still_count(self):
        for text in ['Unit 4, 56 Harbour Street, Wollongong NSW 2500', 'Wollongong, 2500', 'New South Wales', '12 May Street',
                     '14th Avenue, Wollongong', '4/56 Harbour St Wollongong New South Wales 2500']:
            with self.subTest(text): self.assertFalse(idv._dob_fragment(text))
        v = self.make()
        run(v.answer('14 March 1991'))
        self.assertEqual(run(v.answer('Unit 4, 57 Harbour Street, Wollongong NSW 2500')).outcome, 'fail')  # A wrong address still fails.


EMILY_ANSWERS = ['Emily Carter', '14 March 1991', 'unit 4 56 Harbour Street Wollongong NSW 2500']


class CustomerNumberTests(unittest.TestCase):
    """The default flow: the number typed into the box finds one record, and every spoken answer is checked against it alone."""
    RECORDS = [{**EMILY, 'id': 'CUS-10001'}, {**ARJUN, 'id': 'CUS-20002'}]

    def make(self):
        v = idv.Verification(self.RECORDS, idv.validate_config(copy.deepcopy(idv.DEFAULTS)), None, TODAY)
        v.begin('tool')
        return v

    def test_typed_forms_of_the_number_are_the_same(self):
        for text in ['CUS-10001', 'cus 10001', 'cus-10001', ' 10001 ']:
            with self.subTest(text): self.assertEqual(idv.normalize_customer_number(text), 'CUS-10001')
        for text in ['CUS-1000', 'CUS-100011', 'Emily Carter', '', None]:
            with self.subTest(text): self.assertIsNone(idv.normalize_customer_number(text))

    def test_only_the_box_gives_the_number_and_the_name_is_checked_against_that_record(self):
        v = self.make()
        self.assertEqual(v.step, 'customer_number')
        self.assertIsNone(run(v.answer('CUS-10001')))  # Spoken, it doesn't count and costs nothing.
        self.assertIsNone(run(v.answer('Emily Carter')))
        self.assertEqual((v.step, v.history), ('customer_number', []))
        self.assertIn('box', v.note())
        self.assertIn("can't get into their records", v.note())  # They've spoken twice without typing it.
        self.assertNotIn("can't get into their records", self.make().note())  # The first ask isn't a refusal.
        self.assertEqual(v.enter_customer_number('cus 10001').outcome, 'pass')
        self.assertEqual((v.step, v.customer_id), ('full_name', 'CUS-10001'))
        self.assertIn('three quick details', v.note(v.last))
        self.assertIsNone(v.enter_customer_number('CUS-20002'))  # Once found, the record can't be swapped.
        self.assertEqual(run(v.answer('Arjun Raghavan')).outcome, 'fail')  # A real customer, but not this one.
        self.assertEqual(v.customer_id, 'CUS-10001')
        for text in EMILY_ANSWERS: run(v.answer(text))
        self.assertEqual((v.status, v.customer_id), ('verified', 'CUS-10001'))

    def test_unknown_numbers_use_attempts_and_never_reach_the_note(self):
        v = self.make()
        self.assertEqual(v.enter_customer_number('CUS-99999').outcome, 'fail')
        note = v.note(v.last)
        self.assertIn("didn't match a record", note)
        self.assertNotIn('99999', note)
        self.assertIsNone(v.enter_customer_number('not a number'))  # Malformed input is the backend's to reject.
        v.enter_customer_number('CUS-99998'); v.enter_customer_number('CUS-99997')
        self.assertEqual((v.status, v.customer_id), ('failed', None))

    def test_a_name_first_profile_cant_put_the_number_after_the_name(self):
        cfg = copy.deepcopy(idv.DEFAULTS)
        cfg['steps'] = ['full_name', 'customer_number', 'dob']
        with self.assertRaises(ValueError): idv.validate_config(cfg)
        cfg['steps'] = ['full_name', 'dob', 'address']
        self.assertEqual(idv.validate_config(cfg)['steps'][0], 'full_name')


@unittest.skipUnless(importlib.util.find_spec('yaml'), 'PyYAML ships in .venv-livekit')
class ConfigTests(unittest.TestCase):
    def write(self, text):
        handle = tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False)
        handle.write(text); handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_repository_config_keeps_demo_defaults(self):
        cfg = idv.load_config('banking-evidence-local')
        self.assertEqual((cfg['name_match']['high_threshold'], cfg['name_match']['low_threshold']), (0.55, 0.15))
        self.assertTrue(cfg['name_match']['llm_fallback_enabled'])
        strict = idv.load_config('banking-strict')
        self.assertEqual(strict['name_match']['high_threshold'], 0.85)
        self.assertEqual(strict['dob_match'], cfg['dob_match'])  # Unlisted settings keep their defaults.

    def test_per_assistant_override_does_not_leak(self):
        path = self.write('assistants:\n  a:\n    name_match: {high_threshold: 0.7}\n')
        self.assertEqual(idv.load_config('a', path)['name_match']['high_threshold'], 0.7)
        self.assertEqual(idv.load_config('b', path)['name_match']['high_threshold'], 0.55)

    def test_invalid_settings_are_rejected(self):
        for text in ['defaults:\n  name_match: {high_threshold: 0.1, low_threshold: 0.5}\n',
                     'defaults:\n  name_match: {hi: 0.5}\n', 'defaults:\n  steps: [dob, full_name]\n',
                     'defaults:\n  dob_match: {max_attempts: 0}\n', 'defaults:\n  auto_pause_after_non_answers: -1\n']:
            with self.subTest(text), self.assertRaises(ValueError): idv.load_config('x', self.write(text))


if __name__ == '__main__':
    unittest.main()
