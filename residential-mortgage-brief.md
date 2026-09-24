# Brief: Residential Mortgage Origination Platform

We are a mid-sized lender. Today a broker emails us an application, an underwriter re-keys it into three systems, and customers ring us to ask where things stand. Files stall for weeks and nobody can say why. We want one platform that carries a mortgage from "I'd like to borrow" to funds released.

An **applicant** (or joint applicants) applies for a **mortgage** against a **property**, usually through a **broker**. Before a full application, we run an **affordability check** on stated income and outgoings. Many people stop here, and that is fine. It is a decision in principle, not a commitment.

If they proceed, we open an **application** and collect **documents**: payslips, bank statements, ID, proof of deposit. Each is checked for authenticity and recency, and bad ones are sent back. Self-employed applicants need extra documents, and joint applications need a full set per person. Nothing moves to underwriting until the required set is verified. Every applicant also goes through **identity, sanctions and anti-money-laundering screening**. A hit freezes the file until a compliance officer clears or escalates it. We are not allowed to tell the applicant why.

A complete file gets a **credit check** and a **property valuation**, ordered from an external panel surveyor. The valuation can come back lower than the purchase price, which changes the loan-to-value and may push the case outside policy. A downvaluation sends the applicant back to renegotiate, raise more deposit, or withdraw.

**Underwriting** decides against lending policy. Straightforward cases are auto-approved. Anything above a loan size, outside policy, or self-employed goes to a human underwriter. Exceptions to policy need a senior underwriter to sign off, and the person who recommended the exception cannot be the one who approves it.

The outcome is a decline (with reasons) or a **mortgage offer**, which expires after a set period. The applicant can accept, let it lapse, or ask for changes such as a different loan amount or term, which sends the case back through underwriting. Offers carry **conditions** (buildings insurance, a solicitor's report on title, a redemption statement for the old mortgage) that must all be cleared before funds are released.

Once conditions are met, the solicitor requests funds. Finance authorises the **disbursement**, and payment goes out by bank transfer on the completion date. Payments can fail or be recalled, and completion dates slip. After completion the case becomes a **live account** and is handed to servicing.

An applicant can withdraw at any point before completion. Withdrawal after a valuation has been paid for triggers a fee that needs a manager to waive. Applicants and brokers expect a straight answer at any time about where the case is and what is blocking it.

Systems we run: a CRM, a loan origination system, a document store with OCR, a credit bureau integration, a valuation panel portal, a screening and fraud service, a lending policy rules engine, a core banking and payments system, and a notification service for SMS and email.