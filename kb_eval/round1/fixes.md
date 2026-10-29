# Round 1 fixes

The build reports 107 chunks in 10 files with 0 errors and 0 warnings. `python3 -m unittest test_banking` passes (20 tests).

Totals: 41 chunks changed (33 for facts or source, 8 for tags and questions only), 17 added, 0 removed.

## Chunks fixed (facts and source)

- extracare.westpac.power-of-attorney: rewritten from the page's list of seven authority types, replacing "three ways". The incorrect signatory claim was moved to trusted-access with the correct wording. Added general vs enduring vs FMO.
- payments.gov.mistaken-payment: added the "satisfied that a mistaken internet payment has occurred" and "sufficient funds" conditions, plus cl 30.3 (if not satisfied, the bank may seek consent). The 10-business-day figure is now a maximum.
- complaints.gov.rg271-timeframes: 21 days now stated only for default notices. Hardship and postponement complaints are flagged as having exceptions (moved to the new rg271-hardship chunk).
- disputes.westpac.chargeback-limits: removed the internal note "Time limits for other card types were not checked…".
- hardship.gov.written-confirmation: title changed to "Hardship changes confirmed in writing" (dropped the "enforcement paused" promise).
- hardship.westpac.options: url moved to the redirect target (applying-for-financial-hardship-support). Replaced "case by case" with the page's "work with you to understand your needs, circumstances…".
- hardship.westpac.apply: url moved to the redirect target. Joint-holder wording now follows the page. Added the free translator line (13 20 32; Remote Indigenous 1800 230 144), which is on that page.
- hardship.westpac.credit-report: url moved to the redirect target (the page carries the same facts).
- hardship.westpac.third-party: removed the National Debt Helpline line (it belongs to another page). Added the counsellor email, the authorisation forms and the documents needed.
- accounts.westpac.close-own: removed the T&Cs sentence about notifying payers and cancelling direct debits (it now has its own chunk on the T&Cs url).
- accounts.westpac.unclaimed-money: added "you have not notified us you wish to treat the account as active", "exemptions apply", and the $0.01 activation / 132 032.
- scams.westpac.report: added the condition "if the case involves a payment activity" for reporting to ASD/ACSC. Removed the editorial sentence "page does not state 24/7".
- scams.westpac.verify-caller: replaced the pointer "separate security guidance" with the SafeCall page's own check (ask for a reference number, hang up, call 132 032).
- scams.westpac.never-links: removed the reporting-channel sentence (it was not in the flyer). It now gives the flyer's own case-update channel.
- scams.westpac.guarantee-conditions: url moved to /security/how-we-protect-you/, which holds both the guarantee text and the Corporate Online Banking exclusion. Added the page's "how to qualify" conditions.
- security.westpac.card-lock: added the Emergency Card Services numbers 1300 651 089 / +61 2 9155 7700 (the title promised numbers).
- security.westpac.saferpay: removed the first-time-payee and Security Code sentence (moved to new-payee). Title changed so it no longer promises "payment delays". Replaced with SaferPay page facts.
- complaints.westpac.customer-advocate: added that the Advocate supports complex and sensitive complaints. Removed the unsourced "Secure Feedback Form" channel and the duplicated sentence.
- credit.westpac.ban-period: removed the unsourced gloss "often called a credit ban".
- extracare.westpac.bereavement-notify: "certified copies, not originals" changed to "certified copies" (the cited guide does not say "not originals").
- extracare.westpac.family-violence: added the page's confidentiality line and 1800RESPECT 1800 737 732.
- complaints.gov.afca-overview: removed the editorial note about the KB not covering AFCA time limits and caps.
- credit.gov.free-report: removed "or more often if you have recently been rejected for credit" (not on the Moneysmart page).
- scams.gov.what-to-do: IDCARE is now only for "if a scammer has your personal details", with hours Mon–Fri 8am–5pm as Moneysmart states.
- scams.gov.spf-status: removed three claims sourced from other pages (Act passed 2025, banking designated first, 31 March 2027). The body now covers only the 9 June 2026 release: AFCA authorised, an alternative to legal action, and AFCA assesses SPF compliance. Title and questions changed.
- openbanking.gov.cdr: removed the "known in banking as open banking" gloss (kept as a tag). Uses the OAIC wording.
- privacy.gov.access: uses the OAIC "general right… except where the law allows refusal" wording instead of the unsupported "Privacy Act's APPs". Added "can't be excessive" and "in writing".
- privacy.gov.oaic-complaint: AFCA wording now follows the page ("should contact AFCA, which has the power to deal with your privacy complaint").
- privacy.gov.data-breach: now "Generally… 30 days to assess whether a data breach is likely to result in serious harm" (dropped "suspects").
- credit.gov.correct-report: contact the credit provider in the first instance, or a CRB.
- deposits.gov.fcs-overview: "Government-backed safety net" changed to "Australian Government scheme" (the page's own words).
- deposits.gov.fcs-joint: now "any other eligible deposits they hold under their own name".
- deposits.gov.fcs-payment: added "in most cases". The liquidation claim is now "can make a claim… rank third" instead of "must".

## Chunks changed for retrieval only (tags and questions; natural variations, not eval wording)

- security.westpac.safeblock: added tags for gave a code, password, money taken, urgent and hacked, plus one new phrasing.
- extracare.westpac.interpreter: added ESL and language tags, deaf/Auslan and free, plus one new phrasing.
- extracare.westpac.nrs: added can't hear, hearing impaired, can't use the phone, and message in the app.
- extracare.westpac.priority-assist-hours: added elder abuse, older parent and financial abuse.
- idtheft.support.idcare: added data breach, leaked, Optus/Medibank, company hack and exposed ID.
- credit.westpac.ban-period: added data breach, leaked and exposed-details tags, plus one new phrasing.
- complaints.westpac.contact-overseas: added email a complaint, in writing and formal complaint, plus one new phrasing.
- code.overview: added banking code, code of practice, do banks have to follow, and BCCC.
- code.vulnerability: added elderly parent and older customer.
- hardship.westpac.apply: added can't pay credit card or minimum, interest, interpreter and ESL tags, plus one new phrasing.
- privacy.gov.data-breach: added details leaked and cyber attack tags.

## Chunks added (each quote checked on its own url)

- hardship.westpac.faq (applying-for-financial-hardship-support): no collections during an approved arrangement, third-party authorisation, refusal in writing, NDH, AFCA.
- extracare.westpac.trusted-access (getting-help-from-someone-you-trust): authorised third party, signatory (in-branch access only; set up in branch or online/app), account sharing, third-party access.
- extracare.westpac.authority-misuse (same page): appointed authority causing harm goes to Priority Assist; PIN/password change, SafeBlock, report.
- extracare.westpac.bereavement-debts (deceased-estates page): credit card debt offset where permitted, loans continue, executor pays debts, POA invalid after death.
- accounts.westpac.closing-checklist (Personal Accounts T&Cs): return cards and cheques, notify ATO/Medicare, cancel periodical payments and direct debits.
- security.westpac.new-payee (/security/articles/creating-a-payee/): Security Code for first payee, Osko delays, red flags, verify changed bank details.
- payments.westpac.recall (/faq/incorrect-payment/): payment recall/trace via "Report this transaction"; no guarantee.
- complaints.westpac.process (feedback-complaints): reference number, review, keep informed, outcome with reasons, what to have ready.
- complaints.gov.rg271-hardship (RG 271): hardship and postponement complaint timeframes with the insufficient-information and agreement exceptions.
- credit.gov.repayment-history (OAIC repayment-history-and-defaults): more than 14 days late counts as missed, 2 years, hardship info 1 year, no notice required.
- credit.gov.default-listing (same OAIC page): 60 days, $150, two notices, 14-day wait, 3-month limit, no default while hardship is being decided.
- code.enforce-bccc (Banking Code 2025 PDF): IDR, then AFCA (binding, free), court action; BCCC monitors and takes breach reports but "is not a complaints resolution body".
- code.direct-debits (Code paras 150–153): the bank cancels Direct Debits, but recurring card payments must be cancelled with the merchant; 13-month list; unauthorised DD investigation.
- code.card-disputes (Code paras 157–159): chargeback claim within card scheme limits, including recurring arrangements; ePayments Code rights.
- code.guarantor-exit (Code paras 102, 120, 122–123): guarantee limit, withdrawing by written notice, ending liability, written acceptance for increases.
- openbanking.westpac.share (westpac.com.au/about-westpac/innovation/open-banking/): only ACCC-accredited ADRs; authorise with Customer Number + OTP; never the Online Banking password.
- openbanking.westpac.dashboard (same page): data sharing dashboard, withdraw any time, consents up to 12 months, joint-account disable.

## Chunks removed

- None.

## Skill changes (.claude/skills/rag-knowledge-base/)

- SKILL.md Body rules: every sentence must be supported by the chunk's own url (facts from neighbouring pages go in their own chunk or are dropped); research notes are not a source.
- SKILL.md Body rules: keep qualifying conditions ("if satisfied", "in most cases", "generally", "in writing", exceptions).
- SKILL.md Body rules: copy lists whole; never under-count ("three ways" vs seven).
- SKILL.md Body rules: the title must not promise anything the body lacks.
- SKILL.md Body rules: no internal or editorial notes in bodies.
- SKILL.md Body rules: no glosses the source doesn't use; put them in tags.
- SKILL.md Body rules: check redirects; update url only if the target still carries the facts.
- SKILL.md Pipeline step 3: mentions the new editorial error and title/phone warning.
- SKILL.md Retrieval tuning: safety/action chunks need panic and limited-English phrasings; never copy eval questions verbatim; rerun test_banking after tuning.
- SKILL.md Testing step 2 answer rules: no facts outside cited chunks; always give an action channel; say when a rule doesn't apply; never suggest ways around legal obligations.
- build_kb.py: new ERROR when a body contains an internal or editorial note (`EDITORIAL` regex: "knowledge base", "not checked/verified/covered", "page does not state", "does not publish", TODO/TBC).
- build_kb.py: new warning when the title promises a number or phone but the body has no phone number (`TITLE_PHONE` / `PHONE`).

## Not fixed

- AFCA time limits and compensation caps (Q06), AUSTRAC cash threshold reporting and structuring (Q27), and Scamwatch/ACCC SPF commencement: the sources (afca.org.au, austrac.gov.au, scamwatch.gov.au, accc.gov.au) are blocked or time out, so they were skipped as instructed.
- Q27 safety gap is still open. A human should source the AUSTRAC $10,000 threshold and structuring offence before round 2.
- Record retention after closure (Q19), new-arrival ID and tax residency (Q28), alternative ID (Q43), free legal help (Q44), and existing-POA registration documents (Q31): not sourced this round. The trusted-person page does not list the documents needed to register an existing POA.
- The demo entry `hardship-options` in data/banking_kb.json ("the bank pauses collection activity") and other mock entries competing in retrieval were left for the user to decide. Out of scope: data/banking_kb.json was not edited.
- Deceased family liability ("family not liable unless joint or guarantor"): not stated on the Westpac page, so not added.
