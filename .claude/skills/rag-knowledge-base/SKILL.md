---
name: rag-knowledge-base
description: Convert researched facts from official sources (Australian Government regulators, AFCA, the Banking Code of Practice, Westpac public pages) into RAG-ready knowledge-base chunks for the banking chat agent, compile and lint them, and test retrieval and factual accuracy with scenario, answer and validator agents. Use when adding, updating, or auditing policy/compliance knowledge in knowledge/*.md or data/policy_kb.json.
---

# RAG knowledge base for policy and compliance

The chat agent answers policy questions by retrieving the top 3 chunks (BM25 in `kb.py`) and answering only from them. A chunk is useful only if it is retrieved for the right question and is correct on its own. Every rule below serves one of those two goals.

## Pipeline

1. **Research** → `knowledge/_research/*.md`: one fact per bullet with a verbatim quote, exact URL and page date. Never write a chunk from memory.
2. **Author** → `knowledge/<topic>.md`: chunks in the format below.
3. **Build and lint** → `python3 .claude/skills/rag-knowledge-base/scripts/build_kb.py` writes `data/policy_kb.json`. Fix every error, including editorial notes in bodies, and review every warning, such as a title that promises a phone number the body lacks.
4. **Retrieval check** → `python3 .claude/skills/rag-knowledge-base/scripts/query_kb.py "customer question"` shows exactly what the agent would see.
5. **Agent test loop** (see "Testing" below).

## Chunk format

Each topic file starts with a `# Title` line. Each chunk is a `##` section:

```markdown
## Westpac financial hardship: how to ask for help
<!--
id: hardship.westpac.apply
authority: westpac
source: Westpac – Financial hardship assistance
url: https://www.westpac.com.au/personal-banking/assistance/financial-hardship/
verified: 2026-10-28
tags: hardship, can't pay, missed repayment, lost job, illness
questions: I can't pay my mortgage this month, what do I do? | How do I apply for hardship with Westpac?
-->
Westpac customers who are struggling to make repayments can ...
```

Field rules:

| Field | Rule |
| --- | --- |
| `id` | `topic.subject.aspect`, lowercase, unique, stable. Never reuse an id for different content. |
| `authority` | `government` (ASIC, Moneysmart, APRA, OAIC, AUSTRAC, ACCC/Scamwatch, legislation), `ombudsman` (AFCA), `industry-code` (ABA Banking Code of Practice), `westpac`, or `support-service` (National Debt Helpline, IDCARE). The URL domain must match (enforced). |
| `source` | Publisher – page title, as the customer would recognise it. |
| `url` | The single page the facts come from. A chunk needing two sources is two chunks. |
| `verified` | Date the facts were checked against the live page. |
| `tags` | Customer words, not jargon: "can't pay", "scammed", "money back". |
| `questions` | 2–4 real customer phrasings separated by `|`. These are indexed for retrieval. |

## Body rules

- **Every sentence must be supported by the chunk's own `url`.** Round 1's commonest failure was a true fact copied from a neighbouring page (another FAQ, the T&Cs, a different flyer). Before saving, find each sentence's quote on that one page. If the fact lives elsewhere, put it in a separate chunk with that page's url, or drop it. Research notes are not a source; the live page is.
- **Keep qualifying conditions.** Never drop "if satisfied that…", "in most cases", "generally", "in writing", "if… involves a payment", or listed exceptions. Without them, a conditional rule reads as automatic and becomes wrong.
- **Copy lists whole.** If the page lists seven options, don't write "three ways". Say "including" only if you deliberately give part of the list.
- **The title must not promise more than the body.** A title that says "numbers", "enforcement paused" or "delays" needs those facts in the body. If they aren't there, rename the title.
- **No internal or editorial notes** ("not checked for this KB", "the page does not say…"). Bodies are customer-facing facts. Record gaps in `_research/` or the eval report.
- **No glosses the source doesn't use** (e.g. "known as open banking", "often called a credit ban"). Put those words in `tags` instead.
- **Check redirects.** If the url redirects, set `url` to the target, but only after confirming the target page still carries the chunk's facts.
- **One question-sized topic per chunk**, 30–200 words. Split anything longer by aspect (who / how / timeframe / what happens next). Never pad a short chunk with text its source does not say.
- **Self-contained.** Name the subject in the body ("Westpac", "AFCA", "the ePayments Code"); never "as above", "see below", "this policy", "they". The chunk will be read alone.
- **Attribute every rule to its authority**: "Under the National Credit Code…", "Westpac says…", "AFCA can…". The answer must be able to say where a fact comes from.
- **Exact facts verbatim**: numbers, timeframes, dollar limits, phone numbers and conditions copied exactly from the research quote, including their conditions ("up to", "in most cases", "business days" vs "days").
- **Effective dates** for values that change (compensation caps, guarantee limits, code versions): "From 1 January 2026, …" or "As at October 2026, …".
- **No live rates, fees or product eligibility.** Those change daily; point to the official page instead.
- **Say what to do next** when the source does: who to contact, the number, the link.
- **No advice.** Describe rights and processes; do not tell a customer what they should choose.
- When Westpac's practice is stricter or more specific than the regulation, keep both as separate chunks with the same tag set, so both are retrieved together.

## Retrieval tuning

BM25 matches words, not meaning. Safety and action chunks (SafeBlock, hardship apply, interpreter, IDCARE, credit ban, complaint contacts) need the words customers use in a panic or in limited English: "gave the code", "leaked", "my English is not good", "can I email". If a scenario question misses its chunk:
1. Add the customer's words to `tags` or a new phrasing to `questions` (preferred).
2. Rename the `##` title to the words customers use.
3. Only then consider an `EXPAND` synonym in `kb.py`, which affects every document.

Never paste eval questions verbatim into `questions`. Use natural variations, or the next round stops measuring generalisation. Rerun `python3 -m unittest test_banking` after tuning, because new tags can push other expected documents out of the top 3.

## Testing (multi-agent loop)

Run after every substantial KB change. Store each round in `kb_eval/round<N>/`.

1. **Scenario agent** writes `kb_eval/questions.json` from the customer's point of view without reading the KB. It includes out-of-scope and trap questions.
2. **Answer agent** answers each question using only `query_kb.py` output, cites chunk ids, and declines when nothing relevant is retrieved. It writes `answers.json`. Answer rules:
   - No facts beyond the cited chunks, not even true ones ("free interpreters may be available" without the interpreter chunk counts as `unsupported`).
   - Always give an action channel (a number, an app path or a link) from a retrieved chunk. If none was retrieved, say who to contact in general terms without inventing a number.
   - Say explicitly when a rule doesn't apply to the customer's facts (e.g. unclaimed money needs a $500+ balance; a recurring card payment is cancelled with the merchant, not the bank).
   - Never suggest ways around legal obligations (e.g. splitting cash deposits, skipping ID checks).
3. **Validator agent** (a) checks every chunk against its `url`, and (b) grades every answer: `correct`, `incomplete`, `incorrect`, `unsupported` (claim not in the cited chunks), `retrieval_miss` (the KB had it but retrieval didn't surface it), `kb_gap` (not in the KB), or `correct_decline`. It writes `validation.json` and `report.md`.
4. **Error rate** = (incorrect + unsupported + retrieval_miss + chunk factual errors) / (questions + chunks). Gate: **≤ 5% and zero incorrect facts in chunks**. `incomplete` and `kb_gap` are tracked and fixed, but don't count as errors.
5. If the gate fails, a **fixer agent** edits the chunks (and this skill, when a rule caused the failure), rebuilds, and the answer and validator steps rerun on the same questions plus 10 new ones.

Fix factual errors from the authoritative page only, never from the validator's memory.
