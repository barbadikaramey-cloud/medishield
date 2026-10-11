# ADR-010: Verified Policy Denial Recommendations

**Status:** Accepted  
**Date:** 2026-10-08

## Context

The baseline decision contract allowed a generic `policy_verdict == FAIL` to
enter P5. That is insufficient evidence for a policy-denial recommendation:
an empty corpus, a missing clause, or an unverified quote must never be
treated as a confirmed exclusion.

## Decision

P5 remains an `ESCALATE` decision with sub-reason `RECOMMEND_DENIAL`; it is
not a new top-level business decision. A policy-origin P5 recommendation is
permitted only when the policy agent returned `FAIL`, an authoritative
exclusion citation was verified verbatim against the selected policy/version,
and that citation explicitly applies to a billed service code.

Otherwise the policy result is `INDETERMINATE` (or `UNAVAILABLE` for a
dependency failure) and the normal P1/P3 escalation path applies. Decision
confidence is derived from specialist evidence; it is never invented by a
rule constant.
