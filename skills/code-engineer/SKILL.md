---
name: code-engineer
description: Use for software engineering work: code implementation, bug fixes, refactors, code review, debugging, test writing, verification, API/interface design, and small product hardening tasks. Applies general engineering discipline plus language-specific guidance for Python, Java, and C++ when those files are involved.
---

# Code Engineer

Use this skill for coding tasks where the agent must read, reason about, change, review, or verify a real codebase. The goal is not to enforce a personal style; it is to produce small, correct, reviewable engineering work that fits the existing project.

## Operating Principles

- Understand before editing. Read the relevant entry points, call chain, tests, config, and local patterns before proposing a change.
- Keep changes scoped. Modify only what the user request and surrounding code require.
- Prefer existing project conventions over generic best practices when they conflict.
- Do not invent abstractions for one-off use. Add structure only when it removes real complexity or matches an established pattern.
- Preserve user work. Do not revert, overwrite, delete, or reformat unrelated changes.
- Treat verification as part of the task, not a follow-up. Run focused tests or checks when feasible and report exact results.
- For bugs, find the root cause before fixing. Do not patch symptoms without explaining why the failure happens.
- For reviews, lead with concrete findings, severity, file/line references, and behavioral risk. Keep summary secondary.

## Tool Boundaries

- Use code search or symbol lookup first when the project provides it. Locate the narrow files and functions before broad reading.
- Use read-only file access for inspection. Avoid loading large unrelated files into context.
- Use edit tools only after the target file, reason, and expected behavior are clear.
- Prefer small patches over whole-file rewrites.
- Run tests, lint, type checks, builds, or smoke commands only when they are relevant to the change.
- Use git status/diff to audit your own changes. Commit only when the user explicitly asks.
- Do not modify `.env`, `.git`, credential files, generated caches, or unrelated workspace artifacts unless the user explicitly requests it and the safety impact is understood.

## Workflow

### 1. Diagnose

Identify:

- what the user wants changed or understood
- which module owns the behavior
- what existing code path currently does
- what could break if the diagnosis is wrong
- what evidence will prove the result

For unclear or risky work, state assumptions briefly. Ask only when local context cannot resolve the ambiguity.

### 2. Plan the Smallest Useful Change

Choose the lowest-risk path:

- S0: read and explain only
- S1/S2: small label, config, or single-function fix
- S3: small feature through an existing extension point
- S4+: subsystem changes, packaging, security, terminal control, or public API changes

For S3 or higher, define files to touch, expected behavior, verification, and rollback/containment.

### 3. Implement

- Match local style, imports, naming, error handling, and test style.
- Keep public behavior stable unless the request explicitly changes it.
- Keep comments sparse and useful. Explain non-obvious decisions, not obvious assignments.
- Remove only orphaned code created by your change.
- If language-specific code is involved, read the relevant reference:
  - Python: [references/python.md](references/python.md)
  - Java: [references/java.md](references/java.md)
  - C++: [references/cpp.md](references/cpp.md)

### 4. Verify

Use evidence appropriate to risk:

- bug fix: reproduce the old failure or add a focused test when feasible
- feature: test the new behavior and at least one important edge case
- refactor: prove old behavior still works
- packaging/security/install path: run dry-run checks and negative checks
- UI/terminal behavior: use a realistic interactive or smoke test when possible

Do not claim success without fresh verification evidence. If a check cannot run, state why and what risk remains.

### 5. Report

Report:

- what changed
- how it was verified
- what remains risky or intentionally untouched
- how the user can see the effect, when relevant

For code review, use this order: findings, open questions/assumptions, brief summary.

## Task-Specific Guidance

### Implementation and Refactor

- Start from call sites and tests, then edit the owning module.
- Preserve module boundaries.
- Avoid hidden behavior changes in helper functions used broadly.
- Keep compatibility aliases when replacing public identifiers unless the user requests a breaking cleanup.

### Debugging

- Read the complete error message or failed output.
- Reproduce consistently if possible.
- Compare recent changes and affected paths.
- Fix the root cause, then verify the original symptom.

### Testing

- Prefer focused tests near the changed behavior.
- Add broad regression tests only when the blast radius is broad.
- Avoid polluting large catch-all test files when a dedicated test file is clearer.

### Code Review

- Prioritize correctness, security, regressions, missing tests, and user-visible behavior.
- Include precise file and line references.
- Do not list style nits unless they materially affect maintainability or consistency.

## Final Checklist

- [ ] The change directly maps to the user's request.
- [ ] Existing project patterns were followed.
- [ ] Unrelated dirty work was preserved.
- [ ] The smallest practical surface was touched.
- [ ] Relevant tests/checks/smokes were run or the gap was explicitly reported.
- [ ] Final response states changes, evidence, and residual risk.
