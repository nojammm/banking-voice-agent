"""Show exactly what the chat agent's retriever (kb.search) returns for a customer question.

Usage: python3 .claude/skills/rag-knowledge-base/scripts/query_kb.py "question" [--k 3] [--json]"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
import kb  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('question')
parser.add_argument('--k', type=int, default=3)
parser.add_argument('--json', action='store_true')
args = parser.parse_args()
results = kb.search(args.question[:300], k=args.k)
if args.json:
    print(json.dumps(results, indent=1, ensure_ascii=False))
elif not results:
    print('NO RESULTS: the agent would say it has no reviewed information on this.')
for r in [] if args.json else results:
    print(f"[{r['id']}] {r['title']}\n  {r['type']} | {r['source']} | {r['url']}\n  {r['text']}\n")
