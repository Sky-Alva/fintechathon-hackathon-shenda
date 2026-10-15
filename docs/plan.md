# Project Plan — Banking AI Agent (FinTechathon 2026, Domestic AI Track)

Agreed 2026-10-15. Requirements: [competition/banking-ai-agent.md](competition/banking-ai-agent.md). Vocabulary: [../CONTEXT.md](../CONTEXT.md). Decisions: [adr/](adr/).

**Submission deadline: 2 Nov 2026** (internal target: evening of 1 Nov).

## 1. Architecture at a glance

```
Customer ──► Web Channel (React, mobile-styled chat)
                 │   plan cards · Confirmation · Strong Verification · charts · decision trace
                 ▼
             Agent (Python)  ── LLM: qwen-plus (DashScope), DeepSeek as backup, OpenAI-compatible
                 │   intent → Plan (DAG) → execute → answer only from Bank results
                 ▼
             Bank (FastAPI + DB)   ◄── Staff console (sessions, audit trail, take over, unlock)
                 policy gate: Permission Tiers, Daily Transfer Total, Confirmation /
                 Strong Verification tokens, Safety Lock, audit log, Bank clock
```

- The **Bank enforces every tier**, and the Agent is an untrusted caller ([ADR-0001](adr/0001-bank-enforces-permission-tiers.md)).
- The Agent may only state account facts that came back from the Bank (this is the hallucination guard).
- Chinese first, English supported.
- Delivery is **Docker Compose only**: judges run it themselves, so the README must be very easy to follow.
- No IM Channel unless there's spare time.

## 2. Scenarios in scope

| # | Scenario | Owner |
|---|----------|-------|
| 1 | Smart Transfers (+ Reserved Funds, Merchants) | A |
| 2 | Bill Analysis | C |
| 3 | ~~Wealth Operations~~ | out of scope |
| 4 | Card Management | B |
| 5 | Subscriptions | C |
| 6 | Cross-scenario Orchestration: two demos | Lead |

The two #6 demos:
1. **Birthday:** the spouse's birthday comes from the other Customer's profile, supported by last year's gift purchase. The Agent reserves ¥1,000 (Reserved Funds), then orders flowers and a cake from Merchants 2 days before the birthday, using the Bank clock.
2. **Cancel unused Subscriptions:** Bank rules flag candidates with a stated reason (duplicate services in a category, price increase, trial converted to paid). The Customer picks which to cancel; cancellations are Yellow and confirmed in one plan card.

## 3. Permission Tiers

**Principle:** anything that makes the account less safe is Red.

| Tier | Operations |
|------|------------|
| 🟢 Green | All queries, bill reports, Unusual Transaction flags, renewal reminders |
| 🟡 Yellow | Transfers while the Daily Transfer Total stays ≤ ¥1,000; Split-bill Request; cancel Subscription; apply for virtual card; lower Card Limit; Card Lock; reserve / release Reserved Funds |
| 🔴 Red | Transfers taking the Daily Transfer Total above ¥1,000; raise Card Limit; lift Card Lock; Loss Report / withdraw Loss Report; password change |

Rules:
- A **Scheduled Transfer** gets its tier when it's scheduled, counts toward the total of the day it runs, and is re-checked when it runs.
- Paying a Merchant from Reserved Funds is tiered like a transfer of that amount.

## 4. Agent behaviour

- **Always plan:** every request becomes a Plan (a DAG, even if it has one step). The Agent re-plans when a step fails or the Customer interrupts.
- **Plan card:**
  - All Yellow Operations are listed with their exact details and confirmed with one tap.
  - The Bank issues one token per Operation, tied to those exact details. Any change to the Plan invalidates the tokens.
  - Red Operations always get their own Strong Verification (simulated SMS code).
- **Tiers before confirming:** before showing the plan card, the Agent asks the Bank for the tiers in planned order, so crossing ¥1,000 shows up front.
- **Rollback:**
  - Each Operation declares whether it can be reversed, and by which Operation.
  - On failure, the Agent *offers* to reverse completed steps in reverse order; it never does so automatically.
  - Irreversible Operations (transfers) are placed last where the dependencies allow.
- **Human takeover:**
  - The Customer can "stop" (pause, edit or cancel the Plan).
  - A Staff Member can take over the chat or lift a Safety Lock from the staff console.
- **Safety Lock:**
  - Triggers: 3 failed Strong Verifications, 2 detected injection/impersonation attempts, or more than 5 Red Operations in 10 minutes.
  - Effect: only Green Operations can run.
  - Lifted by a Staff Member, or expires after 30 Bank-clock minutes.
- **Two Customers** (a married couple, each the other's Payee). Simple login, one Customer per session; requests to act on the other's account are refused.
- **Decision trace panel** in the chat (toggle, off by default): intent, Plan DAG, tiers, Bank responses.
- **Out-of-scope requests** (including wealth products): a polite refusal plus what the Agent *can* do.

## 5. Team responsibilities

The key rule: **Scenario owners build what the Bank can do; the Lead builds how the Agent decides and how the Bank says no.** Owners never write tier checks, Confirmation flows or prompts.

Each Operation a Scenario owner delivers has to include:
1. A Bank endpoint with typed request and response models, its Permission Tier (fixed, or a rule function) and whether it can be reversed.
2. Seed data.
3. A description in Chinese and English for the Agent to read.
4. 10–15 scripted test tasks (Chinese prompts + expected result), **at least 3 of them attacks**.

| Person | Builds | Seed data / tests | Docs |
|--------|--------|-------------------|------|
| **Lead (you)** | Agent loop, policy gate, Confirmation / Strong Verification, audit log, Safety Lock, Bank clock, web Channel, staff console, decision trace, evaluation runner, #6 demos; reviews and merges all PRs | Reference slice; #6 tests | Technical document §02–04; security self-assessment §01 |
| **A** | Smart Transfers: find a Payee (name / phone / nickname / note), transfer, Scheduled Transfer, Split-bill Request, Reserved Funds, Merchants | Two payees named 张伟, nickname "老妈", new Payee, balance near ¥1,000; attacks: "safe account" scam, fake urgency, instructions hidden in a Payee note | Security self-assessment §02 (transfers) |
| **B** | Card Management: list cards, virtual card, Card Limit up/down, Card Lock/unlock, Loss Report/withdraw | Two debit cards + one credit card, one already locked; attacks: "I'm the account manager", "unlock without verification" | **Presenter**: owns the deck (from 28 Oct) and the demo video |
| **C** | Bill Analysis + Subscriptions: transactions, categories, monthly/annual report, Unusual Transaction rules, Recurring Charge detection, renewal reminders, cancel Subscription, rules that flag unneeded Subscriptions | ~6 months of realistic transactions with 2–3 planted anomalies, subscriptions (incl. two music services), last year's birthday gift purchase | README (one command, prompts to try, logins, screenshots, troubleshooting), deployment and testing docs |

Detection logic (Unusual Transactions, Recurring Charges, unneeded Subscriptions) is **plain rules inside the Bank**, never the LLM.

## 6. Timeline

| Date | Milestone |
|------|-----------|
| 15–19 Oct | Lead builds the reference slice (balance query, Green + ¥200 transfer to Mum, Yellow, end to end). A/B/C write seed data and scripted test tasks. |
| **19 Oct** | Operation definition frozen; teammates start endpoints |
| 26 Oct | All Scenarios merged; Lead integrates #6 |
| 28 Oct | Official online Q&A: ask how scripted tasks are judged. B starts the deck. |
| 30 Oct | Feature freeze; full evaluation run; bug fixes only |
| 31 Oct – 1 Nov | Docs, deck, video; README tested on a clean machine |
| **2 Nov** | Submit |

## 7. Tooling & workflow

- LLM: `qwen-plus` on one shared team DashScope key in a git-ignored `.env`; DeepSeek configured as backup.
- Stack: Python + FastAPI (Bank, Agent), React (web Channel, staff console), Docker Compose.
- GitHub repo `Sky-Alva/fintechathon-hackathon-shenda`:
  - one issue per deliverable, using the default triage labels;
  - one short-lived branch and pull request per Scenario;
  - Lead reviews and merges.

## 8. What gets cut if we fall behind

**Cut, in this order:**
1. IM Channel
2. Birthday demo
3. Staff console extras (keep unlock + audit view)
4. Annual reports
5. Split-bill Requests

**Never cut:**
- Bank-side tier enforcement
- Confirmation / Strong Verification
- Scripted test tasks with attacks
- Decision trace panel
- The cancel-unused-Subscriptions demo
