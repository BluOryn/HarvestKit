<!--
Thanks for the PR. Help reviewers move fast:
-->

## Summary
<!-- 1-3 bullets on what changed and why. -->

## Type of change
- [ ] Bug fix
- [ ] New adapter / site coverage
- [ ] Extraction quality improvement (field fill / accuracy)
- [ ] Performance / throttling
- [ ] Schema or config change (breaking)
- [ ] Docs / build / CI

## Test plan
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] Smoke run against affected site (paste row counts + field fill below)
- [ ] Extension build succeeds (if extension touched)

```text
<paste relevant CSV stats or log excerpts>
```

## Compliance
- [ ] No paywall / login-wall bypass introduced
- [ ] No PII included in test fixtures
- [ ] Respects `obey_robots` flag where it matters

## Breaking changes
<!-- If yes: migration steps. -->
