# The Bank, not the Agent, enforces Permission Tiers

Permission Tiers, the Daily Transfer Total, Confirmation and Strong Verification are enforced inside the Bank's request handling, not in the Agent's prompt or planner. The Agent is treated as an untrusted caller: it can propose any Operation, but a Yellow or Red Operation only runs when the Bank holds a valid Confirmation or Strong Verification for that exact Operation. We chose this so a successful prompt injection or a hallucinated plan cannot move money or weaken the account. Judges score defence against induced transfers and privilege escalation at 30%. A side benefit is that Scenario owners can add Operations without touching any security code.

## Considered Options

- **Agent-side checks (prompt rules + planner guard):** faster to build, but a single jailbreak bypasses every tier.
- **Separate LLM safety classifier only:** probabilistic, so it can complement server-side enforcement but not replace it.
