# Skill library changelog

Each entry: version, what changed, source (general | escalation <link> | protocol | self), and the
hypothesis about why the previous version failed.

## skills-v0 (2026-11-25, stage 3)

Source: general debugging knowledge only (SPEC 4.3), written before any benchmark run or log.
Caveat: the author had seen stage-2 harness logs on three toy tasks (not benchmark tasks); the
skills deliberately do not target behaviours observed there (see reports/stage-3.md).

| Skill | Phase | Loaded |
|---|---|---|
| read-failure-first v1 | operate | read_more_code, minimal_repro, add_logging, bisect |
| minimal-repro v1 | operate | minimal_repro |
| local-fix v1 | operate | local_fix |
| revert-and-narrow v1 | operate | revert; triggers same_error_x3, repeat_diff, no_progress |
| hypothesize-by-category v1 | hypothesize | every hypothesis |
| hypothesis-must-predict v1 | hypothesize | every hypothesis |
