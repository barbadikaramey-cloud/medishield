"use client";

import React, { useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Calendar,
  Check,
  CheckCircle2,
  Clock,
  Compass,
  Copy,
  DollarSign,
  FileCheck,
  FileSpreadsheet,
  FileText,
  HeartPulse,
  Info,
  Layers,
  Maximize2,
  Minimize2,
  Radar,
  Receipt,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  X,
  XCircle,
} from "lucide-react";

export function formatRuleLabel(ruleId: string | null | undefined): string {
  if (!ruleId) return "";
  const map: Record<string, string> = {
    "RULE-P1-SECURITY": "Document Integrity & Security Check",
    "RULE-P1-TECH-FAIL": "Technical Validation Check",
    "RULE-P2-INCOMPLETE": "Intake Completeness Check",
    "RULE-P3-AMBIGUITY": "Information Discrepancy & Verification Review",
    "RULE-P3-AMBIGUITY-ESCALATE": "Information Discrepancy & Verification Review",
    "RULE-P4-HIGH-FRAUD": "Billing Pattern & Anomaly Review",
    "RULE-P5-HARD-REJECT": "Policy Exclusion & Eligibility Denial",
    "RULE-P6-CLEAN-PASS": "Automated Clean Approval",
    "RULE-P7-BORDERLINE-REVIEW": "Medical Necessity & Guideline Review",
    "RULE-P8-CATCH-ALL": "Operational Adjudication Review",
  };
  if (map[ruleId]) return map[ruleId];
  return ruleId
    .replace(/^RULE-/, "")
    .replace(/^P\d+-/, "")
    .replace(/-/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatSubReason(subReason: string | null | undefined): string {
  if (!subReason) return "";
  const map: Record<string, string> = {
    AMBIGUITY: "Information Discrepancy",
    HUMAN_REVIEW: "Clinical & Document Review",
    FRAUD_REVIEW: "Billing Pattern Review",
    TECHNICAL_FAILURE: "Technical System Verification",
    MISSING_EVIDENCE: "Additional Documentation Required",
    INCOMPLETE: "Incomplete Documentation",
    BORDERLINE_CONFIDENCE: "Specialist Confidence Review",
    RECOMMEND_DENIAL: "Policy Non-Coverage",
  };
  return map[subReason] || subReason.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function sanitizeArchitecturalJargon(text: string): string {
  if (!text) return "";
  return text
    // Remove ADR citations like (ADR-015), (ADR-002), (ADR-011), (ADR-001)
    .replace(/\s*\((?:Stage\s+[A-Z]\s+)?ADR-\d+\)/gi, "")
    .replace(/\bADR-\d+\b/gi, "Clinical Review Standard")
    // Replace Rule P3 / Ambiguity escalation jargon
    .replace(/Ambiguity Escalation \(P3\):/gi, "Review Escalation (Information Discrepancy):")
    .replace(/Ambiguity escalation:/gi, "Review escalation (information discrepancy):")
    .replace(/Ambiguity Escalation/gi, "Information Discrepancy Review")
    .replace(/Autonomous Rejection \(P5, ADR-\d+\):/gi, "Autonomous Policy Denial:")
    .replace(/Autonomous Rejection \(P5\):/gi, "Autonomous Policy Denial:")
    .replace(/Autonomous Rejection \(P\d+\):/gi, "Autonomous Policy Denial:")
    // Replace rule IDs when rendered in plain text
    .replace(/RULE-P3-AMBIGUITY(-ESCALATE)?/g, "Information Discrepancy Review")
    .replace(/RULE-P6-CLEAN-PASS/g, "Automated Clean Approval")
    .replace(/RULE-P5-HARD-REJECT/g, "Policy Exclusion Denial")
    .replace(/RULE-P1-SECURITY/g, "Document Integrity Check")
    .replace(/RULE-P1-TECH-FAIL/g, "Technical Validation Check")
    .replace(/RULE-P2-INCOMPLETE/g, "Documentation Completeness Check")
    .replace(/RULE-P4-HIGH-FRAUD/g, "Billing Anomaly Review")
    .replace(/RULE-P7-BORDERLINE-REVIEW/g, "Medical Necessity Review")
    .replace(/RULE-P8-CATCH-ALL/g, "Operational Review")
    .replace(/\b[Rr]ule\s+P\d+\b/g, "Policy Guideline")
    // Humanize standalone ambiguity/ambiguities in prose
    .replace(/\bAmbiguities\b/g, "Information discrepancies")
    .replace(/\bambiguities\b/g, "information discrepancies")
    .replace(/\bAmbiguity\b/g, "Information discrepancy")
    .replace(/\bambiguity\b/g, "information discrepancy")
    // Clean repetitive robotic phrasing
    .replace(/MediShield's autonomous multi-agent pipeline evaluated claim packet [^\s]+ for member [^\s]+ and (?:recommended|determined)/gi, "Evaluated claim packet for member and recommended")
    .replace(/Under deterministic rule [^,]+ with calibrated confidence [^,]+,\s*/gi, "");
}

export interface ClinicalExplanationViewerProps {
  content: string;
  caseId?: string;
  ruleIdMatched?: string | null;
  decisionConfidence?: number | null;
  currentDecision?: string | null;
  modelSource?: string | null;
  agentFindings?: Record<string, any> | null;
  onRefresh?: () => void;
  isRefreshing?: boolean;
  selectedCategory?: string;
  onCategoryChange?: (category: string) => void;
}

interface ParsedSection {
  id: string;
  title: string;
  category:
    | "executive"
    | "kyc"
    | "claims"
    | "policy"
    | "fraud"
    | "consistency"
    | "clinical"
    | "guidance"
    | "other";
  rawLines: string[];
}

const CATEGORY_CONFIG: Record<
  string,
  {
    name: string;
    icon: React.ComponentType<{ className?: string }>;
    accentBorder: string;
    accentBg: string;
    badgeBg: string;
    badgeText: string;
    shortLabel: string;
  }
> = {
  executive: {
    name: "Executive Summary",
    icon: Sparkles,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Executive Summary",
  },
  clinical: {
    name: "Medical Necessity & Alignment",
    icon: Stethoscope,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Medical Necessity",
  },
  claims: {
    name: "Billing & Financial Lines",
    icon: Receipt,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Billing Lines",
  },
  policy: {
    name: "Policy Coverage & Benefits",
    icon: BookOpen,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Policy Coverage",
  },
  kyc: {
    name: "Member Identity & KYC",
    icon: ShieldCheck,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Identity & KYC",
  },
  fraud: {
    name: "Fraud Risk & Anomaly Scoring",
    icon: Radar,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Fraud Risk",
  },
  consistency: {
    name: "Document Attestation",
    icon: FileCheck,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Attestation",
  },
  guidance: {
    name: "Recommended Next Steps",
    icon: Compass,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Next Steps",
  },
  other: {
    name: "Additional Details",
    icon: Info,
    accentBorder: "border-border",
    accentBg: "bg-card",
    badgeBg: "bg-card-inner border-border",
    badgeText: "text-foreground",
    shortLabel: "Details",
  },
};

// Proper healthcare claims audit order
const ORDERED_CATEGORIES: Array<ParsedSection["category"]> = [
  "executive",   // 1. Executive Summary
  "clinical",    // 2. Medical Necessity (Clinical Alignment)
  "claims",      // 3. Billing & Financial Line Items
  "policy",      // 4. Policy Coverage & Exclusions
  "kyc",         // 5. Member Identity & KYC Verification
  "fraud",       // 6. Fraud & Anomaly Risk
  "consistency", // 7. Cross-Document Attestation
  "guidance",    // 8. Next Steps & Recommended Actions
];

export type HealthStatus = "problem" | "warning" | "pass" | "neutral";

export interface CategoryHealth {
  status: HealthStatus;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  pillClass: string;
  activePillClass: string;
  badgeClass: string;
}

const CARD_NEUTRAL =
  "border-border bg-card text-foreground hover:bg-pink-hover/60 hover:border-pink-border hover:text-pink-deep shadow-xs transition-all duration-180 active:scale-[0.99] cursor-pointer";
const CARD_ACTIVE =
  "border-sky-med/60 text-sky-deep bg-sky-soft font-bold shadow-xs transition-all duration-180 cursor-pointer";

const CARD_PROBLEM =
  "border-rose-med/50 bg-rose-blush/20 text-foreground hover:bg-rose-blush/30 hover:border-rose-med shadow-xs transition-all duration-180 active:scale-[0.99] cursor-pointer";
const CARD_ACTIVE_PROBLEM =
  "border-rose-med bg-rose-blush/40 text-rose-deep font-bold shadow-xs transition-all duration-180 cursor-pointer";

const CARD_WARNING =
  "border-status-warning/40 bg-status-warning/10 text-foreground hover:bg-status-warning/20 hover:border-status-warning shadow-xs transition-all duration-180 active:scale-[0.99] cursor-pointer";
const CARD_ACTIVE_WARNING =
  "border-status-warning bg-status-warning/20 text-status-warning font-bold shadow-xs transition-all duration-180 cursor-pointer";

const BADGE_PROBLEM =
  "bg-rose-blush text-rose-deep border-rose-med/40";
const BADGE_WARNING =
  "bg-status-warning/15 text-status-warning border-status-warning/30";
const BADGE_PASS =
  "bg-status-success/15 text-status-success border-status-success/30";
const BADGE_NEUTRAL =
  "bg-sky-pale text-secondary border-border";

function getCardPillClasses(status: HealthStatus): { pillClass: string; activePillClass: string } {
  if (status === "problem") {
    return { pillClass: CARD_PROBLEM, activePillClass: CARD_ACTIVE_PROBLEM };
  }
  if (status === "warning") {
    return { pillClass: CARD_WARNING, activePillClass: CARD_ACTIVE_WARNING };
  }
  return { pillClass: CARD_NEUTRAL, activePillClass: CARD_ACTIVE };
}

export function getCategoryHealth(
  cat: string,
  findings: Record<string, any> | null | undefined,
  currentDecision: string | null | undefined,
  sectionLines: string[] = []
): CategoryHealth {
  if (cat === "clinical") {
    const clin = findings?.clinical || findings?.clinical_alignment;
    const raw = sectionLines.join(" ").toLowerCase();
    const verdict = clin?.verdict ? String(clin.verdict).toUpperCase() : null;
    const linesSupported = clin?.lines_fully_supported_count ?? clin?.lines_supported;
    const totalLines = clin?.total_lines_assessed ?? clin?.lines_assessed;
    const contradicted = clin?.lines_contradicted_count ?? 0;
    const failedNecessity = clin?.lines_failed_necessity_count ?? 0;
    const indeterminateCount = clin?.lines_indeterminate_count ?? 0;
    const bedDiscrepancy = clin?.bed_day_reconciliation?.discrepancy_days ?? 0;
    const blocking = Array.isArray(clin?.blocking_discrepancies) ? clin.blocking_discrepancies.length : 0;

    const textHasProblem =
      raw.includes("not met") ||
      raw.includes("contradicted") ||
      raw.includes("failed medical necessity") ||
      raw.includes("contraindicated") ||
      raw.includes("❌");
    const textHasWarning =
      raw.includes("indeterminate") ||
      raw.includes("stay flag") ||
      raw.includes("discrepancy") ||
      raw.includes("unsupported") ||
      raw.includes("⚠️");

    if (verdict === "FAIL" || contradicted > 0 || failedNecessity > 0 || textHasProblem) {
      return {
        status: "problem",
        label: "Not Met",
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }
    if (
      verdict === "CONFLICT" ||
      verdict === "INDETERMINATE" ||
      (linesSupported !== undefined && totalLines !== undefined && linesSupported < totalLines) ||
      bedDiscrepancy > 0 ||
      indeterminateCount > 0 ||
      blocking > 0 ||
      textHasWarning
    ) {
      const label =
        bedDiscrepancy > 0
          ? "Stay Flag"
          : linesSupported !== undefined && totalLines !== undefined && linesSupported < totalLines
          ? `${linesSupported}/${totalLines} Lines`
          : "Review";
      return {
        status: "warning",
        label,
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "pass",
      label: "Met",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "claims") {
    const claims = findings?.claims;
    const raw = sectionLines.join(" ").toLowerCase();
    const verdict = claims?.verdict ? String(claims.verdict).toUpperCase() : null;
    const lineMathValid = claims?.line_items_sum_valid;
    const schemaValid = claims?.schema_valid;
    const hasErrors = Array.isArray(claims?.validation_errors) && claims.validation_errors.length > 0;

    const textHasProblem = raw.includes("schema invalid") || raw.includes("claim rejection") || raw.includes("❌");
    const textHasWarning =
      raw.includes("mismatch") ||
      raw.includes("discrepancy") ||
      raw.includes("lack cpt") ||
      raw.includes("unmapped") ||
      raw.includes("arithmetic") ||
      raw.includes("⚠️");

    if (verdict === "FAIL" || schemaValid === false || textHasProblem) {
      return {
        status: "problem",
        label: "Failed",
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }
    if (
      verdict === "CONFLICT" ||
      verdict === "INDETERMINATE" ||
      lineMathValid === false ||
      hasErrors ||
      textHasWarning
    ) {
      return {
        status: "warning",
        label: lineMathValid === false ? "Math Mismatch" : "Discrepancy",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "pass",
      label: "Valid",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "policy") {
    const pol = findings?.policy;
    const raw = sectionLines.join(" ").toLowerCase();
    const verdict = pol?.verdict ? String(pol.verdict).toUpperCase() : null;
    const covered = pol?.covered;
    const covPct = pol?.coverage_percentage;
    const exclusions = Array.isArray(pol?.exclusions) ? pol.exclusions : [];
    const exclusionVerified = Boolean(pol?.exclusion_clause_verified);

    const textHasProblem =
      raw.includes("exclusion applies") ||
      raw.includes("not covered") ||
      raw.includes("non-covered") ||
      raw.includes("denial") ||
      raw.includes("❌");
    const textHasWarning =
      raw.includes("prior auth") ||
      raw.includes("unverified") ||
      raw.includes("review") ||
      raw.includes("⚠️");

    if (verdict === "FAIL" || covered === false || (exclusions.length > 0 && exclusionVerified) || textHasProblem) {
      return {
        status: "problem",
        label: "Excluded",
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }
    if (
      verdict === "CONFLICT" ||
      verdict === "INDETERMINATE" ||
      (covPct !== undefined && covPct < 100) ||
      exclusions.length > 0 ||
      textHasWarning
    ) {
      return {
        status: "warning",
        label: covPct !== undefined && covPct < 100 ? `${covPct}% Cov` : "Review",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    const cov = covPct !== undefined ? `${covPct}%` : "100%";
    return {
      status: "pass",
      label: cov,
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "kyc") {
    const kyc = findings?.kyc;
    const raw = sectionLines.join(" ").toLowerCase();

    const tamperScore = typeof kyc?.tamper_score === "number" ? kyc.tamper_score : 0;
    const isTamperDetected = Boolean(kyc?.tamper_detected);
    const forensicStatus = String(kyc?.forensic_status || "").toUpperCase();
    const flags = Array.isArray(kyc?.flags) ? kyc.flags : [];
    const hasTamperFlag = flags.some((f: string) => f.includes("TAMPER") || f.includes("ANOMALY"));
    const verdict = kyc?.verdict ? String(kyc.verdict).toUpperCase() : null;
    const idExpired = Boolean(kyc?.id_expired);
    const memberMatched = kyc?.member_db_matched;

    // Check raw text for forensic anomalies, alterations, or tampering
    const textHasTamper =
      raw.includes("tamper") ||
      raw.includes("tampering") ||
      raw.includes("anomaly detected") ||
      raw.includes("forgery") ||
      raw.includes("altered");
    const textHasFail = raw.includes("fail") || raw.includes("expired") || raw.includes("unmatched");
    const textHasReview = raw.includes("conflict") || raw.includes("indeterminate") || raw.includes("flagged");

    // Critical problem: high tamper score, anomaly detected, tamper detected flag, fail verdict, expired ID, or unmatched
    if (
      tamperScore >= 0.5 ||
      forensicStatus === "ANOMALY_DETECTED" ||
      isTamperDetected ||
      hasTamperFlag ||
      verdict === "FAIL" ||
      idExpired ||
      memberMatched === false ||
      (textHasTamper && (raw.includes("1.0") || raw.includes("0.9") || raw.includes("0.8") || raw.includes("0.7") || raw.includes("0.6") || raw.includes("0.5") || raw.includes("detected")))
    ) {
      const label =
        tamperScore > 0 || isTamperDetected || hasTamperFlag || textHasTamper
          ? "Tamper Flag"
          : idExpired
          ? "ID Expired"
          : memberMatched === false
          ? "Unmatched ID"
          : "Failed";
      return {
        status: "problem",
        label,
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }

    // Warning / Discrepancy under review: Moderate tamper score, retrieval failed, conflict or indeterminate verdict, other flags
    if (
      tamperScore > 0 ||
      forensicStatus === "IMAGE_RETRIEVAL_FAILED" ||
      verdict === "CONFLICT" ||
      verdict === "INDETERMINATE" ||
      flags.length > 0 ||
      textHasTamper ||
      textHasReview
    ) {
      const label =
        tamperScore > 0 || textHasTamper
          ? "Tamper Suspected"
          : verdict === "CONFLICT"
          ? "ID Conflict"
          : forensicStatus === "IMAGE_RETRIEVAL_FAILED"
          ? "Retrieval Flag"
          : "Review Flag";
      return {
        status: "warning",
        label,
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }

    // Clean pass only when zero tamper signals and verified
    return {
      status: "pass",
      label: "Verified",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "fraud") {
    const fraud = findings?.fraud;
    const raw = sectionLines.join(" ").toLowerCase();
    const risk = String(fraud?.risk_level || "").toUpperCase();
    const duplicate = Boolean(fraud?.duplicate_claim_detected);
    const reviewRequired = Boolean(fraud?.fraud_review_required) || duplicate;
    const anomalies = Array.isArray(fraud?.anomalies) ? fraud.anomalies : [];
    const verdict = fraud?.verdict ? String(fraud.verdict).toUpperCase() : null;

    const textHasProblem =
      raw.includes("fraud review required") ||
      raw.includes("review required") ||
      raw.includes("duplicate claim") ||
      raw.includes("❌");
    const textHasWarning =
      raw.includes("anomaly") ||
      raw.includes("⚠️");

    if (reviewRequired || risk === "REVIEW_REQUIRED" || risk === "MEDIUM" || risk === "HIGH" || verdict === "FAIL" || textHasProblem) {
      return {
        status: "warning",
        label: "Fraud Review",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    if (
      anomalies.length > 0 ||
      verdict === "CONFLICT" ||
      verdict === "INDETERMINATE" ||
      textHasWarning
    ) {
      return {
        status: "warning",
        label: "Med Risk",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "pass",
      label: "Low Risk",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "consistency") {
    const cons = findings?.consistency;
    const raw = sectionLines.join(" ").toLowerCase();
    const isConsistent = cons?.is_consistent;
    const docSig = cons?.doctor_signature_verified;
    const patSig = cons?.patient_signature_verified;
    const npi = cons?.npi_verified;
    const priorAuth = cons?.prior_authorization_verified;
    const conflicts = Array.isArray(cons?.conflicts) ? cons.conflicts : [];

    const textHasProblem =
      raw.includes("❌") ||
      raw.includes("mismatch") ||
      raw.includes("fail") ||
      raw.includes("contradiction");
    const textHasWarning =
      raw.includes("⚠️") ||
      raw.includes("unverified") ||
      raw.includes("missing signature") ||
      raw.includes("missing prior authorization");

    if ((isConsistent === false && conflicts.length > 0) || docSig === false || textHasProblem) {
      return {
        status: "problem",
        label: "Mismatch",
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }
    if (
      isConsistent === false ||
      conflicts.length > 0 ||
      patSig === false ||
      npi === false ||
      priorAuth === false ||
      textHasWarning
    ) {
      return {
        status: "warning",
        label: "Review",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "pass",
      label: "Verified",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  if (cat === "executive") {
    const raw = sectionLines.join(" ").toLowerCase();
    const normDecision = String(currentDecision || "").toUpperCase();

    const isReject =
      normDecision === "REJECT" ||
      normDecision === "DENY" ||
      normDecision === "DENIED" ||
      raw.includes("decision: reject") ||
      raw.includes("outcome: reject") ||
      raw.includes("policy denial");
    const isEscalate =
      normDecision === "ESCALATE" ||
      normDecision === "REVIEW" ||
      raw.includes("decision: escalate") ||
      raw.includes("escalation") ||
      raw.includes("human review");

    if (isReject) {
      return {
        status: "problem",
        label: "Denied",
        icon: AlertCircle,
        ...getCardPillClasses("problem"),
        badgeClass: BADGE_PROBLEM,
      };
    }
    if (isEscalate) {
      return {
        status: "warning",
        label: "Review",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "pass",
      label: "Approved",
      icon: CheckCircle2,
      ...getCardPillClasses("pass"),
      badgeClass: BADGE_PASS,
    };
  }

  // Guidance or neutral
  if (cat === "guidance") {
    const raw = sectionLines.join(" ").toLowerCase();
    if (raw.includes("urgent") || raw.includes("action required") || raw.includes("resolve")) {
      return {
        status: "warning",
        label: "Action Req",
        icon: AlertTriangle,
        ...getCardPillClasses("warning"),
        badgeClass: BADGE_WARNING,
      };
    }
    return {
      status: "neutral",
      label: "Actions",
      icon: Compass,
      ...getCardPillClasses("neutral"),
      badgeClass: BADGE_NEUTRAL,
    };
  }

  return {
    status: "neutral",
    label: "Actions",
    icon: Compass,
    ...getCardPillClasses("neutral"),
    badgeClass: BADGE_NEUTRAL,
  };
}

export function ClinicalExplanationViewer({
  content,
  caseId,
  ruleIdMatched,
  decisionConfidence,
  currentDecision,
  modelSource,
  agentFindings,
  onRefresh,
  isRefreshing,
  selectedCategory,
  onCategoryChange,
}: ClinicalExplanationViewerProps) {
  const [internalCategory, setInternalCategory] = useState<string>("all");
  const activeCategory = selectedCategory !== undefined ? selectedCategory : internalCategory;
  const setActiveCategory = (cat: string) => {
    setInternalCategory(cat);
    onCategoryChange?.(cat);
  };

  const [searchQuery, setSearchQuery] = useState("");
  const [isReaderModalOpen, setIsReaderModalOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const handleCopy = (text: string, key: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  // Parse markdown content into structured sections
  const sections = useMemo(() => {
    if (!content) return [];

    const rawLines = content.split("\n");
    const result: ParsedSection[] = [];
    let currentSection: ParsedSection | null = null;

    for (const line of rawLines) {
      const trimmed = line.trim();

      // Skip lines that duplicate verdict header in executive section
      if (
        trimmed.startsWith("**Adjudication Policy Rule:**") ||
        trimmed.startsWith("**Escalation Review Reason:**") ||
        trimmed === "---"
      ) {
        continue;
      }

      // Check section header: ### Executive or #### 1-7
      if (trimmed.startsWith("### ")) {
        if (currentSection && currentSection.rawLines.length > 0) {
          result.push(currentSection);
        }
        currentSection = {
          id: "sec-executive",
          title: "Executive Summary",
          category: "executive",
          rawLines: [],
        };
        continue;
      }

      if (trimmed.startsWith("#### ")) {
        if (currentSection && currentSection.rawLines.length > 0) {
          result.push(currentSection);
        }
        const cleanTitleText = trimmed.replace(/^####\s+/, "");
        const title = sanitizeArchitecturalJargon(cleanTitleText);
        let category: ParsedSection["category"] = "other";
        const lowerTitle = title.toLowerCase();

        if (lowerTitle.includes("executive") || lowerTitle.includes("summary")) {
          category = "executive";
        } else if (lowerTitle.includes("clinical") || lowerTitle.includes("necessity") || lowerTitle.includes("6.")) {
          category = "clinical";
        } else if (lowerTitle.includes("claims") || lowerTitle.includes("financial") || lowerTitle.includes("2.")) {
          category = "claims";
        } else if (lowerTitle.includes("policy") || lowerTitle.includes("benefit") || lowerTitle.includes("3.")) {
          category = "policy";
        } else if (lowerTitle.includes("kyc") || lowerTitle.includes("identity") || lowerTitle.includes("1.")) {
          category = "kyc";
        } else if (lowerTitle.includes("fraud") || lowerTitle.includes("anomaly") || lowerTitle.includes("4.")) {
          category = "fraud";
        } else if (lowerTitle.includes("consistency") || lowerTitle.includes("attestation") || lowerTitle.includes("5.")) {
          category = "consistency";
        } else if (lowerTitle.includes("guidance") || lowerTitle.includes("adjuster") || lowerTitle.includes("operator") || lowerTitle.includes("7.")) {
          category = "guidance";
        }

        currentSection = {
          id: `sec-${category}-${result.length}`,
          title: title.replace(/^\d+\.\s*/, ""),
          category,
          rawLines: [],
        };
        continue;
      }

      if (currentSection) {
        const sanitized = sanitizeArchitecturalJargon(line);
        if (sanitized.trim() || currentSection.rawLines.length > 0) {
          currentSection.rawLines.push(sanitized);
        }
      } else if (trimmed) {
        // Initial lines before any header
        currentSection = {
          id: "sec-intro",
          title: "Executive Summary",
          category: "executive",
          rawLines: [sanitizeArchitecturalJargon(line)],
        };
      }
    }

    if (currentSection && currentSection.rawLines.length > 0) {
      result.push(currentSection);
    }

    return result;
  }, [content]);

  // Compute issue count to show live diagnostics summary
  const issueSummary = useMemo(() => {
    let problemCount = 0;
    let warningCount = 0;
    for (const cat of ORDERED_CATEGORIES) {
      const sec = sections.find((s) => s.category === cat);
      const rawLines = sec ? sec.rawLines : [];
      const h = getCategoryHealth(cat, agentFindings, currentDecision, rawLines);
      if (h.status === "problem") problemCount++;
      else if (h.status === "warning") warningCount++;
    }
    return { problemCount, warningCount, totalIssues: problemCount + warningCount };
  }, [sections, agentFindings, currentDecision]);

  // Filter sections by active tab and search query
  const filteredSections = useMemo(() => {
    return sections.filter((sec) => {
      if (activeCategory !== "all" && sec.category !== activeCategory) {
        return false;
      }
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const matchesTitle = sec.title.toLowerCase().includes(query);
        const matchesContent = sec.rawLines.some((l) => l.toLowerCase().includes(query));
        return matchesTitle || matchesContent;
      }
      return true;
    });
  }, [sections, activeCategory, searchQuery]);

  // Extract decision verdict if present
  const decisionBadge = useMemo(() => {
    if (currentDecision) return currentDecision;
    const lower = content.toLowerCase();
    if (lower.includes("decision: approve") || lower.includes("outcome:** pass") || lower.includes("decision: approve")) {
      return "APPROVE";
    }
    if (lower.includes("decision: reject") || lower.includes("outcome:** reject") || lower.includes("denial")) {
      return "REJECT";
    }
    if (lower.includes("decision: escalate") || lower.includes("escalat")) {
      return "ESCALATE";
    }
    return "EVALUATED";
  }, [content, currentDecision]);

  return (
    <div className="space-y-3">
      {/* Top Filter & Tool Bar */}
      <div className="bg-card-inner p-3 rounded-card border border-border space-y-2.5 transition-colors shadow-xs">
        {/* Navigation Category Matrix - Multi-Line, Non-Scrollable, Full Section Names Fully Visible */}
        <div className="grid min-w-0 grid-cols-2 gap-2">
          {/* All Sections Master Control Card */}
          <button
            type="button"
            onClick={() => setActiveCategory("all")}
            className={`col-span-full flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-btn border p-2.5 text-left transition-all duration-180 active:scale-[0.99] cursor-pointer ${
              activeCategory === "all"
                ? "bg-sky-soft text-sky-deep font-bold shadow-xs border-sky-med/60"
                : "border-border bg-card text-foreground hover:bg-pink-hover/60 hover:border-pink-border hover:text-pink-deep shadow-xs"
            }`}
            title="View All Audit Evaluation Sections Simultaneously"
          >
            <div className="flex min-w-0 flex-1 items-center space-x-2.5">
              <div className={`p-1.5 rounded-btn border shrink-0 transition ${
                activeCategory === "all"
                  ? "bg-sky-soft text-sky-deep border-sky/40 shadow-xs"
                  : "bg-card-inner text-muted border-border"
              }`}>
                <Layers className={`w-4 h-4 ${activeCategory === "all" ? "text-sky-deep" : "text-muted"}`} />
              </div>
              <div className="min-w-0">
                <div className="break-words text-xs font-bold leading-tight text-foreground">All Adjudication Sections</div>
                <div className="break-words text-[10px] text-muted">
                  Full multi-domain audit review across {sections.length} report sections
                </div>
              </div>
            </div>

            <div className="flex max-w-full shrink-0 flex-wrap items-center justify-end gap-2">
              {issueSummary.problemCount > 0 ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-blush text-rose-deep border border-rose-med/40 flex items-center space-x-1 shadow-xs">
                  <AlertCircle className="w-3 h-3 text-rose-deep shrink-0" />
                  <span>{issueSummary.problemCount} Critical {issueSummary.problemCount === 1 ? "Issue" : "Issues"}</span>
                </span>
              ) : issueSummary.warningCount > 0 ? (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-status-warning/15 text-status-warning border border-status-warning/30 flex items-center space-x-1 shadow-xs">
                  <AlertTriangle className="w-3 h-3 text-status-warning shrink-0" />
                  <span>{issueSummary.warningCount} {issueSummary.warningCount === 1 ? "Discrepancy" : "Discrepancies"} Under Review</span>
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-status-success/15 text-status-success border border-status-success/30 flex items-center space-x-1 shadow-xs">
                  <CheckCircle2 className="w-3 h-3 text-status-success shrink-0" />
                  <span>All Verified</span>
                </span>
              )}
              <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-card-inner border border-border text-secondary">
                {sections.length}
              </span>
            </div>
          </button>

          {/* Ordered Clinical Sections (4 Symmetrical Pairs of 2) */}
          {ORDERED_CATEGORIES.map((cat) => {
            const hasCat = sections.some((s) => s.category === cat);
            if (!hasCat) return null;
            const cfg = CATEGORY_CONFIG[cat];
            const sec = sections.find((s) => s.category === cat);
            const CategoryIcon = cfg.icon;
            const health = getCategoryHealth(cat, agentFindings, currentDecision, sec?.rawLines);
            const HealthIcon = health.icon;
            const isActive = activeCategory === cat;

            return (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={`flex min-w-0 items-center justify-between gap-2.5 rounded-btn border p-2.5 text-left transition-all duration-180 active:scale-[0.99] cursor-pointer ${
                  isActive ? health.activePillClass : health.pillClass
                }`}
                title={`Category: ${cfg.name} (${health.label})`}
              >
                <div className="flex min-w-0 flex-1 items-center space-x-2.5">
                  <div
                    className={`p-1.5 rounded-btn border shrink-0 transition ${
                      health.status === "problem"
                        ? isActive
                          ? "bg-rose-blush text-rose-deep border-rose-med/60 shadow-xs"
                          : "bg-rose-blush/40 text-rose-deep border-rose-med/30"
                        : health.status === "warning"
                        ? isActive
                          ? "bg-status-warning/25 text-status-warning border-status-warning/60 shadow-xs"
                          : "bg-status-warning/15 text-status-warning border-status-warning/30"
                        : isActive
                        ? "bg-sky-soft text-accent border-sky/40 shadow-xs"
                        : "bg-card-inner text-muted border-border"
                    }`}
                  >
                    <CategoryIcon
                      className={`w-3.5 h-3.5 ${
                        health.status === "problem"
                          ? "text-rose-deep"
                          : health.status === "warning"
                          ? "text-status-warning"
                          : isActive
                          ? "text-accent"
                          : "text-muted"
                      }`}
                    />
                  </div>
                  <div className="min-w-0">
                    <div className="break-words text-xs font-bold leading-tight text-foreground">
                      {cfg.name}
                    </div>
                  </div>
                </div>

                <span
                  className={`min-w-0 max-w-full px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold flex items-center gap-1 border shadow-xs ${health.badgeClass}`}
                >
                  <HealthIcon className="w-3 h-3 shrink-0" />
                  <span className="break-words text-center leading-tight">{health.label}</span>
                </span>
              </button>
            );
          })}
        </div>

        {/* Action & Filter Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-border">
          {/* Quick Search Input */}
          <div className="relative flex-1 min-w-[180px] sm:max-w-xs">
            <Search className="w-3.5 h-3.5 text-muted absolute left-2.5 top-3" />
            <input
              type="text"
              placeholder="Search reasons, CPT, ICD-10..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full h-9 bg-card text-foreground pl-8 pr-7 py-1.5 rounded-btn text-xs border border-border focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 placeholder:text-muted font-sans shadow-xs transition-colors"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-2.5 text-muted hover:text-foreground"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Quick Diagnostic Status Indicator & Actions */}
          <div className="flex items-center space-x-2">
            {issueSummary.problemCount > 0 ? (
              <div className="flex items-center space-x-1 px-2.5 py-1 rounded-btn bg-rose-blush text-rose-deep border border-rose-med/40 text-[10px] font-semibold">
                <AlertCircle className="w-3 h-3 text-rose-deep shrink-0" />
                <span>
                  {issueSummary.problemCount} Critical {issueSummary.problemCount === 1 ? "Issue" : "Issues"}
                </span>
              </div>
            ) : issueSummary.warningCount > 0 ? (
              <div className="flex items-center space-x-1 px-2.5 py-1 rounded-btn bg-status-warning/15 text-status-warning border border-status-warning/30 text-[10px] font-semibold">
                <AlertTriangle className="w-3 h-3 text-status-warning shrink-0" />
                <span>
                  {issueSummary.warningCount} {issueSummary.warningCount === 1 ? "Discrepancy" : "Discrepancies"} Under Review
                </span>
              </div>
            ) : (
              <div className="flex items-center space-x-1 px-2.5 py-1 rounded-btn bg-status-success/15 text-status-success border border-status-success/30 text-[10px] font-semibold">
                <CheckCircle2 className="w-3 h-3 text-status-success shrink-0" />
                <span>All Checks Passed</span>
              </div>
            )}

            {/* Refresh Button if provided */}
            {onRefresh && (
              <button
                type="button"
                onClick={onRefresh}
                disabled={isRefreshing}
                className="h-8 flex items-center space-x-1.5 px-3 py-1 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border text-[11px] font-medium transition border border-border shadow-xs disabled:opacity-50"
                title="Synthesize fresh clinical narrative"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin text-sky-deep" : "text-muted"}`} />
                <span className="hidden sm:inline">{isRefreshing ? "Updating..." : "Refresh"}</span>
              </button>
            )}

            {/* Reader View Modal Button */}
            <button
              type="button"
              onClick={() => setIsReaderModalOpen(true)}
              className="h-8 flex items-center space-x-1.5 px-3 py-1 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border text-[11px] font-medium transition border border-border shadow-xs"
              title="Expand into comfortable wide Reader View"
            >
              <Maximize2 className="w-3.5 h-3.5 text-muted" />
              <span>Reader View</span>
            </button>
          </div>
        </div>
      </div>


      {/* Render Filtered Sections */}
      <div className="space-y-3.5 max-h-[850px] overflow-y-auto pr-1.5 scrollbar-thin">
        {filteredSections.length === 0 ? (
          <div className="p-8 text-center text-muted text-xs bg-card-inner border border-border rounded-btn space-y-2">
            <Search className="w-6 h-6 text-muted mx-auto" />
            <div>No matching clinical reasons found for &ldquo;{searchQuery}&rdquo;.</div>
            <button
              type="button"
              onClick={() => {
                setSearchQuery("");
                setActiveCategory("all");
              }}
              className="text-foreground hover:underline text-[11px] font-medium"
            >
              Clear filters
            </button>
          </div>
        ) : (
          filteredSections.map((section) => (
            <SectionCard
              key={section.id}
              section={section}
              onCopy={(text) => handleCopy(text, section.id)}
              isCopied={copiedKey === section.id}
              decisionBadge={decisionBadge}
              confidence={decisionConfidence}
              ruleId={ruleIdMatched}
            />
          ))
        )}
      </div>

      {/* Fullscreen / Wide Reader Mode Modal */}
      {isReaderModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-3 sm:p-6 animate-in fade-in duration-150">
          <div className="bg-card border border-border rounded-card w-full max-w-5xl h-[92vh] flex flex-col shadow-2xl overflow-hidden">
            {/* Modal Header */}
            <div className="bg-card-inner px-6 py-4 border-b border-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 rounded-btn bg-card border border-border text-foreground">
                  <Stethoscope className="w-5 h-5 text-muted" />
                </div>
                <div>
                  <div className="flex items-center space-x-2">
                    <h2 className="text-sm font-bold text-foreground tracking-wide">
                      Clinical Adjudication Reader Console
                    </h2>
                    {caseId && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-card text-foreground border border-border">
                        {caseId}
                      </span>
                    )}
                    {ruleIdMatched && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-card text-foreground border border-border">
                        {formatRuleLabel(ruleIdMatched)}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted">
                    Comprehensive clinical alignment, medical necessity grounding, and multi-agent reasoning.
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => handleCopy(content, "modal-all")}
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-btn bg-card hover:bg-card-inner text-foreground text-xs font-medium transition border border-border shadow-xs"
                >
                  {copiedKey === "modal-all" ? (
                    <>
                      <Check className="w-3.5 h-3.5 text-status-success" />
                      <span className="text-status-success">Copied Full Report</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5 text-muted" />
                      <span>Copy Full Report</span>
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setIsReaderModalOpen(false)}
                  className="p-1.5 rounded-btn bg-card hover:bg-border text-muted hover:text-foreground border border-border transition"
                  title="Close Reader Mode"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Modal Body: Left TOC & Right Reader Area */}
            <div className="flex-1 overflow-hidden grid grid-cols-1 md:grid-cols-12 divide-y md:divide-y-0 md:divide-x divide-border">
              {/* Left Column: Quick TOC */}
              <div className="md:col-span-4 p-4 overflow-y-auto space-y-2 bg-card-inner">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-2">
                  Table of Contents
                </div>
                {sections.map((s, idx) => {
                  const cfg = CATEGORY_CONFIG[s.category];
                  const Icon = cfg.icon;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => {
                        const el = document.getElementById(`modal-sec-${idx}`);
                        if (el) el.scrollIntoView({ behavior: "smooth" });
                      }}
                      className="w-full text-left p-2.5 rounded-btn text-xs transition border flex items-start space-x-2.5 bg-card text-foreground border-border hover:bg-card-inner"
                    >
                      <Icon className="w-4 h-4 mt-0.5 shrink-0 text-muted" />
                      <div className="flex-1 truncate">
                        <div className="font-semibold truncate text-foreground">{s.title}</div>
                        <div className="text-[10px] text-muted capitalize">{cfg.name}</div>
                      </div>
                    </button>
                  );
                })}
              </div>

              {/* Right Column: Full Spacious Report */}
              <div className="md:col-span-8 p-6 overflow-y-auto space-y-6 bg-card">
                {sections.map((s, idx) => (
                  <div key={s.id} id={`modal-sec-${idx}`}>
                    <SectionCard
                      section={s}
                      onCopy={(text) => handleCopy(text, s.id)}
                      isCopied={copiedKey === s.id}
                      decisionBadge={decisionBadge}
                      confidence={decisionConfidence}
                      ruleId={ruleIdMatched}
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Subcomponent: Section Card
function SectionCard({
  section,
  onCopy,
  isCopied,
  decisionBadge,
  confidence,
  ruleId,
}: {
  section: ParsedSection;
  onCopy: (text: string) => void;
  isCopied: boolean;
  decisionBadge: string;
  confidence?: number | null;
  ruleId?: string | null;
}) {
  const cfg = CATEGORY_CONFIG[section.category];
  const Icon = cfg.icon;

  // Executive Section
  if (section.category === "executive") {
    return (
      <div className="p-4 rounded-card border border-border bg-card-inner space-y-3 shadow-xs transition-all duration-180 hover:border-pink-border hover:shadow-card-hover">
        <div className="flex items-center justify-between flex-wrap gap-2 border-b border-border pb-2.5">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 rounded-btn bg-[#397FA9] text-white shadow-xs">
              <Icon className="w-3.5 h-3.5" />
            </div>
            <h3 className="text-xs font-bold text-foreground uppercase tracking-wider">{section.title}</h3>
          </div>
          <button
            type="button"
            onClick={() => onCopy(section.rawLines.join("\n"))}
            className="text-[10px] text-muted hover:text-pink-deep flex items-center space-x-1"
          >
            {isCopied ? <Check className="w-3 h-3 text-status-success" /> : <Copy className="w-3 h-3" />}
            <span>{isCopied ? "Copied" : "Copy"}</span>
          </button>
        </div>

        <div className="space-y-1.5 text-xs text-secondary leading-relaxed">
          {section.rawLines.map((line, idx) => (
            <RenderContentLine key={idx} line={line} isClinical={false} />
          ))}
        </div>
      </div>
    );
  }

  // Standard Section Card
  const isClinical = section.category === "clinical";

  return (
    <div
      className="rounded-card border border-border bg-card overflow-hidden shadow-xs transition-all duration-180 hover:border-pink-border hover:shadow-card-hover"
    >
      {/* Header */}
      <div className="px-4 py-2.5 bg-card-inner border-b border-border flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-btn bg-card border border-border text-foreground">
            <Icon className="w-3.5 h-3.5" />
          </div>
          <div>
            <h4 className="text-xs font-bold text-foreground tracking-wide flex items-center space-x-2">
              <span>{section.title}</span>
              {isClinical && (
                <span className="px-2 py-0.5 rounded text-[9px] font-mono font-medium bg-sky-soft text-sky-deep border border-sky/30">
                  Medical Necessity Evaluated
                </span>
              )}
            </h4>
          </div>
        </div>

        <button
          type="button"
          onClick={() => onCopy(section.rawLines.join("\n"))}
          className="text-[10px] text-muted hover:text-pink-deep flex items-center space-x-1 px-2 py-0.5 rounded-btn bg-card hover:bg-pink-hover border border-border hover:border-pink-border transition-all duration-180 active:scale-[0.96]"
        >
          {isCopied ? <Check className="w-3 h-3 text-status-success" /> : <Copy className="w-3 h-3" />}
          <span>{isCopied ? "Copied" : "Copy"}</span>
        </button>
      </div>

      {/* Body */}
      <div className="p-4 space-y-2 text-xs leading-relaxed text-secondary">
        {section.rawLines.map((line, idx) => (
          <RenderContentLine key={idx} line={line} isClinical={isClinical} />
        ))}
      </div>
    </div>
  );
}

// Line Content Parser & Rich Renderer
function RenderContentLine({ line, isClinical }: { line: string; isClinical: boolean }) {
  const trimmed = line.trim();

  if (!trimmed) {
    return <div className="h-1" />;
  }

  if (trimmed === "---") {
    return <hr className="border-border my-2" />;
  }

  // Clinical Sub-Headers (e.g. **Medical Indication Rationale**, **Performance & Evidence Grounding**)
  if (
    trimmed.startsWith("**Medical Indication Rationale") ||
    trimmed.startsWith("**Performance & Evidence Grounding") ||
    trimmed.startsWith("**Clinical Practice Guidelines") ||
    trimmed.startsWith("**Inpatient Bed-Day & Stay Reconciliation") ||
    trimmed.startsWith("**Clinical Discrepancies & Adjuster Guidance") ||
    trimmed.startsWith("- **Itemized Clinical Indication") ||
    trimmed.startsWith("- **Inpatient Hospital Stay & Bed-Day") ||
    trimmed.startsWith("- **Clinical Discrepancies")
  ) {
    const isIndication = trimmed.toLowerCase().includes("indication");
    const isPerformance = trimmed.toLowerCase().includes("performance") || trimmed.toLowerCase().includes("evidence");
    const isGuideline = trimmed.toLowerCase().includes("guideline");
    const isBedDay = trimmed.toLowerCase().includes("bed-day") || trimmed.toLowerCase().includes("stay");
    const isDiscrepancy = trimmed.toLowerCase().includes("discrepanc") || trimmed.toLowerCase().includes("gap");

    const badgeColor = isDiscrepancy
      ? "bg-status-warning/10 text-status-warning border-status-warning/20 shadow-xs"
      : "bg-card-inner text-foreground border-border shadow-xs";

    const HeaderIcon = isDiscrepancy ? AlertTriangle : Stethoscope;

    return (
      <div className={`mt-3 mb-1.5 p-2 rounded-btn border ${badgeColor} flex items-center space-x-2`}>
        <HeaderIcon className="w-3.5 h-3.5 shrink-0 text-muted" />
        <span className="font-semibold text-[11px] uppercase tracking-wider">{renderFormattedTokens(trimmed)}</span>
      </div>
    );
  }

  // Sub-bullet (indented: '  - ')
  if (trimmed.startsWith("  - ") || line.startsWith("    *")) {
    const text = trimmed.replace(/^(\s*-\s*|\s*\*\s*)/, "");
    const isWarning = text.includes("⚠️") || text.includes("Discrepancy");
    return (
      <div
        className={`ml-5 pl-2.5 py-1 my-0.5 rounded text-[11px] border-l-2 ${
          isWarning
            ? "border-status-warning bg-status-warning/10 text-status-warning"
            : "border-border bg-card-inner text-secondary"
        }`}
      >
        {renderFormattedTokens(text)}
      </div>
    );
  }

  // Standard Bullet (starts with '- ' or '* ')
  if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
    const text = trimmed.substring(2);
    const hasPass = text.includes("✅") || text.includes("PASS");
    const hasFail = text.includes("❌") || text.includes("FAIL");
    const hasWarn = text.includes("⚠️") || text.includes("Escalation") || text.includes("Review");

    const bulletIcon = hasPass ? (
      <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0 mt-0.5" />
    ) : hasFail ? (
      <XCircle className="w-3.5 h-3.5 text-rose-deep shrink-0 mt-0.5" />
    ) : hasWarn ? (
      <AlertTriangle className="w-3.5 h-3.5 text-status-warning shrink-0 mt-0.5" />
    ) : (
      <span className="text-muted mt-0.5 text-xs font-bold">&bull;</span>
    );

    return (
      <div className="flex items-start space-x-2 text-foreground py-0.5">
        {bulletIcon}
        <div className="flex-1">{renderFormattedTokens(text)}</div>
      </div>
    );
  }

  // Standard paragraph
  return (
    <p className="text-secondary py-0.5 leading-relaxed">
      {renderFormattedTokens(trimmed)}
    </p>
  );
}

// Token Formatter for CPT, ICD-10, Guidelines, Dollars, and Markdown
function renderFormattedTokens(text: string) {
  // Clean emoji clutter from text if present as icons are rendered separately
  const clean = text.replace(/^[✅❌⚠️ℹ️]\s*/, "");

  const parts: React.ReactNode[] = [];
  // Regex to match: **bold**, `code`, CPT \d{5}, ICD [A-Z]\d{2}(\.\d+)?, [₹€$£]\d+
  const regex = /(\*\*.*?\*\*|`.*?`|\bCPT\s*[0-9]{4,5}\b|\bICD(?:-10)?\s*[:\-]?\s*[A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?\b|[₹€$£][0-9,]+(?:\.[0-9]{2})?|\b(?:PASS|FAIL|APPROVE|REJECT|ESCALATE|MET|NOT_MET|EXPLICITLY_INDICATED|DIRECTLY_DOCUMENTED_PERFORMANCE|MIDNIGHT_CENSUS)\b)/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(clean)) !== null) {
    if (match.index > lastIndex) {
      parts.push(clean.substring(lastIndex, match.index));
    }
    const token = match[0];

    if (token.startsWith("**") && token.endsWith("**")) {
      const inner = token.slice(2, -2);
      parts.push(
        <strong key={match.index} className="text-foreground font-semibold">
          {inner}
        </strong>
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      const inner = token.slice(1, -1);
      parts.push(
        <code
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-card-inner text-foreground border border-border font-mono text-[11px]"
        >
          {inner}
        </code>
      );
    } else if (/^[₹€$£][0-9,]+/.test(token)) {
      parts.push(
        <span key={match.index} className="font-semibold font-mono text-foreground">
          {token}
        </span>
      );
    } else if (/^(?:CPT|ICD)/i.test(token)) {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-card-inner text-secondary border border-border font-mono text-[10px] font-medium"
        >
          {token}
        </span>
      );
    } else if (
      token === "PASS" ||
      token === "APPROVE" ||
      token === "MET" ||
      token === "EXPLICITLY_INDICATED" ||
      token === "DIRECTLY_DOCUMENTED_PERFORMANCE"
    ) {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-status-success/10 text-status-success border border-status-success/20 font-mono text-[10px] font-medium"
        >
          {token}
        </span>
      );
    } else if (token === "FAIL" || token === "REJECT" || token === "NOT_MET") {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-rose-blush text-rose-deep border border-rose-med/30 font-mono text-[10px] font-medium"
        >
          {token}
        </span>
      );
    } else if (token === "ESCALATE" || token === "MIDNIGHT_CENSUS") {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-status-warning/10 text-status-warning border border-status-warning/20 font-mono text-[10px] font-medium"
        >
          {token}
        </span>
      );
    } else {
      parts.push(token);
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < clean.length) {
    parts.push(clean.substring(lastIndex));
  }

  return parts.length > 0 ? parts : clean;
}

