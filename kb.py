"""Banking knowledge retrieval for the voice agent: BM25 over the demo knowledge base plus the
reviewed public Westpac guidance (which keeps its review, expiry and integrity gate)."""
import json
import math
import re
from collections import Counter
from pathlib import Path
import engine

KB_PATH = Path(__file__).resolve().parent / 'data/banking_kb.json'
POLICY_KB_PATH = Path(__file__).resolve().parent / 'data/policy_kb.json'  # Built from knowledge/*.md by the rag-knowledge-base skill.
EXPAND = [  # Spoken phrasings mapped onto the vocabulary the documents use.
    (r'\bmortgage\b', 'home loan'), (r"\b(?:can'?t|cannot|struggl\w*|afford|behind|lost my job|hours (?:got )?cut)\b", 'hardship repayments'),
    (r'\b(?:twice|double)\b', 'duplicate dispute'), (r'\brates?\b', 'interest rate'), (r'\b(?:scam\w*|stolen|hacked)\b', 'fraud security'),
    (r'\b(?:complain\w*|unhappy|annoyed|frustrated|ombudsman)\b', 'complaint afca'), (r'\bborrow\b', 'loan borrowing capacity'),
    (r'\bpaperwork\b', 'documents'), (r'\brefund\b', 'dispute chargeback'),
]
STOP = set('''a about am an and any are as at be been but by can could do does did for from get got had has have how i if
in is it its just me my of on or our please should so tell than that the their them there they this to up us was
we what when where which who why will with would you your want need know like
policy policies information info detail details'''.split())  # Filler in "the loan policy": it matched policy titles, not loans.
MIN_SCORE = 1.5
LABELS = {'regulation': 'Regulatory summary', 'mock_policy': 'Demo policy (mock)', 'westpac_public': 'Westpac public page',
          'government': 'Australian Government source', 'ombudsman': 'AFCA (external dispute resolution)',
          'industry-code': 'Banking Code of Practice', 'westpac': 'Westpac public policy', 'support-service': 'Free support service'}


def _documents():
    docs = []
    for d in json.loads(KB_PATH.read_text())['documents']:
        docs.append({'id': d['id'], 'topic': d['id'], 'facet': d['keywords'], 'section': d['title'], 'text': d['text'],
                     'conditions': [], 'type': d['type'], 'source': d['source'], 'url': d['url']})
    if POLICY_KB_PATH.exists():
        for d in json.loads(POLICY_KB_PATH.read_text())['documents']:
            docs.append({'id': d['id'], 'topic': d['id'], 'facet': d['keywords'], 'section': d['title'], 'text': d['text'],
                         'conditions': [], 'type': d['type'], 'source': d['source'], 'url': d['url']})
    for source, chunks in engine.reviewed_guidance():
        for c in chunks:
            docs.append({**c, 'type': 'westpac_public', 'source': source['title'], 'url': source['url']})
    return docs


def terms(text):
    out = []
    for t in re.findall(r'[a-z0-9]+', text.lower()):
        if t in STOP or len(t) < 2: continue
        for suffix in ('ing', 'ed', 'es', 's'):  # Light stemming: lasts/last, charged/charge, loans/loan.
            if len(t) > len(suffix) + 3 and t.endswith(suffix): t = t[:-len(suffix)]; break
        out.append(t)
    return out


def bm25(query, docs):
    bags = [terms(' '.join([d['section'], d['facet'], d['text']] + d['conditions'])) for d in docs]
    avg = sum(map(len, bags)) / len(bags)
    q = set(terms(query))
    df = {t: sum(t in b for b in bags) for t in q}
    ranked = []
    for d, bag in zip(docs, bags):
        tf = Counter(bag)
        score = sum(math.log(1 + (len(docs) - df[t] + .5) / (df[t] + .5)) * tf[t] * 2.2 /
                    (tf[t] + 1.2 * (.25 + .75 * len(bag) / avg)) for t in q if tf[t])
        if score >= MIN_SCORE: ranked.append((score, d))
    return sorted(ranked, key=lambda pair: (-pair[0], pair[1]['id']))


def search(query, k=3):
    expanded = query + ' ' + ' '.join(extra for pattern, extra in EXPAND if re.search(pattern, query, re.I))
    ranked = bm25(expanded, _documents())
    if not ranked: return []
    top = ranked[0][0]
    return [{'id': d['id'], 'title': d['section'], 'type': LABELS[d['type']], 'source': d['source'], 'url': d['url'],
             'text': d['text'] + (' ' + ' '.join(d['conditions']) if d['conditions'] else '')}
            for score, d in ranked[:k] if score >= top * 0.4]


def document(doc_id):
    return next((d for d in _documents() if d['id'] == doc_id), None)


def files():
    """The RAG files behind the agent, for the Knowledge page: each knowledge/*.md with its compiled chunks and raw text."""
    root = Path(__file__).resolve().parent
    out = []
    if POLICY_KB_PATH.exists():
        by_file = {}
        for d in json.loads(POLICY_KB_PATH.read_text())['documents']: by_file.setdefault(d['file'], []).append(d)
        for name, docs in sorted(by_file.items()):
            raw = (root / 'knowledge' / name).read_text()
            out.append({'file': 'knowledge/' + name, 'title': raw.partition('\n')[0].lstrip('# ').strip(), 'fetched': max(d['verified'] for d in docs),
                        'raw': raw, 'chunks': [{'id': d['id'], 'title': d['title'], 'text': d['text'], 'source': d['source'], 'url': d['url'],
                                                'label': LABELS[d['type']]} for d in docs]})
    corpus = engine.load_corpus()
    sources = {s['id']: s for s in corpus['sources']}
    out.append({'file': 'data/knowledge.json', 'title': 'Westpac digital banking guides', 'fetched': max(s['fetched_at'] for s in sources.values())[:10],
                'raw': json.dumps(corpus['chunks'], indent=1, ensure_ascii=False),
                'chunks': [{'id': c['id'], 'title': c['section'], 'text': ' '.join([c['text']] + c['conditions']), 'source': sources[c['parent']]['title'],
                            'url': sources[c['parent']]['url'], 'label': LABELS['westpac_public']} for c in corpus['chunks']]})
    return out
