#!/usr/bin/env python3
"""Bounded public-source candidate fetch. Never changes approved knowledge.json."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib import request, robotparser
from urllib.parse import urlsplit
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine

AGENT='BankingEvidenceInterviewPrototype/1.0'
class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('Redirect refused; review the source URL manually.')

class CleanSections(HTMLParser):
    """Keep headings and paragraphs in <main>; remove navigation, scripts and forms."""
    def __init__(self):
        super().__init__();self.main_depth=0;self.skip=0;self.section='Overview';self.parts=[];self.sections=[];self.heading=False;self.buffer=[]
    def handle_starttag(self,tag,attrs):
        if tag=='main':self.main_depth+=1
        if tag in ('script','style','nav','header','footer','form','noscript','svg'):self.skip+=1
        if self.main_depth and not self.skip:
            if tag in ('h1','h2','h3','h4'):
                self.flush();self.heading=True;self.buffer=[]
            if tag in ('p','li','br'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if self.main_depth and not self.skip and tag in ('h1','h2','h3','h4'):
            self.section=' '.join(' '.join(self.buffer).split());self.heading=False
        if tag in ('script','style','nav','header','footer','form','noscript','svg') and self.skip:self.skip-=1
        if tag=='main':self.main_depth=max(0,self.main_depth-1)
    def handle_data(self,data):
        if self.main_depth and not self.skip:
            (self.buffer if self.heading else self.parts).append(data)
    def flush(self):
        text=' '.join(' '.join(self.parts).split())
        if text:self.sections.append({'section':self.section,'text':text,'conditions':None,'review_status':'candidate'})
        self.parts=[]
    def result(self):self.flush();return self.sections

def read(url):
    opener=request.build_opener(NoRedirect())
    req=request.Request(url,headers={'User-Agent':AGENT})
    with opener.open(req,timeout=15) as response:
        raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Source exceeds fetch size limit.')
        return raw

def fetch(source_id):
    corpus=engine.load_corpus();source=next(s for s in corpus['sources'] if s['id']==source_id)
    url=source['url']
    if url not in engine.ALLOWED_URLS:raise ValueError('Source URL not allowlisted.')
    robots=robotparser.RobotFileParser();robots.parse(read('https://www.westpac.com.au/robots.txt').decode().splitlines())
    if not robots.can_fetch(AGENT,url):raise ValueError('robots.txt disallows this URL.')
    delay=robots.crawl_delay(AGENT)
    if delay:
        import time;time.sleep(min(delay,60))
        if delay>60:raise ValueError('Long crawl delay: retry manually later.')
    raw=read(url);parser=CleanSections();parser.feed(raw.decode('utf-8',errors='replace'));sections=parser.result()
    if not sections:raise ValueError('No main-content sections found; manual review needed. No fallback to full-page ingestion.')
    now=datetime.now(ZoneInfo('Australia/Sydney')).isoformat()
    candidate={'id':source_id,'title':source['title'],'url':url,'fetched_at':now,'reviewed_at':None,'review_due':None,
        'effective_date':None,'content_hash':hashlib.sha256(raw).hexdigest(),'hash_scope':'raw fetched HTML',
        'version':source['version']+1,'review_status':'candidate','owner':source['owner'],
        'chunks':[dict(s,parent=source_id,source_version=source['version']+1) for s in sections]}
    directory=engine.ROOT/'data/candidates';directory.mkdir(exist_ok=True)
    path=directory/(source_id+'-'+datetime.now().strftime('%Y%m%d%H%M%S')+'.json')
    path.write_text(json.dumps(candidate,indent=2,ensure_ascii=False)+'\n')
    print(f'Candidate saved: {path.name}. Manual conditions and approval review required; active corpus unchanged.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',choices=['lock','details','red','ai']);args=parser.parse_args();fetch(args.source)
