"""Reproduce the small, source-checked interview corpus. Not an auto-approval crawler."""
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DATE = '2026-10-27'
BASE = 'https://www.westpac.com.au/'
SPECS = [
 ('lock', 'Lock and unlock your card', 'personal-banking/online-banking/making-the-most/lock-card/', [
  ('steps', 'How to lock your card', 'In the app, search for Lock card, choose your card, then use the temporary-lock button. In Online Banking, open Service, then Card services, choose the temporary-lock option, select your card and confirm.', ['Use a temporary lock when a misplaced card might be found. If it cannot be found, report it lost or stolen.'], {'purpose': 'temporary lock'}),
  ('duration', 'Duration and finding your card', 'The temporary lock lasts 15 days and then lifts automatically. You can unlock sooner if you find the card. Cancel the card if it remains missing after 15 days.', [], {'lock_days': 15, 'auto_unlock': True}),
  ('effects', 'What a lock affects', 'The lock also covers additional cardholders, Digital Cards, PartPay, mobile wallets and wearables. Direct debits and recurring card payments may be declined.', [], {'recurring': 'may be declined'}),
  ('unlock', 'How to unlock', 'In the app, use the same temporary-lock button again. In Online Banking, repeat the locking route and choose Unlock.', ['Only unlock a card you have found.'], {'unlock': 'same route'})]),
 ('details', 'Update your contact details', 'personal-banking/online-banking/making-the-most/update-details/', [
  ('steps', 'Changing contact details', 'You can change your email, mobile number or address. In the latest app, search for Update contact details, edit the relevant field and save. In Online Banking, navigate through Services, Preferences and Personal details, then Personal and Contact Details. Complete the SMS verification, make your changes and save.', ['Online updates require registration for Westpac Protect Security Code and a one-time password. Enter verification codes only in the official bank service.'], {'verification': 'Westpac Protect Security Code'})]),
 ('red', 'Red virtual assistant', 'personal-banking/online-banking/making-the-most/red-web-chat/', [
  ('overview', 'Availability and scope', 'Red is Westpac’s existing virtual assistant for everyday banking questions, available around the clock in English. It provides factual self-service guidance rather than personalised product advice.', ['When Red cannot answer, a human chat option may be offered, or you can contact the bank directly.'], {'availability': '24/7'}),
  ('access', 'Finding Red', 'In the latest app, open Contact us and then Chat with us. In Online Banking, use the help/chat popup at the bottom left of the main dashboard.', [], {'channel': 'app or Online Banking'})]),
 ('ai', 'Artificial Intelligence at Westpac', 'about-westpac/westpac-group/corporate-governance/artificial-intelligence-at-westpac/', [
  ('principles', 'Responsible AI principles', 'Westpac’s published AI principles address wellbeing; human rights, diversity and autonomy; fairness; privacy and security; transparency; contestability; accountability and human oversight; and reliable, safe operation. These principles guide the design, delivery, deployment and use of its AI systems.', ['This public statement does not describe or verify the bank’s internal technical implementation.'], {'principle_count': 8})])]

def digest(chunks):
    return hashlib.sha256(json.dumps(chunks, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

sources, chunks = [], []
for sid, title, path, specs in SPECS:
    group = []
    for facet, section, text, conditions, facts in specs:
        group.append(dict(id=sid+'.'+facet+'.v1', parent=sid, topic=sid, facet=facet, section=section,
                          text=text, conditions=conditions, facts=facts, source_version=1, access='public'))
    sources.append(dict(id=sid, url=BASE+path, title=title, fetched_at=DATE, reviewed_at=DATE,
                        review_due='2026-11-26', effective_date=None, content_hash=digest(group),
                        hash_scope='canonical reviewed chunks (not raw HTML)', version=1,
                        review_status='approved', owner='Interview prototype maintainer',
                        review_method='Source checked during build; re-review before the review_due date'))
    chunks.extend(group)
(ROOT/'data/knowledge.json').write_text(json.dumps(dict(sources=sources, chunks=chunks), indent=2, ensure_ascii=False)+'\n')
if __name__ == '__main__':
    print('Wrote four source-checked summaries; no live fetch or automatic approval.')
