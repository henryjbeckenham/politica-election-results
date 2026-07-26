# Release notes 1.1.3

Release 1.1.3 is the Stage 11.3 Senate group completion release.

It applies the existing, tested Stage 8 canonical transformers to the two
official AEC 2025 Senate group files that were registered by the original
reproduction before those individual-file transformers existed. The guarded
completion command reuses the existing source identities, creates a new
validated release, and retains the previous release unchanged.

The completed release contains 1,872 active source-group facts: 123 state or
territory group totals and 33 national group totals, each represented across
the six supported vote types as votes and vote share. All eight jurisdictions
must reconcile, and the national file must reconcile to the combined states
and territories before publication.

Application version is 1.1.3. Database schema remains 0.2.0. Publication feed
contract remains 1.0.0. The update does not write to Google Sheets or modify
Grand Database rows.

The distribution also contains a cross-platform test-fixture correction for
macOS. If an initial 1.1.3 installation stopped after the preceding 59 tests
passed, rerunning the corrected installer performs the nine relevant Stage 11
checks and then resumes the governed completion operation.
