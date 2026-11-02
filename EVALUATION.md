# Evaluation

How the voice agent was tested, what the results were, and what is still open. Each run's raw output is saved under `evaluations/` with its date, model and limits.

## Test layers

| Layer | What it covers | How to run | Result |
|---|---|---|---|
| Unit and integration | ID&V parsing and matching, verification gating on every account tool, the number and injection guards, redaction, case records, the callback form, server endpoints | `.venv-livekit/bin/python -m unittest` | 188 pass (146 without the voice dependencies) |
| Browser end to end | A person using the site: navigation, customer records, tabs, reset, knowledge files, the call controls, phone width | `node scripts/e2e_test.cjs` | 14 checks pass; see [Browser tests](#browser-tests) |
| Conversations against the real model | Multi-turn calls through the real prompt, tools, ID&V Worker, LLM fallback and guards. Typed caller turns stand in for speech | `.venv-livekit/bin/python scripts/eval_agent.py` | 70 scenarios; see below |
| Knowledge accuracy | 45 customer questions answered from the policy knowledge base, each answer checked against the source page | `kb_eval/` | 0 incorrect, 1 unsupported, 0 unsafe |

Not tested: real phone audio with accents and background noise, production load, and a full accessibility audit.

## Conversation testing

`scripts/eval_agent.py` runs scripted conversations from `scripts/eval_scenarios.json` (70 scenarios: identity verification, hardship, complaints, disputes and fraud, loans, knowledge questions, safety and prompt injection). Each scenario has programmatic checks: which tools must or must not be called, which records must be created, whether verification passes, and phrases that must or must not be said.

Programmatic checks can't judge tone or near-misses, so every agent turn was also judged against a 16-rule rubric (`evaluations/overnight/PROTOCOL.md`). The rules cover compliance (no data before verification, no invented figures, no promised relief or credit, no financial advice, never asking for a card number or one-time code) and natural speech.

The loop was run by AI agents under that protocol. A tester ran the suite and judged the transcripts. An issue counted only if it reproduced in reruns. A fixer made one minimal change per issue and kept it only if the issue was gone in at least 2 of 3 reruns, the full suite had no new failure and the unit tests passed. Otherwise the change was reverted. Every kept fix is a separate commit with its evidence in [evaluations/overnight/changelog.md](evaluations/overnight/changelog.md).

### Results by round (gpt-5.4-mini)

| | Round 1 | Round 2 | Round 3 | Round 4 |
|---|---|---|---|---|
| Full suite passed | 84/94 | 109/112 | 120/124 | 119/124 |
| Confirmed high-severity issues | 5 | 2 | 1 | 2 |
| Confirmed medium-severity issues | 9 | 7 | 3 | 4 |

Round reports: [round 1](evaluations/overnight/round-1-report.md), [2](evaluations/overnight/round-2-report.md), [3](evaluations/overnight/round-3-report.md), [4](evaluations/overnight/round-4-report.md). Summary: [evaluations/overnight/SUMMARY.md](evaluations/overnight/SUMMARY.md).

### Model comparison

The same 64-conversation suite, run at the same time with the same prompt and tools:

| Model | Passed |
|---|---|
| OpenAI gpt-5.4-mini | 58/64 ([full-hardship-gpt.json](evaluations/full-hardship-gpt.json)) |
| Anthropic claude-haiku-4-5 | 55/64 ([full-hardship-haiku.json](evaluations/full-hardship-haiku.json)) |

gpt-5.4-mini is the default. Set `LIVE_LLM_MODEL=claude-haiku-4-5` to switch.

### Known issues

These were confirmed in round 4 and have not been re-judged since. Neither high-severity issue leaks data or claims an action that didn't happen.

- **High:** when a caller reports an unrecognised charge and reads out their card number in the same turn, the lock-card advice is sometimes missing.
- **High:** asked "are the payments paused?" during hardship, the agent can answer "not yet", which implies relief will follow.
- **Medium:**
  - A distressed caller isn't always told about free financial counselling.
  - Fraud calls can describe the transaction twice.
  - A fee complaint can start verification before it is lodged.
  - Fixed versus variable answers can drift towards advice.

## Knowledge accuracy

45 realistic questions were answered from the policy knowledge base, and each answer was graded against the official page it relied on ([kb_eval/round1/report.md](kb_eval/round1/report.md)).

| Grade | Count |
|---|---|
| Correct | 20 |
| Correctly declined (no source) | 6 |
| Incomplete | 8 |
| Knowledge gap | 10 |
| Unsupported claim | 1 |
| Incorrect or unsafe | 0 |

The agent declines when it has no source, so strict errors are low. The real weakness was coverage. The fixes in [kb_eval/round1/fixes.md](kb_eval/round1/fixes.md) changed 41 chunks and added 17.

## Browser tests

`scripts/e2e_test.cjs` drives a real Chromium browser through the site the way a person would, against the local server (option A in the README). It needs Playwright:

```bash
npm install --prefix .runtime playwright && npx --prefix .runtime playwright install chromium
python3 server.py &           # or scripts/start_live.py, then TEST_BASE_URL=http://127.0.0.1:8766
node scripts/e2e_test.cjs     # PW_CHANNEL=chrome uses the installed Chrome instead
```

It has 14 checks:
- The landing page and test cards, and opening and closing customer records.
- The Customers, Session and Cases tabs, and Reset data (cancel and confirm).
- Start call without keys: the call doesn't start and the microphone is never requested.
- The Security and Knowledge pages, including source links and the raw file view.
- Deep links, keyboard navigation, phone width (390 px), and the three diagram pages.
- No script errors.

On 2 Nov 2026 a full live call was also run in Chrome, with a synthetic caller voice played into the page's microphone one turn at a time. The customer number box, name, date of birth and address all passed, and the agent read the correct balances.

`scripts/live_ui_test.cjs` checks the call page's streaming transcript, microphone handling and forms against a simulated LiveKit room.

## Earlier text-only prototype

Before the voice agent, the project was a text assistant that picked evidence from four reviewed Westpac pages. Its results are kept for reference: `evaluations/evidence-*.json`, `ollama-*.json`, `openai-*.json`, `latest-*.json`, `ui-*` and `voice-*`. They don't describe the voice agent.
