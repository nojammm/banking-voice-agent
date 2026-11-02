# Knowledge

The agent answers general banking questions only from retrieved text. If nothing relevant is found, it says it doesn't have that information. `search_banking_knowledge` ([kb.py](kb.py)) runs BM25 over three sources, maps spoken phrasings onto document vocabulary ("mortgage" → "home loan"), and returns the top matches with their source and URL.

| Source | Content | Built from |
|---|---|---|
| [data/policy_kb.json](data/policy_kb.json) | 107 chunks from official pages: Westpac (60), Australian Government regulators (32), the Banking Code of Practice (12), free support services (3) | `knowledge/*.md`, compiled by the `rag-knowledge-base` skill |
| [data/banking_kb.json](data/banking_kb.json) | 19 entries: 8 regulatory summaries and 11 clearly labelled **mock** policies (rates, fees, loan products) that the demo needs and that no real bank's terms cover | Written by hand for the demo |
| [data/knowledge.json](data/knowledge.json) | 8 reviewed chunks from 4 Westpac pages (lock card, update details, Red, AI at Westpac), with a review gate | `scripts/seed_knowledge.py` |

## Policy knowledge pipeline

1. **Research:** `knowledge/_research/*.md` holds one fact per bullet, each with a verbatim quote, the exact URL and the date the page was checked.
2. **Author:** `knowledge/<topic>.md` holds one `##` section per chunk. Each has metadata (`id`, `authority`, `source`, `url`, `verified`, `tags`, `questions`) and a self-contained body supported by that one URL.
3. **Build and lint:** `python3 .claude/skills/rag-knowledge-base/scripts/build_kb.py` writes `data/policy_kb.json`. It fails on duplicate ids, a URL domain that doesn't match the stated authority, editorial notes in a body, and other format errors.
4. **Check retrieval:** `python3 .claude/skills/rag-knowledge-base/scripts/query_kb.py "customer question"` shows exactly what the agent would see.
5. **Test answers:** realistic questions are answered from the knowledge base and graded against the source page. See [EVALUATION.md](EVALUATION.md#knowledge-accuracy).

The authoring rules are in [.claude/skills/rag-knowledge-base/SKILL.md](.claude/skills/rag-knowledge-base/SKILL.md): keep qualifying conditions, copy lists whole, copy figures verbatim, attribute every rule to its authority, and keep bodies free of internal notes.

## Review gate

Each source in `data/knowledge.json` carries `reviewed_at`, `review_due`, `review_status` and a SHA-256 `content_hash` of its chunks. A source is left out of answers when it is past its review date, not approved, or its chunks no longer match the hash. `python3 scripts/review_knowledge.py` checks the structure and hashes. Moving the date forward without re-checking the page is not a review.

## Limits

- Facts were checked against the live pages on 27–29 Oct 2026 and will go out of date. The `verified` date is on every chunk.
- Rates, fees and loan products are mock data, labelled as such in every result.
- Only public pages were used. Nothing here describes Westpac's internal systems.
- Pages fetched by `scripts/fetch_sources.py` are saved as candidates only. They need a person's review before they can be used.
