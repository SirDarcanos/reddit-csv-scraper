# Domain docs

This repository uses a single-context domain-documentation layout.

## Before exploring

Read these resources when they exist:

- `CONTEXT.md` at the repository root
- ADRs under `docs/adr/` that affect the area being changed

Proceed silently when either resource is absent. Domain-modeling work creates them when terminology or architectural decisions need to be recorded.

## Layout

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary’s vocabulary

When output names a domain concept—including in issue titles, refactor proposals, hypotheses, and test names—use the term defined in `CONTEXT.md`.

If a required concept is missing, reconsider whether it reflects the project’s language or record the gap for domain modeling.

## Flag ADR conflicts

Explicitly identify output that contradicts an existing ADR rather than silently overriding it.
