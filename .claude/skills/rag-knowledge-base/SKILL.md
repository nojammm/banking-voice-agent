---
name: rag-knowledge-base
description: Convert researched facts from official sources (Australian Government regulators, AFCA, the Banking Code of Practice, Westpac public pages) into RAG-ready knowledge-base chunks for the banking chat agent, compile and lint them, and test retrieval and factual accuracy with scenario, answer and validator agents. Use when adding, updating, or auditing policy/compliance knowledge in knowledge/*.md or data/policy_kb.json.
---

# RAG knowledge base for policy and compliance

The chat agent answers policy questions by retrieving the top 3 chunks (BM25 in `kb.py`) and answering only from them. A chunk is useful only if it is retrieved for the right question and is correct on its own. Every rule below serves one of those two goals.

## Pipeline

1. **Research** → `knowledge/_research/*.md`: one fact per bullet with a verbatim quote, exact URL and page date. Never write a chunk from memory.
2. **Author** → `knowledge/<topic>.md`: chunks in the format below.
3. **Build and lint** → `python3 .claude/skills/rag-knowledge-base/scripts/build_kb.py` writes `data/policy_kb.json`. Fix every error; review every warning.
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

BM25 matches words, not meaning. If a scenario question misses its chunk:
1. Add the customer's words to `tags` or a new phrasing to `questions` (preferred).
2. Rename the `##` title to the words customers use.
3. Only then consider an `EXPAND` synonym in `kb.py`, which affects every document.

## Testing (multi-agent loop)

Run after every substantial KB change. Store each round in `kb_eval/round<N>/`.

1. **Scenario agent** writes `kb_eval/questions.json` from the customer's point of view without reading the KB. It includes out-of-scope and trap questions.
2. **Answer agent** answers each question using only `query_kb.py` output, cites chunk ids, and declines when nothing relevant is retrieved. It writes `answers.json`.
3. **Validator agent** (a) checks every chunk against its `url`, and (b) grades every answer: `correct`, `incomplete`, `incorrect`, `unsupported` (claim not in the cited chunks), `retrieval_miss` (the KB had it but retrieval didn't surface it), `kb_gap` (not in the KB), or `correct_decline`. It writes `validation.json` and `report.md`.
4. **Error rate** = (incorrect + unsupported + retrieval_miss + chunk factual errors) / (questions + chunks). Gate: **≤ 5% and zero incorrect facts in chunks**. `incomplete` and `kb_gap` are tracked and fixed, but don't count as errors.
5. If the gate fails, a **fixer agent** edits the chunks (and this skill, when a rule caused the failure), rebuilds, and the answer and validator steps rerun on the same questions plus 10 new ones.

Fix factual errors from the authoritative page only, never from the validator's memory.
