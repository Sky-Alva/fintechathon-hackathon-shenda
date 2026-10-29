# Banking AI Agent

An AI agent that operates a bank on a customer's behalf through natural-language conversation, built for the 2026 FinTechathon Domestic AI Track. The agent plans and requests banking operations; a separate simulated bank decides whether they are allowed and carries them out.

## Language

### Parties and systems

**Customer**:
The person whose money the agent manages and who talks to the agent.
_Avoid_: User (ambiguous with team members / judges), client, account holder

**Agent**:
The AI system that interprets what the Customer says, plans tasks, and requests Operations from the Bank on the Customer's behalf.
_Avoid_: Bot, assistant, chatbot

**Bank**:
The simulated core-banking system that owns all money, accounts, cards and products, and is the sole authority on whether an Operation may run.
_Avoid_: Mock, backend, API

**Merchant**:
A simulated business inside the Bank (e.g. a flower shop, a cake shop) that accepts orders and payments from the Customer.
_Avoid_: Vendor, shop, third-party service

**Staff Member**:
A bank employee who can watch sessions, take over a conversation from the Agent, and set or lift a Safety Lock.
_Avoid_: Admin, operator, human agent

**Channel**:
A surface through which the Customer converses with the Agent (e.g. the web app, an IM bot).
_Avoid_: Frontend, client, UI

### Operations and safety

**Operation**:
A single banking action the Agent can request from the Bank, such as a query, a transfer or a card lock.
_Avoid_: Tool, action, function, API call

**Plan**:
The ordered set of Operations (a dependency graph) the Agent proposes to fulfil one Customer request, shown to the Customer before any Yellow or Red Operation in it runs.
_Avoid_: Workflow, task chain, pipeline

**Permission Tier**:
The risk class of an Operation, which determines the approval it needs: Green (runs automatically), Yellow (needs Customer Confirmation), Red (needs Strong Verification). Any Operation that makes the Customer's account less safe is Red.
_Avoid_: Risk level, security level

**Confirmation**:
The Customer's explicit approval of a specific Operation, with its full details shown, before a Yellow-tier Operation runs.
_Avoid_: Approval, consent

**Strong Verification**:
A multi-factor check (such as an SMS code or face check) the Customer must pass before a Red-tier Operation runs.
_Avoid_: MFA, 2FA, authentication

**Safety Lock**:
A restriction on a Customer, triggered by repeated failed verifications or suspicious behaviour in any of their sessions, or set by hand by a Staff Member, under which only Green Operations may run in every session of theirs (including ones opened after it was set) until a Staff Member lifts it or it expires.
_Avoid_: Circuit breaker, freeze, lockout
_Note_: the Bank API keeps the names `session_locked` and `SessionLocked` for compatibility, although the lock holds the Customer, not one session.

**Daily Transfer Total**:
The sum of the Customer's outgoing transfers on the current calendar day; it decides whether a transfer is Yellow (total ≤ RMB 1,000) or Red (total > RMB 1,000).
_Avoid_: Daily limit, quota

### Transfers

**Payee**:
A person the Customer can send money to, identified by name, phone number, nickname (e.g. "老妈", Mum) or a past transfer note.
_Avoid_: Recipient, beneficiary, contact

**Scheduled Transfer**:
A transfer the Customer authorises now to run at a later date; it counts toward the Daily Transfer Total of the day it runs, not the day it was scheduled.
_Avoid_: Future transfer, standing order

**Split-bill Request**:
A request asking several Payees to each pay the Customer a share of a shared expense; no money leaves the Customer's account.
_Avoid_: AA payment, collection, group pay

**Reserved Funds**:
Money the Customer sets aside in their current account for a stated purpose; it remains theirs but cannot be spent on anything else until used for that purpose or released.
_Avoid_: Savings pot, earmark, frozen funds, hold

### Cards

**Card Lock**:
A Customer-chosen restriction that blocks some or all transactions on a card (e.g. overseas or online) and can be lifted by the Customer.
_Avoid_: Freeze, block

**Loss Report**:
The Customer's declaration that a card is lost, which disables it until the report is withdrawn.
_Avoid_: Card cancellation, freeze

**Card Limit**:
The maximum amount a card may spend in a period; raising it is Red, lowering it is Yellow.
_Avoid_: Quota, cap

### Spending

**Recurring Charge**:
A charge the Bank detects as repeating from the same merchant at a regular interval in the transaction history.
_Avoid_: Auto-debit, direct debit

**Subscription**:
A Recurring Charge the Customer has (or the Bank has) confirmed as an ongoing paid service that can be cancelled.
_Avoid_: Membership, plan

**Unusual Transaction**:
A transaction the Bank's rules flag as out of pattern for this Customer (amount, merchant, time or location).
_Avoid_: Fraud, anomaly, suspicious transaction

### Scope

**Scenario**:
One of the competition's six banking use-case areas (Smart Transfers, Bill Analysis, Wealth Operations, Card Management, Subscriptions, Cross-scenario Orchestration).
_Avoid_: Feature, module, use case
