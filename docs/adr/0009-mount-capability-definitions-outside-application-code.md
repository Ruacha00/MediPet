# Mount capability definitions outside application code

Status: Accepted

Accepted: 2026-08-31

## Context

The first hospital Skill package, Tool contracts, and fake hospital catalog were stored below
`apps/api/src/medipet`. Changing instructions or a Tool description therefore looked like an
application-code change and required those files to be present in the Python package.

MediPet already treats Skills as versioned instruction packages and Tools as separately trusted
capabilities. Their editable definitions should make that boundary visible in development and in
deployment.

## Decision

Store editable capability definitions below the repository-level `capabilities/` directory:

- `capabilities/skills/` contains Agent Skills-compatible packages;
- `capabilities/tools/` contains declarative Tool contracts and development data;
- the API reads the root from `MEDIPET_CAPABILITIES_PATH` and Docker Compose mounts it read-only at
  `/app/capabilities`.

Python retains the trusted Tool executors and enforces an exact match between manifest Tool IDs and
deployed executors. A manifest can change versioned names, descriptions, schemas, and bindings, but
cannot introduce executable Python or downgrade a write executor's effect, approval, confirmation,
or stage policy. Existing registry publication, approval, and immutable-version rules continue to
apply.

## Consequences

Capability files can be edited or supplied as an independent read-only mount without rebuilding the
API image. Development bootstrap loads the mounted files when the API stack starts. A changed Tool
contract must use a new Tool version; changing a contract under an existing version remains rejected.
ADR-0010 governs how that reconciliation preserves Registry governance state after initial provisioning.
