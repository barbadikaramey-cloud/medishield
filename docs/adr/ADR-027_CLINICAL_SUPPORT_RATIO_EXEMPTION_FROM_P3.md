# Architecture Decision Record
## ADR-027: Clinical Support Ratio Exemption from Rule P3

**Status:** Accepted  
**Date:** 2026-10-21  
**Context:** Decision Engine and State Machine Specification § 3 (P3, P6, P7), § 4 note 5, ADR-025  
**Deciders:** MediShield Engineering

---

## 1. Context and Problem Statement

Rule P3 escalates any case whose clinical alignment verdict is `INDETERMINATE`. In practice, a claim where most billed lines are fully supported by clinical documentation, and only one or two lines lack sufficient evidence, is escalated for human review even though every other specialist passes. Escalation in this situation adds reviewer workload without a corresponding risk reduction, because the unsupported lines are a minority of the billed amount and carry no contradiction.

Reviewers observed that, in the current data, every clinical `INDETERMINATE` case had zero fully supported lines, so the escalation was never tested against a partially supported claim. The business requirement is that clinical-only uncertainty should not escalate when the claim is majority supported and nothing contradicts it.

Rule P6 also requires `clinical_output.verdict == PASS` with calibrated confidence of at least 0.75. A clinical `INDETERMINATE` verdict never produces a confidence value, so a clinical-only exemption cannot be expressed by changing P3 alone. P7 would still route the case to human review.

## 2. Decision

1. Add a configuration setting `CLINICAL_SUPPORT_APPROVAL_MIN_RATIO` (default `0.5`, range `0.0` to `1.0`). Setting it to unset disables the exemption.
2. A clinical alignment output is exempt from P3 when all of the following hold:
   - `verdict == INDETERMINATE`
   - `total_lines_assessed > 0`
   - `lines_fully_supported_count / total_lines_assessed` is strictly greater than the configured ratio. A ratio equal to the threshold still escalates.
   - `lines_contradicted_count`, `lines_failed_necessity_count`, and `lines_unavailable_count` are all zero
   - `schema_valid` and `ocr_quality_sufficient` are true
3. An exempt clinical output satisfies the clinical admissibility check in P6. All other P6 conditions still apply, including KYC, claims, policy, fraud, and consistency checks. When the exemption is used, the P6 justification records the supported line count.
4. The exemption does not change `CONFLICT`, `FAIL`, `UNAVAILABLE`, or any other rule. It only applies to clinical-only `INDETERMINATE`.
5. The threshold is read from configuration and passed into `DecisionInputContext`. The decision engine does not hardcode the value.

## 3. Consequences

### Positive
- Majority-supported claims with only clinical uncertainty can be approved without reviewer escalation.
- The threshold is configurable per environment without a code change.
- Contradicted, failed, unavailable, schema-invalid, and OCR-degraded cases still escalate.
- The P6 justification shows the supported line count, which preserves provenance for audit.

### Trade-offs and Follow-up
- The auto-approval rate increases for claims with partially supported clinical evidence. The threshold should be reviewed against reviewer outcomes before production use.
- The threshold is a ratio of billed lines, not billed amount. A high-value unsupported line can be outweighed by several low-value supported lines. A dollar-weighted rule would require a separate ADR.
- `docs/canonical/02_DECISION_ENGINE_AND_STATE_MACHINE.md` was amended with the exemption. The change is governed by this ADR together with the canonical amendment process in ADR-015.
- Clinical `INDETERMINATE` results with `ocr_quality_sufficient` false or `schema_valid` false continue to escalate.
