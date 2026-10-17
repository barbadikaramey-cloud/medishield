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

    lines = [
        f"### {verdict_emoji} Adjudication Decision: {decision_output.decision.value}",
        (
            f"**Matched Rule:** `{decision_output.rule_id_matched}` &bull; "
            f"**Confidence:** `{decision_output.decision_confidence * 100:.1f}%`"
        ),
    ]

    if decision_output.escalation_sub_reason:
        lines.append(f"**Escalation Sub-Reason:** `{decision_output.escalation_sub_reason.value}`")

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
            m_val = mem_id.value if hasattr(mem_id, "value") else str(mem_id)
            lines.append(f"- **Resolved Member ID:** `{m_val}`")
        if context.security_tamper_score > 0:
            lines.append(f"- **Document Integrity / Tamper Score:** `{context.security_tamper_score * 100:.1f}%` (Threshold: 50%)")
        flags = _safe_get(kyc, "flags", [])
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
        claim_amt = _safe_get(claims, "claim_amount")
        if claim_amt is not None:
            claim_val = claim_amt.value if hasattr(claim_amt, "value") else claim_amt
            try:
                lines.append(f"- **Header Claim Total:** `${float(claim_val):,.2f}`")
            except (ValueError, TypeError):
                lines.append(f"- **Header Claim Total:** `${claim_val}`")
        math_valid = _safe_get(claims, "line_items_sum_valid", True)
        lines.append(f"- **Line Items Arithmetic:** {'✅ Exact match (line totals equal header charge)' if math_valid else '❌ Discrepancy detected between line sums and declared total'}")
        service_lines = _safe_get(claims, "service_lines") or _safe_get(claims, "line_items") or []
        if service_lines:
            lines.append(f"- **Itemized Service Lines:** {len(service_lines)} lines parsed")
            for idx, item in enumerate(service_lines[:5], 1):
                cpt = _safe_get(item, "hcpcs") or _safe_get(item, "cpt_code", "UNKNOWN")
                cpt_val = cpt.value if hasattr(cpt, "value") else str(cpt)
                amt = _safe_get(item, "amount") or _safe_get(item, "total_charge", 0.0)
                amt_val = amt.value if hasattr(amt, "value") else amt
                try:
                    lines.append(f"  - Line {idx}: CPT/HCPCS `{cpt_val}` | Charge: `${float(amt_val):,.2f}`")
                except (ValueError, TypeError):
                    lines.append(f"  - Line {idx}: CPT/HCPCS `{cpt_val}` | Charge: `${amt_val}`")
            if len(service_lines) > 5:
                lines.append(f"  - *(and {len(service_lines) - 5} additional line items)*")
        cpt_codes = _safe_get(claims, "cpt_codes", [])
        if cpt_codes:
            cpt_strs = [c.value if hasattr(c, "value") else str(c) for c in cpt_codes]
            lines.append(f"- **Extracted CPT / HCPCS Codes:** {', '.join(cpt_strs)}")
        icd10_codes = _safe_get(claims, "icd10_codes", [])
        if icd10_codes:
            icd_strs = [c.value if hasattr(c, "value") else str(c) for c in icd10_codes]
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
        cov_pct = float(_safe_get(pol, "coverage_percentage", 100.0) or 100.0)
        lines.append(f"- **Coverage Percentage:** `{cov_pct:.1f}%`")
        policy_id = _safe_get(pol, "policy_id")
        if policy_id:
            lines.append(f"- **Active Policy:** `{policy_id}`")
        effective_date_valid = _safe_get(pol, "service_date_within_effective_period")
        if effective_date_valid is None:
            effective_date_valid = _safe_get(pol, "effective_on_service_date", True)
        if effective_date_valid:
            lines.append("- **Service Date Validity:** ✅ Date of service falls within active enrollment window")
        else:
            lines.append("- **Service Date Validity:** ❌ Date of service falls outside policy enrollment window")
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
        fr_score = float(_safe_get(fr, "fraud_score", 0.0) or 0.0)
        lines.append(f"- **Fraud Risk Tier:** `{r_str}` | **Deterministic Fraud Score:** `{fr_score * 100:.1f}%`")
        fr_verdict = _safe_get(fr, "verdict")
        fr_v_str = fr_verdict.value if hasattr(fr_verdict, "value") else str(fr_verdict or "PASS")
        fr_conf = float(_safe_get(fr, "confidence", 1.0) or 1.0)
        lines.append(f"- **Fraud Verdict:** `{fr_v_str}` (Confidence: {fr_conf * 100:.1f}%)")
        anomalies = _safe_get(fr, "anomalies", [])
        if anomalies:
            lines.append(f"- **Detected Anomalies:** {', '.join(anomalies)}")
        else:
            lines.append("- **Detected Anomalies:** None (Standard billing patterns verified)")
        feature_scores: dict[str, float] = {}
        features = _safe_get(fr, "features")
        if features:
            feature_scores = {f.feature_name: f.normalized_score for f in features if hasattr(f, "feature_name")}
        elif _safe_get(fr, "feature_scores"):
            feature_scores = _safe_get(fr, "feature_scores")
        if feature_scores:
            top_features = sorted(feature_scores.items(), key=lambda x: x[1], reverse=True)[:3]
            feat_str = ", ".join(f"{k}: {v:.2f}" for k, v in top_features if v > 0)
            if feat_str:
                lines.append(f"- **Key Contributing Factors:** {feat_str}")
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
        
        doc_sig = _safe_get(cr, "doctor_signature_verified", True)
        lines.append(f"- **Doctor Signature / Attestation:** {'✅ Verified on clinical record' if doc_sig else '❌ Missing or unverified on clinical record'}")

        pat_sig = _safe_get(cr, "patient_signature_verified", True)
        lines.append(f"- **Patient Signature / Assignment:** {'✅ Verified on claim documentation' if pat_sig else '❌ Missing patient signature or assignment of benefits'}")

        npi_ver = _safe_get(cr, "npi_verified", True)
        lines.append(f"- **Rendering Provider NPI:** {'✅ Verified valid 10-digit NPI' if npi_ver else '❌ Missing or invalid rendering provider NPI'}")

        pa_ver = _safe_get(cr, "prior_authorization_verified", True)
        lines.append(f"- **Prior Authorization:** {'✅ Verified or not required' if pa_ver else '❌ Missing mandatory prior authorization for major procedure'}")

        proc_ver = _safe_get(cr, "procedure_codes_verified", True)
        if not proc_ver:
            uncoded_amt = float(_safe_get(cr, "unvalidated_charges_total", 0.0) or 0.0)
            lines.append(f"- **Procedure Code Validation:** ❌ Unvalidated line charges (${uncoded_amt:,.2f}) lack authoritative CPT/HCPCS procedure code")
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
        "#### 6. Clinical Alignment & Medical Necessity (ADR-015)",
    ])

    clin = getattr(context, "clinical_output", None)
    if clin:
        c_verdict = _safe_get(clin, "verdict")
        c_v_str = c_verdict.value if hasattr(c_verdict, "value") else str(c_verdict or "PASS")
        c_conf = _safe_get(clin, "confidence")
        conf_str = f"{float(c_conf) * 100:.1f}%" if c_conf is not None else "Uncalibrated"
        lines.append(f"- **Clinical Alignment Verdict:** `{c_v_str}` (Confidence: {conf_str})")
        lines.append(f"- **Line Items Evaluated:** {getattr(clin, 'total_lines_assessed', 0)} total | {getattr(clin, 'lines_fully_supported_count', 0)} fully supported")
        if getattr(clin, "lines_indeterminate_count", 0) > 0:
            lines.append(f"- **Indeterminate Lines:** {getattr(clin, 'lines_indeterminate_count', 0)} line item(s) require guideline review")
        if getattr(clin, "lines_failed_necessity_count", 0) > 0:
            lines.append(f"- **Necessity Gaps:** {getattr(clin, 'lines_failed_necessity_count', 0)} line item(s) unfulfilled under standard medical guidelines")
        discrepancies = _safe_get(clin, "blocking_discrepancies", [])
        line_assessments = getattr(clin, "line_assessments", []) or []
        all_discs = list(discrepancies)
        for la in line_assessments:
            desc = getattr(la, "discrepancy_description", None)
            if desc and desc not in all_discs:
                all_discs.append(desc)

        if all_discs:
            lines.append("- **Clinical Discrepancies & Guideline Findings:**")
            for d in all_discs:
                lines.append(f"  - ⚠️ {d}")

        c_summary = _safe_get(clin, "clinical_summary")
        if c_summary:
            lines.append(f"- **Clinical Assessment Summary:** {c_summary}")
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
            "policy coverage ambiguities, or fraud risk indicators) and apply human review discretion."
        )

    return "\n".join(lines)


def generate_decision_explanation(
    context: DecisionInputContext,
    decision_output: OrchestratorOutput,
    llm_client: GroqLLMClient | None = None,
) -> str:
    """
    Generates an enriched clinical and policy explanation for the decision.
    Tries the Groq LLM client if configured and available; gracefully falls back
    to deterministic synthesis upon failure or offline environment (Rule 11, Rule 14).
    """
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
            "verdict": str(_safe_get(kyc_out, "verdict", "N/A")),
            "member_matched": bool(_safe_get(kyc_out, "member_db_matched", False)),
            "member_id": str(_safe_get(kyc_out, "member_id", "")),
            "tamper_score": getattr(context, "security_tamper_score", 0.0),
            "flags": list(_safe_get(kyc_out, "flags", [])),
            "reasoning": str(_safe_get(kyc_out, "reasoning_summary", "")),
        },
        "claims": {
            "verdict": str(_safe_get(claims_out, "verdict", "N/A")),
            "claim_amount": claim_val,
            "line_items_valid": bool(_safe_get(claims_out, "line_items_sum_valid", False)),
            "cpt_codes": [str(c.value if hasattr(c, "value") else c) for c in _safe_get(claims_out, "cpt_codes", [])],
            "icd10_codes": [str(c.value if hasattr(c, "value") else c) for c in _safe_get(claims_out, "icd10_codes", [])],
            "validation_errors": list(_safe_get(claims_out, "validation_errors", [])),
        },
        "policy": {
            "verdict": str(_safe_get(pol_out, "verdict", "N/A")),
            "coverage_percentage": float(_safe_get(pol_out, "coverage_percentage", 0.0) or 0.0),
            "policy_id": str(_safe_get(pol_out, "policy_id", "")),
            "service_date_valid": bool(_safe_get(pol_out, "service_date_within_effective_period", _safe_get(pol_out, "effective_on_service_date", True))),
            "exclusion_verified": bool(_safe_get(pol_out, "exclusion_clause_verified", False)),
            "exclusion_clause": str(_safe_get(pol_out, "exclusion_clause_id", "")),
            "cited_clauses": [str(getattr(c, "clause_title", str(c))) for c in _safe_get(pol_out, "cited_clauses", [])],
            "reasoning": str(_safe_get(pol_out, "reasoning_summary", "")),
        },
        "fraud": {
            "verdict": str(_safe_get(fraud_out, "verdict", "PASS")),
            "fraud_score": float(_safe_get(fraud_out, "fraud_score", 0.0) or 0.0),
            "risk_level": str(_safe_get(fraud_out, "risk_level", "LOW")),
            "anomalies": list(_safe_get(fraud_out, "anomalies", [])),
        },
        "consistency": {
            "is_consistent": bool(_safe_get(cr_out, "is_consistent", True)),
            "doctor_signature_verified": bool(_safe_get(cr_out, "doctor_signature_verified", True)),
            "patient_signature_verified": bool(_safe_get(cr_out, "patient_signature_verified", True)),
            "npi_verified": bool(_safe_get(cr_out, "npi_verified", True)),
            "prior_authorization_verified": bool(_safe_get(cr_out, "prior_authorization_verified", True)),
            "specific_escalation_reasons": list(_safe_get(cr_out, "specific_escalation_reasons", []) or []),
            "date_reconciliation_summary": str(_safe_get(cr_out, "date_reconciliation_summary", "") or ""),
            "conflicts": list(_safe_get(cr_out, "conflicts_detected", []) or []),
        },
        "clinical_alignment": {
            "verdict": str(_safe_get(clin_out, "verdict", "N/A")),
            "confidence": float(_safe_get(clin_out, "confidence", 1.0) or 1.0),
            "lines_assessed": int(_safe_get(clin_out, "total_lines_assessed", 0)),
            "lines_supported": int(_safe_get(clin_out, "lines_fully_supported_count", 0)),
            "clinical_summary": str(_safe_get(clin_out, "clinical_summary", "")),
            "blocking_discrepancies": list(_safe_get(clin_out, "blocking_discrepancies", []) or []),
            "clinical_discrepancies": [
                getattr(l, "discrepancy_description", "")
                for l in getattr(clin_out, "line_assessments", [])
                if getattr(l, "discrepancy_description", None)
            ],
        },
    }

    system_prompt = (
        "You are MediShield's Lead Clinical & Healthcare Adjudication Auditor.\n"
        "Your task is to produce a detailed, authoritative, and compassionate explanation of the "
        "already-decided claims adjudication outcome for the clinical operations review console.\n\n"
        "CRITICAL DIRECTIVES:\n"
        "1. You MUST NEVER change, contradict, or override the binding decision provided.\n"
        "2. Structure your output in strict, well-formatted Markdown with the following clear sections:\n"
        "   - ### Executive Decision Summary (decision badge, rule ID, confidence, plain English summary)\n"
        "   - #### 1. Member Identity & KYC Verification\n"
        "   - #### 2. Claims Financial & Line-Item Validation\n"
        "   - #### 3. Policy Coverage & Benefit Grounding\n"
        "   - #### 4. Fraud Risk & Anomaly Assessment\n"
        "   - #### 5. Cross-Document Consistency & Regulatory Attestation (Doctor/Patient signatures, NPI, PA, dates)\n"
        "   - #### 6. Clinical Alignment & Medical Necessity (ADR-015)\n"
        "   - #### 7. Actionable Guidance for Human Reviewer / Adjuster\n"
        "3. Explicitly cite any billed amounts, dollar totals, CPT codes, policy clauses, signatures, or anomalies from the data.\n"
        "4. Keep each section clear and concise (2-3 concise bullet points per section) so the full JSON object closes well within token limits.\n"
        "5. Respond with a JSON object containing a single key: 'detailed_explanation'."
    )

    try:
        prompt_text = f"Adjudication Evidence Snapshot:\n```json\n{json.dumps(prompt_payload, indent=2)}\n```\n\nGenerate the detailed clinical explanation in JSON format."
        response = client.generate_json(
            prompt=prompt_text,
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=2500,
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

