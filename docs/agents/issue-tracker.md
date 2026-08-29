# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are stored individually at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Ticket numbering starts at `01`
- Triage state is recorded as a `Status:` line near the top
- Comments are appended under a `## Comments` heading

## Publishing and fetching

When a skill publishes an issue, create the appropriate Markdown file under `.scratch/<feature-slug>/`.

When a skill fetches a ticket, read the referenced issue path or issue number.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`
- Ticket type is recorded using `Type:`
- Ticket state is recorded using `Status:`
- Dependencies are recorded using `Blocked by: NN, NN`
- Claiming sets `Status: claimed`
- Resolving adds an `## Answer`, sets `Status: resolved`, and updates the map
