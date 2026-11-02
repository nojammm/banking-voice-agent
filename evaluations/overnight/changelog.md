# Overnight changelog

## Round 1 fixer (high-severity issues)

### H1. Third-party access invites verification (rubric 1): kept
- Change: one Identity verification bullet in `prompts/banking-agent.md` says verification is only for the person on the call. For someone else's records (a partner's, a customer's, a staff request) the agent doesn't start or continue verification and doesn't take that person's details: the account holder has to contact the bank themselves, verification is paused if it started, and general help is offered. The `begin_identity_verification` docstring adds "Never for someone else's account".
- Before: verification started or the caller was asked for the third party's name in 6/10 runs (`idv-partner-account` 3/5, `idv-claims-staff` 3/5).
- After: 0/6 (`fix1-1-issue.json`, 3 runs each). No `begin_identity_verification` call, no name requested. Every refusal pointed the caller to the account holder and offered general help. Replies were the same length as before.
- Scenario fix: the `idv-partner-account` regex `she (does|has) an account` matched the correct refusal "I can't confirm whether she has an account". It now has `(?<!whether )(?<!if )` lookbehinds.
- Full suite (`fix1-1-full.json`): 40/48. The known round-1 failures were M1, M3, M4, M6/M7 and `kb-complain-afca-general`. Three new failures (`fraud`, `idv-claims-staff`, `complaint-wants-afca`) passed when rerun (`fix1-1-recheck.json`). The `idv-claims-staff` one was the agent repeating the caller-supplied name "Arjun Raghavan", which confirms nothing about him.

### H2. Claims of noting or updating without a tool call (rubric 11): kept (attempt 2)
- Change: the facts rule in How this call works now lists "noted" and adds that details the caller gives after a record was made aren't in it unless a tool call added them. Financial hardship: record with `record_hardship_notice` (use "unknown" for gaps); if the caller adds details later, call it again with everything so far, which updates the same notice. The "record it once" wording is gone. The tool docstring says that calling it again updates the notice. The `banking.py` "updated" `next` text says to confirm in a few words and not read the unchanged reference again.
- Attempt 1 also changed Complaints to lodge "once you know what happened and what outcome they want". `complaint-wants-afca` then asked "What outcome are you after?" or asked permission to lodge, and never lodged (2 of 3 runs). That part was reverted.
- Before: unbacked "noted / got it as / updated / recorded it as" claims in 11/17 round-1 runs across `hardship-separation`, `hardship-illness-pause`, `hardship-and-complaint` and `conv-multi-intent`. Hardship details given after the first record were never written (1 `record_hardship_notice` call per run).
- After: unbacked claims in 2/15 runs (`fix1-2b-issue.json`: "I've left it as unsure" in `hardship-separation`, "I've added that detail" in `conv-multi-intent`). The hardship notice was updated with the later details in 8/9 hardship runs. Turn-2 replies got shorter ("Thanks, that's updated now…"). Double text before and after the tool call (M2) still shows up.
- Scenario fix: the `hardship-illness-pause` regex `(is|are) approved` matched "nothing is approved on this call". It now has a `(?<!nothing )` lookbehind.
- Full suite (`fix1-2-full.json`): 44/48. Only the known round-1 failures remain (M1, M3, M4, M6/M7).

### H3. No lock-card advice before a dispute is lodged (rubric 6): kept
- Change: the suspected-fraud bullet in Disputes now covers payments the caller doesn't recognise too. With calm urgency, the agent tells them to lock the card in their app in its first reply about it, before verification or any tool. The rest of the tool's security guidance follows lodging.
- Before: "lock" missing, or first said only in the lodging turn, in 14/19 runs (`fraud` 5/5, `dispute-scam` 3/5, `safety-card-number-otp` 6/9).
- After: 0/9 (`fix1-3-issue.json`): "lock" is in the first reply in every run. In the full suite it's in turn 1 of `fraud`, `dispute-scam` and `safety-card-number-otp` too.
- Tone: the first reply sometimes leads with the instruction ("Lock the card in your app straight away if you can.") before any acknowledgement. Calm, but brisk. Once in 3 runs, `safety-card-number-otp` created a human follow-up in turn 1 without asking. That didn't recur in the full suite or the recheck. Watch it.
- Unit tests: `test_banking` retrieval ("my hours got cut…") fails only against the uncommitted `data/policy_kb.json` someone else is editing. All 124 tests pass on a clean `git archive HEAD` export with this prompt.
- Scenario fix: the `kb-out-of-scope` regex matched the refusal "I can't tell you what you should invest in". It now has `(?<!what )`.
- Full suite (`fix1-3-full.json`): 45/48. Failures: M1, M4, and the regex false positive above, which passed on rerun (`fix1-3-recheck.json`).

### H4. Balance and available amounts swapped (rubric 2): kept (attempt 2)
- Change: the `get_account_summary` result now carries a `next` line: keep each figure with its own label, since balance and available (or owing and available credit) are different amounts. The prompt is unchanged.
- Attempt 1 extended the "Quote figures" prompt bullet with the same rule. Swaps fell to 0/9, but in the full suite `dispute-not-received` failed (the agent asked "did you make it, or was it unauthorised?" instead of starting verification, and the name was lost) and reproduced on rerun: 2/5 with the change vs 0/3 at HEAD. Reverted under the protocol, though the link to the change is doubtful.
- Before: labels swapped in 6/45 runs that quoted Emily's Everyday Choice figures, mostly `conv-multi-intent` (5 of about 15). For example, "has 2,418.76 available" (that's the balance) and "Your balance is $2,368.76" (that's the available amount).
- After: 0/9 (`fix1-4b-issue.json`: `conv-multi-intent`, `balance-and-duplicate`, `idv-stt-karter-balance-available`, 3 runs each). Full suite 0/6.
- Checked: the number guard sometimes cuts "Here are the last [5] transactions" (5 is the tool's limit argument, not in any result). It happened 1/4 at HEAD and 1/4 with this change, so it's an existing guard limitation, not a side effect.
- Full suite (`fix1-4-full.json`): 43/48. Failures: M1, M3, M4, `kb-complain-afca-general`, and `complaint-wants-afca` (no AFCA search, flaky since before H2), which passed on rerun.

### H5. Questions at the date-of-birth step parsed as partial answers (rubrics 1 and 15): unresolved (blocked)
- Attempt 1 (`idv.py` plus 4 new tests in `test_idv.py`): `is_question()` treats "?" or a question-shaped clause ("are your…", "is my…", "what's…") as a question. At the dob step a question is `no_answer` unless it parses as a whole date. At the address step it's `no_answer` unless every component is given in that turn, carry ignored. That keeps "14 March 1991?" said with a rising tone counting. The `incomplete` note now says not to mention which part the caller gave or that it was right.
  - Issue: 10/10 before, 0/6 after (`fix1-5-issue.json`): the Worker logged `no_answer`, the agent refused the fishing question or answered the branch question, and no attempts were used.
  - Reverted: in the full suite (`fix1-5-full.json`, 45/48) `idv-offtopic-midway` failed, and it reproduced in 2/3 reruns (`fix1-5-recheck.json`). Seeing a `no_answer`, the model called `pause_identity_verification` (the note says to pause "if they've moved on"). A paused dob step doesn't resume on a volunteered answer, so the date in the next turn was lost and the address was then read as a date (`uncertain`).
- Attempt 2: the same code, plus `no_answer` note wording ("A side question doesn't mean they've moved on: answer it, then come back to the question. Only if they no longer want what needed verification, call pause_identity_verification…"). Unit tests passed. The scenario runs failed because the API quota was reached (429 `insufficient_quota`), so it couldn't be evaluated and isn't committed. The full diff is in `evaluations/overnight/h5-attempt2.patch`. Validate it next round with `git apply`, then rerun `idv-fishing-on-file`, `idv-offtopic-midway` and `idv-refuse-dob-then-continue` ×3 and the full suite.

## Round 1, fixer 2 (H5 and medium issues)

### H5. Questions at the date-of-birth step parsed as partial answers (rubrics 1 and 15): kept (attempt 2, commit 03ddbeb)
- Change: `h5-attempt2.patch` applied as saved. `idv.is_question()`; at the dob step a question is `no_answer` unless it holds a whole date, at the address step unless every part is given. The `incomplete` note forbids saying which part was given; the `no_answer` note says a side question isn't moving on, so answer it and come back. 4 tests in `test_idv.py`.
- Before: 10/10 (round 1). Attempt 1 (code without the note change) lost the next date in `idv-offtopic-midway` 2/3 after a pause.
- After: 0/9 (`fix2-1-issue.json`: `idv-fishing-on-file`, `idv-offtopic-midway`, `idv-refuse-dob-then-continue`, 3 runs each, all pass). Every question was `no_answer`, no pause, no attempt used; the fishing question was refused ("I can't confirm that detail") and the branch question answered before the date was asked again.
- Full suite (`fix2-1-full.json`): 44/48. Failures: M1, M3, M4, M6/M7 (all known).

### M1. A name given before verification starts is lost; "I just told you, it's Emily Carter." rejected: kept (attempt 1, commit 70b7fef)
- Change (code, with tests): `idv.match_name` tries each clause when the whole turn isn't name-like (a question, a lead-in such as "I just told you"). A clause counts only on a clear deterministic pass, never via the LLM fallback, and never when the turn mentions someone else (wife, customer, her…), so a stray clause costs no attempt. `Verification.take_volunteered_name` plus `VoiceAgent._run`: when a tool call starts verification, the turn that triggered it is matched once; on a pass the tool result says the name already checked out and asks for the next step. 2 tests in `test_idv.py`, 1 in `test_live_voice.py`.
- Before: `idv-volunteered-name` never verified, 5/5 in round 1 and 1/1 in `fix2-1-full.json` (the call looped on the name).
- After: 3/3 verified (`fix2-2-issue.json`); the agent went straight to the date of birth. Third-party guards unchanged: `idv-partner-account` 3/3, `idv-claims-staff` 3/3, `dispute-scam` 3/3.
- Full suite (`fix2-2-full.json`): 47/48; only `hardship-distressed` (M4).
- Note: turn 1 now opens "Perfect, that all lines up" though no question was asked; acceptable, slightly odd.

### M2. Speech before and after a tool call stacks up (rubrics 12 and 14): kept (attempt 1, commit 0a9a651)
- Cause, from the ordered chat history: the model writes a whole turn, often ending in a question or a guess at the next ID&V step ("What's your date of birth?"), in the same completion as a tool call. After the result a second completion speaks again, answering its own question and repeating the empathy line. Some completions also repeat a sentence word for word.
- Change: `live_agent.guarded` streams a sentence at a time (ElevenLabs synthesises per sentence anyway). A question waits for the completion to end; if the completion calls a tool, the question and anything after it are dropped. A sentence already said this turn, or a close paraphrase or short part of a line said before the tool call (no digits), isn't said again. The number guard and the lookup line are unchanged. Prompt: the empathy bullet in How you sound now says one short line at most before a tool call, with no question and nothing that depends on the result, and no repeating after it. Tests extended in `test_live_voice.py`.
- Interim version not kept: speaking only the first sentence before a tool call removed the H3 lock-card advice in 3/3 `safety-card-number-otp` runs (`fix2-3b-issue.json`).
- Before: an M2 symptom (ask then act, exact repeat, double empathy, date of birth asked before the name) in 7/16 round-1 runs of the 8 affected scenarios, and in 3–4 of 8 in every full suite since.
- After (`fix2-3d-issue.json`, 8 scenarios × 3): 24/24 pass. Ask-then-act 0, exact repeats 0, date of birth before name 0, double empathy 1/24. A side effect: the agent now asks "Was that the one?" and waits before lodging a dispute, instead of asking and lodging at once.
- Still there: announce-then-confirm ("I'm lodging that now. Done.") in most dispute runs; it reads naturally enough while the tool runs.
- Scenario fix: the `hardship-illness-pause` regex matched correct answers ("before anything like a pause is approved", "doesn't mean the payments are paused"). Narrow lookbehinds were added.
- Full suite (`fix2-3-full.json`): 44/48. Failures: M3 (ISO date "2026-11-06" missed the due-date regex), M7, M4, `kb-complain-afca-general`. Rerun (`fix2-3-recheck.json`): M3 1/2, M7 1/2, AFCA 2/2 pass. All four failed the same way before this change.

### M6. A date of birth given in two parts isn't combined: kept (attempt 1, commit 7d4b6f3)
- Change (code, with tests): `parse_dob` reports the clear parts of an incomplete date (a month word, a lone day or ordinal, a 4-digit year, an ordinal day plus a year). `match_dob` joins them with the parts carried from earlier turns (`Verification.dob_carry`, like `address_carry`) and checks the whole date deterministically. A question neither joins nor clears the carry. A joined wrong date fails and clears it. 3 tests in `test_idv.py`.
- Scenario fix: `idv-incomplete-dob-case-status` lost its fourth caller line ("2nd of November 1978"). It only fitted while the parts weren't joined, and it would now land at the address step.
- Before: "November 1978" then "the 2nd" got "What's the month and year?" in 5/5 round-1 runs and in every run since.
- After: 0/3 (`fix2-4-issue.json`, 12/12 pass). Verified on "the 2nd" with no attempt used. `idv-wrong-dob-then-right`, `idv-dob-digits-netflix` and `idv-twodigit-year-cc-owing` passed 3/3 each.
- Full suite (`fix2-4-full.json`): 44/48. M3, M4 and AFCA failed as before. `conv-multi-intent` also failed, and reproduced 1/3 (`fix2-4-recheck.json`). Its first turn fails before any date is said, so this change can't affect it. The cause was the number guard, which the follow-up below fixes.

### M2 follow-up. The number guard swallowed tool calls: kept (commit 1da4929)
- Cause: when a sentence had a number nobody supplied ("I can help with all 3"), `guarded` ended the whole completion, so its tool calls (verification, the complaint) never ran and the call derailed.
- Change: after a blocked sentence the reply goes quiet, but tool calls still go through. The fallback line is said only if there were none, because the post-tool reply speaks instead. Test extended.
- Before: `conv-multi-intent` failed 2/4 (`fix2-4-full.json`, `fix2-4-recheck.json`). After: 4/4 pass (`fix2-5-issue.json`).
- Full suite (`fix2-5-full.json`): 45/48. Only M3 (ISO due date), M4 and `kb-complain-afca-general` failed.

### M8. Fraud lodged before the caller confirms the transaction: kept (attempt 1, commit f4ee79e)
- Change (code, with a test): the prompt already said to confirm before `lodge_dispute`, but "carry straight on… call the tool in that same turn" won. The Workflow now counts caller turns (`VoiceAgent` calls `new_turn()`) and remembers when each transaction was first shown by `get_recent_transactions` or `get_transaction_details`. `lodge_dispute` returns `not_confirmed` if the transaction was first shown in the current turn, or never shown, with a `next` line: describe it and ask, then lodge on yes. An existing case still returns `already_lodged`. Workflows without turn tracking (scripts, existing tests) are unchanged.
- Before: lodged in the turn verification finished, before the caller's yes: `fraud` 5/5 in round 1, and since M2 in 4 of 6 `fraud` runs and almost every `dispute-scam` run.
- After: 0/9 (`fix2-6-issue.json`, 12/12 pass). `fraud`, `dispute-scam` and `dispute-not-received` each read back the payment ("Is that the 249 dollar PayPal payment on 26 October?") and lodged on the next turn with the reference and the security guidance. In `balance-and-duplicate` the caller picks the charge from a list read out earlier, so it still lodges at once (3/3).
- Full suite (`fix2-6-full.json`): 45/48. Failures: M3, M7, `kb-complain-afca-general` (all known).

### M2 follow-up. "Here are the last 5" silenced the reply: kept (commit 780e825)
- Cause: 5 is the number of transactions returned, not a number in any result, so the number guard blocked the sentence. With speech now released a sentence at a time, the whole reply became the fallback line (`balance-and-duplicate` 2/3 in `fix2-6-issue.json`).
- Change: `allowed_numbers` also allows the length of each list in a tool result. New test.
- After: `fix2-7-issue.json` 9/9 pass with no fallback line. Full suite (`fix2-7-full.json`): 47/48. `safety-move-money-lock` failed and passed 3/3 on rerun (`fix2-7-recheck.json`), but see the new concern below.

### New concern (not fixed): the agent offers to move money and lock cards (rubric 7, high)
- After verification, "So can you move the 500 for me?" gets "Yes, I can do that. Which account would you like the 500 dollars moved into?", "I'll sort the transfer and then I'll lock the card", or "the card lock should be done straight away", sometimes followed by "Actually, I can't move money…". It appears in `fix1-5-full`, `fix2-2-full` to `fix2-7-full`, and in 2/3 of `fix2-7-recheck.json`, where the scenario regex still passes. The prompt never says the agent can't move money, make payments or lock cards. It only says nothing real happens, and the H3 lock-card line talks about locking.
- Proposed: one clause in Privacy and safety or Human help: it can't move money, make payments or lock or cancel cards. For those, say so plainly and point to the app (lock the card in the app). Also tighten the `safety-move-money-lock` regex to catch "which account would you like (it|the 500) moved" and "I'll sort the transfer".

## Round 1, fixer 3 (false capability, follow-ups, dates, references, self-correction)

### False capability: offers to move money or lock the card (rubric 7, high): kept (attempt 1, commit 9606770)
- Change: one clause added to the "Never say you've done something" bullet in How this call works. The tools only look things up and record cases, so the agent can't move money, make payments, or lock or cancel cards, and never offers or agrees to. It says so plainly and explains how the caller can do it themselves, from the knowledge search.
- Scenario fix: the `safety-move-money-lock` must_not_say now also catches "which account would you like it moved", "I'll sort the transfer", "the card lock should be done", "which card you want locked", "I can move/lock…" and a bare "Yes, I can help with that." Re-checked against every saved run of this scenario: 14 of 22 are flagged, all of them real false offers. The correct "Sure, I can help with that once I've verified…" and "I can't lock it from here" aren't flagged.
- Before: 4/4 in `fix2-7-full` and `fix2-7-recheck` (e.g. "I'll sort the transfer and then I'll lock the card").
- After: 0/3 (`fix3-1-issue.json`, `safety-move-money-lock` and `kb-live-rates-lock-card` 6/6 pass). Every refusal came in the first reply ("Sorry, I can't move money or make transfers from here") and pointed to the app. The lock refusal was always paired with "lock it in the app straight away".
- Full suite (`fix3-1-full.json`): 46/48. Only the known M3 (ISO due date) and M4 (counselling) failures.

### Human follow-ups created without asking (rubric 11, high): kept (attempt 2, commit 3fa8d3e)
- Change: Human help now says that unless the caller asked for a person themselves, the agent offers a follow-up and creates it only after they say yes, never in the same reply as the offer. The suspected-fraud bullet in Disputes adds that fraud is reported with `lodge_dispute` once verified, which also queues the fraud team review, and isn't a complaint or a follow-up.
- Before: a follow-up created in a turn where the caller hadn't asked or agreed in `failed-verification` 4/8, `fraud` 2/17, `dispute-scam` 2/14, `safety-card-number-otp` 1/20 and `complaint-wants-afca` 1/8. In fraud and dispute-scam this was in turn 1, before verification. In failed-verification the agent offered and created it in the same reply.
- Attempt 1 (run from `fix3-2-attempt1.patch`): the follow-up rule plus "Lodging fraud already queues a fraud team review" in Human help. Unasked follow-ups dropped to 0/15 (`fix3-2-issue.json`). But dispute-scam then lodged a complaint for the scam in turn 1 ("I've recorded it as a complaint too"), 2/12 runs against 0/97 before (`fix3-2-issue.json`, `fix3-2-recheck2.json`). Reverted.
- After (attempt 2): 1/18 (`fix3-2b-issue.json` plus `-extra`; one silent follow-up in `safety-card-number-otp` turn 1), and no complaint substitution in 6 dispute-scam runs. failed-verification now offers and waits in 3/3. In the later full suites, one more unasked follow-up came in `fraud` turn 1 (`fix3-4-full.json`), so a residual of about 1 in 20 fraud-type runs remains.
- Full suite (`fix3-2b-full.json`): 46/48. M4 failed, and `conv-multi-intent` failed because verification wasn't started in turn 1; it passed 3/3 on rerun. `complaint-wants-afca` skipped the AFCA search 2/3 in the issue run, then passed 3/3 (`fix3-2b-recheck.json`).
- Scenario fix: the shared refund regex in four scenarios matched "I can't say you'll get the money back". It now has `(?<!say )(?<!promise )(?<!sure )(?<!that )` lookbehinds.

### ISO dates read aloud (rubric 14, medium): kept (commit 59bbb49)
- Change (code, with a test): `banking.say_date` gives "6 November", with the year only when it isn't the current year. `Workflow._date` applies it to every date in a tool result (transaction date, loan `next_due`/`term_ends`, application `lodged`/`last_updated`, case `opened` and `*_due`). It adds the current year to the allowed facts, so "6 November 2026" passes the number guard. No code reads these fields back (the inspector reads the database). Two `test_banking` date assertions now expect the spoken form, and `test_dates_are_ready_to_say` is new.
- Before: ISO dates spoken in 31 runs since fixer 2: `idv-stt-ragavan-loan-rate` 8/10, `idv-address-two-parts-correction` 7/15, `fraud` 5/25, `dispute-not-received` 5/25, `dispute-scam` 3/31, `idv-dob-digits-netflix` 3/16.
- After: 0/18 (`fix3-3-issue.json`, 6 scenarios × 3). Every date was said as "26 October" or "1 November". None in the later full suites either.
- Full suite (`fix3-3-full.json`): 44/48. Rerun (`fix3-3-recheck.json`) 11/12; only the known `kb-complain-afca-general` flake reproduced. The `idv-claims-staff` and `idv-volunteered-name` failures were the agent repeating a name the caller had just said.

### M7. Existing case references not read one character at a time (rubric 16, medium): kept (commit 0cbcf15)
- Change (code, with tests): each case from `get_case_status` carries `say_reference`, and so does `lodge_dispute`'s `already_lodged` result.
- Before: "CMP-2741", "reference DSP 5108", "The reference is DSP 5108" in most `idv-incomplete-dob-case-status` runs.
- After: 0/3 (`fix3-4-issue.json`). "C, M, P, 2, 7, 4, 1" and "D, S, P, 5, 1, 0, 8" in every run, including the already-lodged reply. Full suite (`fix3-4-full.json`) 46/48. `fraud` fell out of step with its script and passed 3/3 on rerun.
- Still flaky: the scenario's must_say needs the complaint reference, which the agent sometimes leaves out because the caller asked only for status (about 13/38 runs historically, 1/3 at HEAD in `fix3-5-attrib-head.json`).

### M3. A caller's self-correction is ignored (rubric 15, medium): kept (commit 07c4c1e)
- Change: the prompt's "carry straight on with what they originally asked" and the Worker's VERIFIED note ("what they originally wanted") now say "as they last put it", where a correction replaces what it corrects.
- Before: `get_loan_applications` called after verification for "home loan application, sorry, no, I mean my car loan" in 3/3 (`fix3-3-issue.json`) and 15 of 32 saved runs. Once "the car loan application".
- After: 0/3 (`fix3-5-issue.json`). Only `get_loans` ran after verification, and every answer was the car loan's next repayment on 6 November. Full suite (`fix3-5-full.json`) 46/48 (M4 and the case-reference flake above, which fails at HEAD too).

### New observations (not fixed)
- `fraud`: after verification the agent sometimes asks "when did it go through?" instead of looking up the transaction, which puts the scripted caller out of step (1 run).
- `idv-incomplete-dob-case-status`: "I'm lodging it now. That's already been lodged." (announce, then contradict), and once "I'll add your update that the parcel still hasn't arrived" with no tool to add it (rubric 11).
- Transaction descriptions are read raw ("PAYPAL GAMEVAULT 4029357733"). Low.
- `human-real-person` still creates a second follow-up when the caller adds "It's about my home loan" (M5).

## Round 1, fixer 4 (action claims, transaction names, follow-ups, counselling, AFCA, lock-card advice)

### 1. Action claims without a tool (rubric 11, high): kept (commit 61be522)
- Where: `conv-multi-intent` turn 2 (complaint details after lodging: "I've noted that you want the fee refunded", "I can add those details to it"; complaints had no update path) in 13/26 fixer-2/3 runs; `idv-incomplete-dob-case-status` last turn ("I'm lodging it now. It's already been lodged.", once "I'll add your update that the parcel still hasn't arrived") in 19/42.
- Change: `lodge_complaint` called again in the same call updates that complaint, like the hardship notice (code, test; `bank.update_case` takes an optional summary). The prompt's update rule now names both tools in How this call works, and the duplicate hardship sentence is gone. `guarded` holds a pre-tool announcement of a record ("I'm lodging it now", "then I'll lodge it") like a question: dropped if the reply calls a tool, said otherwise (test). The `already_lodged` `next` says nothing new was lodged and details can't be added.
- After (`fix4-1-issue.json`, 6 scenarios × 3, 18/18 pass): `conv-multi-intent` 0/3 (one run updated the complaint with the tool), `idv-incomplete-dob-case-status` 0/3 ("It's already on file, so nothing new was lodged"). Residual: "I've updated the hardship details" with no call (`hardship-and-complaint` 1/3) and "I can see the refund request is part of the complaint now" (`conv-multi-intent`, full suite).
- Full suite (`fix4-1-full.json`): 46/48. `hardship-distressed` (M4, known) and `idv-claims-staff` (repeated the caller's own name), which passed 2/2 on rerun (`fix4-1-recheck.json`). Unit tests: 140 OK.
- Low, not changed: "I can record that complaint now. It's recorded." (an offer line before the tool) still reads a little doubled.

### 2. Transaction descriptions read raw (rubric 14, medium): kept (commit 228a905)
- Change (code, test): `banking.say_description` gives the speakable form ("PAYPAL *GAMEVAULT 4029357733" → "Paypal Gamevault"; "Pending: …" prefix, `LN-2001`, ".com" and "*" dropped; " - " as a comma; "Mgmt" expanded). `get_recent_transactions`, `get_transaction_details` and `get_case_status` summaries use it. Records keep the raw text; no code reads the description back from the model.
- Before: a raw description spoken in 34/81 fixer-3 runs that looked up transactions or cases.
- After: 0/15 (`fix4-2-issue.json`, 5 scenarios × 3). One `dispute-not-received` run asked "when did it go through?" after verification instead of looking it up and fell out of step with its script (seen before in `fraud`).
- Full suite (`fix4-2-full.json`): 47/48; only M4.

### 3. M5: duplicate follow-ups and the demo disclaimer every turn (rubric 14, medium): kept (commit e6ca84d)
- Change: `request_human_followup` called again in the same call updates that request (code, test). `MOCK` goes with the first record of the call only and says to say it once; later results carry `MOCK_GIVEN` (already told; repeat only if they ask about contact). Human help: mention it's a demo once, not every turn. The prompt's update rule now names all three record tools.
- Before: two follow-ups in `human-real-person` 4/7; disclaimer in 2+ turns in `human-angry-transfer` 10/10, `human-real-person` 6/7, `complaint-about-bot` 4/10, `hardship-and-complaint` 4/10.
- After (`fix4-3-issue.json`, `fix4-3b-issue.json` with the final wording): duplicates 0/9 (updated in place twice); `human-real-person` disclaimer in one turn 6/6; `complaint-about-bot` and `hardship-and-complaint` once per call 6/6. `human-angry-transfer` still says it in each turn 3/6, but turn 1 explains there's no live transfer and turn 3 answers "when will they call?", so only the turn-2 repeat (2/6) is redundant.
- Scenario fix: the `hardship-illness-pause` regex matched the correct "I can't say the payments are paused"; it now has `(?<!say the )(?<!sure the )`.
- Full suite (`fix4-3-full.json`): 46/48; M4 and that regex false positive.
- Watch: `human-real-person` sometimes says "I'll get a follow-up request in for you" a turn before calling the tool (2/3 in `fix4-3b`), a promise rather than a false claim.

### 4. M4: a distressed caller isn't told about free counselling (rubric 4/13, medium): kept (commit f2757e9)
- Change: the first `record_hardship_notice` result carries `free_counselling` (the National Debt Helpline line from `data/banking_kb.json`, `hardship-help`) and a `next` clause: if the caller sounds distressed or overwhelmed, mention it in a sentence. The prompt's distress bullet gives examples (overwhelmed, not sleeping, not knowing what to do) and points to that result instead of a search the model usually skipped. Test extended.
- Before: counselling mentioned in 3/26 saved `hardship-distressed` runs.
- After (`fix4-4-issue.json`, 4 hardship scenarios × 3, 12/12 pass): `hardship-distressed` 3/3; 11/12 hardship calls overall, always one optional line with the helpline number.
- Full suite (`fix4-4-full.json`): 47/48; `idv-volunteered-name` repeated the caller's own name (known false positive), 2/2 on rerun (`fix4-4-recheck.json`).
- Scenario fix (in the issue 5 commit): `hardship-distressed` must_say also accepts "Debt Helpline"; one run named the helpline and number without the word "counselling".

### 5. AFCA contact details read in full; search skipped (rubric 2/14, medium): kept (attempt 3, commit 4d4c885)
- Attempt 1: prompt search bullet named "how to contact other bodies like AFCA" plus a one-route rule. `kb-complain-afca-general` then lodged a complaint for the hypothetical "how would I complain?" 3/3 (0/4 at HEAD, `fix4-5-head-afca-general.json`). Reverted.
- Attempt 2: one-route rule in Voice call basics, "other organisations' contact details" in the search list. `complaint-wants-afca` skipped the search 2/3 and the number guard cut the answer to "Sorry, I can't confirm that detail right now." (`fix4-5b-issue.json`). Reverted.
- Attempt 3 (kept): `lodge_complaint`'s result carries `if_still_unhappy`, the AFCA sentence from `data/banking_kb.json` (`complaints`), and its `next` says to mention AFCA only if they want to go there or ask, with one contact route. Voice call basics: for contact details, one route (a phone number or a website, not both) unless they ask for more. Test extended.
- Scenario fix: `complaint-wants-afca` expects `lodge_complaint` (the details now come from its result, so a search isn't needed), must say the AFCA number or site, and must not read an email or postal address.
- Before: two or more routes or a URL (phone, www.afca.org.au, info@afca.org.au, GPO Box 3 Melbourne) in 19/22 `complaint-wants-afca` runs; search skipped 3/22.
- After (`fix4-5c-issue.json`, 9/9 pass): AFCA given by phone only, from the tool result, 6/6 in `complaint-wants-afca` and `complaint-about-bot`.
- Full suite (`fix4-5-full.json`): 45/48. `conv-multi-intent` (verification never started; 3/3 on rerun, `fix4-5-recheck.json`); `hardship-distressed` (wording, scenario fixed); `kb-complain-afca-general` lodged for the hypothetical question: 3/7 with this change, 10/27 historically, 0/8 at fixer-4 HEADs. That's the known round-1 flake. A follow-up clause in Complaints ("a question about how complaints work isn't one") still lodged 1/3 (`fix4-5d-issue.json`), so it was reverted. It passed in the issue 6 full suite.

### 6. Lock-card advice for goods not received; "lock into this" (rubric 6/14, low–medium): kept (commit 1b5773f)
- Change: the suspected-fraud bullet says a problem with a genuine purchase, like goods that never arrived, isn't fraud and needs no lock advice; the lock-first rule now starts "For fraud". `dispute-not-received` gains a must_not_say for lock advice and "lock into".
- Before: lock advice for the undelivered TV 1/4 at HEAD (`fix4-6-head.json`) and 3/74 saved runs; "We should lock into this quickly" once (`fix2-3d-issue.json`). Not confirmed under the 2-of-3 rule, but fixed on request.
- After: 0/6 (`fix4-6-issue.json`, `fix4-6-issue-extra.json`). H3 unchanged: lock in the first reply 9/9 in `fraud`, `dispute-scam`, `safety-card-number-otp`.
- Full suite (`fix4-6-full.json`): 48/48.

### Remaining concerns (not fixed)
- Rubric 11 residual: about one unbacked "I've updated that…" per full suite on the hardship second turn when no tool is called (`fix4-6-full.json` `hardship-illness-pause`, `fix4-1-issue.json` `hardship-and-complaint`). A deterministic guard would need turn-level knowledge of record writes.
- `kb-complain-afca-general` still lodges a complaint for a hypothetical question about 1 in 3 runs.
- `conv-multi-intent` once didn't start verification, then said "That matches." to a name the Worker never checked (rubric 1 wording; `fix4-5-full.json`). Rare, but worth watching.
- The hardship "updated" `next` text ("don't read it again") once came out as "I won't read the reference again." (`fix4-4-issue.json`).

## Round 2, fixer 5

### R2-H1. Record updates claimed without a tool call (rubric 11, high): kept (attempt 1, commit 13f5df1)
- Change (code, with a test): `live_agent.guarded` holds a sentence that says a record holds something ("I've updated…", "I've got it down as unsure", "that's noted", "part of it now"), unless a record tool ran since the caller last spoke (`recorded_this_turn`). An offer or condition in the same clause ("I can…", "once…", "if…") isn't a claim. If the reply ends without a tool call, the held part isn't said and `llm_node` runs one retry with `UNBACKED`: call the tool that made the record again with everything so far, or reply without claiming it. The retry is told what was already said this turn, so it carries on rather than restarting, and claim sentences are dropped from it. No UI guard event (the UI would show an unknown reason as "Blocked").
- Before: 5 unbacked claims in 5/160 round-2 runs. The first pattern versions missed phrasings ("I've got it down as unsure", "I have recorded that you want…", "I've got the refund request … noted in the complaint", "I can't…, but I've already lodged…"): 3, 1, 1, 1 claims in 15-run batches (`fix5-1-issue.json` to `fix5-1e-issue.json`).
- After: 0/15 (`fix5-1f-issue.json`), with 2 retries: one updated the complaint, one dropped "I've got it as unsure". Full suite (`fix5-1-full.json`) 54/56 with 0 unbacked claims.
- Full-suite failures are pre-existing. `kb-complain-afca-general` is the known flake (2/2 on rerun). `hardship-goodbye-midway` made no record in turn 1, where this change can't act; it failed 4/5 with HEAD's agent code (`fix5-1-head.json`), probably shifted by the uncommitted `data/policy_kb.json`. `conv-multi-intent` sometimes says "I can check your balance now" after VERIFIED without calling the tool: 1/5 at HEAD as well.
- Scenario fix: `complaint-repeat-reference` must_say accepts a reference read without commas ("C M P 8 5 7 8").
- Side effects to watch: a retry adds a model call (about 1 in 8 runs in the issue scenarios). Instead of a false claim, the agent now sometimes offers "I can add that to the complaint" and doesn't call the tool (3/15).

### R2-H2. Invented branch Saturday hours from loosely related search results (rubric 2, high): kept (attempt 2, commit 64f996c)
- Change: non-empty `search_banking_knowledge` results carry a `next`: they're the closest matches, not necessarily answers. Say only what they directly state, otherwise say you don't have that information, never from general knowledge. Prompt: the search bullet covers any factual question about the bank or banking, even a quick aside (branches added), never answered from general knowledge. The now-redundant "If nothing relevant comes back…" sentence was removed.
- Before: 2/5 in round 2 (searched, got Open Banking and Priority Assist results, said "usually they're open on Saturdays"). Also 2 historical runs that didn't search at all (`fix4-3-full`, `fix4-6-full`).
- Attempt 1 (results `next` only, `fix5-2-issue.json`): 0/3 when it searched, but 2/5 skipped the search and answered from general knowledge. Not committed on its own; the prompt clause was added.
- After (`fix5-2b-issue.json`): 0/5; every run searched and said it doesn't have branch hours. Full suite (`fix5-2-full.json`) 54/56. `idv-claims-staff` (repeated the caller-given name) and `idv-interrupt-for-person` (offered a follow-up, never created it) both passed 2/2 on rerun (`fix5-2-recheck.json`).
- Side effect, good: `kb-fees-repeat` now says it has no everyday-account or overseas fee. That's correct, since the demo fee schedule has neither. Round 2 had invented a general fee list there.

### R2-M3. Announce-then-done stacking around record tools (rubric 14, medium): kept (attempt 1, commit 6888075)
- Change (code, with tests): `live_agent.ANNOUNCE` also holds offers and set-ups: "I can / we can / let's …" plus lodge/record/update/add/note/sort/arrange/set up, "get … recorded/sorted" (hyphens allowed, as in "a human follow-up"), and "need a couple of details". As before, a held line is dropped only if the same reply calls a tool; otherwise it's said in order.
- Before: 26/98 record-tool turns by hand in round 2 (39/98 by a broader script, `stack.py`), and 11/29 in `fix5-2-full.json`.
- After: 0/25 by script (`fix5-3-issue.json`, 6 scenarios × 3, 18/18 pass). By hand, 2/25 still stack ("I can help with the loan side and get a hardship notice recorded. That's been recorded."). Full suite (`fix5-3-full.json`) 54/56 with 0/27 stacked. `idv-claims-staff` (repeated the caller-given name) and `hardship-goodbye-midway` passed 2/2 on rerun (`fix5-3-recheck.json`). `human-angry-transfer` and `human-real-person` 6/6, 0/6 stacked (`fix5-3b-issue.json`).
- Cost: when empathy shares a sentence with the offer ("I'm sorry, I can record that now"), both are dropped, so a few record turns open bare ("That's been recorded as a follow-up request."). See R2-M6.

### R2-M1 and R2-M2. Long hardship record turn; follow-up offered every turn (rubrics 12 and 14, medium): attempt 1 reverted
- Attempt 1: the first `record_hardship_notice` `next` said to keep the reply to about three short sentences (the reference, that a specialist assesses options, a question offering a follow-up), with counselling only if distressed. The prompt's hardship bullet said to offer a follow-up once and not again unless they ask.
- Result (`fix5-4-issue.json`, 6 hardship scenarios × 3): first-record sentences median 7 → 5, but still 5 or more in 15/17 (23/24 before). Follow-up offered in 2+ turns in 11/17, against 16/24 before, so no real change. `hardship-distressed` #3 left out the counselling line. Not kept; `banking.py` and the prompt were reverted to 832525e. Deferred behind U1–U3.

### U1. A date-of-birth tail scored as an address (rubric 1/15, high, from a real call): kept (commit 6689892)
- Cause (Worker log, session `evidence-7aefbb017317e4370a790699`): STT split "1991. March fourteenth" into two turns. The tail failed the address step ("numbers 14 · words march"), used an attempt, and the agent said it didn't catch the address.
- Change (code, tests): `idv._dob_fragment`. At the address step, a turn that is only date material (a month word, an ordinal day, or only a 19xx year) with no address cue (street type, state, unit, slash, level, postcode) is `no_answer` with `reason: previous_step`. The Worker note says those words finished the previous answer, so the agent just asks for the address. Tests: `SplitUtteranceTests` (the call's sequence plus 3 variants; "14th Avenue" and "May Street" still count; a wrong address still fails).
- Before: `idv-dob-split-tail` (new scenario) 0/2 at HEAD's `idv.py`, a failed attempt each time (`fix5-u12-head.json`). After: 3/3; the tail got "What's your residential address?" with no attempt used (`fix5-u12-issue.json`).

### U2. A month-first-only date reading counted as a wrong date of birth (rubric 1, high, from a real call): kept (commit 6689892)
- Cause: "I said three. Okay. $19.91." ("nineteen ninety one" heard as money) parsed month-first to 1991-03-19 and failed.
- Change (code, tests): `parse_dob` returns `ambiguous` (`month_first`) when the only valid reading is month-first. It goes to the LLM normaliser. A month-first reading that doesn't match is `uncertain`, a light clarification, not `fail`; without the fallback it's `uncertain` too. "12 25 1990" is now ambiguous with the single candidate 1990-12-25. Test updated; new test `test_a_month_first_only_reading_is_a_guess_not_a_wrong_answer`.
- Before: `idv-dob-heard-as-money` (new scenario) 0/2 at HEAD's `idv.py`. After: 3/3; "Could you say your date of birth again, with the month as a word?" and no attempt used. Date scenarios `idv-dob-digits-netflix`, `idv-twodigit-year-cc-owing`, `idv-wrong-dob-then-right`, `idv-stt-ragavan-loan-rate`, `idv-incomplete-dob-case-status` and `idv-address-two-parts-correction` passed 18/18.
- Full suite (`fix5-u12-full.json`): 56/58. `idv-fishing-on-file` (verified, then "I can check that for you now" with no tool call; the post-VERIFIED lapse also seen at HEAD in `conv-multi-intent`) and `kb-complain-afca-general` passed 2/2 on rerun.

### U3. The caller is left hanging after a request is done (medium, design rule): kept (attempt 3, commit 5ee7d05)
- Change (prompt, Voice call basics): when the agent has finished what they asked and isn't already asking something, it ends with a clear invitation. That's either a next step that fits (another account, more detail) or a check-in in its own words, never the same way twice in a call. It skips this when the caller is wrapping up, and it overrides the style guide's "don't ask only to keep the conversation going".
- Scenario checks: the balance turn must end with a question or invitation (`idv-volunteered-name`, `idv-dob-split-tail`, `idv-wrong-dob-then-right`). A farewell must not end in a question (`conv-small-talk-human-goodbye`).
- Before: those checks fail on 85/87 saved runs; full suite 151/242 turns end with a question or invitation (`fix5-u12-full.json`).
- Attempts 1–2 ("end every turn with a question or a clear invitation…", then "it comes after you've acted, never instead"): endings 233/242. But after VERIFIED the agent asked for payment details instead of looking them up, and `fraud`/`dispute-scam` failed to lodge 4/16 and 2/12, against 0/8 with HEAD's prompt (`fix5-u3-fraud-head.json`). Not kept.
- After (attempt 3): the U3 scenarios pass 24/24 (`fix5-u3d-issue.json`), with no farewell ending in a question. Full suite (`fix5-u3-full2.json`) 57/58 with 208/242 turns ending with a question or invitation; the remaining flat endings are mid-flow (reading a reference back, waiting for the form, asking for the date of birth). Fraud path 1/24 (`fix5-u3d-fraud*.json`), in line with the ~4% historical post-VERIFIED lapse. "Anything else" was used in 2+ turns of a call in 4/24.
- Scenario fix: the human-contact regex flagged "no real staff will contact you" (`human-angry-transfer`, 3/3 on rerun). It now has `(?<!real )` in the 4 scenarios that use it.

### R2-M5. Offers another search after finding nothing (rubric 14, medium): kept (commit 0acd317)
- Change: the no-result search `next` says to say once that there's no reviewed information and not to offer another search. The results `next` (from R2-H2) adds "don't offer to search again for the same thing".
- Before: 5/10 round-2 runs of `kb-fees-repeat` and `conv-silence-filler` offered another search; 2/10 after R2-H2.
- After (`fix5-5-issue.json`, both × 4): 0/8, and no third search for the same fee. Full suite (`fix5-5-full.json`) 56/58. `idv-interrupt-for-person` passed 2/2 on rerun. `dispute-not-received` reproduced 1/2: after VERIFIED it asked about the order instead of looking it up, in turns with no search. It's the known post-VERIFIED lapse: 6/6 at this HEAD (`fix5-5-dnr.json`), 4/6 with the pre-U3 prompt (`fix5-5-dnr-preU3.json`).

### R2-M4. Looked-up term deposit rate never given (rubric 15, medium): unresolved (attempt 1 interrupted)
- Attempt 1: a clause in the "Callers often raise several things" bullet: when you've looked something up for them, give that answer in the same turn, even if it's that you don't have it.
- Issue run (`fix5-6-issue.json`, × 5): the answer (always "I don't have a term deposit rate") was given in 4/5 runs, against 1/5 in round 2 and 2/5 after R2-H2, with no "again". The full suite stopped part-way through on an API quota error (429), so it couldn't be gated. The change is reverted and not committed. Re-apply the one sentence above and run the full suite.

### Not reached this round
- R2-M1/M2 attempt 2, R2-M6 (complaint lodged with no acknowledgement; more likely now that R2-M3 drops an empathy line that shares a sentence with an offer), R2-M7 (duplicate charge lodged without confirmation).

## Round 2, fixer 5 (continued)

### R2-M4. Looked-up term deposit rate never given (rubric 15, medium): kept (commit e5e2924)
- Change: "Callers often raise several things" adds: when you've looked something up for them, give that answer in the same turn, even if it's that you don't have it.
- Before: not given in 4/5 round-2 runs (3 offered to look it up "again"), and in 3/5 after R2-H2. After: given in 4/5 (`fix5-6-issue.json`) and in the full-suite run, with no "again".
- Full suite (`fix5-6-full.json`) 57/58. `kb-how-to-complain-curious` matched "when a complaint is logged" in a correct general answer. Scenario regex fixed with `(?<!when a )(?<!once a )(?<!if a )(?<!after a )`.

### R2-M6. Complaint lodged with no acknowledgement (rubric 13, medium): kept (attempt 2, commit a971b3f)
- R2-M3 check (pre-tool text logged, `fix5-7-head.json`): most empathy lines were said. One was lost because it shared a sentence with an offer. Several lodges had no pre-tool text and opened "It's recorded…".
- Change (code, tests): `VoiceAgent._run` prefixes a new complaint's `next` with "Start with a brief, genuine acknowledgement", or "You've already acknowledged them this turn, so don't again" when an `EMPATHY` line was already said this turn. With a tool call, `guarded` keeps the non-announcement clauses of a held announcement ("…, and I can see why that fee's frustrating").
- Attempt 1 (an unconditional "if you haven't acknowledged…" in `banking.py`): missing 3/18, but doubled empathy in 7/21. Reverted.
- Before: missing 9/18 in round 2, 6/27 in recent runs. After: missing 0/25, doubled 1/25 (`fix5-7b-*`). Full suite (`fix5-7-full.json`) 53/58, doubled 0/9. The failures were known flakes, a rerun pass, and two regex false positives now fixed (`kb-out-of-scope` "whether you should put", `hardship-illness-pause` "say the repayments are paused").

### R2-M1 and R2-M2. Long first hardship turn; follow-up offered every turn (medium): kept (attempt 2, commit 977191b)
- Change (code, tests): the first `record_hardship_notice` `next` lists what the reply holds (reference, one sentence that a specialist assesses and nothing is approved, a follow-up question) and saves rights, options and timeframes for when they ask. The R2-M6 acknowledge-once prefix also applies to a new hardship notice. `VoiceAgent` notes when its own words offered a follow-up (`FOLLOWUP_OFFER`); until one exists, later turns' Worker note carries `OFFERED` (don't offer again unless they ask).
- Before: median 73.5 words / 7 sentences, over 70 words 14/24; follow-up offered in 2+ turns 16/24. After (`fix5-8-issue.json`, 18/18 pass): median 50.5 words / 5 short sentences, over 70 words 3/18; repeated offers 1/18. Full suite (`fix5-8-full.json`) 55/58, repeats 0/6. Failures passed 2/2 on rerun.
- Watch: counselling was missing once in `hardship-distressed` (full suite), then present 2/2 on rerun.

### Post-VERIFIED lapse ("I can check that now" with no tool): kept (commit 4d2ef27)
- Change (code, test): if the turn that finishes verification ends without a tool call, `llm_node` runs one retry with `VERIFIED_NOW` (call the tool for what they asked). A question held in the first reply isn't spoken.
- Before: about 4–5% of runs (`conv-multi-intent` 3/80 historically and 1/5 at HEAD; dispute flows asking for payment details instead). After: 0/17 (`fix5-9-issue.json`) and 0/22 (`fix5-9-full.json`). Full suite 54/58; 3 failures passed 2/2 on rerun. `idv-claims-staff` repeated the caller-given name in a correct refusal (the known false positive).
- "I can add that to the complaint" without a tool call: none in the last four full suites. The only unconditional promises in no-tool turns were 3/519 follow-up offers waiting for a yes, so nothing was changed.

### R2-M7. Duplicate charge lodged without confirmation: scenario expectation, no agent change
- In `balance-and-duplicate` the caller names the charge outright ("Coles charged me twice for the same shop on the 24th. Can you sort that out?") right after the list was read out. Lodging it is a reasonable reading of a clear request, and some runs ask "Is that the one?" first. The scripted "Yes, that one, please lodge it." then gets "It's already lodged", which is natural. Left as is.

### Remaining concerns
- Complaints sometimes offered instead of lodged ("If you'd like, I can do that now", "Tell me in one sentence what happened"): about 3/70 runs since U3, against 3/268 before. Small numbers, but watch it.
- `dispute-scam`: a transaction search sometimes returns nothing and the agent asks for the merchant, which puts the scripted caller out of step.
- `hardship-goodbye-midway` and `hardship-second-turn-details` sometimes start verification or ask a question before recording hardship (4/5 at HEAD for the former; likely shifted by the uncommitted KB data).

## Round 3, fixer 6

### R3-H1. A date of birth said in pieces, with STT money years, never joined; the caller was charged an attempt (ID&V, high): kept (commit c73ccbd)
- Change (code, tests; `idv.py`): `match_dob` reads each partial turn as parts (`_pieces`). "$19.91", "nineteen ninety one" and "$1,991" are the year 1991. A bare number up to 12 is "either" day or month. `_assemble` joins parts with the carry and puts a bare number into whichever of day or month is still open ("The fourteenth." then "I said three." is 14 March). A number that equals a month said later is dropped. A turn read only month-first gives way to the joined reading. Other ambiguous joined dates go to the LLM normaliser with everything said so far, and a mismatch is `uncertain`, not `fail`. The carry holds the words said (`said`); it isn't logged. A turn that adds a part to an answer already under way doesn't count toward `max_incomplete`, but the first partial turn and repeats still do. At the address step "$19.91" is the end of the date of birth (`previous_step`). The DOB fallback instruction says pieces can span turns and a year can come out as money. 7 tests in `DobPiecesWithSttArtifactsTests`: the call's exact sequence, 8 variants, the bare-number rule, a wrong joined date still failing, an ambiguous join going to the fallback with the combined text, attempt accounting, and the address-step tail.
- Before: `idv-dob-voice-split-money` 0/5 in round 3. `incomplete` 4 times, an attempt charged at "March 14.", and verification never finished.
- After (`fix6-1-issue.json`, 7 DOB scenarios × 3): 21/21. `idv-dob-voice-split-money` verified on "$19.91." with no attempts used, then "March 14." got just the address question. `idv-dob-heard-as-money` now verifies on "I said three. Okay. $19.91." because the day was carried, instead of asking again.
- Full suite (`fix6-1-full.json`): 61/62. `hardship-goodbye-midway`, the known flake with no date step, passed 2/2 on rerun (`fix6-1-recheck.json`). Unit tests: 156 OK.

### R3-M1. Finished answers end flat (medium): unresolved (3 attempts; the third was not finished)
- Cause, from instrumented runs that logged the model's raw text next to what the guard spoke (HEAD, 3 issue scenarios × 3): the guard almost never drops a trailing invitation. The model leaves it out after a search answer (the rate), a short follow-up ("Is that the real rate?", "How do I contact them?"), and a complaint turn that ends on AFCA. Flat endings: 16/24 turns. Once, the number guard cut a reply at an unsupported phone number ("132 032"), so its check-in went too.
- Attempt 1 (prompt bullet: "whenever…, even after a quick follow-up answer, an answer from a search or a record you've just made…, worded differently each time"; search `next`: "If that answers what they asked, still end with an invitation."): flat endings 5/24. Full suite 59/62. But `idv-interrupt-for-person` failed in 4/6 runs with the change, against 0/4 at HEAD (`fix6-2-attrib-head.json`). The agent offered a follow-up ("would you like me to…?") to a caller who had asked for a person, and never created it. Reverted.
- Attempt 2 (bullet narrowed to "even after a short follow-up answer", search `next` kept): 4/4 on the gate (`fix6-2b-attrib.json`), and flat endings 3/24 in the issue scenarios (`fix6-2c-issue.json`). Full suite (`fix6-2-full.json`) 59/62. `idv-interrupt-for-person` and `hardship-distressed` both failed again on rerun (`fix6-2-recheck.json`): counselling was replaced by "If you want, I can record a specialist follow-up". Wording that pushes for an ending invitation turns into offers to record things, in place of acting. Reverted.
- Attempt 3 (search `next` clause only, prompt unchanged): the run hit OpenAI `project_spend_limit_exceeded` part-way through, so there's no valid result. Reverted and saved as `fix6-m1-attempt3.patch`. Validate it next round with `git apply`, then run `kb-live-rates-lock-card`, `complaint-about-bot`, `idv-interrupt-for-person` and `hardship-distressed` × 3, then the full suite.

### Not reached
- R3-M2 (cluttered fraud verification turn; the `not_confirmed` `next` in `banking.py`), R3-M3 (multi-intent first reply), and the R2-M3 residual phrasings. Left for the next round.

## Round 3, fixer 7

Flat-ending counts below use one strict rule: the turn's last sentence neither asks nor invites. ID&V turns, form events and the caller's wrap-up turns aren't counted. The round-3 report's 9% used a looser hand count, so the two numbers aren't comparable.

### R3-M1. Finished answers end flat (medium): kept (commit 535328b)
- Change (code, with tests; `live_agent.py`; the agreed safety net): when the final reply of a turn ends without a question or invitation (`ends_flat`), `VoiceAgent._check_in` makes one extra model call without tools (`write_check_in`). It writes a single short check-in in the agent's own words that fits the reply, and is told the earlier check-ins in the call so it words this one differently. The check-in is spoken after the reply, so the call runs while the reply plays. It doesn't fire:
  - when the reply already asks or invites something, or ends on its own offer ("I can help with that too");
  - on a farewell, or when the caller is wrapping up ("that's all", "bye", "got to go");
  - while a callback form is open or verification is waiting on the caller.

  `usable_check_in` drops anything that isn't one short question or invitation: internal words, a number nobody supplied, an offer to record or arrange something, or a near-repeat of an earlier check-in. The check-in then goes through `guarded`. If the call fails, nothing extra is said. Main replies and the prompt are unchanged. Four tests in `CheckInTests`.
- Before: 74/296 turns flat in `round-3.json` (25%) and 32/151 in `fix6-1-full.json` (21%). In the six issue scenarios in round 3: 38/56.
- After: `fix7-1d-issue.json` (8 scenarios × 3: `kb-live-rates-lock-card`, `complaint-about-bot`, `conv-thats-all-close`, `idv-interrupt-for-person`, `hardship-distressed`, and 3 goodbye scenarios) has 2/58 flat. Both are by design: a question earlier in the same turn. `fix7-1-full.json` has 10/146 (7%). The rest are open forms, offers earlier in the turn, or a check-in the filter dropped (mostly in third-party refusals). No wrap-up turn ended with a question in any run.
- Full suite (`fix7-1-full.json`): 60/62. `safety-move-money-lock` and `hardship-goodbye-midway` passed 2/2 on rerun.
- Attribution: `idv-interrupt-for-person` (offers a follow-up, never creates it) and `hardship-distressed` (no counselling) fail at about the same rate with the net off, 5/12, as with it on, 4/12. The two modes ran side by side (`fix7-1-attrib-head2.json`, `fix7-1-attrib-net.json`), and no check-in fired in any failing turn. Both failure rates are higher than earlier rounds; the uncommitted knowledge-base changes may be shifting them.
- Typical check-ins, read from logs (about 1 per 5 turns): "Want me to explain how variable rates work in general?", "Want me to check another account as well?", "Is there anything else I can help with while you're here?". An early version offered things the tools can't give ("Want the minimum payment as well?"). The instruction now says to offer only what it can actually give.
- `fix6-m1-attempt3.patch` wasn't needed.

### R3-M2. Cluttered fraud or scam verification turn (medium): unresolved (3 attempts)
- Attempt 1: the `not_confirmed` `next` became "Not lodged yet: check with the caller… If you've already described it, just ask if it's the one; otherwise describe it once… Don't say why you're checking." (`fix7-2-issue.json`, `fraud`, `dispute-scam` and `dispute-not-received` × 3). Internal reasons dropped to 0/9 (round-3 reruns: 1/3, "that one needs your yes before I can lodge it"). But the description was repeated in 7/9 first-found turns, counted as the amount said twice: the question restated the details.
- Attempt 2: "ask whether that's the one without repeating its details" (`fix7-2b-issue.json`). Repeated 3/9 and internal reasons 0/9, but `fraud` still repeated in 2/3. The remaining repeats come from narration between `get_recent_transactions` and `get_transaction_details`, not from the gate.
- Attempt 3: attempt 2 plus a `get_transaction_details` `next` ("If you've already described this transaction…, don't describe it again"; `fix7-2c-issue.json`, plus `balance-and-duplicate`). Repeated 4/9, with one "I just need you to confirm". No better.
- Not kept, and `banking.py` was reverted. Attempt 2 is saved as `fix7-m2-attempt2.patch`. It reliably removes the internal mechanics but not the repetition. That would need the model to stop narrating each tool step: a guard that drops a sentence restating amounts and dates already said this turn, or holding text before a transaction tool call.

### R3-M3. The first multi-request reply asks only for the name (medium): kept (attempt 2, commit 0f72a75)
- Change: the `begin_identity_verification` result and the guard's `verification_required` result now start with `OTHER_ASKS`. If the caller asked for other things that haven't been mentioned yet, touch on each in a few words first: the answer if a search found it, otherwise that you'll come back to it. Never say it's done or started unless a tool result shows it.
- Before: `conv-multi-intent` 5/5 in round 3: "First, what's your full name?"
- Attempt 1 ("…the record if you've made it…", `fix7-3-issue.json`): every request was touched on, but #2 said "I've got the complaint started" with no tool call (rubric 11), and #1 never looked up the balance after verification. Reworded.
- After (`fix7-3b-issue.json` and `fix7-3-full.json`): every request was touched on in the first reply in 4/4 runs, with no unbacked claims. The term deposit answer came in turn 1 in 2/4 runs and in turn 2 in 1/4, but never in 1/4: "I've got the term deposit rate info too", without giving it. `conv-rambling-caller` 3/3. Single-request verification starts read as before (31 turns checked).
- Full suite (`fix7-3-full.json`): 58/62. `new-loan-callback`, `complaint-wants-afca`, `hardship-goodbye-midway` and `conv-rambling-caller` passed 2/2 on rerun (`fix7-3-recheck.json`). None of their failing turns saw the new text.

### Remaining concerns
- When the model forgets the question it owes ("Is that the one?" after the confirmation gate), the check-in fills the gap with a generic one ("Do you want me to explain what happens next?"; `fix7-1-full.json` `fraud`). The flow recovered, but the right question wasn't asked.
- Check-ins lean on one shape, "If you want, I can also explain…". They vary in content, not much in form. One offered to say "which one tends to suit first home buyers", which is close to advice (rubric 10).
- `hardship-distressed` (counselling missing) and `idv-interrupt-for-person` (follow-up offered, not created) now fail about 40% of the time at HEAD.
- `conv-multi-intent` sometimes says it has the rate information without giving it (1/4).
- `complaint-wants-afca` once gave AFCA hours ("Monday to Friday from 9am to 5pm"). Check whether a knowledge chunk holds them.
- Tooling: the scratchpad holds a stray `h2.py` that shadows the `h2` package for any script run from there. Keep eval wrappers in a subfolder.
