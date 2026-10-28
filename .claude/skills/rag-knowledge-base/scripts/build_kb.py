"""Compile knowledge/*.md chunks into data/policy_kb.json and lint them against the rag-knowledge-base rules.

Usage: python3 .claude/skills/rag-knowledge-base/scripts/build_kb.py [--check]   (--check lints without writing)
Exit code 1 on any error."""
import datetime
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / 'knowledge'
OUT = ROOT / 'data/policy_kb.json'
DOMAINS = {
    'government': ('asic.gov.au', 'moneysmart.gov.au', 'apra.gov.au', 'fcs.gov.au', 'oaic.gov.au', 'austrac.gov.au',
                   'scamwatch.gov.au', 'accc.gov.au', 'cyber.gov.au', 'treasury.gov.au', 'legislation.gov.au', 'cdr.gov.au'),
    'ombudsman': ('afca.org.au',),
    'industry-code': ('ausbanking.org.au', 'bankingcode.org.au'),
    'westpac': ('westpac.com.au',),
    'support-service': ('ndh.org.au', 'idcare.org'),
}
REQUIRED = ('id', 'authority', 'source', 'url', 'verified', 'tags', 'questions')
DANGLING = re.compile(r'\b(as (mentioned |noted )?above|see (above|below)|the following|as follows|this policy|mentioned earlier)\b', re.I)
LIVE_RATE = re.compile(r'\d+(\.\d+)?\s*% ?p\.?a\.?', re.I)
MIN_WORDS, MAX_WORDS, STALE_DAYS = 30, 200, 180


def parse(path):
    chunks = []
    for section in re.split(r'^## ', path.read_text(), flags=re.M)[1:]:
        title, _, rest = section.partition('\n')
        meta_match = re.match(r'\s*<!--(.*?)-->', rest, re.S)
        meta = {}
        if meta_match:
            for line in meta_match.group(1).strip().splitlines():
                key, _, value = line.partition(':')
                meta[key.strip()] = value.strip()
            rest = rest[meta_match.end():]
        chunks.append({'file': path.name, 'title': title.strip(), 'meta': meta, 'body': ' '.join(rest.split())})
    return chunks


def lint(chunk, today):
    errors, warnings = [], []
    meta, body = chunk['meta'], chunk['body']
    for key in REQUIRED:
        if not meta.get(key): errors.append(f'missing {key}')
    if not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', meta.get('id', '')): errors.append('id must be dotted lowercase, e.g. hardship.westpac.apply')
    authority, url = meta.get('authority'), meta.get('url', '')
    if authority not in DOMAINS: errors.append(f'authority must be one of {sorted(DOMAINS)}')
    else:
        host = urlparse(url).hostname or ''
        if urlparse(url).scheme != 'https' or not any(host == d or host.endswith('.' + d) for d in DOMAINS[authority]):
            errors.append(f'url {url!r} is not an https page on an allowed {authority} domain')
    try:
        age = (today - datetime.date.fromisoformat(meta.get('verified', ''))).days
        if age < 0: errors.append('verified date is in the future')
        elif age > STALE_DAYS: warnings.append(f'verified {age} days ago; re-check the source')
    except ValueError: errors.append('verified must be YYYY-MM-DD')
    words = len(body.split())
    if words < MIN_WORDS: errors.append(f'body has {words} words (min {MIN_WORDS})')
    if words > MAX_WORDS: errors.append(f'body has {words} words (max {MAX_WORDS}); split it by aspect')
    if DANGLING.search(body): errors.append(f'not self-contained: {DANGLING.search(body).group(0)!r}')
    if len([q for q in meta.get('questions', '').split('|') if q.strip()]) < 2: errors.append('needs at least 2 questions')
    if authority == 'westpac' and 'Westpac' not in body: errors.append('westpac chunk body must name Westpac')
    if LIVE_RATE.search(body): warnings.append('looks like a live interest rate; link to the rate page instead')
    return errors, warnings


def document(chunk):
    meta = chunk['meta']
    tags = [t.strip() for t in meta['tags'].split(',') if t.strip()]
    questions = [q.strip() for q in meta['questions'].split('|') if q.strip()]
    return {'id': meta['id'], 'title': chunk['title'], 'type': meta['authority'], 'source': meta['source'], 'url': meta['url'],
            'verified': meta['verified'], 'keywords': ' '.join(tags + questions), 'tags': tags, 'questions': questions,
            'text': chunk['body'], 'file': chunk['file']}


def main():
    today = datetime.date.today()
    chunks = [c for path in sorted(SRC.glob('*.md')) for c in parse(path)]
    failed, seen = 0, set()
    for c in chunks:
        errors, warnings = lint(c, today)
        cid = c['meta'].get('id', '?')
        if cid in seen: errors.append('duplicate id')
        seen.add(cid)
        for level, items in (('ERROR', errors), ('warn', warnings)):
            for item in items: print(f"{level:5} {c['file']} {cid}: {item}")
        failed += bool(errors)
    print(f'{len(chunks)} chunks in {len({c["file"] for c in chunks})} files; {failed} with errors')
    if failed: sys.exit(1)
    if '--check' not in sys.argv:
        docs = [document(c) for c in chunks]
        OUT.write_text(json.dumps({'generated': today.isoformat(), 'note': 'Compiled from knowledge/*.md by the rag-knowledge-base skill. '
                                   'Each entry summarises one official page; check verified dates before relying on them.',
                                   'documents': docs}, indent=1, ensure_ascii=False) + '\n')
        print(f'wrote {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
