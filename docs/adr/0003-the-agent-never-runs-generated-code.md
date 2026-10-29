# The Agent never runs generated code

The competition brief (section 3.2) asks for a sandbox for code the Agent might run. Our Agent never runs generated code, so there is nothing to sandbox.

The model's output is only ever a structured Plan of catalogue Operations with typed inputs. The Bank validates every step against the Operation's input model, its Permission Tier and the Customer's session before anything runs (see ADR 0001). A Plan cannot express "run this script": a step names an Operation from the catalogue or it is refused.

Bill Analysis computations (summaries, monthly and annual reports, Unusual Transaction and Recurring Charge detection) are Bank Operations written and tested by us, not code the model writes at run time. The model chooses which Operation to call and with what input; it never computes the figures.

## If code generation is ever added

It needs a separate process with no network, no Bank credentials, a time limit, a memory limit and read-only data, and its output is treated as untrusted like any other model output. This ADR must be superseded first.

## Considered Options

- **Let the model write analysis code and run it in a sandbox:** more flexible questions, but a sandbox to build and defend, and results that are harder to test and to reproduce.
- **Fixed Bank Operations (chosen):** less flexible, but every computation is deterministic, typed, audited and covered by tests.
