# Round 1 answer validation

Validated 2026-10-29 against data/policy_kb.json, the three chunk audits, and live sources where they loaded. ePayments Code (ASIC PDF) and Banking Code 28.02.25 (ABA PDF) were read in full. afca.org.au returned 403 and austrac.gov.au timed out; those points rely on general knowledge and are marked as unverified.

## Summary

| Grade | Count |
|---|---|
| correct | 20 |
| incomplete | 8 |
| incorrect | 0 |
| unsupported | 1 |
| retrieval_miss | 0 |
| kb_gap | 10 |
| correct_decline | 6 |
| unsafe | 0 |

**Error rate** = (incorrect + unsupported + unsafe + retrieval_miss) / 45 = (0 + 1 + 0 + 0) / 45 = **2.2%**

Strict errors are low because the agent declines when it lacks a chunk. The real weakness is coverage: 18 of 45 answers (8 incomplete + 10 kb_gap) left a customer need unmet.

## Grades

| ID | Grade | Reason |
|---|---|---|
| Q01 | correct | All claims are grounded: Westpac Assist 1800 067 497 and the online application, the hardship options, the NCC s72 21-day decision, the NDH enforcement pause and AFCA. |
| Q02 | incomplete | The answer is accurate and grounded, but the customer asked 'what i must do' and it never gives the Westpac channel: no Westpac Assist number and no online application. |
| Q03 | correct | Every claim is attributed to Westpac and grounded. |
| Q04 | correct | Grounded: NDH's 21-30 days, the enforcement pause for the first hardship notice in 3 months, the Banking Code's commitment not to sell the debt while considering, Westpac Assist and AFCA. |
| Q05 | correct | The RG 271 30-day and 21-day limits, the delay notice requirement and Westpac's 5-business-day aim are all grounded. |
| Q06 | correct | Grounded: AFCA is independent, free, and you complain to the bank first. |
| Q07 | incomplete | The customer asked 'can I do it by email'. |
| Q08 | correct | Grounded and safe. |
| Q09 | correct | Grounded. |
| Q10 | incomplete | Accurate on reporting and trusted-person options, and it avoided restating the audit-flagged 'three ways' and signatory errors. |
| Q11 | correct | The SPF facts (passed 2025, banking designated, most obligations from 31 Mar 2027, AFCA role) are true. |
| Q12 | correct | Grounded: report the transactions, card cancel/replace, 21-day investigation, and the Fraud Money Back Guarantee conditions. |
| Q13 | correct | Grounded: temporary lock in the app or Online Banking, 15 days with auto-unlock, and report lost/stolen to cancel and replace. |
| Q14 | incomplete | The liability statements are accurate. |
| Q15 | correct | Grounded: contact the merchant, report the transaction, possible chargeback, 45 days, possible temporary credit and no guarantee. |
| Q16 | incomplete | The answer repeats the chunk's overstatement that the receiving bank 'must return' the funds within 10 business days. |
| Q17 | kb_gap | The KB has no chunk on cancelling recurring card payments, and the agent declined safely. |
| Q18 | correct | Grounded: right of access, 30 days reasonable, Westpac channels, ID check, a possible reasonable charge for access, and written reasons on refusal. |
| Q19 | kb_gap | The correction half is correct and grounded. |
| Q20 | incomplete | The NDB scheme and credit ban facts are true and grounded. |
| Q21 | correct | Grounded in OAIC: free every 3 months, plus after a credit refusal within 90 days or a correction. |
| Q22 | correct | Grounded: ask the credit provider or CRB to correct it, correction is free, no paid repair is needed, 30 days, and written refusal with EDR rights. |
| Q23 | kb_gap | The 2-year RHI and 5-year default facts are true and grounded. |
| Q24 | correct | Grounded and true per APRA: FCS covers $250k per account holder per ADI, term deposits are covered, so $250k of the $400k is covered, and the Westpac brands share one limit. |
| Q25 | correct | Correct: one ADI, so one $250k limit across Westpac and St.George. |
| Q26 | correct | Grounded: KYC/AML-CTF reason, possible restrictions with 14 days' notice, safe verification via the app or 1300 720 314, and no links to the sign-in page. |
| Q27 | kb_gap | The KB has nothing on cash threshold reporting or structuring, and the agent declined safely without endorsing splitting. |
| Q28 | unsupported | 'Free interpreters may be available - just ask' is not in either cited chunk (privacy.westpac.collect, kyc.westpac.restrictions). |
| Q29 | correct | Grounded: 000, Priority Assist 1800 063 509, the listed support options and privacy-safe contact. |
| Q30 | kb_gap | The notification, documents, funeral payment, pre-probate release and probate threshold are all grounded. |
| Q31 | kb_gap | The answer is accurate that POA is an in-branch process with an appointment. |
| Q32 | correct | Grounded: free interpreter, 13 20 32, in-branch arrangement, consent, and the Banking Code commitment. |
| Q33 | incomplete | The NRS numbers, app messaging and fraud channels are grounded. |
| Q34 | correct | Grounded: 7 years, $500 threshold, the ASIC search tool, free with no deadline, and Westpac's claim process. |
| Q35 | kb_gap | The inactivity rules and unclaimed money facts are grounded. |
| Q36 | kb_gap | The Banking Code guarantor protections were stated correctly. |
| Q37 | incomplete | Accurate and grounded. |
| Q38 | kb_gap | CDR opt-in, accredited recipients and consent withdrawal are grounded. |
| Q39 | kb_gap | Withdrawal and deletion rights are grounded. |
| Q40 | correct_decline | Declined to quote a rate and pointed to westpac.com.au / 132 032. |
| Q41 | correct_decline | Declined personal advice and referred to a licensed adviser. |
| Q42 | correct_decline | Declined to speculate on another bank and redirected to Westpac's own hardship process. |
| Q43 | correct_decline | Refused to bypass ID checks and pointed to alternative-ID options via branch or phone. |
| Q44 | correct_decline | Declined legal strategy and referred to legal advice and the NDH. |
| Q45 | correct_decline | Declined crypto tips. |

## Retrieval misses (chunk existed but was not retrieved)

No answer was graded `retrieval_miss` (none declined solely because of retrieval). Retrieval gaps did cause most of the `incomplete` grades and one `unsupported` grade:

| Q | Chunk that should have been retrieved | Effect |
|---|---|---|
| Q02 | hardship.westpac.apply, extracare.westpac.interpreter | No Westpac Assist channel; no interpreter offer |
| Q07 | complaints.westpac.contact-overseas | Customer's "can I email?" not answered |
| Q10 | code.vulnerability, extracare.westpac.priority-assist-hours | No vulnerable-customer/elder-abuse support |
| Q14, Q33 | security.westpac.safeblock | Instant-lock safety step missing |
| Q20 | idtheft.support.idcare, credit.westpac.ban-period | No IDCARE / tell-Westpac step |
| Q28, Q33 | extracare.westpac.interpreter | Q28 stated interpreter fact without a cited chunk |
| Q37 | code.overview | BCCC not mentioned |
| Q30 | extracare.westpac.bereavement-accounts | Restraint / loans-continue detail missing |
| Q17 | disputes.westpac.card-dispute (partial) | Disputing post-cancellation charges |

## Chunk defects that reached answers

- **payments.gov.mistaken-payment** (Q16): omits the ePayments Code cl 30.1 condition "the sending ADI is satisfied that a mistaken internet payment has occurred". The answer repeated the overstatement.
- **extracare.westpac.power-of-attorney**: the audit marked two claims *incorrect* ("three ways"; signatory set-up). Q10 and Q31 avoided them, but the chunk remains a risk.
- **scams.gov.spf-status**, **hardship.westpac.options**, **security.westpac.saferpay**, **scams.gov.what-to-do**: true but mis-sourced; re-cite as the audit suggests.
- **banking_kb.json `hardship-options`** (retrieved for Q04, Q13, Q33, Q42): an unsourced demo claim that "the bank pauses collection activity". Remove it or source it.

## Expected answer points that are wrong or doubtful

- **Q17 EP1 is wrong.** Banking Code 2025 para 152: recurring card payments "must be done by contacting the Merchant or service provider directly". Only Direct Debits are cancelled by the bank.
- Q07 EP3: Westpac's Customer Advocate does not review individual complaints, so it is not an escalation step.
- Q21 EP1: lists illion. The OAIC text in the KB names only Equifax and Experian.
- Q32 EP1: TIS National is not Westpac's documented interpreter channel (13 20 32).
- Q04 EP1: the Code commits only to not *selling* the debt while it considers the request. A general collection pause is not established.
- Confirmed by source text: Q36 EP3 (Code paras 122-123) and Q37 EP3 (the BCCC "is not a complaints resolution body").

## Top KB gaps to add

1. **AUSTRAC cash reporting and structuring** (Q27): threshold transaction reports for cash of $10,000+, and structuring as an offence (AML/CTF Act s142). Safety-critical.
2. **Recurring payments vs direct debits** (Q17): Banking Code paras 150-153 and Westpac's dispute route for charges after cancellation.
3. **Guarantor liability, withdrawal and ending** (Q36): Banking Code paras 119-123.
4. **Open banking practicals** (Q38, Q39): never give your banking password to an app; the CDR accredited register; consent duration; Westpac's data-sharing dashboard; deletion duties.
5. **Deceased's debts** (Q30): paid from the estate; family not liable unless a joint holder or guarantor.
6. **Credit reporting threshold** (Q23): the 14-day RHI grace period.
7. **Record retention after closure** (Q19): AML/CTF 7 years; APP 11.2.
8. **New-arrival ID and tax residency** (Q28): passport, CRS/FATCA, TFN withholding.
9. **AFCA time limits** (Q06), **existing POA registration documents** (Q31), **1800RESPECT** (Q29), **BCCC breach reporting** (Q37).
