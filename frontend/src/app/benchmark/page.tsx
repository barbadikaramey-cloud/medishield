"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  FileText,
  Filter,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import benchmarkData from "@/data/benchmark_cases.json";
import { formatRuleLabel } from "@/components/ClinicalExplanationViewer";

interface BenchmarkCase {
  cluster_id: string;
  patient_id: string;
  patient_name: string;
  dob: string;
  ssn: string;
  policy_number: string;
  final_decision: string;
  rule_id_matched: string;
  escalation_sub_reason: string | null;
  all_failing_checks: string[];
  kyc_verdict: string;
  kyc_confidence: number;
  claims_verdict: string;
  claims_confidence: number;
  policy_verdict: string;
  policy_confidence: number;
  fraud_score: number;
  fraud_verdict: string;
  reviewed_adjudication_label?: "APPROVE" | "REJECT" | "ESCALATE" | null;
  justification: string;
  detailed_explanation: string;
  model_source: string;
  documents: Array<{
    doc_id: string;
    doc_type: string;
    file_name?: string;
    fraud_label?: boolean;
    fraud_reason?: string;
  }>;
  disputed_record?: {
    audit_finding: string;
    synthetic_anomaly: string;
    formal_disposition: string;
  } | null;
}

export default function BenchmarkEvaluationPage() {
  const [activeFilter, setActiveFilter] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const cases: BenchmarkCase[] = benchmarkData as BenchmarkCase[];

  const filteredCases = useMemo(() => {
    return cases.filter((c) => {
      const matchFilter =
        activeFilter === "ALL" || c.final_decision === activeFilter;
      const q = searchQuery.toLowerCase().trim();
      const matchSearch =
        !q ||
        c.cluster_id.toLowerCase().includes(q) ||
        c.patient_id.toLowerCase().includes(q) ||
        c.patient_name.toLowerCase().includes(q) ||
        c.policy_number.toLowerCase().includes(q) ||
        c.rule_id_matched.toLowerCase().includes(q) ||
        c.detailed_explanation.toLowerCase().includes(q);
      return matchFilter && matchSearch;
    });
  }, [cases, activeFilter, searchQuery]);

  const toggleCard = (clusterId: string) => {
    setExpandedCards((prev) => ({
      ...prev,
      [clusterId]: !prev[clusterId],
    }));
  };

  const expandAll = (expand: boolean) => {
    const next: Record<string, boolean> = {};
    for (const c of cases) {
      next[c.cluster_id] = expand;
    }
    setExpandedCards(next);
  };

  const handleCopy = (clusterId: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(clusterId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const approveCount = cases.filter((c) => c.final_decision === "APPROVE").length;
  const rejectCount = cases.filter((c) => c.final_decision === "REJECT").length;
  const escalateCount = cases.filter((c) => c.final_decision === "ESCALATE").length;

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 w-full space-y-6">
      {/* Top Banner */}
      <div className="bg-card border border-border rounded-card p-5 sm:p-6 shadow-xs relative overflow-hidden">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-1.5 text-accent text-xs font-semibold uppercase tracking-wider mb-1">
              <Sparkles className="w-3.5 h-3.5" />
              <span>Full Benchmark Evaluation & LLM Explainability Portal</span>
            </div>
            <h1 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight">
              30-Case Benchmark Evaluation & Decision Explainability
            </h1>
            <p className="text-secondary text-xs sm:text-sm mt-0.5 max-w-3xl leading-relaxed">
              Exhaustive evaluation of all 30 benchmark patient clusters (<code className="text-accent font-mono text-xs">C_001</code> to <code className="text-accent font-mono text-xs">C_030</code>).
              Each case combines strict deterministic policy rules with multi-agent clinical narratives synthesized via LLM.
            </p>
          </div>

          <div className="flex items-center gap-2 self-start md:self-auto shrink-0">
            <Link
              href="/"
              className="h-8 px-3.5 py-1.5 rounded-btn bg-card-inner hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border text-xs font-semibold transition border border-border flex items-center space-x-1.5 shadow-xs"
            >
              <span>Review Queue</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {/* Metric Badges Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-5 pt-4 border-t border-border">
          <div className="bg-card-inner p-3 rounded-btn border border-border transition hover:border-pink-border">
            <div className="text-[11px] text-muted font-medium uppercase tracking-wider">Total Evaluated</div>
            <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-0.5">30 Clusters</div>
            <div className="text-[10px] text-muted mt-0.5">100% Pipeline Coverage</div>
          </div>
          <div className="bg-card-inner p-3 rounded-btn border border-status-success/30 transition hover:border-status-success/50">
            <div className="text-[11px] text-status-success font-medium uppercase tracking-wider">Clean Approvals</div>
            <div className="text-xl font-bold text-status-success font-mono tabular-nums mt-0.5">{approveCount} (26.7%)</div>
            <div className="text-[10px] text-muted mt-0.5">Automated Clean Pass</div>
          </div>
          <div className="bg-card-inner p-3 rounded-btn border border-rose-med/30 transition hover:border-rose-med/50">
            <div className="text-[11px] text-rose-deep font-medium uppercase tracking-wider">Policy Denials</div>
            <div className="text-xl font-bold text-rose-deep font-mono tabular-nums mt-0.5">{rejectCount} (30.0%)</div>
            <div className="text-[10px] text-muted mt-0.5">Confirmed Exclusion</div>
          </div>
          <div className="bg-card-inner p-3 rounded-btn border border-status-warning/30 transition hover:border-status-warning/50">
            <div className="text-[11px] text-status-warning font-medium uppercase tracking-wider">Specialist Review</div>
            <div className="text-xl font-bold text-status-warning font-mono tabular-nums mt-0.5">{escalateCount} (43.3%)</div>
            <div className="text-[10px] text-muted mt-0.5">Human Review Queue</div>
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-card border border-border rounded-card p-3 flex flex-col md:flex-row items-center justify-between gap-3 shadow-xs">
        {/* Filter Tabs */}
        <div className="flex items-center space-x-1.5 flex-wrap gap-1.5 w-full md:w-auto">
          <button
            type="button"
            onClick={() => setActiveFilter("ALL")}
            className={`h-8 px-3 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 flex items-center space-x-1.5 ${
              activeFilter === "ALL"
                ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/50 shadow-xs"
                : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-border hover:border-pink-border"
            }`}
          >
            <span>All Cases</span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${activeFilter === "ALL" ? "bg-[#397FA9] text-white" : "bg-card-inner text-muted"}`}>
              {cases.length}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("APPROVE")}
            className={`h-8 px-3 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 flex items-center space-x-1.5 ${
              activeFilter === "APPROVE"
                ? "bg-status-success/15 text-status-success font-bold border border-status-success/40 shadow-xs"
                : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-border hover:border-pink-border"
            }`}
          >
            <span>Approved</span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${activeFilter === "APPROVE" ? "bg-status-success text-white" : "bg-card-inner text-muted"}`}>
              {approveCount}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("REJECT")}
            className={`h-8 px-3 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 flex items-center space-x-1.5 ${
              activeFilter === "REJECT"
                ? "bg-rose-blush text-rose-deep font-bold border border-rose-med/40 shadow-xs"
                : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-border hover:border-pink-border"
            }`}
          >
            <span>Rejected</span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${activeFilter === "REJECT" ? "bg-rose-deep text-white" : "bg-card-inner text-muted"}`}>
              {rejectCount}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("ESCALATE")}
            className={`h-8 px-3 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 flex items-center space-x-1.5 ${
              activeFilter === "ESCALATE"
                ? "bg-status-warning/15 text-status-warning font-bold border border-status-warning/40 shadow-xs"
                : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-border hover:border-pink-border"
            }`}
          >
            <span>Escalated</span>
            <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono ${activeFilter === "ESCALATE" ? "bg-status-warning text-white" : "bg-card-inner text-muted"}`}>
              {escalateCount}
            </span>
          </button>
        </div>

        {/* Search & Bulk Expand */}
        <div className="flex items-center space-x-2 w-full md:w-auto">
          <div className="relative flex-1 md:w-72">
            <Search className="w-3.5 h-3.5 text-muted absolute left-3 top-3" />
            <input
              type="text"
              placeholder="Search patient, cluster, policy, rule..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full h-9 bg-card border border-border rounded-btn pl-8 pr-3 py-1.5 text-xs text-foreground placeholder:text-muted focus:outline-none focus:border-sky-med focus:ring-2 focus:ring-sky/30 shadow-xs transition-colors"
            />
          </div>
          <button
            type="button"
            onClick={() => expandAll(true)}
            className="h-9 px-3 py-1.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border text-xs font-medium border border-border transition shadow-xs shrink-0"
            title="Expand all case cards"
          >
            Expand All
          </button>
          <button
            type="button"
            onClick={() => expandAll(false)}
            className="h-9 px-3 py-1.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border text-xs font-medium border border-border transition shadow-xs shrink-0"
            title="Collapse all case cards"
          >
            Collapse All
          </button>
        </div>
      </div>

      {/* Case List */}
      <div className="space-y-3">
        {filteredCases.length === 0 ? (
          <div className="p-12 text-center text-muted bg-card rounded-card border border-border text-xs">
            No benchmark cases match the specified filters.
          </div>
        ) : (
          filteredCases.map((c) => {
            const isExpanded = expandedCards[c.cluster_id] ?? false;
            const isApprove = c.final_decision === "APPROVE";
            const isReject = c.final_decision === "REJECT";
            const caseTrackingId = `CASE-${c.cluster_id.replace("_", "")}-${c.patient_id.replace("_", "")}`;

            return (
              <div
                key={c.cluster_id}
                className={`bg-card border border-border rounded-card overflow-hidden transition-all duration-180 hover:shadow-card-hover hover:-translate-y-0.5 ${
                  isApprove
                    ? "hover:border-status-success/50"
                    : isReject
                    ? "hover:border-rose-med/50"
                    : "hover:border-status-warning/50"
                }`}
              >
                {/* Case Card Header */}
                <div
                  onClick={() => toggleCard(c.cluster_id)}
                  className="p-3.5 sm:p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 cursor-pointer hover:bg-pink-hover/40 transition-colors select-none"
                >
                  <div className="flex items-start sm:items-center space-x-3 min-w-0">
                    <span
                      className={`w-8 h-8 rounded-btn flex items-center justify-center font-bold text-xs shrink-0 ${
                        isApprove
                          ? "bg-status-success/10 text-status-success border border-status-success/20"
                          : isReject
                          ? "bg-rose-blush text-rose-deep border border-rose-med/30"
                          : "bg-status-warning/10 text-status-warning border border-status-warning/20"
                      }`}
                    >
                      {c.cluster_id.replace("C_", "#")}
                    </span>

                    <div className="min-w-0">
                      <div className="flex items-center space-x-2 flex-wrap gap-1">
                        <span className="text-xs font-bold text-foreground truncate max-w-[200px]">
                          {c.patient_name}
                        </span>
                        <span className="text-xs text-muted font-mono shrink-0">
                          ({c.patient_id})
                        </span>
                        <span className="text-[10px] px-2 py-0.2 rounded-full bg-card-inner text-muted font-mono border border-border shrink-0 truncate max-w-[150px]">
                          {c.policy_number}
                        </span>
                      </div>
                      <div className="text-[11px] text-muted mt-0.5 flex items-center space-x-2 flex-wrap gap-1">
                        <span className="shrink-0">DOB: <strong className="text-secondary font-mono">{c.dob}</strong></span>
                        <span className="text-muted/60">&bull;</span>
                        <span className="shrink-0">SSN: <strong className="text-secondary font-mono">{c.ssn}</strong></span>
                        <span className="text-muted/60">&bull;</span>
                        <span className="shrink-0">Docs: <strong className="text-accent font-mono">{c.documents.length}</strong></span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-2 self-start sm:self-auto shrink-0">
                    <span
                      className={`px-2 py-0.5 rounded-full text-[11px] font-bold font-mono border flex items-center space-x-1 ${
                        isApprove
                          ? "bg-status-success/10 text-status-success border-status-success/20"
                          : isReject
                          ? "bg-rose-blush text-rose-deep border border-rose-med/30"
                          : "bg-status-warning/10 text-status-warning border border-status-warning/20"
                      }`}
                    >
                      {isApprove && <CheckCircle2 className="w-3 h-3" />}
                      {isReject && <ShieldAlert className="w-3 h-3" />}
                      {!isApprove && !isReject && <AlertCircle className="w-3 h-3" />}
                      <span>{c.final_decision}</span>
                    </span>

                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-card-inner text-secondary border border-border truncate max-w-[180px]">
                      {formatRuleLabel(c.rule_id_matched)}
                    </span>

                    <button
                      type="button"
                      className="p-1 rounded bg-card-inner hover:bg-pink-hover text-muted hover:text-pink-deep border border-border hover:border-pink-border transition"
                      title={isExpanded ? "Collapse dossier" : "Expand dossier"}
                    >
                      {isExpanded ? (
                        <ChevronUp className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronDown className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Expanded Dossier View */}
                {isExpanded && (
                  <div className="p-4 border-t border-border bg-card-inner space-y-4">
                    {/* Multi-Agent Scorecard */}
                    <div>
                      <div className="text-[10px] font-bold text-muted uppercase tracking-wider mb-2">
                        Multi-Agent Evaluator Scorecard
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                        <div className="p-2.5 rounded-btn bg-card border border-border">
                          <div className="text-[10px] text-muted font-medium">KYC Identity</div>
                          <div className={`text-xs font-bold mt-0.5 font-mono ${c.kyc_verdict === "PASS" ? "text-status-success" : "text-status-danger"}`}>
                            {c.kyc_verdict} ({(c.kyc_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-btn bg-card border border-border">
                          <div className="text-[10px] text-muted font-medium">Line-Item Arithmetic</div>
                          <div className={`text-xs font-bold mt-0.5 font-mono ${c.claims_verdict === "PASS" ? "text-status-success" : "text-status-warning"}`}>
                            {c.claims_verdict} ({(c.claims_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-btn bg-card border border-border">
                          <div className="text-[10px] text-muted font-medium">Policy Coverage</div>
                          <div className={`text-xs font-bold mt-0.5 font-mono ${c.policy_verdict === "PASS" ? "text-status-success" : "text-status-danger"}`}>
                            {c.policy_verdict} ({(c.policy_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-btn bg-card border border-border">
                          <div className="text-[10px] text-muted font-medium">Fraud Score</div>
                          <div className={`text-xs font-bold font-mono mt-0.5 ${c.fraud_score > 0 ? "text-status-warning" : "text-status-success"}`}>
                            {c.fraud_score > 0 ? "REVIEW REQUIRED" : "CLEAR"} ({c.fraud_verdict})
                          </div>
                        </div>
                        <div className="p-2.5 rounded-btn bg-card border border-border">
                          <div className="text-[10px] text-muted font-medium">Reviewed Decision Label</div>
                          <div className="text-xs font-bold text-accent mt-0.5 font-mono">
                            {c.reviewed_adjudication_label ?? "UNREVIEWED"}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* LLM-Backed Clinical Decision Explanation */}
                    <div className="bg-card border border-border rounded-card overflow-hidden shadow-xs">
                      <div className="bg-card-inner px-3.5 py-2 border-b border-border flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <Sparkles className="w-3.5 h-3.5 text-accent" />
                          <span className="text-[11px] font-bold text-foreground uppercase tracking-wider">
                            Clinical & Policy Decision Narrative
                          </span>
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-card text-muted border border-border font-mono">
                            {c.model_source.replace(/\s*\/\s*ADR-\d+\s*Engine/gi, "")}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleCopy(c.cluster_id, c.detailed_explanation)}
                          className="h-7 flex items-center space-x-1.5 px-2.5 py-0.5 rounded-btn bg-card text-muted hover:text-pink-deep hover:bg-pink-hover hover:border-pink-border text-[10px] font-mono transition border border-border shadow-xs"
                        >
                          {copiedId === c.cluster_id ? (
                            <>
                              <Check className="w-3 h-3 text-status-success" />
                              <span className="text-status-success font-sans">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3 text-muted" />
                              <span className="font-sans">Copy Markdown</span>
                            </>
                          )}
                        </button>
                      </div>

                      <div className="p-3.5 text-xs text-secondary leading-relaxed space-y-2 whitespace-pre-wrap font-sans">
                        {c.detailed_explanation}
                      </div>
                    </div>

                    {/* Forensic Auditor Note (if Disputed) */}
                    {c.disputed_record && (
                      <div className="p-3 bg-status-warning/10 border border-status-warning/20 rounded-btn text-xs space-y-1">
                        <div className="font-bold text-status-warning flex items-center space-x-1.5">
                          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                          <span>Synthetic Ground Truth Disputed Record Note</span>
                        </div>
                        <p className="text-secondary mt-1">
                          <strong className="text-foreground">Audit Finding:</strong> {c.disputed_record.audit_finding}
                        </p>
                        <p className="text-secondary">
                          <strong className="text-foreground">Synthetic Anomaly:</strong> {c.disputed_record.synthetic_anomaly}
                        </p>
                        <p className="text-status-warning">
                          <strong>Formal Disposition:</strong> {c.disputed_record.formal_disposition}
                        </p>
                      </div>
                    )}

                    {/* Evidence Document Roster & Direct Navigation */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-1">
                      <div className="flex items-center space-x-2 text-xs text-muted flex-wrap gap-1">
                        <FileText className="w-3.5 h-3.5 text-muted shrink-0" />
                        <span>Submitted Intake Documents:</span>
                        {c.documents.map((d) => (
                          <span
                            key={d.doc_id}
                            className="px-1.5 py-0.5 rounded bg-card border border-border font-mono text-[10px] text-foreground"
                          >
                            {d.doc_id}
                          </span>
                        ))}
                      </div>

                      <Link
                        href={`/cases/${encodeURIComponent(caseTrackingId)}`}
                        className="h-8 inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold transition shrink-0 self-start sm:self-auto shadow-xs"
                      >
                        <span>Open in Review Workspace</span>
                        <ExternalLink className="w-3.5 h-3.5" />
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
