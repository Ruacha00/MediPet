# Separate capability reconciliation from runtime governance

Status: Accepted

Accepted: 2026-08-31

## Context

The development seed loads Skill packages and Tool contracts from the external `capabilities/`
mount. The original bootstrap also enabled every Tool and published or activated every Skill on
each API start. As a result, restarting the development stack could silently undo an administrator's
disablement, retirement, or rollback decision stored in the Registry.

## Decision

Treat an entirely empty Skill and Tool Registry as first-time development provisioning: synchronize
the repository capabilities, enable their trusted Tools, bind their Skills, and publish them so the
development chat remains ready to use.

Once either Registry contains governance state, startup performs reconciliation only. It synchronizes
deployed Tool contracts and stages new or changed Skill definitions, but it does not change existing
Tool enablement, Skill lifecycle state, or the selected active Skill version. New Tool versions remain
disabled and new or changed Skill versions remain drafts until an administrator explicitly governs
them through the management API.

## Consequences

Ordinary restarts preserve administrative decisions. Updating a capability definition can temporarily
leave the new version unavailable until it is reviewed and enabled, which is intentional: deployment
may introduce executable availability, but it does not grant runtime authority. The first empty
development deployment remains automatic and requires no manual setup.
