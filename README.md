# Banking Voice Agent

A voice assistant prototype: an AI phone agent for a bank that verifies the caller, answers account and policy questions, and hands off to humans safely. **It is not a Westpac service.** All customers and accounts are fictional, and nothing touches a real bank.

The focus is agent security: the code, not the prompt, decides who the caller is and what data the agent can reach.

## Run it

| | What you get | You need | Time |
|---|---|---|---|
| **A** | Look around: the UI, the demo customers' records, how replies are checked | Python 3.9+ | 1 min |
| **B** | Live voice call with the banking agent (the main demo) | Python 3.10+ and 4 service accounts | 15 min |

Start by cloning:

```bash
git clone https://github.com/nojammm/banking-voice-agent.git
cd banking-voice-agent
```

### A. Look around, no keys

```bash
python3 server.py
```

Open http://127.0.0.1:8765. Nothing to install. You can browse the **Security** and **Knowledge** pages and the **Customer data** panel; **Start call** needs option B. Press `Ctrl+C` to stop.

### B. Live voice call

**1. Install** (once):

```bash
python3.12 -m venv .venv-livekit          # any Python 3.10+ works
.venv-livekit/bin/python -m pip install -r requirements-livekit.txt
```

**2. Add keys.** Copy the template and fill it in:

```bash
cp .env.example .env
```

| Key in `.env` | Where to get it |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | [LiveKit Cloud](https://cloud.livekit.io) → your project → Settings → API keys. The URL looks like `wss://your-project.livekit.cloud` |
| `DEEPGRAM_API_KEY` | [Deepgram console](https://console.deepgram.com) |
| `OPENAI_API_KEY` | [OpenAI platform](https://platform.openai.com/api-keys) |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | [ElevenLabs](https://elevenlabs.io): API key under Developers; voice ID from **My Voices** |

`.env` is gitignored. Any key you leave out is asked for in the terminal when you start.

**3. Start:**

```bash
python3 scripts/start_live.py
```

The browser opens at http://127.0.0.1:8766 on the **Call** page. Wear headphones, click **Start call**, allow the microphone, and talk after the greeting. You can interrupt the agent. Calls end after 10 minutes.

**4. Verify as a demo customer.** When the agent asks who you are, a box appears for the customer number. Then answer name, date of birth and address out loud.

| Customer | Number | Date of birth | Address | Good for |
|---|---|---|---|---|
| Alice Wonderland | `CUS-10482` | 1 Sep 1999 | 50 Alice Street, Newtown NSW 2020 | Balance, transactions, a double charge, an unknown PayPal charge |
| Minji Kim | `CUS-20917` | 24 Oct 1998 | 17 Kooyong Road, Caulfield North VIC 3161 | Mortgage hardship, complaints, existing disputes |

The **Customer data** panel on the right shows each customer's records, test scenarios to try (**Customers**), verification scores and events (**Session**), and cases opened during the call (**Cases**). **Reset data** restores the starting state.

A five-minute call walkthrough is in [DEMO.md](DEMO.md).

Use only the fictional details above. Don't say real personal information on a call.

## How it works

```
Caller's voice → LiveKit → Deepgram (speech to text) → Worker (identity check, bank tools, safety checks)
              → OpenAI (writes the reply) → ElevenLabs (speaks it) → Caller
```

The **Worker** ([live_agent.py](live_agent.py)) sits between the caller and the model and enforces the rules in code:

- **Identity verification is run by the Worker, not the model** ([idv.py](idv.py)). Each answer is normalised (for example "oh one, oh nine, ninety-nine" → `1999-09-01`), then matched exactly, then fuzzy-scored. A tightly constrained LLM is consulted only when a score is borderline. The model never sees the caller's answers or the stored values. Too many failed answers end verification and keep account tools locked for the rest of the call. Thresholds are in [config/assistants.yaml](config/assistants.yaml).
- **Account tools check verification themselves** ([banking.py](banking.py)), so a prompt injection can't unlock them.
- **Sensitive details are redacted in the transcript** (card numbers, emails, passwords, codes) before the model sees them.
- **Every number the agent says must come from a tool result, the caller, or the Worker.** Otherwise the reply is stopped before it is spoken.
- **Instruction-override attempts are blocked** before reaching the model.
- **Fraud and disputes are routed in code.** Unauthorised charges open a fraud case and are escalated.
- **Policy answers come from retrieval** ([kb.py](kb.py)) over summaries of public regulator and bank pages. If nothing relevant is found, the agent says so.
- **Privacy settings:** OpenAI `store=false`, Deepgram opt-out of training, LiveKit recording off.

Diagrams: open [architecture.html](architecture.html), [tool-calling.html](tool-calling.html) and [tool-safeguards.html](tool-safeguards.html) in a browser.

## Tests

```bash
python3 -m unittest test_app test_banking test_idv test_speech     # no keys, about 5 s
.venv-livekit/bin/python -m unittest                               # everything, including the voice agent (after step B1)
.venv-livekit/bin/python scripts/eval_agent.py                     # multi-turn conversations against the real model (needs OPENAI_API_KEY)
node scripts/e2e_test.cjs                                          # the site in a real browser, with server.py running (needs Playwright)
```

What was tested, the results by round and the known issues are in [EVALUATION.md](EVALUATION.md). Knowledge sources and their review process are in [KNOWLEDGE.md](KNOWLEDGE.md).

## Project map

| Path | What it is |
|---|---|
| `live_agent.py` | Voice agent Worker: call flow, tools, safety checks |
| `idv.py`, `config/assistants.yaml` | Identity verification and its thresholds |
| `banking.py`, `bank.py`, `data/bank_seed.json` | Bank tools and the fictional SQLite bank (created on first run) |
| `kb.py`, `data/*_kb.json`, `knowledge/` | Policy knowledge and retrieval |
| `prompts/` | Agent prompts |
| `server.py` | Local web server |
| `static/` | Browser UI and the demo inspector |
| `scripts/` | Launchers and evaluation scripts |
| `evaluations/` | Saved test and evaluation results |

## Limits

- Runs on your own machine only (localhost). There is no production login or rate limiting.
- The two customers and their accounts are made up. Policy summaries were checked against public pages on 27 Oct 2026 and may go out of date.
- Tests were run with synthetic voices. Real accents and background noise may behave differently.
- Two high-severity conversation issues are still open; see [EVALUATION.md](EVALUATION.md#known-issues).
