# 2026 FinTechathon — Domestic AI Track: Banking AI Agent

Source: https://fintechathon.g-ican.com/#/en. The English and Chinese text was extracted from the site's JS bundles on 2026-10-15. Where the Chinese version (中文) is more specific than the English one, the extra detail is marked **[ZH]**.

Organisers: Shenzhen Municipal Local Financial Administration, Shenzhen University and WeBank (strategic guidance); hosted by the Shenzhen University–WeBank Institute of FinTech.

## Track summary

The Domestic Track focuses on **"AI operating banks on behalf of users" (safety + experience)**. Teams must build an AI Banking Agent that:

- understands financial intent through natural-language dialogue
- autonomously plans and executes banking operations
- connects with users through channels such as **an app or IM**
- covers **at least 3 core scenarios**; full coverage is encouraged

> Design and build a financial AI Agent that can understand intent, plan tasks and safely execute banking operations while maintaining user control and transparent risk safeguards.

## 1. Challenge background

Mobile banking journeys are often long and hidden behind multiple entry points. **[ZH]** Completing a single transfer takes 3+ steps on average. With multimodal models and Agent technology becoming practical, "just say it and get it done" is moving from concept to reality.

- Teams may reference open-source agent projects such as **OpenClaw**.
- Industry benchmark: **Revolut AIR** (Revolut's AI banking assistant). Teams are encouraged to go further on scenario depth and smoothness of interaction.

## 2. Scenarios (cover ≥ 3; the table is a reference, not a limit)

| # | Scenario | Example capabilities |
|---|----------|----------------------|
| 1 | Smart transfers | Transfer by name / phone number / note; scheduled transfers; split-bill (AA) collection |
| 2 | Bill analysis | Spending categories; unusual-transaction detection; monthly / annual reports |
| 3 | Wealth operations | Product recommendation & comparison; risk assessment; one-click subscribe / redeem |
| 4 | Card management | Card application; limit adjustment; transaction restriction / unlock; card loss reporting |
| 5 | Subscriptions / direct debit | Detect recurring charges; renewal reminders; one-click cancellation |
| 6 | Cross-scenario orchestration | Detect "my spouse's birthday", reserve RMB 1,000 of current-account funds that month, order flowers and a cake 2 days before the birthday |

## 3. Core technical requirements

### 3.1 Permission tiers (priority: HIGH)

Principle: **low risk → automatic; high risk → user confirmation; very high risk → strong verification.**

| Tier | Policy | Example operations |
|------|--------|--------------------|
| 🟢 Green | Automatic | Pure queries: balance, transaction history, bill analysis, investment-return queries. **[ZH]** Run directly once the intent is confirmed; no second confirmation. |
| 🟡 Yellow | User confirmation | Small transfers (**daily cumulative ≤ RMB 1,000**), subscription query / cancellation, virtual-card application. **[ZH]** Show the operation details before running it and get explicit confirmation. |
| 🔴 Red | Strong verification (MFA) | Large transfers (**daily cumulative > RMB 1,000**), password change, card loss report / un-report, wealth-product subscription. **[ZH]** Requires MFA: face recognition, SMS code or a U-shield (USB key). |

Note that the limit is a **daily cumulative** amount, not a per-transaction one.

### 3.2 Security mechanisms (priority: HIGH)

- **Hallucination guard:** never fabricate transaction results or account information.
- **Injection defence:** prevent special prompts from bypassing permission controls.
- **Audit:** log the complete decision chain and execution log for every key operation.
- **Circuit breaker:** repeated failures or suspicious behaviour trigger a safety lock.
- **Sandbox:** generated code runs in a restricted, monitored, logically isolated environment.

### 3.3 Intent understanding & task planning

- Handle vague expressions, omitted context and implicit intent.
- Decompose complex tasks into an ordered **sub-task DAG**.
- Support **interruption, rollback and human takeover** during execution.

## 4. Judging criteria

| Weight | Criterion | What is assessed |
|--------|-----------|------------------|
| **40%** | Task Completion | Completion of scripted financial tasks, quality of multi-step planning, correctness of tool calls |
| **30%** | Security & Compliance | Defence against **induced transfers, information extraction, privilege escalation, prompt injection**; confirmation and risk warnings for sensitive operations |
| **30%** | Innovation & Interaction | Scenario novelty, real-world value, dialogue efficiency (number of turns), clarifying questions, error recovery, colloquial input, out-of-scope fallback |

"Scripted financial tasks" suggests judges will run a fixed set of test tasks against the agent.

## 5. Timeline (China Standard Time, UTC+8)

| Date | Event |
|------|-------|
| 31 Aug – 1 Nov | Registration (team leader submits all member info on the official site) |
| 15 Sep / 17 Sep | Online livestreams — Technical Open Class [Session 1](https://www.bilibili.com/video/BV1Z1hk68Esc/), [Session 2](https://www.bilibili.com/video/BV1Eyhk6wEUk/) |
| **28 Oct** | Online Q&A |
| **2 Nov** | **Submission deadline** |
| 3 – 8 Nov | Online review; judges select the top 10 |
| 10 Nov | 10 finalists announced |
| 21 – 22 Nov | On-site AI Hackathon + project defence at WeBank, Shenzhen |
| 23 Nov | Awards ceremony |

## 6. Submission package (by 2 Nov)

| Item | Status | Template |
|------|--------|----------|
| Project brief: name + ≤ 200 words (concept and AI technologies used), via the registration system | Required | — |
| Technical documentation: architecture diagram, core algorithms, security design | Required | `templates/ai-technical-document.docx` |
| Presentation deck: talk of ≤ 10 min | Required | `templates/presentation-template.pptx` |
| Source code: README, GitHub repo, deployment guide, test cases | Required | `templates/source-code-readme-template-en.md` |
| Security self-assessment: permission-tier implementation + known-risk list | Required on the site (the template file itself says "optional") | `templates/ai-security-self-assessment.docx` |
| Demo video: ≤ 5 min, 16:9, 1080p preferred | Optional for the domestic track | `templates/demo-video-guide-en.docx` |
| Team information form | Required at registration | `templates/team-information-form.xlsx` |

What the templates ask for:

- **Technical doc:** (01) overview: background, target users, scenarios, problem and innovations; (02) architecture: diagram, module responsibilities, models / tools / data sources / external services; (03) core algorithm & agent design: intent understanding, task planning, tool calls, state management, key algorithms / prompts / training & evaluation; (04) security: tiers and confirmation, prompt-injection / privilege-escalation / privacy / error-recovery protection; (05) deployment and testing.
- **Security self-assessment:** (01) tiers: users, operations, permission boundaries; confirm / verify / undo for sensitive ops; (02) risk tests: induced transfers, info extraction, privilege escalation, prompt injection; abnormal input, tool failure, error recovery; (03) data & privacy: sources, authorisation, masking, storage, deletion; (04) known risks: description, impact, current mitigation, next steps.
- **README:** overview, architecture (with diagram), environment & dependencies (no real secrets), install & run from a clean environment, testing, repo / demo / deployment, security notes.

## 7. Eligibility & rules

- Full-time students (bachelor's, master's, PhD) at universities in mainland China, Hong Kong, Macao or Taiwan, including international, exchange and visiting students at Chinese universities. Students enrolled overseas must enter the International Track instead.
- Teams only, **2–5 members**; cross-year, cross-major and cross-university teams are allowed. Each person may join one team only.
- Work must be done independently; no cheating, fraud or IP infringement. Participants keep the IP, and the organiser may use the work for publicity.
- No fee. Finalists' accommodation and travel are arranged by the organisers.
- Contact: fintechathon@g-ican.com

## 8. Prizes (per track, pre-tax)

First Prize RMB 100,000 · Second Prize RMB 80,000 · Third Prize RMB 50,000 · Best Creative Award RMB 20,000. Each winning team also gets a certificate and trophy.
