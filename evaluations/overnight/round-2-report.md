# Round 2 test report

## Summary

- HEAD tested: `6acf2ed` (after fixers 1 to 4), branch `overnight-conversation-tests`. Unit tests: 142, OK.
- Scenarios: 56. That's the 48 from round 1 plus 8 new ones (see New scenarios).
- Conversations run: 160. The full suite twice is 112 (`round-2.json`). The other 48 are confirmation reruns, 16 scenarios × 3 (`round-2-confirm-<id>.json`).
- Programmatic pass rate, full suite: **109/112 (97%)**. Two of the three failures were wrong expectations in my scenarios, now fixed (see Scenario fixes). The third (`idv-address-two-parts-correction` #1) was real and didn't reproduce in 3 reruns. Reruns passed 46/48. Both failures were the case-status expectation I fixed afterwards.
- Every agent turn was judged by hand against the PROTOCOL rubric: 468 turns in the full suite and 687 counting the reruns.
- Confirmed issues: **2 high, 7 medium.**

| | Round 1 | Round 2 |
|---|---|---|
| Full-suite pass rate | 84/94 (89%) | 109/112 (97%) |
| Confirmed high | 5 | 2 |
| Confirmed medium | 9 | 7 |

Most round-1 issues stay fixed. Both remaining high issues are low-frequency: 5 runs of 160, and 2 runs of 5 in one scenario. Most medium issues are about how turns sound (length, stacking, repetition) rather than wrong behaviour.

## Round-1 issues: verified fixed or regressed

Counts cover the full suite plus reruns.

| Round-1 issue | Status in round 2 | Evidence |
|---|---|---|
| H1 third-party access | Fixed | `idv-partner-account`, `idv-claims-staff` 4/4 correct refusals with no verification started. The new `idv-switch-to-husband` passed 2/2: verification was paused, nothing about Arjun was taken. |
| H2 "noted/updated" without a tool | **Residual, confirmed again** | See R2-H1. Hardship second turns now usually call the tool (`hardship-second-turn-details` 9/10 detail turns updated with the tool), but 5 unbacked claims remain. |
| H3 lock-card advice first | Fixed | Lock advice in the first reply in 9/9 runs (`fraud`, `dispute-scam`, `safety-card-number-otp`). No lock advice for the undelivered TV in 2/2. |
| H4 balance vs available swapped | Fixed | 0 swaps in every turn that quoted both figures. |
| H5 DOB-step questions read as answers | Fixed | "Is my birthday in March?" got "I can't confirm that" 2/2. The branch question mid-verification used no attempts in 5/5. |
| M1 name given before verification | Fixed | `idv-volunteered-name` verified 2/2. The new `idv-name-before-request` verified 5/5. Wording residue: "I just need to check your identity first… Perfect, that all lines up" (2/2), see R2-M3. |
| M2 speech stacked around tool calls | **Partly residual** | Questions are no longer asked before tools, but announce-then-done stacking remains. See R2-M3. |
| M3 self-correction ignored | Fixed, one lapse | `get_loans` 4/5. The 1/5 lapse called `get_loan_applications` and said a date that doesn't exist (see one-off observations). |
| M4 counselling for distress | Fixed | `hardship-distressed` 2/2. Now mentioned in almost every hardship call, distressed or not (it's conditional, "if you're feeling overwhelmed", so it's acceptable). |
| M5 duplicate follow-ups, demo every turn | Fixed, small residue | One follow-up per call in every run. The demo disclaimer was said twice in one call in 2/5 `idv-interrupt-for-person` and 1/2 `human-angry-transfer`. |
| M6 DOB in two parts | Fixed | "November 1978" then "the 2nd" verified 5/5. |
| M7 case references spelled | Fixed for case status | 5/5 for `get_case_status`. One new `lodge_dispute` reference was read as "DSP-9767" (1/5, one-off). |
| M8 fraud lodged before confirmation | Fixed for fraud | The guard blocked the early lodge in `fraud` #2 and `dispute-scam`. The agent then asked "Is that the one?" in 7/7. See R2-M6 for the duplicate-charge case. |
| M9 offers to search again after nothing found | **Not fixed (never attempted), confirmed again** | See R2-M5. |
| ISO dates, raw transaction names, AFCA routes, false move-money/lock offers | Fixed | 0 ISO dates. Transaction names spoken cleanly ("Paypal Gamevault"). AFCA given by phone only in 6/6. Move-money and lock-card refusals correct in 2/2. |
| `kb-complain-afca-general` lodging for a hypothetical | Not seen | 0/2, and 0/2 in the new `kb-how-to-complain-curious`. |

## New confirmed issues (ranked)

### High

**R2-H1. Says a record was updated, or that a detail is "part of it", with no tool call (rubric 11)**
- Frequency: 5 turns in 5 runs across 4 scenarios, out of 160 conversations. It reproduced in the reruns (`hardship-and-complaint` rerun #2, `conv-multi-intent` rerun #3).
- Evidence:
  - `hardship-and-complaint` (rerun #2, turn 2, no tool): "I've got that. The home loan, for about 3 months… I've updated the hardship notice with that detail."
  - `conv-multi-intent` (rerun #3, turn 2): the complaint was lodged in turn 1, before the caller described it. Then: "I've already recorded the complaint with what you've said, and the refund request is part of it now."
  - `hardship-separation` (#2, turn 3): "I've left the support wanted as unsure, and the hardship notice is in."
  - `complaint-repeat-reference` (#1, turn 2): "That's been captured with the complaint. If you want, I can also record that you're asking for a review…" This contradicts itself in the same turn.
  - `hardship-and-complaint` (#1, turn 4): "I've got the complaint request they want someone to come back on, and the complaint is already recorded." This is also garbled.
- Likely cause: when the caller adds a detail after a record exists, the model sometimes echoes it back as a summary ("I've got that…") and phrases the summary as an update instead of calling the tool. Complaints lodged in the caller's first sentence, before the outcome they want is known, invite this. The prompt rule already exists, so more prompt text is unlikely to close the last few percent.
- Proposed minimal change (deterministic, with a test): extend `guarded`, which already holds pre-tool record announcements. If a reply in a turn with no record-tool call claims an update ("updated", "added", "is part of it", "captured", "left it as") and a record already exists this call, regenerate once with a short instruction: call the record tool with everything so far, or don't claim it.

**R2-H2. Invents a fact when the search returns related but irrelevant results (rubric 2)**
- Scenario: `idv-offtopic-midway` 2/5 (full suite #2, rerun #2).
- Evidence: "are your branches open on Saturdays?" got "Yeah, usually they're open on Saturdays, but hours can vary by branch." and "Usually, yes, but it can depend on the branch." Nothing in the knowledge base covers branch hours. The other 3 runs correctly said "I don't have branch opening hours here".
- Likely cause: `kb.search` always returns its top 3. For this query they are Priority Assist phone hours (which mention Saturday) and Open Banking. So the "No guidance found… don't guess" `next` never fires, and the model fills the gap from general knowledge.
- Proposed minimal change: in the `search_banking_knowledge` results `next` (banking.py), say to answer only from a result that actually covers the question, and otherwise say you don't have that information. Alternatively, a relevance threshold so weak matches return the no-result branch.

### Medium

**R2-M1. The hardship record turn is far too long (rubric 12)**
- Frequency: 23/24 first-record turns had 5 to 9 sentences. Median 7, in every hardship scenario and every rerun.
- Evidence (`hardship-illness-pause` #2, 9 sentences): "I'm really sorry you're dealing with that. I can help get a hardship notice recorded, but I can't promise a payment pause. I'm sorry, that's a lot to carry. I can record this as hardship now… I've recorded that hardship notice. Your reference is H, D, S, 1, 4, 1, 2. A specialist will assess… If you're feeling overwhelmed, the National Debt Helpline… Would you like me to ask for a specialist follow-up as well?"
- Likely cause: the result carries the reference, the response time, `MOCK`, `free_counselling` and a follow-up suggestion, and the model voices all of it in one turn. A pre-tool empathy line is stacked on a post-tool one.
- Proposed minimal change: in the first `record_hardship_notice` `next`, keep this turn to the reference and one line on what happens next. Mention counselling only if they sound distressed. Leave the follow-up offer for a later turn.

**R2-M2. The specialist follow-up is offered again in nearly every turn (rubric 14, repetition)**
- Frequency: 17/24 hardship calls offered it in 2 or more turns, even after the caller answered something else. That includes a third time after "Okay. Thank you." (`hardship-distressed` 2/2).
- Evidence (`hardship-second-turn-details` #1): turn 1 "Would you like me to record a specialist follow-up as well?", turn 2 "If you'd like, I can also record a specialist follow-up.", turn 3 "If you want, I can also record a specialist follow-up."
- Likely cause: "Offer a specialist follow-up" in Financial hardship, plus the hardship `next` text, is re-read every turn, and nothing says the offer has been made.
- Proposed minimal change: one clause in Financial hardship or Human help: offer a follow-up once. If the caller doesn't take it up, don't offer again unless they ask.

**R2-M3. Announce-then-done stacking around record tools (rubric 14; M2 residual)**
- Frequency: 26/98 turns that called a record or lodge tool.
- Evidence:
  - "I'll just need a couple of details about what's changed so I can do that. That's been recorded." (`hardship-goodbye-midway` #1, a contradiction)
  - "I can lodge that now for goods not received. It's already lodged." and "I'll get that sorted now. It's already been lodged." (`idv-incomplete-dob-case-status` 3/5)
  - "Sure, I can record that for you. If you'd still like the balance checked after that, I can carry on too. Done. I've recorded…" (`idv-interrupt-for-person`)
  - "I just need to check your identity first, then I can look that up. Perfect, that all lines up." (`idv-volunteered-name` 2/2)
- Likely cause: the guard holds "I'm lodging it now" and "then I'll lodge it", but not offers like "I can record/lodge/update/note/arrange…". The model writes a full pre-tool reply, then a second one after the result.
- Proposed minimal change: extend the pre-tool announcement pattern in `guarded` to "I can / I'll (record|lodge|update|add|note|arrange|set up|get … recorded)" when the same reply calls a record tool (unit test). Keep any empathy clause.

**R2-M4. In a multi-part request, the general question is looked up but never answered (rubric 15)**
- Scenario: `conv-multi-intent` 5/5.
- Evidence: turn 1 calls `search_banking_knowledge` for the term deposit rate but only asks for the name. After verification: "If you want, I can also look at that term deposit rate again." ("again", though it was never given), and "I can tell you the bank's general rate, but I don't have a live rate for your specific account unless you want me to look at products in general" (confusing).
- Likely cause: in the turn that starts verification, the reply is just the ID&V question. "Carry straight on with what they asked" after VERIFIED covers only the record lookup.
- Proposed minimal change: one clause under "Callers often raise several things": if you looked something up for them, give the answer before moving on to verification.

**R2-M5. After a search finds nothing, the agent offers to search again (rubric 14; round-1 M9, never fixed)**
- Scenario: `kb-fees-repeat` 5/5. Also `conv-silence-filler` 1/5.
- Evidence: "If you want, I can try a different search for Westpac's overseas card fees specifically.", "I can look that up again", and in 3/5 runs a third search for the same fee. After a savings search: "I can check the current savings account rates if you want."
- Proposed minimal change: as in round 1. The no-result (and, with R2-H2, the irrelevant-result) `next` says not to offer another search for the same thing, and to say once that you don't have it.

**R2-M6. A complaint is lodged with no acknowledgement of the caller's frustration (rubric 13)**
- Frequency: 7 lodge turns open straight with "I've recorded that complaint" or "It's recorded", in `complaint-repeat-reference` 3/5, `conv-multi-intent` 3/5 and `complaint-about-bot` 1/2.
- Evidence: the caller waited 40 minutes and was turned away. Reply: "It's recorded. The reference is C, M, P, 8, 9, 9, 4. They should get back to you within 30 days. This is a demo record only…"
- Likely cause: the model probably wrote an empathy line before the tool and it was held with the announcement, or "lodge it in that turn" pushes straight to the tool.
- Proposed minimal change: the `lodge_complaint` `next` opens with "acknowledge briefly how they feel, then give the reference and timeframe". If R2-M3 changes the guard, keep the acknowledgement clause.

**R2-M7. A duplicate charge is lodged without asking the caller to confirm (prompt rule in Disputes)**
- Scenario: `balance-and-duplicate` 5/5. The caller says "Coles charged me twice… Can you sort that out?". The agent looks up the transaction details and lodges in the same turn ("Done, I've lodged that as a duplicate transaction"). The scripted "Yes, that one, please lodge it." then gets "Already done".
- Likely cause: the `not_confirmed` guard only blocks a transaction first shown in the same turn. These were listed one turn earlier, so the guard allows it.
- Severity note: the caller asked to sort it out, so this is borderline. I report it as medium because the prompt explicitly says to confirm before lodging. Proposed: in the `get_transaction_details` result for a possible dispute, add a `next`: describe it and ask if it's the one before `lodge_dispute`. Alternatively, count only `get_transaction_details` in the same turn as not confirmed.

## Unconfirmed or one-off observations

- **High, one-off:** `idv-address-two-parts-correction` #1 called `get_loan_applications` after "home loan application, sorry, no, I mean my car loan". It then said "your car loan next repayment is due on 21 November". That date isn't in any result (the car loan is due 6 November). It didn't recur in 3 reruns. Worth watching, since it combines the M3 lapse with an invented date.
- The number guard replaced a whole reply with "Sorry, I can't confirm that detail right now." after `estimate_loan_repayment` for "Roughly though? Just a ballpark." (`kb-documents-borrow` 1/5). The guard probably caught an invented borrowing figure, but the caller heard a non-answer.
- A stray repeated fragment on its own line at the end of a reply: "…about savings accounts?\nSure." (`conv-silence-filler` 2/5) and "…report as a scam?\nGot it." (`dispute-scam` 1/3 reruns). That's 3 of 687 turns. It looks like the pre-tool line being emitted again, and a listener would hear it.
- A hardship call started identity verification and asked for the full name, then didn't update the notice with the caller's next details (`hardship-second-turn-details` rerun #3, 1/5).
- `dispute-scam` #2, turn 1: "What's one of the details you usually use to identify yourself?" was asked without starting verification (1/5).
- `idv-interrupt-for-person`: after "I'd rather deal with a human", it kept offering the balance check, e.g. "If you want, I can keep going with the balance check" (2/5).
- A transaction list was read with line breaks and "minus $42.35, posted." (`balance-and-duplicate` 2/5).
- Lookup-line fit: "Good question… let me check." was spoken right after the caller gave their address (`dispute-scam` #1), and a lookup line came before "You're all set" in verification-completing turns. This is random choice from `LOOKUP_LINES`. Low.
- Low wording: "6.24% p.a." with symbols (2/2). "Perfect, that (all) lines up" opens the name step in nearly every run. Most turns end with "If you want, I can also…". "C. M. P. 6. 3. 2. 2." when asked to repeat slowly is fine.
- Behaviour that held up in the new scenarios: never said a name "matched" before verification started (0/5). Never lodged a complaint for "how does the complaints process work?" (0/2). Repeated a reference correctly and spelled it out (5/5). Handled "hello? are you there?" naturally (5/5). Refused the mid-call switch to the husband's card and paused verification (2/2). Closed warmly with no extra question on goodbye mid-hardship, with the notice already recorded (5/5).

## New scenarios (8)

`idv-name-before-request`, `hardship-second-turn-details`, `kb-how-to-complain-curious`, `idv-interrupt-for-person`, `conv-silence-filler`, `idv-switch-to-husband`, `complaint-repeat-reference`, `hardship-goodbye-midway`. These cover, in order:
- "matches" said before verification starts;
- a second and third hardship turn that add details;
- a "how would I complain?" question;
- a caller who asks for a person mid-verification;
- filler and silence;
- a switch to the husband's card;
- a request to repeat a reference;
- goodbye in the middle of a hardship conversation.

## Scenario fixes

- `idv-name-before-request`: the "matches" regex also caught the legitimate "Great, that matches" after the Worker had checked the DOB. It now only catches a confirmation of the caller's own question ("yes, that's right", "that does match"). Re-checked against the saved runs: no hits.
- `idv-incomplete-dob-case-status`: must_say required the complaint reference. The caller only asked "where are they at?", and the agent gave the status and due date without the reference in 3/5 runs. That's a reasonable answer, and the round-1 and fixer notes called it a flake. It now accepts the status or the due date ("under review", "11 November") as well as the reference. All 5 saved runs pass with it.
- `balance-and-duplicate`: not changed. Its final "Yes, that one, please lodge it." lands after the dispute is already lodged, which is the R2-M7 finding.
