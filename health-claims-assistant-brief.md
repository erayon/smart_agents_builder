# Brief: Health Insurance Policy Assistant

We want an agentic framework behind a policy assistant our members talk to
directly. Today they phone a call centre, wait, and are told to post documents.
We want the assistant to take them from "something happened" through to money
in their account.

A **member** holds one or more **policies**. Before anything else the assistant
has to work out whether what they are describing is actually covered — the
policy has a plan, an excess, an annual cap and a list of exclusions. Members
often ask about cover without ever filing anything, and that is fine; a
**coverage enquiry** is its own thing and usually ends there.

If it is covered, the assistant opens a **claim**. The member describes what
happened and uploads **documents** — hospital bills, prescriptions, discharge
summaries. Documents are checked for readability and for being the right kind
of document; bad ones are rejected and the member is asked again. A claim
cannot move forward until the required documents are all verified.

A complete claim goes for **assessment**. Someone checks the treatment against
the policy terms and works out the covered amount after the excess. Assessment
can stall — if something is missing the claim is parked and the member is
chased. Anything over a value threshold, or flagged by the fraud check, must be
looked at by a human assessor before it goes anywhere.

The outcome is either a rejection with a stated policy clause, or a
**settlement offer**. The member can accept the offer or dispute it. Accepted
offers become a **payment** to their bank account, which can fail and need
retrying. Rejections and disputed offers can be taken to **appeal**, reviewed by
someone senior who did not make the original decision, and either upheld or
overturned. An overturned appeal puts the claim back into assessment.

Members can ask about status at any point and expect a straight answer about
where their claim is and what is holding it up.

Systems we run: a policy administration system, a claims database, a document
store with OCR, a fraud scoring service, a pricing and benefits engine, a
payments gateway, and a notification service for SMS and email.
