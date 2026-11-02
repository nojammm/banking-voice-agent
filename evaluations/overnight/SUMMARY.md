# Conversation testing: summary (28–29 Oct 2026)

Four rounds of testing against the real model (gpt-5.4-mini), with fixes between rounds. The method is in [PROTOCOL.md](PROTOCOL.md): run the suite, judge every agent turn against a 16-rule rubric, confirm an issue with reruns, then make one minimal fix and keep it only if the reruns and the full suite agree.

## Results

| | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| Scenarios | 47 | 56 | 62 | 62 |
| Full suite passed | 84/94 | 109/112 | 120/124 | 119/124 |
| Confirmed high | 5 | 2 | 1 | 2 |
| Confirmed medium | 9 | 7 | 3 | 4 |

Each round ran the full suite twice. Round 4 also judged 738 agent turns by hand, counting the reruns.

## Notable fixes kept

| Issue | Change | Before → after |
|---|---|---|
| Someone asks about another person's account and is invited to verify | Verification is only for the person on the call; anyone else is pointed to the account holder | 6/10 → 0/6 |
| Says "noted" or "updated" without a tool call | Covered by the rule never to claim an action no tool performed; later hardship details update the notice | 11/17 → 2/15 |
| No lock-card advice for scams or unrecognised charges | Given in the first reply, before verification | 14/19 → 0/9 |
| Balance and available amounts swapped | The account tool keeps each figure with its label | 6/45 → 0/15 |
| A date of birth said in pieces, with speech-to-text artefacts ("I said three", "$19.91") | The Worker joins the pieces, and a turn that only adds a part never counts as a failed attempt | Failed → 21/21 |
| Finished answers leave the caller hanging | A check-in safety net in code, with tests | 21–25% → 3% of turns |

Every fix, including those reverted, is in [changelog.md](changelog.md) with its evidence. Reverted attempts that may be useful later are saved as `*.patch`.

## Open after round 4

- **R4-H1 (high):** an unrecognised charge reported together with a card number gets no lock-card advice.
- **R4-H2 (high):** "Are the payments paused?" gets "No, not yet", which implies relief will follow.
- **Medium:**
  - A distressed caller isn't always told about free counselling.
  - Fraud and scam calls describe the transaction twice.
  - A fee complaint starts verification before it is lodged.
  - Near-advice offers on fixed versus variable rates.

Details and evidence: [round-4-report.md](round-4-report.md).

## Files

- `round-<n>.json`: the full suite for each round. `round-<n>-confirm-<scenario>.json`: the confirmation reruns.
- `round-<n>-report.md`: issues, evidence and likely causes.
- `fix<round>-<issue>-*.json`: each fix's issue reruns, recheck and full suite.
- Scenarios: [scripts/eval_scenarios.json](../../scripts/eval_scenarios.json).
