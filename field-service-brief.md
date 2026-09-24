# Brief: Field Service Dispatch Platform

We maintain industrial equipment on customer sites — chillers, compressors,
pumps. Right now dispatch is two people with a whiteboard and it falls over
whenever someone is off sick.

A **customer** has **assets** installed at their sites, each under a **service
contract** with a response time we are contractually held to. When something
breaks the customer raises a **work order**, or our remote monitoring raises one
automatically when an asset reports a fault.

Work orders get triaged: what is wrong, how urgent, and does the contract cover
it. Uncovered work needs the customer to accept a quote before we send anyone,
and they can decline, in which case the order is closed unbilled.

Covered or accepted work is scheduled. A **technician** has to be assigned —
they are only assignable if they hold the right certification for that asset
class and are not already on a job. Technicians go available, assigned,
travelling, on site, then available again. Jobs get reassigned when someone
calls in sick, and a job that cannot be staffed before the contract response
time breaches has to be escalated to a duty manager, who can authorise
overtime or a third-party contractor.

On site the technician either fixes it or finds they need a **part**. If the
part is in the van they fit it. If not, a **parts order** is raised against our
warehouse; if the warehouse is out of stock it goes to the supplier, which
takes days. The job is suspended meanwhile and a revisit gets scheduled, which
means assigning a technician all over again.

Completed jobs need the customer to sign off on site. Unsigned jobs sit in a
disputed state until someone chases them. Signed jobs generate an **invoice**,
except where the contract covers the work entirely. Invoices can be queried by
the customer, which sends them back to be corrected and reissued.

Anything involving overtime authorisation, a third-party contractor, or a
credit note against an invoice needs a manager to approve it.

Systems we run: a CRM, an asset register, the scheduling system, a workforce
app the technicians use, a warehouse stock system, the supplier ordering portal,
our finance system, and a notification service.
