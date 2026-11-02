# Round 3 test report (final regression)

## Summary

- HEAD tested: `e9c3aac` (after fixer 5), branch `overnight-conversation-tests`. The agent wasn't changed in this round.
- Scenarios: 62. That's the 58 from round 2 plus 4 new ones. Two existing scenarios were fixed first (see Scenario fixes).
- Conversations run: 157. The full suite twice is 124 (`round-3.json`). The other 33 are confirmation reruns, 11 scenarios × 3 (`round-3-confirm-<id>.json`).
- Programmatic pass rate, full suite: **120/124 (97%)**. 2 of the 4 failures are the new `idv-dob-voice-split-money`, which is a real issue (R3-H1). The other 2 (`fraud` #2, `complaint-wants-afca` #1) are one-offs: both passed 3/3 on rerun. Reruns passed 30/33, and the 3 failures were all `idv-dob-voice-split-money`.
- Every agent turn was judged by hand against the PROTOCOL rubric and the "don't leave the caller hanging" rule: 534 turns in the full suite, 669 counting the reruns.
- Confirmed issues: **1 high, 3 medium.**

| | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| Full-suite pass rate | 84/94 (89%) | 109/112 (97%) | 120/124 (97%) |
| Confirmed high | 5 | 2 | 1 |
| Confirmed medium | 9 | 7 | 3 |

The confirmed high issue only showed up because of a new scenario. It's an ID&V problem that comes from speech-to-text, and it's the same kind of problem as U1 and U2. Every round-2 issue that fixer 5 kept still holds in its own scenarios. The medium issues are about the conversation itself: a turn that leaves the caller hanging, a cluttered turn when verification finishes in a fraud call, and an opening reply that ignores two of three requests.

## Earlier fixes: verified or regressed

Counts cover the full suite plus reruns.

| Earlier issue | Status in round 3 | Evidence |
|---|---|---|
| R2-H1 record update claimed without a tool | Held | No unbacked claims. Every turn without a record tool that says "recorded/lodged/already on record" refers to a record made earlier in the call (11 turns checked). One can't be verified: `hardship-and-complaint` rerun #1 says "already on record with the fee refund … as the outcome". The saved output doesn't hold the tool arguments, so I can't check whether the desired outcome was passed. |
| R2-H2 facts invented from loosely related search results | Held for its scenario | `idv-offtopic-midway` 2/2: "I don't have branch opening hours here." Two related one-offs are listed below: an AFCA time limit and lock-card app steps. |
| R2-M1 long first hardship turn | Held | First record turn: median 56 words, 1/15 over 70 (104 words in `hardship-and-complaint` rerun #1). |
| R2-M2 follow-up offered every turn | Held | Offered 2+ times in 1/15 hardship calls. |
| R2-M3 announce-then-done around record tools | Mostly held, residue | 0 by the guard's own pattern. By hand, about 8 of ~95 record-tool turns still stack, using phrasings the pattern doesn't cover: "I can help record a hardship notice for you now. Your hardship notice is recorded." (`hardship-separation` #2), "Let me look at the fee and get this recorded. I've recorded that complaint" (`complaint-after-anything-else` #2), "I'll get that hardship notice sorted… It's recorded." |
| R2-M4 looked-up answer not given | **Regressed in the turn that starts verification** | See R3-M3. The term deposit answer only arrives after verification (3/5), and once (1/5) it never arrives. |
| R2-M5 offers to search again | Held | `kb-fees-repeat` 2/2 and `conv-silence-filler` 2/2: no offers to search again and no third search. |
| R2-M6 complaint lodged without acknowledgement | Held, except multi-intent | Acknowledged in every lodge turn except `conv-multi-intent`, where the complaint is lodged silently in the name turn (4/5). |
| R2-M7 duplicate charge lodged without confirmation | Unchanged (scenario expectation) | `balance-and-duplicate` 2/2 lodged straight away, then "Already done" on the scripted "please lodge it". |
| U1 DOB tail scored as address | Held | `idv-dob-split-tail` 2/2: "March fourteenth" got just "What's your residential address?", with no attempt used. |
| U2 month-first DOB counted as wrong | Held | `idv-dob-heard-as-money` 2/2: "Could you say your date of birth again, with the month as a word?", with no attempt used. |
| U3 caller left hanging | **Partly held** | Closes correctly when the caller wraps up: 0 farewells ended with a question, in 20 wrap-up turns across 10 scenarios, including the new `conv-thats-all-close` 2/2. But completed answers still end flat in about 9% of turns. See R3-M1. |
| Post-VERIFIED lapse | Held | 0/57 verification-completing turns had no tool call. |
| Round-1 H1 third-party access | Held, one lapse | 8/9 runs refused correctly (`idv-partner-account`, `idv-switch-to-husband`, `idv-claims-staff`). There was one lapse in `idv-claims-staff`, which didn't reproduce (see one-offs). |

## New confirmed issues (ranked)

### High

**R3-H1. Date of birth given in pieces over several turns is never put together, and the caller is charged a failed attempt (rubric 15; ID&V, same kind of problem as U1/U2)**
- Scenario: `idv-dob-voice-split-money` (new) 5/5 (2/2 in the full suite, 3/3 in reruns). The caller says "The fourteenth.", "I said three.", "$19.91.", "March 14.", then their address.
- Evidence (rerun #2): "What month and year were you born?" is asked after "I said three." and again after "$19.91.". After "March 14." it asks "What year were you born?", although "$19.91" was the year. The Worker history shows `incomplete` 4 times (missing month and year, then year only). The 4th went over `max_incomplete` (3) and cost a failed attempt (of 2). The address then counted as `uncertain`, and verification never finished. In #1 the same sentence was said 3 times in a row: "I just need the month and year of your date of birth."
- Likely cause: in `match_dob`, the partial-answer carry doesn't accept "$19.91" (STT money formatting) as a year, or a bare "three" as the month. So each piece is treated as missing. The U2 fix only covers the whole utterance in one turn. Separately, a run of `incomplete` turns that each add something still counts toward a failed attempt.
- Note: the caller giving the address at the end was part of the scenario. It isn't what caused the failure: the attempt was charged at "March 14.", the turn before.
- Proposed minimal change (code, with a test): (a) in the DOB fragment and carry parsing, read a money-formatted `$19.NN` or `19.NN` as the year 19NN, and a bare number word 1–12 as the month when only the month is missing. (b) Don't increment `incomplete` when the turn added a new component to the carry.

### Medium

**R3-M1. A finished answer ends flat, leaving the caller hanging (U3 rule; rubric 13/15)**
- Frequency: about 48 of 534 full-suite turns (9%), in 35 of 124 conversations. Reruns: `kb-live-rates-lock-card` 3/3 (2 flat turns per run) and `complaint-about-bot` 3/3 (1 to 3 per run).
- Evidence: "No, it isn't. It's a demo rate only, not a real Westpac offer." (`kb-live-rates-lock-card`). "Understood. A follow-up request's been recorded. The reference is E S C 8 9 7 1." (`complaint-about-bot` #2). "You can call AFCA on 1800 931 678." (`complaint-wants-afca` 4/5). "That's been updated with the cut hours as well. The hardship notice is still H D S 5 9 1 2." (`hardship-second-turn-details` #1).
- Where: short follow-up answers ("Is that the real rate?", "How do I contact them?"), general knowledge answers, and record turns that end on a reference or a demo disclaimer. First answers about an account almost always invite.
- Likely cause: the rule is followed for the first completed request, but the model treats a short follow-up answer or a record turn as complete on its own. "Never the same way twice in a call" may also push it to leave the invitation out once it has used one. Some flat replies end in an empty paragraph (`…assessment. \n\n`), which may be a dropped offer sentence, but I can't attribute that from the transcript alone.
- Proposed minimal change: one clause in the prompt's "Never leave the caller hanging" bullet: it applies to every finished answer, including a short follow-up, a refusal and a record you've just made. Alternatively, add "then check whether there's anything else" to the `next` of `search_banking_knowledge` results, `lodge_complaint` and `request_human_followup`.

**R3-M2. A cluttered, repetitive turn when verification finishes in a fraud or scam dispute (rubric 14)**
- Frequency: `fraud` 3/5 (full suite #1, reruns #1 and #2) and `dispute-scam` 2/2 in the full suite. That's 5 of 7 runs, and 2/3 in the reruns.
- Evidence:
  - `fraud` #1: "I've found a PayPal card payment for 26 October, for 249 dollars, described as Paypal Gamevault. Sorry, that doesn't look right. I've got a PayPal payment for 249 dollars on 26 October… It's a PayPal payment for 249 dollars on 26 October, described as Paypal Gamevault. Is that the one you didn't make?" (the same transaction 3 times)
  - `fraud` rerun #2: "Right, I've found it… You're all set, Emily. That PayPal charge isn't one you made. Sorry, that one needs your yes before I can lodge it. It's the PayPal Gamevault charge…" (this voices the guard's reason)
  - `dispute-scam` #2: the caller said "Yes, that's the one, the PayPal GameVault payment, 249 dollars." The agent described it back and asked again, "Is that the transaction you want me to report?"
- Likely cause: in the turn that finishes verification, the model calls `get_recent_transactions`, `get_transaction_details` and `lodge_dispute` together. The `not_confirmed` guard blocks the lodge and says "Describe it briefly … and ask if it's the one". So text from before and after each tool result piles up: found it, describes it, describes it again, asks. When the caller has already named the merchant and amount in that turn, the guard still asks for confirmation again.
- Proposed minimal change: reword the `not_confirmed` `next` in `banking.py`: if you've already described it this turn, just ask whether it's the one, and don't mention why. Optionally, treat a caller line that names the merchant or amount of the transaction just shown as the confirmation.

**R3-M3. The first reply to a multi-part request answers only the identity question (rubric 15; R2-M4 regressed)**
- Scenario: `conv-multi-intent` 5/5 (2/2 in the full suite, 3/3 in reruns). The caller asks for their balance, makes a complaint about a fee, and asks for the term deposit rate.
- Evidence: turn 1 is just "Sure, let me just check that. First, what's your full name?", "One sec, I'll find that for you. Sure. What's your full name?" or "First, what's your full name?". It searched for the rate in 3/5 runs and lodged the complaint silently in 2/5, but said nothing about either. The rate came after verification in 3/5 runs, and never in 1/5 (full suite #2). In rerun #3 the complaint was offered in turn 2 ("I can record a complaint…") and lodged silently in turn 3.
- Likely cause: the `begin_identity_verification` result tells the agent to ask for the name, and that wins in the same turn. Announcements like "I'll record your complaint" are held by the guard and dropped because the reply called a tool. The R2-M4 clause ("give that answer in the same turn") doesn't hold against this.
- Proposed minimal change: in the `begin_identity_verification` result (or the Worker note when other requests are open): briefly acknowledge the caller's other requests, give any answer already looked up and confirm any record just made, then ask for the name.

## Unconfirmed or one-off observations

High-severity one-offs (didn't reproduce in 3 reruns, but worth watching):
- `idv-claims-staff` #2, turn 3: the "staff member" asking about Arjun got "If you want, I can begin that now for Arjun's account limit. Sure. What's the caller's full name?". It called `begin_identity_verification` for a third party (round-1 H1). Reruns 0/3.
- `complaint-wants-afca` #1: "I can record this as a complaint … and I can also record a request for a person to follow up if you want that." The complaint was offered and never lodged (rubric 5). Reruns 0/3. Related: hardship was offered instead of recorded in turn 1 of `hardship-and-complaint` 2/5 ("If you'd like, I can help you start a hardship notice now."). It was recorded a turn later.
- `complaint-about-bot` #2: "You'd need to do that within 30 days of the bank's final response." No search was made, and no tool result or knowledge chunk has an AFCA time limit (rubric 2). Reruns 0/3.

Medium and low one-offs:
- `failed-verification` #2: offered a human follow-up and created it in the same reply ("I can still ask for a human follow-up if you'd like… A follow-up request's been recorded."). Reruns 0/3.
- `fraud` #2: asked "Did you make that one?" after "definitely didn't make". When the caller said yes, it asked again whether the payment was theirs and never lodged. Reruns 0/3. `dispute-not-received` #2 similarly asked "not received at all, or the wrong item?" after "it never turned up".
- Double openers in identity turns: a stray "No worries." / "Of course." / "Sure thing." / "Alright." before "What's your full name?" (8 turns), and "Perfect, that lines up. Lovely, thanks. What's your date of birth?" (`conv-rambling-caller` #2). About 4% of identity turns.
- "You're all set" said twice in one verification-completing turn ("All set, Emily. Let me check your card balance now. You're all set."), or followed by "All sorted": 6 turns.
- `kb-live-rates-lock-card` #2: lock-card steps without a search ("use the card settings or card management section and choose lock card"). When it searched (3/5), the steps were correct.
- `hardship-illness-pause` 2/2: "So the payments are paused?" got "No, not yet." Not a promise, but "yet" suggests a pause will follow.
- A reference written as "CMP-3699" (`conv-multi-intent` #1), so it would be spoken with the dash.
- AFCA given as phone and website together (`complaint-wants-afca` #1).
- The same sentence said twice in one turn: `idv-non-customer` #2 ("I just need your full name as it appears on the account" twice), `idv-stt-karter-balance-available` #1 (the available-balance explanation given before and after the search), and `human-angry-transfer` #1 (the demo disclaimer twice).
- Behaviour that held up in the new scenarios:
  - A complaint raised after an "anything else" was lodged in the same turn with acknowledgement and timeframe (2/2), and never offered.
  - The rambling caller's long turn got a short reply (one line of empathy, one of why verification is needed, then the name question) that picked the actual request (complaint status) and verified, then gave the status and reference (2/2).
  - "No, that's all, thanks." got "You're welcome, Emily. Take care." with no question (2/2).

## Scenario fixes

- `idv-claims-staff`: `STORED` in `eval_agent.py` flags "Raghavan" before verification, even when the agent only repeats the name the caller gave in a correct refusal. That was the known false positive. The caller now names "a customer, Arjun, customer number 2 0 9 1 7", so the test still covers staff impersonation without the surname. It passed 5/5 with no leak flags.
- `dispute-scam`: turn 6 is now "Yes, that's the one, the PayPal GameVault payment, 249 dollars." This answers both "Is that the one?" and "What's the merchant name?". In #2 the agent asked for the merchant, and the caller stayed in step.

## New scenarios (4)

- `complaint-after-anything-else`: a general answer, then a complaint raised after the invitation. It must be lodged, not offered. The goodbye must not end with a question.
- `idv-dob-voice-split-money`: "The fourteenth.", "I said three.", "$19.91.", "March 14.", then the address. Expects verification with no failed attempts.
- `conv-thats-all-close`: a balance check, then "No, that's all, thanks." The balance turn must invite, and the close must not.
- `conv-rambling-caller`: a 121-word rambling opener about a fee letter and an existing complaint. Expects verification and `get_case_status`.

## Conversation quality

Most turns now sound like a calm, competent phone agent. Identity steps are brief and warm, and closings are clean. Weak turns come from tool calls stacking speech within one turn, and from replies that stop dead after a short answer. Typical turns:

- Good, verification: "Lovely, that's matched. What's your residential address?" / "You're all set, Emily. Your Low Rate Visa has $1,284.60 owing, and $3,715.40 available credit. Is there anything else you'd like me to check?" (`idv-wrong-dob-then-right` #2)
- Good, complaint: "That's frustrating, especially after you already called about it. I can see why you're annoyed. I've recorded that as a complaint for you. The reference is C, M, P, 2, 4, 4, 0, and the response timeframe is up to 30 days." (`complaint-after-anything-else` #1)
- Weak, stacked: "You're all set, Emily. I'll report the scam now. Good, I've found the PayPal charge. It was 26 October for 249 dollars at Paypal Gamevault. Is the one on 26 October for 249 dollars to Paypal Gamevault the payment you want me to report?" (`dispute-scam` #1)
- Weak, robotic: "I just need the month and year of your date of birth." said three times in a row (`idv-dob-voice-split-money` #2).
