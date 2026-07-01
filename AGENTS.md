# AGENTS.md

This file defines the repository-wide collaboration rules for ProspectAlpha.
Subdirectory `AGENTS.md` files may add stricter or more specific rules, but they
must not remove, override, or loosen any rule in this file.

## 1. Collaboration Principles

- Keep changes surgical. Touch only the module or files needed for the assigned task.
- Prefer clear module boundaries over shared shortcuts. Do not place feature logic in
  global helpers merely because it is convenient.
- State assumptions when a task is ambiguous. If a change crosses module boundaries,
  describe the affected modules before implementation.
- Do not commit secrets, credentials, private datasets, model keys, or generated reports
  containing non-public data.
- Do not rewrite published Git history. If a subdirectory has stricter Git rules, follow
  both the root rule and the subdirectory rule.

## 2. Rule Hierarchy

- Root `AGENTS.md` is the minimum standard for the whole repository.
- A subdirectory `AGENTS.md` is a supplement for that subtree only.
- Subdirectory rules can add constraints, naming conventions, tool commands, and local
  workflows.
- Subdirectory rules cannot override, delete, or weaken root rules.
- When two rules seem to conflict, follow the stricter interpretation and document the
  decision in the change summary.

## 3. Module Ownership

Each contributor should work inside the module they own unless the task explicitly
requires a shared interface change.

For every module-level feature, keep these items clear:

- Module responsibility: what the module owns and what it does not own.
- Inputs and outputs: request objects, files, records, events, or model artifacts.
- Public interfaces: routes, service methods, schemas, configuration keys, or CLI entrypoints.
- Dependencies: upstream modules consumed and downstream modules affected.
- Error boundary: what errors are raised, returned, logged, or retried.
- Tests: unit, integration, contract, or manual verification required for the change.
- TODOs: unfinished work must be explicit and scoped to the module.

## 4. Backend Module Rules

- The backend is organized by pipeline capability: ingestion, parsing, retrieval,
  features, agents, skills, prediction, backtesting, reporting, api, and common.
- Keep API route handlers thin. They should validate input, call application services,
  and return responses; they should not contain feature extraction, retrieval, agent,
  prediction, or reporting logic.
- Keep `common/` boring. It is for shared configuration, logging, exceptions, and stable
  types only. Do not hide feature-specific behavior there.
- Do not create fake business behavior to make a skeleton look complete. Prefer explicit
  TODO notes over stubbed outputs that could be mistaken for real functionality.
- Every risk conclusion implemented later must preserve evidence traceability back to
  source text, page, section, and screenshot metadata.

## 5. Frontend Rules

- Frontend changes should keep UI behavior and API contracts aligned with backend module boundaries.

## 6. Change Delivery

Every PR or handoff summary should include:

- Modules changed.
- Public interfaces changed, if any.
- Verification commands run.
- Known gaps or TODOs.
- Whether the change touches data handling, evidence traceability, model behavior, or
  generated reports.

If a task only creates a skeleton, do not claim functionality exists. Say exactly which
boundaries were created and which behavior remains unimplemented.
