# Release notes 1.0.1

Release 1.0.1 is the Stage 10.1 elected-person identity correction.

The 2025 AEC candidate register names `Anne Maree STANLEY` and `Luke John GOSLING`, while the authoritative People records use the shorter public names Anne Stanley and Luke Gosling. The original import correctly preserved both AEC candidate names but left their canonical person links empty.

This release:

- resolves each identity only when first given name plus family name identifies exactly one active synced People record;
- refuses ambiguous matches and refuses to overwrite any different existing person link;
- updates both the canonical candidacy and elected-member link in a disposable database copy;
- validates and freezes that copy as a new immutable release before activation;
- retains the prior Stage 9.1 release unchanged;
- applies the same conservative matching rule to future full imports; and
- leaves database schema 0.2.0 and all seven Stage 10 feed contracts at version 1.0.0.

The complete Stage 4–10.1 regression suite contains 51 passing tests.
