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

export interface ClinicalExplanationViewerProps {
  content: string;
  caseId?: string;
  ruleIdMatched?: string | null;
  decisionConfidence?: number | null;
  currentDecision?: string | null;
  modelSource?: string | null;
  onRefresh?: () => void;
  isRefreshing?: boolean;
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
    accentBorder: "border-cyan-500/40",
    accentBg: "bg-cyan-950/15",
    badgeBg: "bg-cyan-950/80 border-cyan-800/80",
    badgeText: "text-cyan-300",
    shortLabel: "Executive",
  },
  kyc: {
    name: "Member Identity & KYC",
    icon: ShieldCheck,
    accentBorder: "border-sky-500/30",
    accentBg: "bg-sky-950/10",
    badgeBg: "bg-sky-950/80 border-sky-800/80",
    badgeText: "text-sky-300",
    shortLabel: "1. KYC",
  },
  claims: {
    name: "Claims Financial & Lines",
    icon: Receipt,
    accentBorder: "border-blue-500/30",
    accentBg: "bg-blue-950/10",
    badgeBg: "bg-blue-950/80 border-blue-800/80",
    badgeText: "text-blue-300",
    shortLabel: "2. Claims",
  },
  policy: {
    name: "Policy Coverage & Benefit",
    icon: BookOpen,
    accentBorder: "border-purple-500/30",
    accentBg: "bg-purple-950/10",
    badgeBg: "bg-purple-950/80 border-purple-800/80",
    badgeText: "text-purple-300",
    shortLabel: "3. Policy",
  },
  fraud: {
    name: "Fraud Risk & Anomaly",
    icon: Radar,
    accentBorder: "border-amber-500/30",
    accentBg: "bg-amber-950/10",
    badgeBg: "bg-amber-950/80 border-amber-800/80",
    badgeText: "text-amber-300",
    shortLabel: "4. Fraud",
  },
  consistency: {
    name: "Cross-Doc Consistency",
    icon: FileCheck,
    accentBorder: "border-indigo-500/30",
    accentBg: "bg-indigo-950/10",
    badgeBg: "bg-indigo-950/80 border-indigo-800/80",
    badgeText: "text-indigo-300",
    shortLabel: "5. Attestation",
  },
  clinical: {
    name: "Clinical Alignment & Medical Necessity (ADR-015)",
    icon: Stethoscope,
    accentBorder: "border-emerald-500/50",
    accentBg: "bg-emerald-950/20",
    badgeBg: "bg-emerald-950/90 border-emerald-700/80",
    badgeText: "text-emerald-300",
    shortLabel: "6. Clinical Alignment",
  },
  guidance: {
    name: "Reviewer Action Guidance",
    icon: Compass,
    accentBorder: "border-cyan-500/30",
    accentBg: "bg-cyan-950/10",
    badgeBg: "bg-cyan-950/80 border-cyan-800/80",
    badgeText: "text-cyan-300",
    shortLabel: "7. Guidance",
  },
  other: {
    name: "Additional Rationale",
    icon: Info,
    accentBorder: "border-slate-800",
    accentBg: "bg-slate-900/40",
    badgeBg: "bg-slate-800 border-slate-700",
    badgeText: "text-slate-300",
    shortLabel: "Other",
  },
};

export function ClinicalExplanationViewer({
  content,
  caseId,
  ruleIdMatched,
  decisionConfidence,
  currentDecision,
  modelSource,
  onRefresh,
  isRefreshing,
}: ClinicalExplanationViewerProps) {
  const [activeCategory, setActiveCategory] = useState<string>("all");
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

      // Check section header: ### Executive or #### 1-7
      if (trimmed.startsWith("### ")) {
        if (currentSection) {
          result.push(currentSection);
        }
        currentSection = {
          id: "sec-executive",
          title: trimmed.replace(/^###\s+/, ""),
          category: "executive",
          rawLines: [],
        };
        continue;
      }

      if (trimmed.startsWith("#### ")) {
        if (currentSection) {
          result.push(currentSection);
        }
        const title = trimmed.replace(/^####\s+/, "");
        let category: ParsedSection["category"] = "other";
        const lowerTitle = title.toLowerCase();

        if (lowerTitle.includes("kyc") || lowerTitle.includes("identity") || lowerTitle.includes("1.")) {
          category = "kyc";
        } else if (lowerTitle.includes("claims") || lowerTitle.includes("financial") || lowerTitle.includes("2.")) {
          category = "claims";
        } else if (lowerTitle.includes("policy") || lowerTitle.includes("benefit") || lowerTitle.includes("3.")) {
          category = "policy";
        } else if (lowerTitle.includes("fraud") || lowerTitle.includes("anomaly") || lowerTitle.includes("4.")) {
          category = "fraud";
        } else if (lowerTitle.includes("consistency") || lowerTitle.includes("attestation") || lowerTitle.includes("5.")) {
          category = "consistency";
        } else if (lowerTitle.includes("clinical") || lowerTitle.includes("necessity") || lowerTitle.includes("6.")) {
          category = "clinical";
        } else if (lowerTitle.includes("guidance") || lowerTitle.includes("adjuster") || lowerTitle.includes("operator") || lowerTitle.includes("7.")) {
          category = "guidance";
        }

        currentSection = {
          id: `sec-${category}-${result.length}`,
          title,
          category,
          rawLines: [],
        };
        continue;
      }

      if (currentSection) {
        currentSection.rawLines.push(line);
      } else if (trimmed) {
        // Initial lines before any header
        currentSection = {
          id: "sec-intro",
          title: "Adjudication Summary",
          category: "executive",
          rawLines: [line],
        };
      }
    }

    if (currentSection) {
      result.push(currentSection);
    }

    return result;
  }, [content]);

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
      <div className="flex flex-wrap items-center justify-between gap-2 bg-slate-950/80 p-2.5 rounded-lg border border-slate-800">
        {/* Navigation Category Tabs */}
        <div className="flex flex-wrap items-center gap-1.5 overflow-x-auto text-xs">
          <button
            type="button"
            onClick={() => setActiveCategory("all")}
            className={`px-2.5 py-1 rounded-md transition font-medium text-[11px] flex items-center space-x-1.5 ${
              activeCategory === "all"
                ? "bg-slate-800 text-white font-semibold shadow-sm border border-slate-700"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
            }`}
          >
            <span>All Sections</span>
            <span className="px-1.5 py-0.2 rounded-full text-[9px] bg-slate-900 text-slate-400 border border-slate-800">
              {sections.length}
            </span>
          </button>

          {/* Clinical Alignment Priority Tab */}
          {sections.some((s) => s.category === "clinical") && (
            <button
              type="button"
              onClick={() => setActiveCategory("clinical")}
              className={`px-2.5 py-1 rounded-md transition font-medium text-[11px] flex items-center space-x-1.5 border ${
                activeCategory === "clinical"
                  ? "bg-emerald-950 text-emerald-300 border-emerald-700 font-bold shadow-sm"
                  : "bg-emerald-950/30 text-emerald-400/90 border-emerald-900/60 hover:bg-emerald-900/40"
              }`}
            >
              <HeartPulse className="w-3.5 h-3.5 text-emerald-400" />
              <span>Clinical Alignment</span>
              <span className="px-1.5 py-0.2 rounded-full text-[9px] bg-emerald-900/80 text-emerald-200 border border-emerald-700">
                ADR-015
              </span>
            </button>
          )}

          {/* Other Categories */}
          {["executive", "claims", "policy", "fraud", "consistency", "guidance"].map((cat) => {
            const hasCat = sections.some((s) => s.category === cat);
            if (!hasCat) return null;
            const cfg = CATEGORY_CONFIG[cat];
            const Icon = cfg.icon;
            const isActive = activeCategory === cat;
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={`px-2 py-1 rounded-md transition font-medium text-[11px] flex items-center space-x-1 ${
                  isActive
                    ? "bg-slate-800 text-cyan-300 border border-slate-700 font-semibold"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-900"
                }`}
              >
                <Icon className="w-3 h-3 text-slate-400" />
                <span>{cfg.shortLabel}</span>
              </button>
            );
          })}
        </div>

        {/* Search & Action Controls */}
        <div className="flex items-center space-x-2 w-full sm:w-auto">
          {/* Quick Search Input */}
          <div className="relative flex-1 sm:w-44">
            <Search className="w-3 h-3 text-slate-500 absolute left-2 top-2" />
            <input
              type="text"
              placeholder="Search reasons, CPT, ICD..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-900 text-slate-200 pl-7 pr-2 py-1 rounded text-[11px] border border-slate-800 focus:outline-none focus:border-cyan-600 placeholder:text-slate-500 font-sans"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2 top-1.5 text-slate-500 hover:text-slate-300"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>

          {/* Reader View Modal Button */}
          <button
            type="button"
            onClick={() => setIsReaderModalOpen(true)}
            className="flex items-center space-x-1 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-[11px] font-medium transition border border-slate-700"
            title="Expand into comfortable wide Reader View"
          >
            <Maximize2 className="w-3 h-3 text-cyan-400" />
            <span className="hidden md:inline">Reader View</span>
          </button>
        </div>
      </div>

      {/* Render Filtered Sections */}
      <div className="space-y-3.5 max-h-[700px] overflow-y-auto pr-1">
        {filteredSections.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-xs bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
            <Search className="w-6 h-6 text-slate-600 mx-auto" />
            <div>No matching clinical reasons found for &ldquo;{searchQuery}&rdquo;.</div>
            <button
              type="button"
              onClick={() => {
                setSearchQuery("");
                setActiveCategory("all");
              }}
              className="text-cyan-400 hover:underline text-[11px]"
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
        <div className="fixed inset-0 z-50 bg-slate-950/90 backdrop-blur-md flex items-center justify-center p-3 sm:p-6 animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-5xl h-[92vh] flex flex-col shadow-2xl overflow-hidden">
            {/* Modal Header */}
            <div className="bg-slate-950/90 px-6 py-4 border-b border-slate-800 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 rounded-xl bg-gradient-to-br from-indigo-900 to-cyan-950 border border-cyan-800/80">
                  <Stethoscope className="w-5 h-5 text-cyan-400" />
                </div>
                <div>
                  <div className="flex items-center space-x-2">
                    <h2 className="text-sm font-bold text-white tracking-wide">
                      Clinical Adjudication Reader Console
                    </h2>
                    {caseId && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-cyan-300 border border-slate-700">
                        {caseId}
                      </span>
                    )}
                    {ruleIdMatched && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800">
                        {ruleIdMatched}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-slate-400">
                    Comprehensive clinical alignment, medical necessity grounding, and multi-agent reasoning.
                  </p>
                </div>
              </div>

              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => handleCopy(content, "modal-all")}
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium transition border border-slate-700"
                >
                  {copiedKey === "modal-all" ? (
                    <>
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                      <span className="text-emerald-400">Copied Full Report</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5 text-slate-400" />
                      <span>Copy Full Report</span>
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setIsReaderModalOpen(false)}
                  className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition"
                  title="Close Reader Mode"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Modal Body: Left TOC & Right Reader Area */}
            <div className="flex-1 overflow-hidden grid grid-cols-1 md:grid-cols-12 divide-y md:divide-y-0 md:divide-x divide-slate-800">
              {/* Left Column: Quick TOC */}
              <div className="md:col-span-4 p-4 overflow-y-auto space-y-2 bg-slate-950/50">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-2">
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
                      className={`w-full text-left p-2.5 rounded-lg text-xs transition border flex items-start space-x-2.5 ${
                        s.category === "clinical"
                          ? "bg-emerald-950/40 text-emerald-200 border-emerald-800/80 hover:bg-emerald-900/50"
                          : "bg-slate-900/60 text-slate-300 border-slate-800/80 hover:bg-slate-800 hover:text-white"
                      }`}
                    >
                      <Icon className="w-4 h-4 mt-0.5 shrink-0 text-cyan-400" />
                      <div className="flex-1 truncate">
                        <div className="font-semibold truncate">{s.title}</div>
                        <div className="text-[10px] text-slate-400 capitalize">{cfg.name}</div>
                      </div>
                    </button>
                  );
                })}
              </div>

              {/* Right Column: Full Spacious Report */}
              <div className="md:col-span-8 p-6 overflow-y-auto space-y-6 bg-slate-950/70">
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

  // Executive Section has a specialized hero banner
  if (section.category === "executive") {
    return (
      <div className={`p-4 rounded-xl border ${cfg.accentBorder} ${cfg.accentBg} space-y-3 shadow-md`}>
        <div className="flex items-center justify-between flex-wrap gap-2 border-b border-slate-800/80 pb-2.5">
          <div className="flex items-center space-x-2">
            <Icon className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-bold text-white uppercase tracking-wider">{section.title}</h3>
          </div>
          <button
            type="button"
            onClick={() => onCopy(section.rawLines.join("\n"))}
            className="text-[10px] text-slate-400 hover:text-cyan-300 flex items-center space-x-1"
          >
            {isCopied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
            <span>{isCopied ? "Copied" : "Copy"}</span>
          </button>
        </div>

        <div className="flex items-center justify-between flex-wrap gap-3 bg-slate-900/80 p-3 rounded-lg border border-slate-800">
          <div className="flex items-center space-x-2">
            <span
              className={`px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wider ${
                decisionBadge === "APPROVE"
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                  : decisionBadge === "REJECT"
                  ? "bg-rose-950 text-rose-400 border border-rose-800"
                  : "bg-amber-950 text-amber-400 border border-amber-800"
              }`}
            >
              Outcome: {decisionBadge}
            </span>
            {ruleId && (
              <span className="px-2 py-1 rounded-md text-xs font-mono bg-slate-800 text-slate-200 border border-slate-700">
                Rule: {ruleId}
              </span>
            )}
          </div>

          {confidence !== undefined && confidence !== null && (
            <div className="flex items-center space-x-2">
              <span className="text-[11px] text-slate-400">Calibrated Confidence:</span>
              <span className="text-sm font-bold font-mono text-cyan-300">
                {(confidence * 100).toFixed(0)}%
              </span>
            </div>
          )}
        </div>

        <div className="space-y-1.5 text-xs text-slate-200 leading-relaxed">
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
      className={`rounded-xl border ${cfg.accentBorder} ${cfg.accentBg} overflow-hidden shadow-sm transition hover:border-slate-700`}
    >
      {/* Header */}
      <div className="px-4 py-2.5 bg-slate-900/90 border-b border-slate-800/80 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center space-x-2">
          <div className={`p-1.5 rounded-lg ${cfg.badgeBg} border`}>
            <Icon className={`w-3.5 h-3.5 ${cfg.badgeText}`} />
          </div>
          <div>
            <h4 className="text-xs font-bold text-white tracking-wide flex items-center space-x-2">
              <span>{section.title}</span>
              {isClinical && (
                <span className="px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-700">
                  Deterministic ADR-015 Verified
                </span>
              )}
            </h4>
          </div>
        </div>

        <button
          type="button"
          onClick={() => onCopy(section.rawLines.join("\n"))}
          className="text-[10px] text-slate-400 hover:text-cyan-300 flex items-center space-x-1 px-1.5 py-0.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 transition"
        >
          {isCopied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          <span>{isCopied ? "Copied" : "Copy"}</span>
        </button>
      </div>

      {/* Body */}
      <div className="p-4 space-y-2 text-xs leading-relaxed">
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
    return <hr className="border-slate-800 my-2" />;
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

    const badgeColor = isIndication
      ? "bg-sky-950 text-sky-300 border-sky-800"
      : isPerformance
      ? "bg-emerald-950 text-emerald-300 border-emerald-800"
      : isGuideline
      ? "bg-purple-950 text-purple-300 border-purple-800"
      : isBedDay
      ? "bg-indigo-950 text-indigo-300 border-indigo-800"
      : isDiscrepancy
      ? "bg-amber-950 text-amber-300 border-amber-800"
      : "bg-slate-800 text-slate-300 border-slate-700";

    const HeaderIcon = isIndication
      ? Activity
      : isPerformance
      ? FileCheck
      : isGuideline
      ? BookOpen
      : isBedDay
      ? Calendar
      : isDiscrepancy
      ? AlertTriangle
      : Stethoscope;

    return (
      <div className={`mt-3 mb-1.5 p-2 rounded-lg border ${badgeColor} flex items-center space-x-2`}>
        <HeaderIcon className="w-3.5 h-3.5 shrink-0" />
        <span className="font-bold text-[11px] uppercase tracking-wider">{renderFormattedTokens(trimmed)}</span>
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
            ? "border-amber-500 bg-amber-950/20 text-amber-200"
            : "border-slate-700 bg-slate-900/50 text-slate-300"
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
      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
    ) : hasFail ? (
      <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />
    ) : hasWarn ? (
      <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
    ) : (
      <span className="text-cyan-400 mt-0.5 text-xs font-bold">&bull;</span>
    );

    return (
      <div className="flex items-start space-x-2 text-slate-200 py-0.5">
        {bulletIcon}
        <div className="flex-1">{renderFormattedTokens(text)}</div>
      </div>
    );
  }

  // Standard paragraph
  return (
    <p className="text-slate-300 py-0.5 leading-relaxed">
      {renderFormattedTokens(trimmed)}
    </p>
  );
}

// Token Formatter for CPT, ICD-10, Guidelines, Dollars, and Markdown
function renderFormattedTokens(text: string) {
  // Clean emoji clutter from text if present as icons are rendered separately
  const clean = text.replace(/^[✅❌⚠️ℹ️]\s*/, "");

  const parts: React.ReactNode[] = [];
  // Regex to match: **bold**, `code`, CPT \d{5}, ICD [A-Z]\d{2}(\.\d+)?, $\d+
  const regex = /(\*\*.*?\*\*|`.*?`|\bCPT\s*[0-9]{4,5}\b|\bICD(?:-10)?\s*[:\-]?\s*[A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?\b|\$[0-9,]+(?:\.[0-9]{2})?|\b(?:PASS|FAIL|APPROVE|REJECT|ESCALATE|MET|NOT_MET|EXPLICITLY_INDICATED|DIRECTLY_DOCUMENTED_PERFORMANCE|MIDNIGHT_CENSUS)\b)/g;

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
        <strong key={match.index} className="text-white font-semibold">
          {inner}
        </strong>
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      const inner = token.slice(1, -1);
      parts.push(
        <code
          key={match.index}
          className="px-1.5 py-0.5 rounded bg-slate-900 text-cyan-300 border border-slate-800 font-mono text-[11px]"
        >
          {inner}
        </code>
      );
    } else if (/^\$[0-9,]+/.test(token)) {
      parts.push(
        <span key={match.index} className="font-bold text-emerald-400 font-mono">
          {token}
        </span>
      );
    } else if (/^CPT\s*[0-9]+/i.test(token)) {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.2 rounded bg-cyan-950 text-cyan-300 border border-cyan-800 font-mono text-[10px] font-bold"
        >
          {token}
        </span>
      );
    } else if (/^ICD/i.test(token)) {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.2 rounded bg-teal-950 text-teal-300 border border-teal-800 font-mono text-[10px] font-bold"
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
          className="px-1.5 py-0.2 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono text-[10px] font-bold"
        >
          {token}
        </span>
      );
    } else if (token === "FAIL" || token === "REJECT" || token === "NOT_MET") {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.2 rounded bg-rose-950 text-rose-300 border border-rose-800 font-mono text-[10px] font-bold"
        >
          {token}
        </span>
      );
    } else if (token === "ESCALATE" || token === "MIDNIGHT_CENSUS") {
      parts.push(
        <span
          key={match.index}
          className="px-1.5 py-0.2 rounded bg-amber-950 text-amber-300 border border-amber-800 font-mono text-[10px] font-bold"
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

