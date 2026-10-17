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
  expected_decision: string;
  is_fraud_ground_truth: boolean;
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
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full space-y-6">
      {/* Top Banner */}
      <div className="bg-gradient-to-r from-slate-900 via-indigo-950/80 to-slate-900 border border-indigo-900/50 rounded-2xl p-6 shadow-xl relative overflow-hidden">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2 text-cyan-400 text-xs font-mono uppercase tracking-wider">
              <Sparkles className="w-4 h-4 animate-pulse" />
              <span>Full Benchmark Evaluation & LLM Explainability Portal</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-white mt-1">
              30-Case Benchmark Evaluation & Decision Explainability
            </h1>
            <p className="text-sm text-slate-300 max-w-3xl mt-2 leading-relaxed">
              Exhaustive evaluation of all 30 benchmark patient clusters (<code className="text-cyan-300 font-mono text-xs">C_001</code> to <code className="text-cyan-300 font-mono text-xs">C_030</code>).
              Each case combines strict deterministic policy rules with rich, multi-agent clinical narratives synthesized via LLM.
            </p>
          </div>

          <div className="flex items-center gap-2 self-start md:self-auto shrink-0">
            <Link
              href="/"
              className="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition border border-slate-700 flex items-center space-x-1.5"
            >
              <span>Review Queue</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {/* Metric Badges Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-6 pt-6 border-t border-indigo-900/40">
          <div className="bg-slate-950/80 p-3 rounded-xl border border-slate-800">
            <div className="text-[11px] text-slate-400 font-medium uppercase tracking-wider">Total Evaluated</div>
            <div className="text-2xl font-extrabold text-white font-mono mt-0.5">30 Clusters</div>
            <div className="text-[10px] text-slate-500 mt-0.5">100% Pipeline Coverage</div>
          </div>
          <div className="bg-slate-950/80 p-3 rounded-xl border border-emerald-900/40">
            <div className="text-[11px] text-emerald-400 font-medium uppercase tracking-wider">Clean Approvals</div>
            <div className="text-2xl font-extrabold text-emerald-300 font-mono mt-0.5">{approveCount} (26.7%)</div>
            <div className="text-[10px] text-emerald-500 mt-0.5">Automated Clean Pass</div>
          </div>
          <div className="bg-slate-950/80 p-3 rounded-xl border border-rose-900/40">
            <div className="text-[11px] text-rose-400 font-medium uppercase tracking-wider">Policy Denials</div>
            <div className="text-2xl font-extrabold text-rose-300 font-mono mt-0.5">{rejectCount} (30.0%)</div>
            <div className="text-[10px] text-rose-500 mt-0.5">Confirmed Policy Exclusion</div>
          </div>
          <div className="bg-slate-950/80 p-3 rounded-xl border border-amber-900/40">
            <div className="text-[11px] text-amber-400 font-medium uppercase tracking-wider">Specialist Review</div>
            <div className="text-2xl font-extrabold text-amber-300 font-mono mt-0.5">{escalateCount} (43.3%)</div>
            <div className="text-[10px] text-amber-500 mt-0.5">Human Review Queue</div>
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col md:flex-row items-center justify-between gap-3 shadow-md">
        {/* Filter Tabs */}
        <div className="flex items-center space-x-1.5 flex-wrap gap-1 w-full md:w-auto">
          <button
            type="button"
            onClick={() => setActiveFilter("ALL")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeFilter === "ALL"
                ? "bg-cyan-600 text-white shadow-md"
                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            All Cases ({cases.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("APPROVE")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeFilter === "APPROVE"
                ? "bg-emerald-600 text-white shadow-md"
                : "bg-slate-800 text-emerald-400 hover:bg-slate-700"
            }`}
          >
            Approved ({approveCount})
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("REJECT")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeFilter === "REJECT"
                ? "bg-rose-600 text-white shadow-md"
                : "bg-slate-800 text-rose-400 hover:bg-slate-700"
            }`}
          >
            Rejected ({rejectCount})
          </button>
          <button
            type="button"
            onClick={() => setActiveFilter("ESCALATE")}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeFilter === "ESCALATE"
                ? "bg-amber-600 text-white shadow-md"
                : "bg-slate-800 text-amber-400 hover:bg-slate-700"
            }`}
          >
            Escalated ({escalateCount})
          </button>
        </div>

        {/* Search & Bulk Expand */}
        <div className="flex items-center space-x-2 w-full md:w-auto">
          <div className="relative flex-1 md:w-72">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search patient, cluster, policy, rule..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
            />
          </div>
          <button
            type="button"
            onClick={() => expandAll(true)}
            className="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition shrink-0"
            title="Expand all case cards"
          >
            Expand All
          </button>
          <button
            type="button"
            onClick={() => expandAll(false)}
            className="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition shrink-0"
            title="Collapse all case cards"
          >
            Collapse All
          </button>
        </div>
      </div>

      {/* Case List */}
      <div className="space-y-4">
        {filteredCases.length === 0 ? (
          <div className="p-12 text-center text-slate-500 bg-slate-900/50 rounded-xl border border-slate-800">
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
                className={`bg-slate-900 border rounded-xl overflow-hidden transition shadow-lg ${
                  isApprove
                    ? "border-emerald-950 hover:border-emerald-700"
                    : isReject
                    ? "border-rose-950 hover:border-rose-700"
                    : "border-amber-950 hover:border-amber-700"
                }`}
              >
                {/* Case Card Header */}
                <div
                  onClick={() => toggleCard(c.cluster_id)}
                  className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 cursor-pointer hover:bg-slate-800/40 transition select-none"
                >
                  <div className="flex items-start sm:items-center space-x-3">
                    <span
                      className={`w-9 h-9 rounded-xl flex items-center justify-center font-bold text-xs shrink-0 ${
                        isApprove
                          ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                          : isReject
                          ? "bg-rose-950 text-rose-400 border border-rose-800"
                          : "bg-amber-950 text-amber-400 border border-amber-800"
                      }`}
                    >
                      {c.cluster_id.replace("C_", "#")}
                    </span>

                    <div>
                      <div className="flex items-center space-x-2 flex-wrap gap-1">
                        <span className="text-sm font-bold text-white">
                          {c.patient_name}
                        </span>
                        <span className="text-xs text-slate-400 font-mono">
                          ({c.patient_id})
                        </span>
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 font-mono border border-slate-700">
                          {c.policy_number}
                        </span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5 flex items-center space-x-3">
                        <span>DOB: <strong className="text-slate-300">{c.dob}</strong></span>
                        <span>•</span>
                        <span>SSN: <strong className="text-slate-300">{c.ssn}</strong></span>
                        <span>•</span>
                        <span>Docs: <strong className="text-cyan-400">{c.documents.length}</strong></span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center space-x-2.5 self-start sm:self-auto shrink-0">
                    <span
                      className={`px-2.5 py-1 rounded-full text-xs font-bold font-mono border flex items-center space-x-1 ${
                        isApprove
                          ? "bg-emerald-950 text-emerald-400 border-emerald-700"
                          : isReject
                          ? "bg-rose-950 text-rose-400 border-rose-700"
                          : "bg-amber-950 text-amber-400 border-amber-700"
                      }`}
                    >
                      {isApprove && <CheckCircle2 className="w-3.5 h-3.5" />}
                      {isReject && <ShieldAlert className="w-3.5 h-3.5" />}
                      {!isApprove && !isReject && <AlertCircle className="w-3.5 h-3.5" />}
                      <span>{c.final_decision}</span>
                    </span>

                    <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-950 text-slate-300 border border-slate-800">
                      {formatRuleLabel(c.rule_id_matched)}
                    </span>

                    <button
                      type="button"
                      className="p-1 rounded bg-slate-800 text-slate-400 hover:text-white"
                      title={isExpanded ? "Collapse dossier" : "Expand dossier"}
                    >
                      {isExpanded ? (
                        <ChevronUp className="w-4 h-4" />
                      ) : (
                        <ChevronDown className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Expanded Dossier View */}
                {isExpanded && (
                  <div className="p-5 border-t border-slate-800 bg-slate-950/70 space-y-5">
                    {/* Multi-Agent Scorecard */}
                    <div>
                      <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
                        Multi-Agent Evaluator Scorecard
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
                        <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="text-[10px] text-slate-400 font-medium">KYC Identity</div>
                          <div className={`text-xs font-bold mt-0.5 ${c.kyc_verdict === "PASS" ? "text-emerald-400" : "text-rose-400"}`}>
                            {c.kyc_verdict} ({(c.kyc_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="text-[10px] text-slate-400 font-medium">Line-Item Arithmetic</div>
                          <div className={`text-xs font-bold mt-0.5 ${c.claims_verdict === "PASS" ? "text-emerald-400" : "text-amber-400"}`}>
                            {c.claims_verdict} ({(c.claims_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="text-[10px] text-slate-400 font-medium">Policy Coverage</div>
                          <div className={`text-xs font-bold mt-0.5 ${c.policy_verdict === "PASS" ? "text-emerald-400" : "text-rose-400"}`}>
                            {c.policy_verdict} ({(c.policy_confidence * 100).toFixed(0)}%)
                          </div>
                        </div>
                        <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="text-[10px] text-slate-400 font-medium">Fraud Score</div>
                          <div className={`text-xs font-bold font-mono mt-0.5 ${c.fraud_score < 0.3 ? "text-emerald-400" : c.fraud_score < 0.7 ? "text-amber-400" : "text-rose-400"}`}>
                            {c.fraud_score.toFixed(2)} ({c.fraud_verdict})
                          </div>
                        </div>
                        <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800">
                          <div className="text-[10px] text-slate-400 font-medium">Expected Ground Truth</div>
                          <div className="text-xs font-bold text-cyan-400 mt-0.5">
                            {c.expected_decision} {c.is_fraud_ground_truth ? "(Fraud)" : "(Clean)"}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* LLM-Backed Clinical Decision Explanation */}
                    <div className="bg-slate-900 border border-indigo-900/60 rounded-xl overflow-hidden shadow-md">
                      <div className="bg-gradient-to-r from-indigo-950 via-slate-900 to-slate-950 px-4 py-2.5 border-b border-indigo-900/40 flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <Sparkles className="w-4 h-4 text-cyan-400" />
                          <span className="text-xs font-bold text-white uppercase tracking-wider">
                            Clinical & Policy Decision Narrative
                          </span>
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono">
                            {c.model_source.replace(/\s*\/\s*ADR-\d+\s*Engine/gi, "")}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleCopy(c.cluster_id, c.detailed_explanation)}
                          className="flex items-center space-x-1 px-2 py-0.5 rounded bg-slate-800 text-slate-300 hover:text-white text-[10px] font-mono transition border border-slate-700"
                        >
                          {copiedId === c.cluster_id ? (
                            <>
                              <Check className="w-3 h-3 text-emerald-400" />
                              <span className="text-emerald-400 font-sans">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3 text-slate-400" />
                              <span className="font-sans">Copy Markdown</span>
                            </>
                          )}
                        </button>
                      </div>

                      <div className="p-4 text-xs text-slate-300 leading-relaxed space-y-2 whitespace-pre-wrap font-sans">
                        {c.detailed_explanation}
                      </div>
                    </div>

                    {/* Forensic Auditor Note (if Disputed) */}
                    {c.disputed_record && (
                      <div className="p-3.5 bg-amber-950/40 border border-amber-700/50 rounded-xl text-xs space-y-1">
                        <div className="font-bold text-amber-300 flex items-center space-x-1.5">
                          <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
                          <span>Synthetic Ground Truth Disputed Record Note</span>
                        </div>
                        <p className="text-slate-300 mt-1">
                          <strong>Audit Finding:</strong> {c.disputed_record.audit_finding}
                        </p>
                        <p className="text-slate-300">
                          <strong>Synthetic Anomaly:</strong> {c.disputed_record.synthetic_anomaly}
                        </p>
                        <p className="text-amber-200">
                          <strong>Formal Disposition:</strong> {c.disputed_record.formal_disposition}
                        </p>
                      </div>
                    )}

                    {/* Evidence Document Roster & Direct Navigation */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2">
                      <div className="flex items-center space-x-2 text-xs text-slate-400 flex-wrap gap-1">
                        <FileText className="w-4 h-4 text-slate-500 shrink-0" />
                        <span>Submitted Intake Documents:</span>
                        {c.documents.map((d) => (
                          <span
                            key={d.doc_id}
                            className="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 font-mono text-[10px] text-slate-300"
                          >
                            {d.doc_id}
                          </span>
                        ))}
                      </div>

                      <Link
                        href={`/cases/${encodeURIComponent(caseTrackingId)}`}
                        className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition shrink-0 self-start sm:self-auto shadow-md"
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
