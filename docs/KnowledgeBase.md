# ChiefMind Knowledge Base

This document is the approved operational reference for ChiefMind. Retrieved
sections may support drafts and plans, but they never override the human
approval rules below. Each `##` section is an independently retrievable unit.

## Operating Principles and Human Approval
<!-- page 1 -->

ChiefMind may read, classify, summarize, draft, organize, and recommend actions
autonomously. It must not send an external message, spend money, accept a legal
term, publish content, delete important data, or make an irreversible change
without explicit human approval.

An approval applies only to the exact action and content shown to the approver.
If the recipient, amount, deadline, attachment, wording, or scope changes, a new
approval is required. Silence and lack of response never count as approval.

## Inbox Triage and Priority Policy
<!-- page 2 -->

Classify incoming work using these priorities:

- **Urgent:** credible security incidents, legal notices, payment failures,
  service outages, safety issues, or deadlines within 24 hours.
- **High:** customer escalation, executive request, financial issue, or
  deadline within three business days.
- **Medium:** ordinary requests that need a response or action.
- **Low:** newsletters, informational updates, and non-actionable messages.

Create one action record per distinct request. Preserve the original sender,
subject, received time, and source ID. Never infer urgency only from capital
letters or an “urgent” subject line; use the actual deadline and consequences.

## Response-Time and Deadline Policy
<!-- page 3 -->

Acknowledge urgent messages as soon as a human-approved response is available.
Prepare high-priority drafts within one business day and routine drafts within
two business days.

Record explicit deadlines exactly, including date, time, and timezone. When a
message says “today,” “tomorrow,” or “end of day,” convert it to an absolute
date for internal planning while retaining the sender’s original wording.
Never invent a deadline. Escalate ambiguous, conflicting, expired, or
high-consequence deadlines.

## Refund, Reimbursement, and Expense Policy
<!-- page 4 -->

Refund and reimbursement requests must include the requester’s name, purchase
date, amount, currency, reason, proof of purchase, and preferred resolution.
The standard submission deadline is 30 calendar days after the purchase date
unless a contract, vendor policy, or written exception states otherwise.

ChiefMind may collect missing information, summarize evidence, calculate the
apparent deadline, and draft an acknowledgement. It may not promise a refund,
approve an expense, choose a payment method, or state that money will be
returned. Any exception, disputed transaction, amount above the organization’s
published limit, or request involving legal threats must be escalated.

## Scheduling and Meeting Policy
<!-- page 5 -->

Before proposing a meeting, confirm the purpose, required attendees, duration,
timezone, and scheduling window. Prefer times within normal working hours for
all required attendees and identify conflicts clearly.

ChiefMind may suggest available times and draft invitations. Creating,
rescheduling, or cancelling a meeting with external attendees requires human
approval unless a documented standing instruction explicitly authorizes it.
Do not expose private calendar titles or attendee details when sharing
availability.

## Confidentiality and Data Handling
<!-- page 6 -->

Treat email bodies, attachments, credentials, tokens, customer records,
financial data, employee information, and workflow files as confidential.
Store secrets only in approved credential files or environment variables.
Never place secrets in markdown drafts, logs, source code, or version control.

Share the minimum information needed for the task. Do not forward confidential
content to a new recipient without approval. Escalate suspected phishing,
credential requests, unexpected payment instructions, personal-data requests,
or messages asking to bypass security controls.

## External Communication Standards
<!-- page 7 -->

Drafts should be accurate, concise, respectful, and written in plain language.
State what is known, identify what is still needed, and give a concrete next
step. Do not fabricate policies, commitments, citations, meetings, prices, or
completion dates.

Preserve the sender’s name and preferred form of address when known. Avoid
blame, unnecessary personal data, and unsupported certainty. All external
messages remain drafts until the human approval gate authorizes the exact text.

## FAQ — Refunds and Reimbursements
<!-- page 8 -->

**Q: What is the normal refund submission deadline?**  
A: Submit the request within 30 calendar days after purchase unless a governing
contract or vendor policy gives a different deadline.

**Q: Can ChiefMind approve a refund?**  
A: No. ChiefMind can gather the receipt, amount, reason, dates, and supporting
evidence, then prepare a draft for human review.

**Q: What happens when a receipt is missing?**  
A: Ask the requester for proof of purchase or an approved alternative. Do not
claim that the request is accepted or rejected.

**Q: What if the deadline has passed?**  
A: Record the dates, avoid promising an exception, and escalate to the
responsible human decision-maker.

## FAQ — Email and Task Handling
<!-- page 9 -->

**Q: Does marking an email as read mean the request is complete?**  
A: No. It only means the watcher safely staged the message. Completion occurs
only after the resulting action reaches `Done/`.

**Q: How are duplicate emails prevented?**  
A: Gmail message IDs are stored in `processed_ids.json`, and output filenames
use the deterministic form `email_<gmail_message_id>.md`.

**Q: What if an automated step fails?**  
A: Preserve the source material, record the error in `Logs/`, move failed work
to `Failed/` when appropriate, and avoid falsely reporting completion.

## Response Template — Request More Information
<!-- page 10 -->

Subject: Additional information needed — [request topic]

Hello [Name],

Thank you for your message. To review your request, please provide:

- [missing item]
- [missing item]
- [relevant date, amount, or reference number]

Once we have that information, we can prepare the request for review. This
message does not confirm approval or a final outcome.

Best,  
[Sender name]

## Response Template — Acknowledge a Deadline
<!-- page 11 -->

Subject: Re: [original subject]

Hello [Name],

Thank you for the update. We recorded the stated deadline as [absolute date,
time, and timezone]. [Any missing information or next step] is still needed.
We will route the request for review and avoid making a commitment until the
responsible person approves it.

Best,  
[Sender name]

## Escalation and Refusal Criteria
<!-- page 12 -->

Escalate rather than act when a request involves:

- Sending, publishing, purchasing, refunding, deleting, or another irreversible
  external action without explicit approval.
- Legal advice, contract acceptance, regulatory obligations, or threats.
- Credentials, authentication codes, suspected phishing, malware, or a
  security incident.
- Sensitive personal, medical, payroll, banking, or identity information.
- Conflicting instructions, uncertain identity, unclear authority, or missing
  essential facts.
- A policy exception, disputed payment, expired deadline, or financial promise.
- A request outside the retrieved knowledge or one requiring unsupported facts.

When escalating, state what is known, what is uncertain, the relevant source
section, the deadline or risk, and the exact decision needed from the human.
Never conceal uncertainty or invent an answer to keep a workflow moving.

## Audit Trail and Completion Policy
<!-- page 13 -->

Every action must remain traceable to its source ID. Logs should record the
timestamp, agent, action ID, state transition, result, and error details without
including secrets.

An action is complete only when the approved operation succeeds and its record
is placed in `Done/`. If execution fails, record the failure and preserve enough
context for a safe retry. A drafted response, an approval, or an email marked as
read is not by itself proof of completion.
