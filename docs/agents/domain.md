# Domain Docs

MediPet uses a single-context domain-documentation layout.

## Before exploring

Read these files when they exist:

- `CONTEXT.md` at the repository root
- Relevant ADRs under `docs/adr/`

If they do not exist, proceed silently. Domain-modeling skills create them when terminology or architectural decisions are resolved.

## File structure

```
/
├── CONTEXT.md
├── docs/
│   └── adr/
│       └── NNNN-decision-title.md
└── src/
```

## Vocabulary

Use domain concepts as defined in `CONTEXT.md`. Avoid introducing synonyms that conflict with its glossary.

## ADR conflicts

Explicitly identify proposed changes that contradict an existing ADR instead of silently overriding it.
