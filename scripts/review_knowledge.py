#!/usr/bin/env python3
"""Validate a manually prepared corpus; explicit revoke is the only automatic mutation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine

def validate(corpus):
    errors=[]; ids=[c['id'] for c in corpus['chunks']]
    if len(ids)!=len(set(ids)):errors.append('Duplicate chunk IDs')
    for source in corpus['sources']:
        missing=set(['id','url','title','fetched_at','reviewed_at','effective_date','content_hash','version','review_status','owner','review_due'])-set(source)
        if missing:errors.append('Missing fields: '+','.join(missing));continue
        group=[c for c in corpus['chunks'] if c['parent']==source['id'] and c['source_version']==source['version']]
        if source['url'] not in engine.ALLOWED_URLS:errors.append('Non-allowlisted URL')
        if not group:errors.append('Source version has no chunks')
        if engine.canonical_hash(group)!=source['content_hash']:errors.append('Hash mismatch: '+source['id'])
        for c in group:
            if set(['parent','section','conditions','source_version','facts','access','id','text','topic','facet'])-set(c):errors.append('Missing chunk fields')
    return errors

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--revoke',choices=['lock','details','red','ai']);args=parser.parse_args()
    corpus=engine.load_corpus()
    if args.revoke:
        for source in corpus['sources']:
            if source['id']==args.revoke:source['review_status']='revoked'
        engine.CORPUS.write_text(json.dumps(corpus,indent=2,ensure_ascii=False)+'\n')
        print('Revoked:',args.revoke)
    errors=validate(corpus)
    print('\n'.join(errors) if errors else 'Corpus structure and content hashes valid. This is not a factual approval.')
    raise SystemExit(bool(errors))
