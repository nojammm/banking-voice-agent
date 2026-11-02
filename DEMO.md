# Demo walkthrough (about five minutes)

Start the live agent with `python3 scripts/start_live.py` (README, option B). The page opens on **Call**, with the **Customer data** panel on the right. Wear headphones. Under **Try these**, pick a card to open that customer's record, then click **Start call**.

Use only the fictional customers in the README.

## 1. Verification (Alice, "Balance")

Say "Hi, what's my balance?". When the box appears, type `CUS-10482`. Then say the name, the date of birth as digits ("oh one, oh nine, ninety nine"), and the address in two parts ("50 Alice Street, Newtown"… "New South Wales 2020").

What it shows:
- The model never decides who the caller is. The Worker normalises each answer, matches deterministically first, fuzzy-scores names and place names, and asks a constrained LLM only in the borderline band.
- The model never sees the caller's answers or the stored values, so it can't leak them.
- The account tools check the Worker's verification state themselves. The prompt isn't the control.
- Thresholds are per-assistant config ([config/assistants.yaml](config/assistants.yaml)). A name match alone never unlocks anything, because every step has to pass.

Open **Session** in the panel to see the normalised date, the address parts carried between turns, the scores, and which path decided each step.

## 2. Records and a dispute

Say "And my last few transactions?", then "Coles charged me twice on the 24th.", then confirm.

What it shows:
- Balances and transactions come from backend tools, not the prompt.
- The tool flags the identical same-day charge. The agent lodges a dispute and reads back the reference without promising a refund.
- Any number the agent says must come from a tool result, the caller or a Worker note. Otherwise the reply is stopped before it is spoken.

The new case appears under **Cases**.

## 3. Hardship and a complaint (Minji, no verification needed)

Say "My hours got cut and I'm worried about next month's mortgage." Then: "Honestly, I'm fed up. I asked to downgrade my card weeks ago and still got charged."

What it shows:
- Neither "hardship" nor "complaint" was said. Both are recognised from meaning.
- The hardship notice is recorded without verification and without promising relief.
- The complaint timeframes come from the knowledge base.

## 4. A new loan and the callback form

Say "What's the difference between fixed and variable?", then "What would repayments be on 500,000 over 30 years?", then "Could someone help me apply?", then "Yes."

What it shows:
- General answers come from retrieval over labelled sources: regulatory summaries and clearly marked mock policy.
- The estimate comes from a calculation tool, not the model.
- The agent checks what the loan is for, then opens a real form. The backend validates and saves it and sends the result into the call. The agent responds whether the caller submits, cancels or hits an error.

## Next steps for production

- Telephony, and step-up verification for higher-risk actions.
- Integration with a real case-management system.
- Hybrid retrieval over a larger reviewed corpus.
- An evaluation set of real accents and noisy audio to tune the matching thresholds.

If the call can't connect, the recorded conversations in [evaluations/agent-conversations.json](evaluations/agent-conversations.json) show the same flows.
