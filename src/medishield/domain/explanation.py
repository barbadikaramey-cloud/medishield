"""LLM-Aided & Deterministic Decision Explanation Generator (ADR-011, Spec 02, Spec 04).

Synthesizes multi-agent specialist findings (KYC, Claims, Policy RAG, Fraud) into a
detailed, human-understandable clinical and financial narrative for the frontend reviewer console.
Strictly respects Rules 10, 11, and 14:
- The decision verdict and rule match are 100% deterministic and binding.
- The LLM only explains the deterministic outcome; it never decides or alters the verdict.
- When the LLM is offline or unconfigured, an authoritative deterministic generator provides
  the identical structured explanation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from medishield.core.llm import GroqLLMClient, default_groq_client
from medishield.db.enums import BusinessDecision
from medishield.domain.contracts import OrchestratorOutput
from medishield.domain.decision import DecisionInputContext

logger = logging.getLogger(__name__)


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely extracts attribute or key from object or dict without raising AttributeError."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except (AttributeError, KeyError):
        return default


def _enum_str(val: Any, default: str = "N/A") -> str:
    """Safely extracts enum string value or string representation."""
    if val is None:
        return default
    return str(val.value if hasattr(val, "value") else val)


def _safe_unwrap_value(val: Any, default: Any = "") -> Any:
    """Recursively unwraps ProvenanceValue objects or serialized {'value': ...} dicts to raw scalar values."""
    if val is None:
        return default
    if hasattr(val, "value"):
        return _safe_unwrap_value(val.value, default)
    if isinstance(val, dict) and "value" in val:
        return _safe_unwrap_value(val["value"], default)
    return val


def _format_tamper_findings(signals: list[Any]) -> list[str]:
    """Groups tamper signals by source document as short bullets (document -> inconsistency)."""
    by_doc: dict[str, list[Any]] = {}
    for sig in signals:
        if _safe_get(sig, "signal_type") == "FORENSIC_RETRIEVAL_FAILED":
            continue
        doc = _safe_get(sig, "document_name") or _safe_get(sig, "document_id") or "Document not identified"
        by_doc.setdefault(str(doc), []).append(sig)

    out: list[str] = []
    for doc, doc_signals in by_doc.items():
        out.append(f"  - 📄 **{doc}**")
        for sig in doc_signals:
            kind = str(_safe_get(sig, "signal_type", "SIGNAL")).replace("_", " ").title()
            sev = _safe_get(sig, "severity", "")
            conf = float(_safe_get(sig, "confidence", 0.0) or 0.0)
            desc = _safe_get(sig, "description", "")
            out.append(f"    - {kind} ({sev}, {conf * 100:.0f}%): {desc}")
    return out


def generate_deterministic_explanation(
    context: DecisionInputContext,
    decision_output: OrchestratorOutput,
) -> str:
    """
    Generates a structured, comprehensive markdown explanation deterministically from agent findings.
    Serves as the zero-latency baseline and offline fallback (Rule 11).
    """
    verdict_emoji = {
        BusinessDecision.APPROVE: "✅",
        BusinessDecision.REJECT: "❌",
        BusinessDecision.ESCALATE: "⚠️",
    }.get(decision_output.decision, "ℹ️")

    rule_name_map = {
        "RULE-P1-SECURITY": "Document Security & Tamper Check",
        "RULE-P1-TECH-FAIL": "Technical Validation Check",
        "RULE-P2-INCOMPLETE": "Intake Documentation Completeness",
        "RULE-P3-AMBIGUITY": "Information Discrepancy & Verification Review",
        "RULE-P3-AMBIGUITY-ESCALATE": "Information Discrepancy & Verification Review",
        "RULE-P4-HIGH-FRAUD": "Billing Pattern & Anomaly Review",
        "RULE-P5-HARD-REJECT": "Policy Exclusion & Eligibility Denial",
        "RULE-P6-CLEAN-PASS": "Automated Clean Approval",
        "RULE-P7-BORDERLINE-REVIEW": "Medical Necessity & Guideline Review",
        "RULE-P8-CATCH-ALL": "Operational Adjudication Review",
    }
    human_rule = rule_name_map.get(
        decision_output.rule_id_matched,
        decision_output.rule_id_matched.replace("RULE-", "").replace("-", " ").title(),
    )

    lines = [
        f"### {verdict_emoji} Adjudication Decision: {decision_output.decision.value}",
        (
            f"**Adjudication Policy Rule:** {human_rule} (`{decision_output.rule_id_matched}`) &bull; "
            f"**Confidence:** `{decision_output.decision_confidence * 100:.1f}%`"
        ),
    ]

    if decision_output.escalation_sub_reason:
        sub_val = decision_output.escalation_sub_reason.value
        friendly_sub = "Information Discrepancy" if sub_val == "AMBIGUITY" else sub_val
        lines.append(f"**Escalation Review Reason:** {friendly_sub} (`{sub_val}`)")

    lines.extend([
        "",
        "#### Executive Summary",
        decision_output.justification,
        "",
        "---",
        "",
        "#### 1. Member Identity & KYC Verification",
    ])

    if context.kyc_output:
        kyc = context.kyc_output
        kyc_verdict = _safe_get(kyc, "verdict")
        kyc_v_str = kyc_verdict.value if hasattr(kyc_verdict, "value") else str(kyc_verdict or "PASS")
        kyc_conf = float(_safe_get(kyc, "confidence", 1.0) or 1.0)
        lines.append(f"- **KYC Verdict:** `{kyc_v_str}` (Confidence: {kyc_conf * 100:.1f}%)")
        db_match = _safe_get(kyc, "member_db_matched", False)
        lines.append(f"- **Member DB Match:** {'✅ Verified in master database' if db_match else '❌ Unmatched or identity mismatch'}")
        mem_id = _safe_get(kyc, "member_id")
        if mem_id is not None:
            m_val = _safe_unwrap_value(mem_id)
            lines.append(f"- **Resolved Member ID:** `{m_val}`")
        flags = _safe_get(kyc, "flags", [])
        tamper_lines = _format_tamper_findings(_safe_get(kyc, "tamper_signals", []) or [])
        if tamper_lines:
            lines.append(f"- **Document Integrity / Tamper Score:** `{context.security_tamper_score * 100:.1f}%`")
            lines.append("- **⚠️ Tamper Flag — Flagged Documents:**")
            lines.extend(tamper_lines)
        elif context.security_tamper_score > 0:
            lines.append(f"- **Document Integrity / Tamper Score:** `{context.security_tamper_score * 100:.1f}%`")
        if flags:
            lines.append(f"- **Identity Flags:** {', '.join(flags)}")
        reasoning = _safe_get(kyc, "reasoning_summary")
        if reasoning:
            lines.append(f"- **Analysis:** {reasoning}")
    else:
        lines.append("- *KYC specialist data not available for this case.*")

    lines.extend([
        "",
        "#### 2. Claims Financial & Line-Item Validation",
    ])

    if context.claims_output:
        claims = context.claims_output
        cl_verdict = _safe_get(claims, "verdict")
        cl_v_str = cl_verdict.value if hasattr(cl_verdict, "value") else str(cl_verdict or "PASS")
        cl_conf = float(_safe_get(claims, "confidence", 1.0) or 1.0)
        lines.append(f"- **Claims Verdict:** `{cl_v_str}` (Confidence: {cl_conf * 100:.1f}%)")
        service_lines = _safe_get(claims, "service_lines") or _safe_get(claims, "line_items") or []
        first_line_sym = _safe_get(service_lines[0], "currency_symbol") if service_lines else None
        curr_sym = _safe_get(claims, "currency_symbol") or first_line_sym
        if not curr_sym:
            curr_code = _safe_get(claims, "currency")
            curr_sym = "₹" if curr_code == "INR" else "$"
        claim_amt = _safe_get(claims, "claim_amount")
        if claim_amt is not None:
            claim_val = _safe_unwrap_value(claim_amt)
            try:
                lines.append(f"- **Header Claim Total:** `{curr_sym}{float(claim_val):,.2f}`")
            except (ValueError, TypeError):
                lines.append(f"- **Header Claim Total:** `{curr_sym}{claim_val}`")
        math_valid = _safe_get(claims, "line_items_sum_valid")
        if math_valid is None:
            lines.append("- **Line Items Arithmetic:** Not available")
        else:
            lines.append(f"- **Line Items Arithmetic:** {'✅ Exact match (line totals equal header charge)' if math_valid else '❌ Discrepancy detected between line sums and declared total'}")
        if service_lines:
            lines.append(f"- **Itemized Service Lines:** {len(service_lines)} lines parsed")
            for idx, item in enumerate(service_lines[:5], 1):
                cpt = _safe_get(item, "hcpcs") or _safe_get(item, "cpt_code")
                cpt_val = _safe_unwrap_value(cpt, "")
                rev = _safe_get(item, "rev_code")
                rev_val = _safe_unwrap_value(rev, "")
                amt = _safe_get(item, "amount") or _safe_get(item, "total_charge", 0.0)
                amt_val = _safe_unwrap_value(amt, 0.0)
                line_sym = _safe_get(item, "currency_symbol") or curr_sym

                if cpt_val and str(cpt_val).strip() not in ("", "None", "UNKNOWN", "null"):
                    code_lbl = f"CPT/HCPCS `{cpt_val}`"
                elif rev_val and str(rev_val).strip() not in ("", "None", "UNKNOWN", "null"):
                    code_lbl = f"Rev Code `{rev_val}`"
                else:
                    code_lbl = "Code `UNKNOWN`"

                try:
                    lines.append(f"  - Line {idx}: {code_lbl} | Charge: `{line_sym}{float(amt_val):,.2f}`")
                except (ValueError, TypeError):
                    lines.append(f"  - Line {idx}: {code_lbl} | Charge: `{line_sym}{amt_val}`")
            if len(service_lines) > 5:
                lines.append(f"  - *(and {len(service_lines) - 5} additional line items)*")
        cpt_codes = _safe_get(claims, "cpt_codes", [])
        if cpt_codes:
            cpt_strs = [str(_safe_unwrap_value(c)) for c in cpt_codes if _safe_unwrap_value(c)]
            if cpt_strs:
                lines.append(f"- **Extracted CPT / HCPCS Codes:** {', '.join(cpt_strs)}")
        icd10_codes = _safe_get(claims, "icd10_codes", [])
        if icd10_codes:
            icd_strs = [str(_safe_unwrap_value(c)) for c in icd10_codes if _safe_unwrap_value(c)]
            if icd_strs:
                lines.append(f"- **Diagnosis Codes (ICD-10):** {', '.join(icd_strs)}")
        val_errors = _safe_get(claims, "validation_errors", [])
        if val_errors:
            lines.append(f"- **Validation Warnings:** {'; '.join(val_errors)}")
    else:
        lines.append("- *Claims extraction specialist data not available.*")

    lines.extend([
        "",
        "#### 3. Policy Coverage & Benefit Grounding",
    ])

    if context.policy_output:
        pol = context.policy_output
        p_verdict = _safe_get(pol, "verdict")
        p_v_str = p_verdict.value if hasattr(p_verdict, "value") else str(p_verdict or "PASS")
        p_conf = float(_safe_get(pol, "confidence", 1.0) or 1.0)
        lines.append(f"- **Policy Verdict:** `{p_v_str}` (Confidence: {p_conf * 100:.1f}%)")
        cov_pct = _safe_get(pol, "coverage_percentage")
        if cov_pct is None:
            lines.append("- **Coverage Percentage:** Not available")
        else:
            lines.append(f"- **Coverage Percentage:** `{float(cov_pct):.1f}%`")
        policy_id = _safe_get(pol, "policy_id")
        if policy_id:
            lines.append(f"- **Active Policy:** `{policy_id}`")
        effective_date_valid = _safe_get(pol, "service_date_within_effective_period")
        if effective_date_valid is None:
            effective_date_valid = _safe_get(pol, "effective_on_service_date")
        if effective_date_valid is True:
            lines.append("- **Service Date Validity:** ✅ Date of service falls within active enrollment window")
        elif effective_date_valid is False:
            lines.append("- **Service Date Validity:** ❌ Date of service falls outside policy enrollment window")
        else:
            lines.append("- **Service Date Validity:** Not available")
        if _safe_get(pol, "exclusion_clause_verified", False):
            ex_id = _safe_get(pol, "exclusion_clause_id") or "Applicable Exclusion"
            lines.append(f"- **Exclusion Notice:** ⚠️ Confirmed policy exclusion: {ex_id}")
        cited_clauses = _safe_get(pol, "cited_clauses", [])
        if cited_clauses:
            cited_strs = []
            for c in cited_clauses:
                if hasattr(c, "clause_title"):
                    cited_strs.append(c.clause_title)
                elif hasattr(c, "section_number"):
                    cited_strs.append(f"Section {c.section_number}")
                else:
                    cited_strs.append(str(c))
            lines.append(f"- **Cited Policy Clauses:** {', '.join(cited_strs)}")
        reasoning = _safe_get(pol, "reasoning_summary")
        if reasoning:
            lines.append(f"- **Coverage Rationale:** {reasoning}")
    else:
        lines.append("- *Policy coverage specialist data not available.*")

    lines.extend([
        "",
        "#### 4. Fraud Risk & Anomaly Assessment",
    ])

    if context.fraud_output:
        fr = context.fraud_output
        risk_level = _safe_get(fr, "risk_level")
        r_str = risk_level.value if hasattr(risk_level, "value") else str(risk_level or "LOW")
        review_required = bool(_safe_get(fr, "fraud_review_required", False))
        lines.append(
            f"- **Fraud Review Required:** `{'YES' if review_required else 'NO'}` "
            f"| **Status:** `{r_str}`"
        )
        fr_verdict = _safe_get(fr, "verdict")
        fr_v_str = fr_verdict.value if hasattr(fr_verdict, "value") else str(fr_verdict or "PASS")
        fr_conf = float(_safe_get(fr, "confidence", 1.0) or 1.0)
        lines.append(f"- **Fraud Verdict:** `{fr_v_str}` (Confidence: {fr_conf * 100:.1f}%)")
        anomalies = _safe_get(fr, "anomalies", [])
        if anomalies:
            lines.append(f"- **Detected Anomalies:** {', '.join(anomalies)}")
        else:
            lines.append("- **Detected Anomalies:** None reported by the fraud assessment")
        detected_features: list[str] = []
        features = _safe_get(fr, "features")
        if features:
            detected_features = [
                f.feature_name
                for f in features
                if getattr(f, "detected", False) and hasattr(f, "feature_name")
            ]
        if detected_features:
            lines.append(f"- **Detected Factors:** {', '.join(detected_features)}")
    else:
        lines.append("- *Fraud detection evaluation not available.*")

    lines.extend([
        "",
        "#### 5. Cross-Document Consistency & Regulatory Attestation",
    ])

    cr = getattr(context, "consistency_report", None)
    if cr:
        is_consistent = _safe_get(cr, "is_consistent", True)
        lines.append(f"- **Overall Consistency:** {'✅ Cross-document integrity verified' if is_consistent else '⚠️ Inconsistency or missing attestation detected'}")
        
        doc_sig = _safe_get(cr, "doctor_signature_verified", False)
        lines.append(f"- **Doctor Signature / Attestation:** {'✅ Verified on clinical record' if doc_sig else '❌ Missing or unverified on clinical record'}")

        pat_sig = _safe_get(cr, "patient_signature_verified", True)
        lines.append(f"- **Patient Signature / Assignment:** {'✅ Verified or not required on claim documentation' if pat_sig else '❌ Missing patient signature or assignment of benefits'}")

        off_sig = _safe_get(cr, "officer_signature_verified", True)
        if not off_sig:
            lines.append("- **Authorized Officer Signature:** ❌ Missing authorized officer signature on policy amendment document")

        npi_ver = _safe_get(cr, "npi_verified", True)
        lines.append(f"- **Rendering Provider NPI:** {'✅ Verified valid 10-digit NPI' if npi_ver else '❌ Missing or invalid rendering provider NPI'}")

        pa_ver = _safe_get(cr, "prior_authorization_verified", True)
        lines.append(f"- **Prior Authorization:** {'✅ Verified or not required' if pa_ver else '❌ Missing mandatory prior authorization for major procedure'}")

        proc_ver = _safe_get(cr, "procedure_codes_verified", True)
        if not proc_ver:
            uncoded_amt = float(_safe_get(cr, "unvalidated_charges_total", 0.0) or 0.0)
            c_sym = _safe_get(getattr(context, "claims_output", None), "currency_symbol") or "$"
            lines.append(f"- **Procedure Code Validation:** ❌ Unvalidated line charges ({c_sym}{uncoded_amt:,.2f}) lack authoritative CPT/HCPCS procedure code")
        else:
            lines.append("- **Procedure Code Validation:** ✅ All billed line charges mapped to recognized procedure codes")

        date_summary = _safe_get(cr, "date_reconciliation_summary")
        if date_summary:
            lines.append(f"- **Date Reconciliation:** {date_summary}")
        else:
            dates_ok = _safe_get(cr, "dates_consistent", True)
            lines.append(f"- **Date Consistency:** {'✅ Chronologically aligned across records' if dates_ok else '❌ Incompatible service and discharge dates'}")

        conflicts = _safe_get(cr, "conflicts_detected", [])
        if conflicts:
            lines.append(f"- **Detected Conflicts:** {'; '.join(conflicts)}")

        reasons = _safe_get(cr, "specific_escalation_reasons", [])
        if reasons:
            lines.append(f"- **Specific Escalation Triggers:** {'; '.join(reasons)}")
    else:
        lines.append("- *Cross-document consistency evaluation record not available.*")

    lines.extend([
        "",
        "#### 6. Clinical Alignment & Medical Necessity",
    ])

    clin = getattr(context, "clinical_output", None)
    if clin:
        c_verdict = _safe_get(clin, "verdict")
        c_v_str = c_verdict.value if hasattr(c_verdict, "value") else str(c_verdict or "PASS")
        c_conf = _safe_get(clin, "confidence")
        conf_str = f"{float(c_conf) * 100:.1f}%" if c_conf is not None else "Uncalibrated"
        tot_lines = int(_safe_get(clin, "total_lines_assessed", 0) or 0)
        supp_lines = int(_safe_get(clin, "lines_fully_supported_count", 0) or 0)
        lines.append(f"- **Clinical Alignment Verdict:** `{c_v_str}` (Confidence: {conf_str})")
        lines.append(f"- **Line Items Evaluated:** {tot_lines} total | {supp_lines} fully supported")
        if _safe_get(clin, "lines_indeterminate_count", 0) > 0:
            lines.append(f"- **Indeterminate Lines:** {_safe_get(clin, 'lines_indeterminate_count')} line item(s) require guideline review")
        if _safe_get(clin, "lines_failed_necessity_count", 0) > 0:
            lines.append(f"- **Necessity Gaps:** {_safe_get(clin, 'lines_failed_necessity_count')} line item(s) unfulfilled under standard medical guidelines")

        # Granular Line-Item Clinical Evidence Grounding
        line_assessments = _safe_get(clin, "line_assessments", []) or []
        if line_assessments:
            lines.append("- **Itemized Clinical Indication & Evidence Grounding:**")
            line_reasons: set[str] = set()
            for la in line_assessments:
                l_num = _safe_get(la, "line_number", 1)
                code = _safe_get(la, "service_code", "UNKNOWN")
                amt = float(_safe_get(la, "billed_amount", 0.0) or 0.0)
                finding = _safe_get(la, "discrepancy_description") or _safe_get(la, "clinical_finding_summary", "")
                l_verdict = _safe_get(la, "line_verdict")
                l_v_str = l_verdict.value if hasattr(l_verdict, "value") else str(l_verdict or "PASS")
                if finding and l_v_str != "PASS":
                    line_reasons.add(str(finding))
                badge = "✅" if l_v_str == "PASS" else "⚠️" if l_v_str in ("INDETERMINATE", "UNAVAILABLE") else "❌"
                curr_sym = _safe_get(la, "currency_symbol") or "$"
                lines.append(
                    f"  - {badge} **Line {l_num} — {code} ({curr_sym}{amt:,.2f}):** "
                    f"{finding or f'Clinical assessment {l_v_str.lower()}.'}"
                )

        # Inpatient Bed-Day Reconciliation
        bdr = _safe_get(clin, "bed_day_reconciliation")
        if bdr:
            b_days = float(_safe_get(bdr, "billed_bed_days", 0.0) or 0.0)
            c_stayed = _safe_get(bdr, "calendar_days_stayed")
            admit_d = _safe_get(bdr, "admission_date")
            disch_d = _safe_get(bdr, "discharge_date")
            b_rule = _safe_get(bdr, "billing_rule")
            conv = _safe_get(b_rule, "convention") if b_rule else "MIDNIGHT_CENSUS"
            conv_str = conv.value if hasattr(conv, "value") else str(conv)
            r_verdict = _safe_get(bdr, "reconciliation_verdict")
            r_v_str = r_verdict.value if hasattr(r_verdict, "value") else str(r_verdict or "PASS")
            is_rec = bool(_safe_get(bdr, "is_reconciled", False))

            stay_dates_str = (
                f"Admit: {admit_d} &bull; Discharge: {disch_d}"
                if admit_d and disch_d
                else "Dates documented in clinical record"
            )
            cal_str = f" ({c_stayed} calendar days)" if c_stayed is not None else ""

            status_badge = "✅" if is_rec else "⚠️"
            lines.append(
                f"- **Inpatient Hospital Stay & Bed-Day Reconciliation:** {status_badge} `{r_v_str}` "
                f"({b_days:.1f} billed bed day(s) vs stay chronology: {stay_dates_str}{cal_str} under `{conv_str}` convention)."
            )
            remarks = _safe_get(bdr, "remarks")
            if remarks:
                lines.append(f"  - *Chronology Notes:* {remarks}")

        # Reviewer Flags & Discrepancies
        discrepancies = _safe_get(clin, "blocking_discrepancies", [])
        all_discs = list(dict.fromkeys(str(item) for item in discrepancies if str(item) not in line_reasons))

        if all_discs:
            lines.append("- **Clinical Discrepancies & Reviewer Flags:**")
            for d in all_discs:
                lines.append(f"  - ⚠️ {d}")

    else:
        lines.append("- *Clinical alignment specialist assessment not available.*")

    lines.extend([
        "",
        "#### 7. Operator Guidance & Recommended Action",
    ])

    if decision_output.decision == BusinessDecision.APPROVE:
        lines.append(
            "- **Straight-Through Processing:** All clinical, arithmetic, and policy eligibility criteria "
            "have passed with calibrated confidence. No manual operator intervention is required."
        )
    elif decision_output.decision == BusinessDecision.REJECT:
        lines.append(
            "- **Adjudication Denial Recommendation:** A confirmed policy exclusion or hard non-eligibility rule "
            "was triggered. Operator should review the cited exclusion clauses above before final patient notice."
        )
    else:  # ESCALATE
        lines.append(
            "- **Human Review Required:** Automated straight-through processing was halted per governance rules. "
            "Operator should inspect the flagged items above (such as missing signatures, prior authorization, "
            "policy coverage discrepancies, or billing risk indicators) and apply human review discretion."
        )

    return "\n".join(lines)


def generate_decision_explanation(
    context: DecisionInputContext,
    decision_output: OrchestratorOutput,
    llm_client: GroqLLMClient | None = None,
) -> str:
    """
    Generates an enriched clinical and policy explanation for the decision.
    Returns deterministic synthesis so every displayed assertion stays grounded
    in specialist findings and cannot be invented by an LLM.
    """
    return generate_deterministic_explanation(context, decision_output)

    client = llm_client or default_groq_client
    if not client or not client.is_available():
        logger.info("Groq LLM is not available; generating deterministic decision explanation.")
        return generate_deterministic_explanation(context, decision_output)

    # Compile structured summary of deterministic facts for LLM synthesis
    claims_out = getattr(context, "claims_output", None)
    claim_val = 0.0
    if claims_out:
        ca = _safe_get(claims_out, "claim_amount")
        if ca is not None:
            raw_ca = ca.value if hasattr(ca, "value") else ca
            try:
                claim_val = float(raw_ca)
            except (ValueError, TypeError):
                claim_val = 0.0

    kyc_out = getattr(context, "kyc_output", None)
    pol_out = getattr(context, "policy_output", None)
    fraud_out = getattr(context, "fraud_output", None)
    cr_out = getattr(context, "consistency_report", None)
    clin_out = getattr(context, "clinical_output", None)

    # Format clinical line assessments for prompt payload
    formatted_clinical_lines = []
    line_assessments = _safe_get(clin_out, "line_assessments", []) or []
    for la in line_assessments:
        gc = _safe_get(la, "guideline_citation")
        gc_data = None
        if gc:
            criteria_evals = []
            for c in _safe_get(gc, "criteria_evaluations", []) or []:
                c_out = _safe_get(c, "evaluation_outcome")
                c_out_str = c_out.value if hasattr(c_out, "value") else str(c_out)
                criteria_evals.append({
                    "criterion_id": _safe_get(c, "criterion_id"),
                    "description": _safe_get(c, "description"),
                    "outcome": c_out_str,
                    "is_mandatory": _safe_get(c, "is_mandatory", True),
                })
            gc_data = {
                "guideline_id": _safe_get(gc, "guideline_id"),
                "guideline_title": _safe_get(gc, "guideline_title"),
                "guideline_version": _safe_get(gc, "guideline_version"),
                "all_mandatory_criteria_satisfied": _safe_get(gc, "all_mandatory_criteria_satisfied", True),
                "criteria_evaluations": criteria_evals,
            }

        modality = _safe_get(la, "modality")
        mod_str = modality.value if hasattr(modality, "value") else str(modality or "")
        p_stat = _safe_get(la, "performance_status")
        p_stat_str = p_stat.value if hasattr(p_stat, "value") else str(p_stat or "")
        ind_stat = _safe_get(la, "indication_status")
        ind_stat_str = ind_stat.value if hasattr(ind_stat, "value") else str(ind_stat or "")
        nec_stat = _safe_get(la, "necessity_status")
        nec_stat_str = nec_stat.value if hasattr(nec_stat, "value") else str(nec_stat or "")
        l_verd = _safe_get(la, "line_verdict")
        l_verd_str = l_verd.value if hasattr(l_verd, "value") else str(l_verd or "")
        pa_req = _safe_get(la, "prior_auth_requirement")
        pa_req_str = pa_req.value if hasattr(pa_req, "value") else str(pa_req or "")
        pa_ver = _safe_get(la, "prior_auth_verification")
        pa_ver_str = pa_ver.value if hasattr(pa_ver, "value") else str(pa_ver or "")

        formatted_clinical_lines.append({
            "line_number": _safe_get(la, "line_number"),
            "service_code": _safe_get(la, "service_code"),
            "modality": mod_str,
            "billed_amount": float(_safe_get(la, "billed_amount", 0.0) or 0.0),
            "billed_units": float(_safe_get(la, "billed_units", 1.0) or 1.0),
            "service_date": str(_safe_get(la, "service_date")) if _safe_get(la, "service_date") else None,
            "primary_icd10_codes": list(_safe_get(la, "primary_icd10_codes", []) or []),
            "secondary_icd10_codes": list(_safe_get(la, "secondary_icd10_codes", []) or []),
            "performance_status": p_stat_str,
            "indication_status": ind_stat_str,
            "necessity_status": nec_stat_str,
            "guideline_citation": gc_data,
            "clinical_finding_summary": _safe_get(la, "clinical_finding_summary", ""),
            "discrepancy_description": _safe_get(la, "discrepancy_description"),
            "prior_auth_requirement": pa_req_str,
            "prior_auth_verification": pa_ver_str,
            "line_verdict": l_verd_str,
        })

    # Bed-day reconciliation
    bdr = _safe_get(clin_out, "bed_day_reconciliation")
    bdr_data = None
    if bdr:
        b_rule = _safe_get(bdr, "billing_rule")
        conv = _safe_get(b_rule, "convention") if b_rule else "MIDNIGHT_CENSUS"
        conv_str = conv.value if hasattr(conv, "value") else str(conv)
        r_verd = _safe_get(bdr, "reconciliation_verdict")
        r_verd_str = r_verd.value if hasattr(r_verd, "value") else str(r_verd or "")
        bdr_data = {
            "billed_bed_days": float(_safe_get(bdr, "billed_bed_days", 0.0) or 0.0),
            "calendar_days_stayed": _safe_get(bdr, "calendar_days_stayed"),
            "admission_date": str(_safe_get(bdr, "admission_date")) if _safe_get(bdr, "admission_date") else None,
            "discharge_date": str(_safe_get(bdr, "discharge_date")) if _safe_get(bdr, "discharge_date") else None,
            "is_reconciled": bool(_safe_get(bdr, "is_reconciled", False)),
            "reconciliation_verdict": r_verd_str,
            "billing_convention": conv_str,
            "billing_rule_id": _safe_get(b_rule, "rule_id") if b_rule else None,
            "discrepancy_units": float(_safe_get(bdr, "discrepancy_units", 0.0) or 0.0),
            "remarks": str(_safe_get(bdr, "remarks", "")),
        }

    prompt_payload = {
        "case_id": getattr(context, "case_id", "UNKNOWN"),
        "binding_decision": decision_output.decision.value,
        "rule_id_matched": decision_output.rule_id_matched,
        "escalation_sub_reason": (
            decision_output.escalation_sub_reason.value
            if decision_output.escalation_sub_reason
            else None
        ),
        "confidence": round(decision_output.decision_confidence, 4),
        "deterministic_justification": decision_output.justification,
        "kyc": {
            "verdict": _enum_str(_safe_get(kyc_out, "verdict", "N/A")),
            "member_matched": bool(_safe_get(kyc_out, "member_db_matched", False)),
            "member_id": str(_safe_get(kyc_out, "member_id", "")),
            "tamper_score": getattr(context, "security_tamper_score", 0.0),
            "flags": list(_safe_get(kyc_out, "flags", [])),
            "reasoning": str(_safe_get(kyc_out, "reasoning_summary", "")),
        },
        "claims": {
            "verdict": _enum_str(_safe_get(claims_out, "verdict", "N/A")),
            "claim_amount": claim_val,
            "line_items_valid": bool(_safe_get(claims_out, "line_items_sum_valid", False)),
            "cpt_codes": [str(c.value if hasattr(c, "value") else c) for c in _safe_get(claims_out, "cpt_codes", [])],
            "icd10_codes": [str(c.value if hasattr(c, "value") else c) for c in _safe_get(claims_out, "icd10_codes", [])],
            "validation_errors": list(_safe_get(claims_out, "validation_errors", [])),
        },
        "policy": {
            "verdict": _enum_str(_safe_get(pol_out, "verdict", "N/A")),
            "coverage_percentage": float(_safe_get(pol_out, "coverage_percentage", 0.0) or 0.0),
            "policy_id": str(_safe_get(pol_out, "policy_id", "")),
            "service_date_valid": bool(_safe_get(pol_out, "service_date_within_effective_period", _safe_get(pol_out, "effective_on_service_date", True))),
            "exclusion_verified": bool(_safe_get(pol_out, "exclusion_clause_verified", False)),
            "exclusion_clause": str(_safe_get(pol_out, "exclusion_clause_id", "")),
            "cited_clauses": [str(getattr(c, "clause_title", str(c))) for c in _safe_get(pol_out, "cited_clauses", [])],
            "reasoning": str(_safe_get(pol_out, "reasoning_summary", "")),
        },
        "fraud": {
            "verdict": _enum_str(_safe_get(fraud_out, "verdict", "PASS"), "PASS"),
            "fraud_score": float(_safe_get(fraud_out, "fraud_score", 0.0) or 0.0),
            "risk_level": _enum_str(_safe_get(fraud_out, "risk_level", "LOW"), "LOW"),
            "anomalies": list(_safe_get(fraud_out, "anomalies", [])),
        },
        "consistency": {
            "is_consistent": bool(_safe_get(cr_out, "is_consistent", True)),
            "doctor_signature_verified": bool(_safe_get(cr_out, "doctor_signature_verified", False)),
            "patient_signature_verified": bool(_safe_get(cr_out, "patient_signature_verified", True)),
            "officer_signature_verified": bool(_safe_get(cr_out, "officer_signature_verified", True)),
            "npi_verified": bool(_safe_get(cr_out, "npi_verified", True)),
            "prior_authorization_verified": bool(_safe_get(cr_out, "prior_authorization_verified", True)),
            "specific_escalation_reasons": list(_safe_get(cr_out, "specific_escalation_reasons", []) or []),
            "date_reconciliation_summary": str(_safe_get(cr_out, "date_reconciliation_summary", "") or ""),
            "conflicts": list(_safe_get(cr_out, "conflicts_detected", []) or []),
        },
        "clinical_alignment": {
            "verdict": _enum_str(_safe_get(clin_out, "verdict", "N/A")),
            "confidence": float(_safe_get(clin_out, "confidence", 1.0) or 1.0) if _safe_get(clin_out, "confidence") is not None else None,
            "total_lines_assessed": int(_safe_get(clin_out, "total_lines_assessed", 0) or 0),
            "lines_fully_supported": int(_safe_get(clin_out, "lines_fully_supported_count", 0) or 0),
            "lines_indeterminate": int(_safe_get(clin_out, "lines_indeterminate_count", 0) or 0),
            "lines_failed_necessity": int(_safe_get(clin_out, "lines_failed_necessity_count", 0) or 0),
            "lines_contradicted": int(_safe_get(clin_out, "lines_contradicted_count", 0) or 0),
            "clinical_documents_present": list(_safe_get(clin_out, "clinical_documents_present", []) or []),
            "clinical_summary": str(_safe_get(clin_out, "clinical_summary", "")),
            "line_assessments": formatted_clinical_lines,
            "bed_day_reconciliation": bdr_data,
            "blocking_discrepancies": list(_safe_get(clin_out, "blocking_discrepancies", []) or []),
            "clinical_discrepancies": [
                _safe_get(l, "discrepancy_description", "")
                for l in line_assessments
                if _safe_get(l, "discrepancy_description")
            ],
        },
    }

    system_prompt = (
        "You are MediShield's Lead Clinical & Healthcare Adjudication Auditor.\n"
        "Your task is to produce an authoritative, comprehensive, and compassionate clinical and financial "
        "decision explanation of the already-decided claims adjudication outcome for the clinical operations review console.\n\n"
        "STRICT VOCABULARY & HEALTHCARE PRESENTATION DIRECTIVES:\n"
        "1. NEVER use internal architectural codes, engineering acronyms, or specification jargon:\n"
        "   - Do NOT use 'ADR', 'ADR-015', 'ADR-011', 'ADR-002', or similar acronyms.\n"
        "   - Do NOT use internal priority codes like 'rule P3', 'RULE-P3', 'rule P1', 'P5', 'P6', or 'P-rules'.\n"
        "   - Do NOT use the word 'ambiguity' or 'ambiguities'.\n"
        "   - Instead, always use clear, professional medical and claims adjudication terms:\n"
        "     * Instead of 'rule P3' or 'ambiguity', state 'escalated for human review due to information discrepancy', 'conflicting clinical records', 'unverified documentation', or 'clarification required'.\n"
        "     * Instead of 'rule P6', state 'automated policy approval'.\n"
        "     * Instead of 'rule P5' or 'ADR-002', state 'policy exclusion' or 'ineligibility criterion'.\n"
        "     * Instead of 'ADR-015', state 'Clinical Alignment & Medical Necessity Evaluation'.\n"
        "2. You MUST NEVER change, contradict, or override the binding decision provided.\n"
        "3. Structure your output in strict, well-formatted Markdown with the following clear sections:\n"
        "   - ### Executive Decision Summary (decision badge, policy rule description, calibrated confidence, plain English summary)\n"
        "   - #### 1. Member Identity & KYC Verification\n"
        "   - #### 2. Claims Financial & Line-Item Validation\n"
        "   - #### 3. Policy Coverage & Benefit Grounding\n"
        "   - #### 4. Fraud Risk & Anomaly Assessment\n"
        "   - #### 5. Cross-Document Consistency & Regulatory Attestation (Doctor/Patient signatures, NPI, PA, dates)\n"
        "   - #### 6. Clinical Alignment & Medical Necessity\n"
        "   - #### 7. Actionable Guidance for Human Reviewer / Adjuster\n"
        "4. STRICT REGULATORY ATTESTATION DIRECTIVES FOR SECTION 5:\n"
        "   - Accurately report the verified/unverified status of Doctor Attestation and Patient Signature from the consistency snapshot.\n"
        "   - If 'doctor_signature_verified' is false, you MUST explicitly state that the physician signature or clinical attestation is missing, blank, or unverified.\n"
        "   - If 'patient_signature_verified' is false, you MUST explicitly state that the patient signature or assignment of benefits is missing, blank, or unverified.\n"
        "   - NEVER state that doctor or patient signatures are verified or present if the consistency snapshot reports them as false.\n"
        "5. EXPLICIT CLINICAL ALIGNMENT DIRECTIVES FOR SECTION 6:\n"
        "   - DO NOT merely state high-level numbers, scores, or counts (e.g. avoid '3 lines evaluated, 100% score').\n"
        "   - Provide comprehensive, medical-grade clinical rationale for why the procedures were or were not justified:\n"
        "     * Medical Indication Rationale: For each billed procedure code (CPT/HCPCS), explain how the primary diagnosis (chief complaint) and any secondary diagnoses (comorbidities or inpatient complications) establish medical indication. Explain the underlying clinical rationale connecting the diagnosis to the surgical, diagnostic, or medical service.\n"
        "     * Performance & Evidence Grounding: State the exact clinical documentation (e.g. operative report, discharge summary hospital course, lab panel, diagnostic imaging) corroborating that the service was performed (not merely ordered).\n"
        "     * Clinical Practice Guidelines: Cite the governing clinical guidelines (e.g. ACS, AHA, AMA/CPT, ACC, CMS LCD/NCD) evaluated and explain how the patient met (or failed) standard of care necessity criteria.\n"
        "     * Inpatient Bed-Day & Stay Reconciliation: If room-and-board bed days were billed, detail the admission and discharge timeline, calendar stay duration vs billed units, and why the stay length is clinically justified under facility billing conventions (e.g. midnight census or calendar day span).\n"
        "     * Clinical Discrepancies & Adjuster Guidance: If any line item is indeterminate, unperformed, unindicated, or lacks guideline evidence, detail the exact clinical gap and what clinical documentation is required.\n"
        "5. Keep Sections 1-5 and 7 concise and factual (2-3 bullet points each), while providing full clinical depth in Section 6.\n"
        "6. Respond with a JSON object containing a single key: 'detailed_explanation'."
    )

    try:
        prompt_text = (
            f"Adjudication Evidence Snapshot:\n```json\n{json.dumps(prompt_payload, indent=2)}\n```\n\n"
            "Generate the detailed clinical explanation in JSON format."
        )
        response = client.generate_json(
            prompt=prompt_text,
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=3500,
        )
        if isinstance(response, dict) and "detailed_explanation" in response:
            explanation = response["detailed_explanation"]
            if isinstance(explanation, str) and len(explanation.strip()) > 50:
                logger.info("Successfully synthesized LLM decision explanation for case %s", context.case_id)
                return explanation.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Groq LLM explanation generation failed for case %s: %s; falling back to deterministic generator.",
            context.case_id,
            exc,
        )

    # Graceful fallback to deterministic explanation
    return generate_deterministic_explanation(context, decision_output)

