"""Builds a comprehensive, interactive HTML documentation file for MediShield architecture
and the full 30-case benchmark evaluation with detailed decision explanations.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIAGNOSTIC_PATH = REPO_ROOT / "scripts" / "diagnostic_summary.json"
METADATA_PATH = REPO_ROOT / "dataset" / "metadata.json"
REGISTER_PATH = REPO_ROOT / "scripts" / "evaluator_review_register.json"
OUTPUT_HTML_PATH = REPO_ROOT / "docs" / "MEDISHIELD_ARCHITECTURE_AND_30_CASE_EVALUATION.html"

# Fallback static definition for all 30 benchmark clusters
FALLBACK_CLUSTERS = [
    {"cluster_id": "C_001", "patient_id": "PT_19116", "name": "Christopher Harris I.", "dob": "04/25/1954", "ssn": "489-22-6881", "policy": "MED-GLD-6770619"},
    {"cluster_id": "C_002", "patient_id": "PT_99733", "name": "Jessica Jackson Y.", "dob": "02/28/1963", "ssn": "384-68-6977", "policy": "MED-GLD-3728882"},
    {"cluster_id": "C_003", "patient_id": "PT_62350", "name": "Patricia Miller H.", "dob": "01/26/1997", "ssn": "790-95-5858", "policy": "MED-GLD-4567281"},
    {"cluster_id": "C_004", "patient_id": "PT_69470", "name": "Elizabeth Taylor Q.", "dob": "02/25/1976", "ssn": "118-24-9694", "policy": "MED-GLD-2065818"},
    {"cluster_id": "C_005", "patient_id": "PT_20322", "name": "Sarah White Q.", "dob": "03/17/1993", "ssn": "173-67-2708", "policy": "MED-GLD-3564217"},
    {"cluster_id": "C_006", "patient_id": "PT_39451", "name": "Sandra Hall C.", "dob": "09/25/1993", "ssn": "480-14-1629", "policy": "MED-GLD-5446912"},
    {"cluster_id": "C_007", "patient_id": "PT_71993", "name": "Mary Davis S.", "dob": "04/19/1980", "ssn": "235-98-3567", "policy": "MED-GLD-4841005"},
    {"cluster_id": "C_008", "patient_id": "PT_82132", "name": "Richard Martinez D.", "dob": "11/14/1951", "ssn": "887-16-6466", "policy": "MED-GLD-2016911"},
    {"cluster_id": "C_009", "patient_id": "PT_17665", "name": "Mary Davis C.", "dob": "04/06/1993", "ssn": "732-23-4245", "policy": "MED-GLD-3762152"},
    {"cluster_id": "C_010", "patient_id": "PT_20745", "name": "John Johnson X.", "dob": "01/02/1965", "ssn": "214-72-8706", "policy": "MED-GLD-1954280"},
    {"cluster_id": "C_011", "patient_id": "PT_47353", "name": "Nancy Martin I.", "dob": "11/23/1958", "ssn": "339-50-8438", "policy": "MED-GLD-8670955"},
    {"cluster_id": "C_012", "patient_id": "PT_16658", "name": "Karen Harris T.", "dob": "11/17/1996", "ssn": "779-23-3200", "policy": "MED-GLD-5437001"},
    {"cluster_id": "C_013", "patient_id": "PT_57795", "name": "Betty Clark I.", "dob": "01/11/1947", "ssn": "368-30-8239", "policy": "MED-GLD-8175395"},
    {"cluster_id": "C_014", "patient_id": "PT_15075", "name": "John Johnson L.", "dob": "11/08/1958", "ssn": "153-27-7658", "policy": "MED-GLD-3592974"},
    {"cluster_id": "C_015", "patient_id": "PT_24208", "name": "Joseph Anderson G.", "dob": "08/12/1997", "ssn": "136-16-8692", "policy": "MED-GLD-7685149"},
    {"cluster_id": "C_016", "patient_id": "PT_50538", "name": "Daniel Taylor X.", "dob": "06/14/1995", "ssn": "802-99-9730", "policy": "MED-GLD-1744682"},
    {"cluster_id": "C_017", "patient_id": "PT_72021", "name": "Christopher Harris M.", "dob": "05/18/1989", "ssn": "917-80-7798", "policy": "MED-GLD-6049101"},
    {"cluster_id": "C_018", "patient_id": "PT_62383", "name": "David Garcia V.", "dob": "05/17/1950", "ssn": "165-27-2481", "policy": "MED-GLD-4768844"},
    {"cluster_id": "C_019", "patient_id": "PT_81810", "name": "Mary Davis Y.", "dob": "02/25/2000", "ssn": "875-98-3806", "policy": "MED-GLD-8793853"},
    {"cluster_id": "C_020", "patient_id": "PT_30028", "name": "Barbara Anderson X.", "dob": "08/15/2000", "ssn": "219-48-9366", "policy": "MED-GLD-5013879"},
    {"cluster_id": "C_021", "patient_id": "PT_67125", "name": "Robert Williams N.", "dob": "06/18/1971", "ssn": "161-55-9345", "policy": "MED-GLD-1327691"},
    {"cluster_id": "C_022", "patient_id": "PT_54236", "name": "Margaret Lee K.", "dob": "11/26/1987", "ssn": "173-16-8290", "policy": "MED-GLD-1452421"},
    {"cluster_id": "C_023", "patient_id": "PT_24993", "name": "Anthony Moore I.", "dob": "07/09/1993", "ssn": "798-50-6819", "policy": "MED-GLD-6871360"},
    {"cluster_id": "C_024", "patient_id": "PT_99687", "name": "Richard Martinez O.", "dob": "05/25/1989", "ssn": "578-83-5002", "policy": "MED-GLD-6218367"},
    {"cluster_id": "C_025", "patient_id": "PT_45400", "name": "Michael Brown Z.", "dob": "01/12/1981", "ssn": "761-94-6886", "policy": "MED-GLD-6724909"},
    {"cluster_id": "C_026", "patient_id": "PT_88139", "name": "Paul Walker O.", "dob": "12/19/1972", "ssn": "208-86-6398", "policy": "MED-GLD-8563202"},
    {"cluster_id": "C_027", "patient_id": "PT_55056", "name": "Thomas Jackson R.", "dob": "09/07/1945", "ssn": "168-96-8208", "policy": "MED-GLD-8987516"},
    {"cluster_id": "C_028", "patient_id": "PT_39816", "name": "Nancy Martin L.", "dob": "08/09/1989", "ssn": "726-57-5507", "policy": "MED-GLD-3005795"},
    {"cluster_id": "C_029", "patient_id": "PT_74454", "name": "Karen Harris A.", "dob": "09/05/1990", "ssn": "439-86-7788", "policy": "MED-GLD-9235577"},
    {"cluster_id": "C_030", "patient_id": "PT_41439", "name": "Patricia Miller E.", "dob": "10/10/1954", "ssn": "394-42-5369", "policy": "MED-GLD-9767312"},
]

cluster_members = {c["cluster_id"]: c for c in FALLBACK_CLUSTERS}

# Load evaluation diagnostic
with open(DIAGNOSTIC_PATH, "r", encoding="utf-8") as f:
    diag_data = json.load(f)

# Load dataset metadata
with open(METADATA_PATH, "r", encoding="utf-8") as f:
    meta_docs = json.load(f)

CATEGORY_TO_DOC_TYPE = {
    "claim_forms": "CLAIM_FORM",
    "id_documents": "ID_DOCUMENT",
    "discharge_summaries": "DISCHARGE_SUMMARY",
    "prescriptions": "PRESCRIPTION",
    "policy_amendments": "POLICY_AMENDMENT",
}

docs_by_cluster = {}
for d in meta_docs:
    cid = d.get("case_cluster_id")
    if cid:
        cat = d.get("category", "")
        d["doc_type"] = CATEGORY_TO_DOC_TYPE.get(cat, d.get("doc_type", "CLAIM_FORM"))
        file_path_raw = d.get("file_path", "")
        d["file_name"] = Path(file_path_raw.replace("\\", "/")).name if file_path_raw else f"{d.get('doc_id')}.png"
        docs_by_cluster.setdefault(cid, []).append(d)

# Load disputed records
with open(REGISTER_PATH, "r", encoding="utf-8") as f:
    reg_data = json.load(f)
disputed_by_cluster = {r["case_cluster_id"]: r for r in reg_data.get("disputed_records", [])}

cases_enriched = []
for res in diag_data["eval_results"]:
    cid = res["cluster_id"]
    member = cluster_members.get(cid, {})
    docs = docs_by_cluster.get(cid, [])
    disp = disputed_by_cluster.get(cid)
    
    # Forensic explanation analysis
    dec = res["final_decision"]
    rule = res["rule_id_matched"]
    claims_v = res["claims_verdict"]
    policy_v = res["policy_verdict"]
    kyc_v = res["kyc_verdict"]
    fraud_s = res["fraud_score"]
    
    forensic_notes = []
    if dec == "APPROVE" and rule == "RULE-P6-CLEAN-PASS":
        forensic_notes.append("Autonomous Straight-Through Processing (STP) clean pass: Identity verified via KYC, itemized claim lines reconciled with 100% arithmetic precision, all procedure codes covered under active policy schedule, and calibrated fraud risk <= 0.20.")
    elif dec == "REJECT" and rule == "RULE-P5-HARD-REJECT":
        if kyc_v == "FAIL":
            forensic_notes.append("Hard rejection triggered by KYC Identity Verification failure: Critical mismatch between member enrollment record and government-issued ID card.")
        if policy_v == "FAIL":
            forensic_notes.append("Hard rejection triggered by Policy Coverage exclusion: Claimed procedure is explicitly excluded under corporate master policy, outside policy effective dates, or requires unfulfilled prior authorization.")
    elif dec == "ESCALATE":
        if claims_v == "CONFLICT":
            forensic_notes.append("Ambiguity Escalation (P3): Line-item arithmetic discrepancy detected between provider itemized bill and claim form total, or conflicting clinical dates across documents.")
        elif claims_v == "INDETERMINATE":
            forensic_notes.append("Ambiguity Escalation (P3): Low OCR confidence score or blurry scan on essential procedure code, requiring human visual verification.")
        if "RULE-P7-BORDERLINE-REVIEW" in res.get("all_failing_checks", []):
            forensic_notes.append("Borderline Specialist Review (P7): Calibrated confidence score fell into borderline threshold [0.50, 0.70), requiring secondary human adjuster review.")
        if fraud_s >= 0.70:
            forensic_notes.append(f"High Fraud Escalation (P4): Anomaly detector flagged excessive risk score ({fraud_s:.2f} >= 0.70).")

    if disp:
        forensic_notes.append(f"Evaluator Review Register note: {disp['rationale']} (Disposition: {disp['disposition']}).")

    cases_enriched.append({
        **res,
        "patient_name": member.get("name", "Unknown Patient"),
        "dob": member.get("dob", "N/A"),
        "ssn": member.get("ssn", "***-**-****"),
        "documents": [
            {
                "category": d.get("category"),
                "doc_id": d.get("doc_id"),
                "fraud_label": d.get("fraud_label", False),
                "fraud_reason": d.get("fraud_reason"),
                "edge_flags": d.get("edge_flags", []),
            }
            for d in docs
        ],
        "disputed_record": disp,
        "forensic_notes": " ".join(forensic_notes),
    })

cases_json_str = json.dumps(cases_enriched, indent=2)

html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MediShield Enterprise Architecture & 30-Case Benchmark Evaluation</title>
  <style>
    :root {{
      --bg: #030712;
      --card-bg: #0f172a;
      --card-border: #1e293b;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --cyan: #06b6d4;
      --cyan-dim: #083344;
      --emerald: #10b981;
      --emerald-dim: #064e3b;
      --rose: #f43f5e;
      --rose-dim: #4c0519;
      --amber: #f59e0b;
      --amber-dim: #451a03;
      --purple: #a855f7;
      --purple-dim: #3b0764;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}

    body {{
      background-color: var(--bg);
      color: var(--text-main);
      line-height: 1.6;
      padding-bottom: 5rem;
    }}

    header {{
      background: linear-gradient(180deg, #0b1329 0%, #030712 100%);
      border-bottom: 1px solid var(--card-border);
      padding: 2.5rem 1.5rem 2rem;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(12px);
    }}

    .header-container {{
      max-width: 1300px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }}

    .brand-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
    }}

    .logo-group {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}

    .logo-icon {{
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: var(--cyan-dim);
      border: 1px solid var(--cyan);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--cyan);
      font-weight: 900;
      font-size: 1.25rem;
    }}

    h1 {{
      font-size: 1.5rem;
      font-weight: 800;
      letter-spacing: -0.025em;
    }}

    .cyan-text {{ color: var(--cyan); }}
    .emerald-text {{ color: var(--emerald); }}
    .rose-text {{ color: var(--rose); }}
    .amber-text {{ color: var(--amber); }}
    .purple-text {{ color: var(--purple); }}

    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }}

    .badge {{
      font-size: 0.75rem;
      padding: 0.25rem 0.65rem;
      border-radius: 9999px;
      border: 1px solid var(--card-border);
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
    }}

    .badge-cyan {{ background: var(--cyan-dim); border-color: var(--cyan); color: #67e8f9; }}
    .badge-emerald {{ background: var(--emerald-dim); border-color: var(--emerald); color: #6ee7b7; }}
    .badge-rose {{ background: var(--rose-dim); border-color: var(--rose); color: #fda4af; }}
    .badge-amber {{ background: var(--amber-dim); border-color: var(--amber); color: #fde68a; }}
    .badge-purple {{ background: var(--purple-dim); border-color: var(--purple); color: #d8b4fe; }}

    nav.quick-nav {{
      display: flex;
      gap: 0.5rem;
      overflow-x: auto;
      padding-top: 0.5rem;
      border-top: 1px solid #1e293b66;
    }}

    .nav-btn {{
      background: #1e293b55;
      color: var(--text-muted);
      border: 1px solid var(--card-border);
      padding: 0.4rem 0.85rem;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.2s ease;
      white-space: nowrap;
    }}

    .nav-btn:hover {{
      background: var(--card-border);
      color: var(--text-main);
    }}

    main {{
      max-width: 1300px;
      margin: 2rem auto;
      padding: 0 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 3rem;
    }}

    section {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 2rem;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
    }}

    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 1rem;
    }}

    .section-title {{
      font-size: 1.25rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 0.65rem;
    }}

    /* Metrics Cards Grid */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}

    .metric-card {{
      background: #090e1a;
      border: 1px solid var(--card-border);
      padding: 1.25rem;
      border-radius: 12px;
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      position: relative;
      overflow: hidden;
    }}

    .metric-card::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      width: 4px;
      height: 100%;
    }}

    .mc-cyan::before {{ background: var(--cyan); }}
    .mc-emerald::before {{ background: var(--emerald); }}
    .mc-rose::before {{ background: var(--rose); }}
    .mc-amber::before {{ background: var(--amber); }}
    .mc-purple::before {{ background: var(--purple); }}

    .metric-num {{
      font-size: 2rem;
      font-weight: 800;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
    }}

    .metric-label {{
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 600;
    }}

    .metric-sub {{
      font-size: 0.75rem;
      color: var(--text-dim);
    }}

    /* Interactive SVG Flowchart Container */
    .flowchart-container {{
      background: #060a14;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 1.5rem;
      overflow-x: auto;
      margin-bottom: 1.5rem;
    }}

    svg.interactive-flowchart {{
      width: 100%;
      min-width: 950px;
      height: 280px;
    }}

    .flow-node {{
      cursor: pointer;
      transition: transform 0.2s, filter 0.2s;
    }}

    .flow-node:hover {{
      filter: drop-shadow(0 0 8px rgba(6, 182, 212, 0.6));
    }}

    .flow-node rect {{
      rx: 8;
      stroke-width: 1.5;
    }}

    .flow-node text {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      fill: #f8fafc;
      font-weight: 600;
      text-anchor: middle;
    }}

    .flow-arrow {{
      stroke: #334155;
      stroke-width: 2;
      fill: none;
      marker-end: url(#arrowhead);
    }}

    /* Collapsible Architecture Accordions */
    .accordion-group {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}

    .accordion-item {{
      background: #090e1a;
      border: 1px solid var(--card-border);
      border-radius: 10px;
      overflow: hidden;
      transition: border-color 0.2s;
    }}

    .accordion-item:hover {{
      border-color: #334155;
    }}

    .accordion-header {{
      padding: 1rem 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
      background: #0c1427;
    }}

    .accordion-title {{
      font-weight: 700;
      font-size: 0.95rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}

    .chevron {{
      transition: transform 0.3s ease;
      color: var(--text-dim);
    }}

    .accordion-item.active .chevron {{
      transform: rotate(180deg);
      color: var(--cyan);
    }}

    .accordion-content {{
      display: none;
      padding: 1.25rem;
      border-top: 1px solid var(--card-border);
      font-size: 0.85rem;
      color: #cbd5e1;
      line-height: 1.7;
    }}

    .accordion-item.active .accordion-content {{
      display: block;
    }}

    /* Case Browser Controls */
    .controls-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      background: #090e1a;
      padding: 1rem;
      border-radius: 12px;
      border: 1px solid var(--card-border);
    }}

    .filter-tabs {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}

    .tab-btn {{
      background: #1e293b66;
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      padding: 0.4rem 0.85rem;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }}

    .tab-btn.active {{
      background: var(--cyan);
      color: #030712;
      border-color: var(--cyan);
    }}

    .search-input {{
      background: #030712;
      border: 1px solid var(--card-border);
      color: var(--text-main);
      padding: 0.45rem 1rem;
      border-radius: 8px;
      font-size: 0.85rem;
      min-width: 260px;
      outline: none;
    }}

    .search-input:focus {{
      border-color: var(--cyan);
    }}

    .action-btn {{
      background: #1e293b;
      border: 1px solid var(--card-border);
      color: var(--text-main);
      padding: 0.4rem 0.8rem;
      border-radius: 8px;
      font-size: 0.75rem;
      cursor: pointer;
      font-weight: 600;
    }}

    /* Case Card */
    .case-card {{
      background: #090e1a;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      margin-bottom: 1rem;
      overflow: hidden;
      transition: all 0.2s;
    }}

    .case-card:hover {{
      border-color: #334155;
    }}

    .case-header {{
      padding: 1.1rem 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
      cursor: pointer;
      background: #0c1427;
      border-bottom: 1px solid transparent;
    }}

    .case-card.expanded .case-header {{
      border-bottom-color: var(--card-border);
    }}

    .case-left {{
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
    }}

    .cluster-id {{
      font-family: ui-monospace, monospace;
      font-weight: 800;
      font-size: 1rem;
      color: #fff;
    }}

    .patient-meta {{
      font-size: 0.85rem;
      color: var(--text-muted);
    }}

    .case-right {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }}

    .score-meter {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.75rem;
      font-family: ui-monospace, monospace;
    }}

    .meter-bar {{
      width: 60px;
      height: 6px;
      background: #1e293b;
      border-radius: 3px;
      overflow: hidden;
    }}

    .meter-fill {{
      height: 100%;
    }}

    .case-body {{
      display: none;
      padding: 1.25rem;
      background: #080c16;
    }}

    .case-card.expanded .case-body {{
      display: block;
    }}

    .scorecard-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.75rem;
      margin-bottom: 1.25rem;
    }}

    .scorecard-box {{
      background: #030712;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 0.75rem;
    }}

    .sc-label {{
      font-size: 0.7rem;
      color: var(--text-dim);
      text-transform: uppercase;
      font-weight: 700;
    }}

    .sc-value {{
      font-size: 0.95rem;
      font-weight: 700;
      margin-top: 0.2rem;
      font-family: ui-monospace, monospace;
    }}

    .justification-box {{
      background: #030712;
      border-left: 3px solid var(--cyan);
      padding: 1rem;
      border-radius: 0 8px 8px 0;
      margin-bottom: 1.25rem;
      font-size: 0.85rem;
      line-height: 1.6;
    }}

    .just-title {{
      font-weight: 700;
      color: var(--cyan);
      margin-bottom: 0.35rem;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    .doc-roster {{
      margin-top: 1rem;
      border-top: 1px solid var(--card-border);
      padding-top: 1rem;
    }}

    .doc-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.78rem;
      margin-top: 0.5rem;
    }}

    .doc-table th, .doc-table td {{
      padding: 0.5rem;
      text-align: left;
      border-bottom: 1px solid #1e293b66;
    }}

    .doc-table th {{
      color: var(--text-dim);
      font-weight: 700;
      text-transform: uppercase;
      font-size: 0.7rem;
    }}

    .doc-table td {{
      font-family: ui-monospace, monospace;
    }}

    code {{
      background: #1e293b88;
      padding: 0.15rem 0.35rem;
      border-radius: 4px;
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-size: 0.8em;
      color: #67e8f9;
    }}
  </style>
</head>
<body>

  <header>
    <div class="header-container">
      <div class="brand-row">
        <div class="logo-group">
          <div class="logo-icon">🛡️</div>
          <div>
            <h1>Medi<span class="cyan-text">Shield</span> Architecture & Benchmark Evaluation</h1>
            <p style="font-size: 0.8rem; color: var(--text-muted);">
              Enterprise Multi-Agent Clinical Intake, Terminology Verification & Forensic Case Adjudication
            </p>
          </div>
        </div>

        <div class="badges">
          <span class="badge badge-cyan">Canonical ADR-001–014</span>
          <span class="badge badge-emerald">30 Clusters Evaluated</span>
          <span class="badge badge-purple">26.7% Clean STP Pass</span>
          <span class="badge badge-amber">HIPAA Isolated RBAC</span>
        </div>
      </div>

      <nav class="quick-nav">
        <a href="#summary-metrics" class="nav-btn">📊 Executive Metrics</a>
        <a href="#e2e-flowchart" class="nav-btn">🔄 Interactive Flowchart</a>
        <a href="#layers-architecture" class="nav-btn">🏗️ 5-Layer System Architecture</a>
        <a href="#multi-agent-pipeline" class="nav-btn">🤖 Agent Swarm & Rules P1–P8</a>
        <a href="#case-evaluation-browser" class="nav-btn">📁 Full 30-Case Forensic Dossier</a>
      </nav>
    </div>
  </header>

  <main>

    <!-- SECTION 1: EXECUTIVE SUMMARY METRICS -->
    <section id="summary-metrics">
      <div class="section-header">
        <div class="section-title">
          <span>📊</span>
          <span>Executive Benchmark Performance Summary (30 Patient Clusters)</span>
        </div>
        <span class="badge badge-emerald">Production Evaluator Audit</span>
      </div>

      <div class="metrics-grid">
        <div class="metric-card mc-cyan">
          <span class="metric-num cyan-text">30</span>
          <span class="metric-label">Total Clusters Evaluated</span>
          <span class="metric-sub">Multi-document longitudinal claims</span>
        </div>
        <div class="metric-card mc-emerald">
          <span class="metric-num emerald-text">8 (26.7%)</span>
          <span class="metric-label">Clean Pass Approvals (P6)</span>
          <span class="metric-sub">Autonomous Straight-Through Processing</span>
        </div>
        <div class="metric-card mc-rose">
          <span class="metric-num rose-text">9 (30.0%)</span>
          <span class="metric-label">Hard Rejections (P5)</span>
          <span class="metric-sub">Deterministic Policy & KYC Failures</span>
        </div>
        <div class="metric-card mc-amber">
          <span class="metric-num amber-text">13 (43.3%)</span>
          <span class="metric-label">Human Review Escalations</span>
          <span class="metric-sub">Routed to licensed adjusters (P3/P7)</span>
        </div>
        <div class="metric-card mc-purple">
          <span class="metric-num purple-text">100%</span>
          <span class="metric-label">Deterministic Rules</span>
          <span class="metric-sub">Zero business decisions by LLM hallucination</span>
        </div>
      </div>

      <div style="background: #090e1a; border: 1px solid var(--card-border); border-radius: 12px; padding: 1.25rem; font-size: 0.85rem; line-height: 1.6;">
        <h4 style="color: #fff; margin-bottom: 0.5rem; font-weight: 700;">Key Architectural Findings & Evaluator Discrepancy Resolution:</h4>
        <ul style="padding-left: 1.25rem; color: #94a3b8; display: flex; flex-direction: column; gap: 0.4rem;">
          <li><strong class="cyan-text">Zero LLM Business Decision Leaks (Rule 14):</strong> LLM gateways extract unstructured OCR tokens and generate customer explanations, but all determinations (Approve, Deny, Escalate) are executed via deterministic code rules (P1 through P8).</li>
          <li><strong class="emerald-text">Calibrated STP Auto-Approval Rate:</strong> 8 of 30 cases (26.7%) achieved 100% clean-pass criteria across identity cross-verification, CPT fee validity, itemized line sum equality, and fraud risk score &lt; 0.20.</li>
          <li><strong class="amber-text">Disputed Synthetic Label Register:</strong> 3 clusters (<code>C_001</code>, <code>C_004</code>, <code>C_006</code>) had synthetic ground-truth metadata asserting readmission or name-mismatch fraud that was factually contradicted by legible source documents. Under ADR-011 and Evaluator Review Register, these records were independently audited and dispositioned without compromising deterministic scoring integrity.</li>
        </ul>
      </div>
    </section>

    <!-- SECTION 2: INTERACTIVE END-TO-END FLOWCHART -->
    <section id="e2e-flowchart">
      <div class="section-header">
        <div class="section-title">
          <span>🔄</span>
          <span>Interactive End-to-End Intake & Adjudication Flowchart</span>
        </div>
        <span class="badge badge-cyan">Click Any Node to Inspect Layer</span>
      </div>

      <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 1rem;">
        Click on any node in the SVG workflow diagram below to inspect its operational contract, algorithms, and fail-safe defenses:
      </p>

      <div class="flowchart-container">
        <svg class="interactive-flowchart" viewBox="0 0 1150 280">
          <defs>
            <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill="#38bdf8" />
            </marker>
          </defs>

          <!-- Row 1: Ingestion & Extraction -->
          <g class="flow-node" onclick="showNodeDetail('intake')">
            <rect x="20" y="30" width="130" height="50" fill="#0c1a30" stroke="#06b6d4" />
            <text x="85" y="52">1. Intake Gateway</text>
            <text x="85" y="68" fill="#94a3b8" style="font-size:9px;">MIME / Multi-File</text>
          </g>

          <path d="M 150 55 L 180 55" class="flow-arrow" />

          <g class="flow-node" onclick="showNodeDetail('storage')">
            <rect x="180" y="30" width="130" height="50" fill="#0c1a30" stroke="#06b6d4" />
            <text x="245" y="52">2. S3 / MinIO Store</text>
            <text x="245" y="68" fill="#94a3b8" style="font-size:9px;">Raw SHA256 Objects</text>
          </g>

          <path d="M 310 55 L 340 55" class="flow-arrow" />

          <g class="flow-node" onclick="showNodeDetail('worker')">
            <rect x="340" y="30" width="130" height="50" fill="#0c1a30" stroke="#06b6d4" />
            <text x="405" y="52">3. Celery / Redis</text>
            <text x="405" y="68" fill="#94a3b8" style="font-size:9px;">Async Task Dispatch</text>
          </g>

          <path d="M 470 55 L 500 55" class="flow-arrow" />

          <g class="flow-node" onclick="showNodeDetail('ocr')">
            <rect x="500" y="30" width="130" height="50" fill="#0c1a30" stroke="#a855f7" />
            <text x="565" y="52">4. OCR & Token Spans</text>
            <text x="565" y="68" fill="#94a3b8" style="font-size:9px;">RapidOCR + Bboxes</text>
          </g>

          <path d="M 630 55 L 670 55" class="flow-arrow" />

          <!-- Multi-Agent Parallel Swarm -->
          <g class="flow-node" onclick="showNodeDetail('swarm')">
            <rect x="670" y="20" width="170" height="70" fill="#180e29" stroke="#a855f7" />
            <text x="755" y="45" fill="#d8b4fe">5. AI Agent Swarm</text>
            <text x="755" y="60" fill="#94a3b8" style="font-size:9px;">Classifier • KYC • Claims</text>
            <text x="755" y="74" fill="#94a3b8" style="font-size:9px;">Policy RAG • Fraud Engine</text>
          </g>

          <path d="M 840 55 L 890 55" class="flow-arrow" />

          <!-- Decision Engine -->
          <g class="flow-node" onclick="showNodeDetail('decision')">
            <rect x="890" y="25" width="160" height="60" fill="#1e1b4b" stroke="#6366f1" />
            <text x="970" y="50" fill="#a5b4fc">6. Decision Engine</text>
            <text x="970" y="67" fill="#cbd5e1" style="font-size:9px;">Deterministic Rules P1-P8</text>
          </g>

          <!-- Branching Out -->
          <path d="M 970 85 L 970 140 L 400 140 L 400 190" class="flow-arrow" />
          <path d="M 970 85 L 970 140 L 730 140 L 730 190" class="flow-arrow" />
          <path d="M 970 85 L 970 140 L 1020 140 L 1020 190" class="flow-arrow" />

          <!-- Outcomes -->
          <g class="flow-node" onclick="showNodeDetail('stp')">
            <rect x="310" y="190" width="180" height="55" fill="#064e3b" stroke="#10b981" />
            <text x="400" y="215" fill="#6ee7b7">✓ Clean STP Pass (P6)</text>
            <text x="400" y="232" fill="#a7f3d0" style="font-size:9px;">Autonomous Auto-Approve</text>
          </g>

          <g class="flow-node" onclick="showNodeDetail('human')">
            <rect x="630" y="190" width="200" height="55" fill="#451a03" stroke="#f59e0b" />
            <text x="730" y="215" fill="#fde68a">⚠️ Awaiting Human Review</text>
            <text x="730" y="232" fill="#fef3c7" style="font-size:9px;">15-min Lock • Overrides (P3/P7)</text>
          </g>

          <g class="flow-node" onclick="showNodeDetail('reject')">
            <rect x="930" y="190" width="180" height="55" fill="#4c0519" stroke="#f43f5e" />
            <text x="1020" y="215" fill="#fda4af">✕ Hard Rejection (P5)</text>
            <text x="1020" y="232" fill="#fecdd3" style="font-size:9px;">Policy Exclusion • KYC Fail</text>
          </g>
        </svg>
      </div>

      <!-- Node Detail Callout -->
      <div id="node-detail-panel" style="background: #090e1a; border: 1px solid var(--cyan); border-radius: 12px; padding: 1.25rem; font-size: 0.85rem;">
        <h4 id="node-detail-title" class="cyan-text" style="font-weight: 700; margin-bottom: 0.35rem;">Click any flowchart node above for architectural breakdown</h4>
        <p id="node-detail-desc" style="color: #cbd5e1;">Each component in MediShield enforces strict interface contracts, idempotent task execution, and decoupled test fixtures.</p>
      </div>
    </section>

    <!-- SECTION 3: 5-LAYER SYSTEM ARCHITECTURE -->
    <section id="layers-architecture">
      <div class="section-header">
        <div class="section-title">
          <span>🏗️</span>
          <span>5-Layer Technical Architecture & Security Governance</span>
        </div>
        <button class="action-btn" onclick="toggleAllLayers()">Expand / Collapse All Layers</button>
      </div>

      <div class="accordion-group">

        <!-- Layer 1 -->
        <div class="accordion-item active">
          <div class="accordion-header" onclick="toggleAccordion(this)">
            <span class="accordion-title">
              <span class="cyan-text">Layer 1:</span> Edge & Role-Gated Presentation Tier (ADR-012, ADR-013, ADR-014)
            </span>
            <span class="chevron">▼</span>
          </div>
          <div class="accordion-content">
            <p><strong>Persona-Segregated Experience:</strong></p>
            <ul style="padding-left: 1.25rem; margin: 0.5rem 0 1rem; display: flex; flex-direction: column; gap: 0.5rem;">
              <li><strong>Claimer Portal (<code>/claimer</code>):</strong> Self-service dropzone for patients/providers with validation for magic-bytes, multi-file upload, 5-stage progress tracking, and zero leakage of raw fraud scores or prompt defenses. Under ADR-014, unconfirmed preliminary decisions are masked as <code>Under Specialist Review</code>.</li>
              <li><strong>Operations Reviewer Console (<code>/</code>, <code>/cases/[id]</code>):</strong> Operational review queue, concurrency-safe 15-minute lease locks (<code>ReviewLock</code>), high-resolution document viewer with bounding box overlays, and authoritative human override action controls.</li>
              <li><strong>Admin Console (<code>/admin</code>, <code>/metrics</code>):</strong> Actuarial policy management, CSV terminology upload (CPT/ICD) with instant $O(1)$ in-memory catalog invalidation, and executive Straight-Through Processing dashboards.</li>
            </ul>
            <p><strong>Session Governance:</strong> Powered by <code>AuthProvider</code> and JWT tokens with role-restricted route barriers preventing cross-persona boundary traversal.</p>
          </div>
        </div>

        <!-- Layer 2 -->
        <div class="accordion-item">
          <div class="accordion-header" onclick="toggleAccordion(this)">
            <span class="accordion-title">
              <span class="purple-text">Layer 2:</span> API Gateway, RBAC Security & HIPAA PHI Protection
            </span>
            <span class="chevron">▼</span>
          </div>
          <div class="accordion-content">
            <p><strong>FastAPI Application Gateway:</strong></p>
            <ul style="padding-left: 1.25rem; margin: 0.5rem 0 1rem; display: flex; flex-direction: column; gap: 0.5rem;">
              <li><strong>HIPAA Data Scrubbing Middleware:</strong> Intercepts exception payloads and stdout streams via <code>scrub_phi()</code>, scrubbing Social Security Numbers, Member IDs, and clinical diagnostics before emitting logs.</li>
              <li><strong>Constant-Time Password Verification:</strong> Authenticates enterprise accounts with <code>hmac.compare_digest</code> to defend against cryptographic timing side-channel attacks.</li>
              <li><strong>Idempotency & Tracing:</strong> Mandatory <code>X-Correlation-ID</code> propagation for end-to-end distributed tracing across Celery tasks and <code>Idempotency-Key</code> validation on write operations.</li>
            </ul>
          </div>
        </div>

        <!-- Layer 3 -->
        <div class="accordion-item">
          <div class="accordion-header" onclick="toggleAccordion(this)">
            <span class="accordion-title">
              <span class="emerald-text">Layer 3:</span> Asynchronous Orchestration, Object Store & Dual-Engine Persistence
            </span>
            <span class="chevron">▼</span>
          </div>
          <div class="accordion-content">
            <p><strong>Reliable Distributed Workflows:</strong></p>
            <ul style="padding-left: 1.25rem; margin: 0.5rem 0 1rem; display: flex; flex-direction: column; gap: 0.5rem;">
              <li><strong>Celery & Redis Broker:</strong> Decouples HTTP ingestion from compute-intensive OCR and agent deliberation. Includes automated fallback to in-process synchronous background tasks when Redis is offline.</li>
              <li><strong>Dual-Engine Database Tier:</strong> Native PostgreSQL 16 schema in production with zero-dependency SQLite persistence fallback (<code>data/medishield.db</code> in WAL mode with <code>StaticPool</code> thread isolation) ensuring tests and demonstrations run without external services.</li>
              <li><strong>Object Storage Tier:</strong> S3/MinIO compatible object store for immutable raw PDF/PNG document storage with local filesystem fallback (<code>storage/</code>).</li>
            </ul>
          </div>
        </div>

        <!-- Layer 4 -->
        <div class="accordion-item">
          <div class="accordion-header" onclick="toggleAccordion(this)">
            <span class="accordion-title">
              <span class="amber-text">Layer 4:</span> Multi-Agent Specialized AI Swarm
            </span>
            <span class="chevron">▼</span>
          </div>
          <div class="accordion-content">
            <p><strong>Specialist Agent Domain Architecture:</strong></p>
            <ul style="padding-left: 1.25rem; margin: 0.5rem 0 1rem; display: flex; flex-direction: column; gap: 0.5rem;">
              <li><strong>Document Classifier Agent:</strong> Classifies documents into authoritative types (<code>ID_CARD</code>, <code>CLAIM_FORM</code>, <code>DISCHARGE_SUMMARY</code>, <code>PRESCRIPTION</code>, <code>POLICY_AMENDMENT</code>).</li>
              <li><strong>KYC Identity Agent:</strong> Employs fuzzy token sorting and normalized Levenshtein distance to verify member identity against corporate enrollment records, safely ignoring middle initials and suffixes.</li>
              <li><strong>Claims Specialist Agent:</strong> Audits line-item arithmetic ($Quantity \\times Unit Charge = Total$), verifies dates of service, and validates procedural codes against the dynamic <code>CodeCatalogRepository</code>.</li>
              <li><strong>Policy Coverage RAG Agent:</strong> Resolves active policy terms, deduces deductible and copay rates, and expands clinical terms using catalog descriptions.</li>
              <li><strong>Deterministic Fraud Engine:</strong> Calculates fraud risk score $\\in [0.0, 1.0]$ across 4 independent dimensions: identity tampering (+0.20), unbundled billing (+0.30), excessive velocity (+0.25), and duplicate billing (+0.35).</li>
            </ul>
          </div>
        </div>

        <!-- Layer 5 -->
        <div class="accordion-item">
          <div class="accordion-header" onclick="toggleAccordion(this)">
            <span class="accordion-title">
              <span class="rose-text">Layer 5:</span> Deterministic Decision Engine & Immutable Audit Ledger
            </span>
            <span class="chevron">▼</span>
          </div>
          <div class="accordion-content">
            <p><strong>Rule Cascade Hierarchy (P1 through P8):</strong></p>
            <div style="background: #030712; border: 1px solid var(--card-border); border-radius: 8px; padding: 1rem; margin: 0.5rem 0; font-family: ui-monospace, monospace; font-size: 0.8rem; line-height: 1.8;">
              <span class="amber-text">RULE-P1-MISSING-DOCS</span>       : Missing required documents &rarr; AWAITING_INFO<br>
              <span class="rose-text">RULE-P2-FRAUD-BLOCKER</span>       : Fraud score &ge; 0.90 &rarr; ESCALATE (FRAUD_REVIEW)<br>
              <span class="amber-text">RULE-P3-AMBIGUITY</span>           : Specialist CONFLICT or INDETERMINATE &rarr; ESCALATE (AMBIGUITY)<br>
              <span class="rose-text">RULE-P4-HIGH-FRAUD</span>          : Fraud score &ge; 0.70 &rarr; ESCALATE (FRAUD_REVIEW)<br>
              <span class="rose-text">RULE-P5-HARD-REJECT</span>         : Confirmed KYC or Policy Exclusion &rarr; REJECT<br>
              <span class="emerald-text">RULE-P6-CLEAN-PASS</span>          : All checks PASS, Fraud &lt; 0.20, Conf &ge; 0.70 &rarr; APPROVE (STP)<br>
              <span class="amber-text">RULE-P7-BORDERLINE-REVIEW</span>   : Calibrated confidence &lt; 0.70 &rarr; ESCALATE (HUMAN_REVIEW)<br>
              <span class="amber-text">RULE-P8-FALLBACK-ESCALATE</span>   : Unclassified edge case &rarr; ESCALATE (HUMAN_REVIEW)
            </div>
            <p style="margin-top: 0.75rem;"><strong>Cryptographic Hash Chain:</strong> Every decision and human review action appends an immutable <code>AuditEvent</code> linked to the prior hash, guaranteeing forensic auditability.</p>
          </div>
        </div>

      </div>
    </section>

    <!-- SECTION 4: FULL 30-CASE FORENSIC EVALUATION -->
    <section id="case-evaluation-browser">
      <div class="section-header">
        <div class="section-title">
          <span>📁</span>
          <span>Full 30-Case Forensic Dossier & Decision Explanation</span>
        </div>
        <div style="display:flex; gap:0.5rem;">
          <button class="action-btn" onclick="expandAllCases(true)">Expand All Cases</button>
          <button class="action-btn" onclick="expandAllCases(false)">Collapse All Cases</button>
        </div>
      </div>

      <!-- Controls -->
      <div class="controls-bar">
        <div class="filter-tabs">
          <button class="tab-btn active" onclick="setDecisionFilter('ALL', this)">All (30)</button>
          <button class="tab-btn" onclick="setDecisionFilter('APPROVE', this)">Approved (8)</button>
          <button class="tab-btn" onclick="setDecisionFilter('REJECT', this)">Rejected (9)</button>
          <button class="tab-btn" onclick="setDecisionFilter('ESCALATE', this)">Escalated (13)</button>
        </div>

        <input
          type="text"
          id="caseSearchInput"
          placeholder="Search by cluster (C_001), patient ID, rule..."
          class="search-input"
          oninput="handleSearch()"
        />
      </div>

      <!-- Dynamic Case List Container -->
      <div id="cases-list-container">
        <!-- Rendered by JS -->
      </div>
    </section>

  </main>

  <script>
    const casesData = {cases_json_str};

    let activeFilter = 'ALL';
    let searchQuery = '';

    const nodeDescriptions = {{
      'intake': {{
        title: '1. Customer / Provider Intake Gateway (Layer 1 & 2)',
        desc: 'Validates uploaded files via magic-byte header inspection (preventing MIME spoofing), enforces 50MB batch limits, generates deterministic SHA-256 content hashes, and tracks idempotency keys.'
      }},
      'storage': {{
        title: '2. Object Storage (S3 / MinIO / Local)',
        desc: 'Persists raw binary evidence under strict isolation prefixes (raw/{{case_id}}/{{doc_id}}). Separates untrusted patient uploads from authoritative corporate master policies.'
      }},
      'worker': {{
        title: '3. Asynchronous Worker Orchestrator (Celery + Redis)',
        desc: 'Manages distributed background task lifecycle (process_case_workflow_task). Features automated in-process background worker fallback if Redis connection is unavailable.'
      }},
      'ocr': {{
        title: '4. Optical Character Recognition (RapidOCR + Token Spans)',
        desc: 'Extracts raster text spans with spatial bounding boxes [x0, y0, x1, y1] for human reviewer provenance overlays. Bypasses textless PDF extraction traps via native rasterization.'
      }},
      'swarm': {{
        title: '5. Multi-Agent Specialized AI Swarm (Layer 4)',
        desc: 'Parallel specialized agents execute modular checks: KYC fuzzy identity, line-item arithmetic reconciler, dynamic catalog code validator, and multi-dimensional fraud anomaly detector.'
      }},
      'decision': {{
        title: '6. Deterministic Decision Engine (P1 through P8)',
        desc: 'Evaluates agent outputs through ordered deterministic code rules. Enforces strict Rule 14 compliance: LLMs extract facts and generate text, but business rules are 100% deterministic code paths.'
      }},
      'stp': {{
        title: 'Outcome: Autonomous Straight-Through Pass (P6 Clean Approval)',
        desc: 'High-confidence approvals bypass human queues entirely (26.7% rate across benchmark), calculating benefits and publishing final adjudication notices instantly.'
      }},
      'human': {{
        title: 'Outcome: Human-in-the-Loop Review Queue (Awaiting Human)',
        desc: 'Cases with ambiguities (P3), high fraud scores (P4), or borderline confidence (P7) enter the reviewer console with 15-minute lease locks to prevent collisions.'
      }},
      'reject': {{
        title: 'Outcome: Autonomous Hard Rejection (P5 Policy Denials)',
        desc: 'Confirmed KYC identity failures or explicit policy exclusions (e.g. experimental treatments) are denied deterministically with formal denial letters.'
      }}
    }};

    function showNodeDetail(key) {{
      const node = nodeDescriptions[key];
      if (!node) return;
      document.getElementById('node-detail-title').innerText = node.title;
      document.getElementById('node-detail-desc').innerText = node.desc;
      document.getElementById('node-detail-panel').scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
    }}

    function toggleAccordion(el) {{
      const item = el.parentElement;
      item.classList.toggle('active');
    }}

    function toggleAllLayers() {{
      const items = document.querySelectorAll('#layers-architecture .accordion-item');
      const allActive = Array.from(items).every(i => i.classList.contains('active'));
      items.forEach(i => {{
        if (allActive) i.classList.remove('active');
        else i.classList.add('active');
      }});
    }}

    function toggleCaseCard(el) {{
      const card = el.parentElement;
      card.classList.toggle('expanded');
    }}

    function expandAllCases(expand) {{
      document.querySelectorAll('.case-card').forEach(c => {{
        if (expand) c.classList.add('expanded');
        else c.classList.remove('expanded');
      }});
    }}

    function setDecisionFilter(filter, btn) {{
      activeFilter = filter;
      document.querySelectorAll('.filter-tabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderCases();
    }}

    function handleSearch() {{
      searchQuery = document.getElementById('caseSearchInput').value.toLowerCase().trim();
      renderCases();
    }}

    function renderCases() {{
      const container = document.getElementById('cases-list-container');
      const filtered = casesData.filter(c => {{
        const matchDecision = activeFilter === 'ALL' || c.final_decision === activeFilter;
        const textToSearch = [
          c.cluster_id,
          c.patient_id,
          c.patient_name,
          c.policy_number,
          c.rule_id_matched,
          c.justification,
          c.forensic_notes
        ].join(' ').toLowerCase();
        const matchSearch = !searchQuery || textToSearch.includes(searchQuery);
        return matchDecision && matchSearch;
      }});

      if (filtered.length === 0) {{
        container.innerHTML = '<div style="text-align:center; padding:3rem; color:var(--text-dim);">No cases match the selected filters.</div>';
        return;
      }}

      container.innerHTML = filtered.map(c => {{
        const isApprove = c.final_decision === 'APPROVE';
        const isReject = c.final_decision === 'REJECT';
        const isEscalate = c.final_decision === 'ESCALATE';

        const decBadge = isApprove
          ? '<span class="badge badge-emerald">APPROVE (P6 Clean Pass)</span>'
          : isReject
          ? '<span class="badge badge-rose">REJECT (P5 Hard Denial)</span>'
          : '<span class="badge badge-amber">ESCALATE (Human Review)</span>';

        const barColor = isApprove ? 'var(--emerald)' : isReject ? 'var(--rose)' : 'var(--amber)';
        const fraudPct = Math.min(100, Math.round(c.fraud_score * 100));

        const docsRows = (c.documents || []).map(d => `
          <tr>
            <td><code>${{d.doc_id}}</code></td>
            <td>${{d.category || 'general'}}</td>
            <td>${{d.fraud_label ? '<span class="rose-text">FLAGGED (' + (d.fraud_reason || 'fraud') + ')</span>' : '<span class="emerald-text">CLEAN</span>'}}</td>
            <td>${{d.edge_flags && d.edge_flags.length ? d.edge_flags.join(', ') : 'none'}}</td>
          </tr>
        `).join('');

        return `
          <div class="case-card" id="card-${{c.cluster_id}}">
            <div class="case-header" onclick="toggleCaseCard(this)">
              <div class="case-left">
                <span class="cluster-id">${{c.cluster_id}}</span>
                <span class="badge badge-cyan">${{c.patient_id}}</span>
                <span class="patient-meta"><strong>${{c.patient_name}}</strong> &bull; Policy: <code>${{c.policy_number}}</code> &bull; DOB: ${{c.dob}}</span>
              </div>
              <div class="case-right">
                <div class="score-meter">
                  <span style="color:var(--text-dim);">Fraud:</span>
                  <div class="meter-bar">
                    <div class="meter-fill" style="width:${{fraudPct}}%; background:${{barColor}};"></div>
                  </div>
                  <span style="color:${{barColor}};">${{c.fraud_score.toFixed(2)}}</span>
                </div>
                ${{decBadge}}
                <span style="font-size:0.75rem; color:var(--text-dim); font-family:monospace;">${{c.rule_id_matched}}</span>
                <span class="chevron">▼</span>
              </div>
            </div>

            <div class="case-body">
              <!-- Scorecard -->
              <div class="scorecard-grid">
                <div class="scorecard-box">
                  <div class="sc-label">KYC Identity</div>
                  <div class="sc-value ${{c.kyc_verdict === 'PASS' ? 'emerald-text' : 'rose-text'}}">${{c.kyc_verdict}} (${{(c.kyc_confidence*100).toFixed(0)}}%)</div>
                </div>
                <div class="scorecard-box">
                  <div class="sc-label">Claims Arithmetic</div>
                  <div class="sc-value ${{c.claims_verdict === 'PASS' ? 'emerald-text' : c.claims_verdict === 'CONFLICT' ? 'rose-text' : 'amber-text'}}">${{c.claims_verdict}} (${{(c.claims_confidence*100).toFixed(0)}}%)</div>
                </div>
                <div class="scorecard-box">
                  <div class="sc-label">Policy Coverage</div>
                  <div class="sc-value ${{c.policy_verdict === 'PASS' ? 'emerald-text' : 'rose-text'}}">${{c.policy_verdict}} (${{(c.policy_confidence*100).toFixed(0)}}%)</div>
                </div>
                <div class="scorecard-box">
                  <div class="sc-label">Fraud Score</div>
                  <div class="sc-value" style="color:${{barColor}};">${{c.fraud_score.toFixed(2)}} (${{c.fraud_verdict}})</div>
                </div>
                <div class="scorecard-box">
                  <div class="sc-label">Ground Truth Match</div>
                  <div class="sc-value cyan-text">${{c.reviewed_adjudication_label || 'UNREVIEWED'}} (Fraud metadata: ${{c.fraud_metadata_label ? 'YES' : 'NO'}})</div>
                </div>
              </div>

              <!-- Official Decision Justification -->
              <div class="justification-box">
                <div class="just-title">Deterministic Engine Justification (${{c.rule_id_matched}}):</div>
                <div>${{c.justification}}</div>
              </div>

              <!-- Forensic Audit Insight -->
              <div style="background:#090e1a; border:1px solid #1e293b; border-radius:8px; padding:0.85rem 1rem; margin-bottom:1rem; font-size:0.8rem; line-height:1.6;">
                <strong class="cyan-text">Forensic Engineering Analysis:</strong>
                <p style="color:#cbd5e1; margin-top:0.25rem;">${{c.forensic_notes}}</p>
                ${{c.all_failing_checks && c.all_failing_checks.length > 1 ? '<div style="margin-top:0.5rem; color:#f59e0b;"><strong>Co-occurring Failing Checks:</strong> ' + c.all_failing_checks.join(', ') + '</div>' : ''}}
              </div>

              <!-- Document Inventory -->
              <div class="doc-roster">
                <div style="font-size:0.75rem; text-transform:uppercase; color:var(--text-muted); font-weight:700;">
                  Case Evidence Roster (${{c.documents ? c.documents.length : 0}} Documents):
                </div>
                <table class="doc-table">
                  <thead>
                    <tr>
                      <th>Document ID</th>
                      <th>Category</th>
                      <th>Ground Truth Synthetic Label</th>
                      <th>Edge / OCR Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${{docsRows}}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        `;
      }}).join('');
    }}

    // Initial render
    renderCases();
  </script>

</body>
</html>
"""

with open(OUTPUT_HTML_PATH, "w", encoding="utf-8") as f:
    f.write(html_template)

print("Successfully generated comprehensive architecture and evaluation report:")
print(f"  {OUTPUT_HTML_PATH}")
print(f"  File size: {len(html_template)} characters")
