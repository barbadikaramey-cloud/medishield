"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  FileText,
  Filter,
  Layers,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  TrendingUp,
  Upload,
  X,
  XCircle,
  Zap,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  getCases,
  getOperationalMetrics,
  submitIntake,
  CaseListItem,
  OperationalMetricsResponse,
} from "@/lib/api";
import { formatRelativeTime, formatDateTime } from "@/lib/date";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS_FILTERS = [
  { label: "All Cases", value: "" },
  { label: "Awaiting Review", value: "AWAITING_HUMAN" },
  { label: "Awaiting Info", value: "AWAITING_INFO" },
  { label: "Completed", value: "COMPLETED" },
  { label: "Failed", value: "FAILED" },
];

export default function QueueDashboardPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [cases, setCases] = useState<CaseListItem[]>([]);
  const [metrics, setMetrics] = useState<OperationalMetricsResponse | null>(null);
  const [totalCount, setTotalCount] = useState(0);
  const [activeFilter, setActiveFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Ingestion Modal State & Multi-Folder Staging
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [claimType, setClaimType] = useState("STANDARD_REIMBURSEMENT_CLAIM");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Quick keyboard shortcut to focus search with '/'
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [casesRes, metricsRes] = await Promise.allSettled([
        getCases({
          state: activeFilter || undefined,
          page: 1,
          page_size: 50,
        }),
        getOperationalMetrics(),
      ]);

      if (casesRes.status === "fulfilled") {
        setCases(casesRes.value.items || []);
        setTotalCount(casesRes.value.total_count || 0);
      } else {
        throw casesRes.reason;
      }

      if (metricsRes.status === "fulfilled") {
        setMetrics(metricsRes.value);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load cases.");
    } finally {
      setLoading(false);
    }
  }, [activeFilter]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Derived real-time statistics
  const stats = useMemo(() => {
    const total = cases.length;
    let approved = 0;
    let rejected = 0;
    let awaitingHuman = 0;
    let awaitingInfo = 0;
    let completed = 0;
    let failed = 0;

    for (const c of cases) {
      if (c.current_decision === "APPROVE") approved += 1;
      else if (c.current_decision === "REJECT") rejected += 1;

      if (c.workflow_state === "AWAITING_HUMAN") awaitingHuman += 1;
      else if (c.workflow_state === "AWAITING_INFO") awaitingInfo += 1;
      else if (c.workflow_state === "COMPLETED") completed += 1;
      else if (c.workflow_state === "FAILED") failed += 1;
    }

    const totalDecisions = approved + rejected + awaitingHuman;
    const approvePct = totalDecisions > 0 ? (approved / totalDecisions) * 100 : 0;
    const rejectPct = totalDecisions > 0 ? (rejected / totalDecisions) * 100 : 0;
    const reviewPct = totalDecisions > 0 ? (awaitingHuman / totalDecisions) * 100 : 0;

    // Operational metrics fallback or enhanced
    const stpRate = metrics?.straight_through_processing_rate != null
      ? metrics.straight_through_processing_rate * 100
      : total > 0 ? ((approved + rejected) / total) * 100 : 0;

    const latencyMs = metrics?.average_adjudication_latency_ms;

    return {
      total: totalCount || total,
      approved,
      rejected,
      awaitingHuman: metrics?.cases_awaiting_human ?? awaitingHuman,
      awaitingInfo,
      completed,
      failed,
      approvePct,
      rejectPct,
      reviewPct,
      stpRate,
      latencyDisplay: latencyMs != null ? (latencyMs < 1000 ? `${latencyMs}ms` : `${(latencyMs / 1000).toFixed(1)}s`) : "—",
    };
  }, [cases, totalCount, metrics]);

  const handleAddFiles = (incoming: FileList | File[] | null) => {
    if (!incoming || incoming.length === 0) return;
    const newFiles = Array.from(incoming);
    setStagedFiles((prev) => {
      const existingKeys = new Set(prev.map((f) => `${f.name}_${f.size}_${f.lastModified}`));
      const unique = newFiles.filter((f) => !existingKeys.has(`${f.name}_${f.size}_${f.lastModified}`));
      return [...prev, ...unique];
    });
    setUploadError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleRemoveFile = (index: number) => {
    setStagedFiles((prev) => prev.filter((_, idx) => idx !== index));
  };

  const handleClearFiles = () => {
    setStagedFiles([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setStagedFiles([]);
    setUploadError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleFileUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (stagedFiles.length === 0) {
      setUploadError("Please select at least one document to upload.");
      return;
    }
    setUploading(true);
    setUploadError(null);

    const formData = new FormData();
    for (const file of stagedFiles) {
      formData.append("files", file);
    }
    formData.append("claim_type", claimType);

    try {
      await submitIntake(formData);
      handleCloseModal();
      await loadData();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const filteredCases = cases.filter((c) => {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase();
    return (
      c.case_id.toLowerCase().includes(query) ||
      (c.current_sub_reason && c.current_sub_reason.toLowerCase().includes(query)) ||
      (c.current_decision && c.current_decision.toLowerCase().includes(query))
    );
  });

  if (!authLoading && user?.role === "CLAIMER") {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center">
        <div className="p-4 bg-accent/10 border border-accent/20 rounded-2xl mb-6 inline-block">
          <ShieldCheck className="w-12 h-12 text-accent mx-auto" />
        </div>
        <h2 className="text-2xl font-bold text-foreground mb-2">Claimer Account Detected</h2>
        <p className="text-sm text-secondary mb-6 max-w-lg mx-auto leading-relaxed">
          You are authenticated as a <strong>Claimer</strong> (Patient / Provider). Under MediShield RBAC, the internal operational review queue is reserved for licensed claims adjusters.
        </p>
        <Link
          href="/claimer"
          className="inline-flex items-center space-x-2 px-5 py-2.5 bg-accent hover:bg-accent-hover text-accent-fg rounded-btn font-semibold text-sm transition-colors shadow-sm"
        >
          <span>Open Claimer Portal</span>
          <ArrowRight className="w-4 h-4" />
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <h1 className="text-2xl font-bold tracking-tight text-foreground">Operations Queue</h1>
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-sky-soft text-accent border border-sky/30 shadow-xs">
              Live Control Plane
            </span>
          </div>
          <p className="text-xs text-secondary mt-0.5">
            Real-time human-in-the-loop review queue & deterministic intake pipeline
          </p>
        </div>

        <div className="flex items-center space-x-2.5">
          <button
            onClick={() => loadData()}
            disabled={loading}
            className="inline-flex items-center space-x-1.5 h-9 px-3.5 py-2 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep text-xs font-medium border border-border hover:border-pink-border transition-all duration-180 shadow-xs active:scale-[0.98] disabled:opacity-50"
            title="Refresh case queue"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-sky-deep" : "text-muted"}`} />
            <span>Refresh</span>
          </button>
          <button
            onClick={() => setIsModalOpen(true)}
            className="inline-flex items-center space-x-1.5 h-9 px-4 py-2 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            <span>New Case Intake</span>
          </button>
        </div>
      </div>

      {/* Executive KPI Cards Grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {/* Total Processed */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-pink-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-muted text-[11px] font-medium">
            <span>Total Roster</span>
            <Layers className="w-3.5 h-3.5 text-muted" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5">
            {stats.total}
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            Active control plane
          </div>
        </div>

        {/* Automated Approvals */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-success-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-success text-[11px] font-medium">
            <span>Approvals</span>
            <CheckCircle2 className="w-3.5 h-3.5" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5 flex items-baseline space-x-1.5">
            <span>{stats.approved}</span>
            <span className="text-xs font-medium text-success">
              {stats.approvePct.toFixed(0)}%
            </span>
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            Deterministic rule pass
          </div>
        </div>

        {/* Policy Denials */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-danger-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-danger text-[11px] font-medium">
            <span>Denials</span>
            <XCircle className="w-3.5 h-3.5" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5 flex items-baseline space-x-1.5">
            <span>{stats.rejected}</span>
            <span className="text-xs font-medium text-danger">
              {stats.rejectPct.toFixed(0)}%
            </span>
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            Hard policy exclusion
          </div>
        </div>

        {/* Pending Human Review */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-warning-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-warning text-[11px] font-medium">
            <span>Needs Review</span>
            <AlertTriangle className="w-3.5 h-3.5" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5 flex items-baseline space-x-1.5">
            <span>{stats.awaitingHuman}</span>
            <span className="text-xs font-medium text-warning">
              {stats.reviewPct.toFixed(0)}%
            </span>
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            Awaiting adjuster action
          </div>
        </div>

        {/* Automation Rate (STP) */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-pink-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-sky-deep text-[11px] font-medium">
            <span>Automation (STP)</span>
            <Zap className="w-3.5 h-3.5" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5">
            {stats.stpRate.toFixed(1)}%
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            Target &gt; 80.0%
          </div>
        </div>

        {/* Adjudication Latency */}
        <div className="bg-card hover:bg-pink-hover/40 border border-border hover:border-pink-border rounded-card p-3.5 shadow-xs hover:shadow-card-hover transition-all duration-180 hover:-translate-y-0.5">
          <div className="h-5 flex items-center justify-between text-muted text-[11px] font-medium">
            <span>Avg Latency</span>
            <Clock className="w-3.5 h-3.5" />
          </div>
          <div className="text-xl font-bold text-foreground font-mono tabular-nums mt-1.5">
            {stats.latencyDisplay}
          </div>
          <div className="text-[11px] text-muted mt-1 truncate">
            P95 intake to decision
          </div>
        </div>
      </div>

      {/* Decision Distribution Segmented Meter */}
      <div className="bg-card border border-border rounded-card p-4 space-y-2.5">
        <div className="flex items-center justify-between text-xs">
          <span className="font-semibold text-foreground flex items-center space-x-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-accent" />
            <span>Adjudication Distribution</span>
          </span>
          <span className="text-muted text-[11px]">
            {cases.length} loaded cases in current view
          </span>
        </div>

        {/* Horizontal Progress Bar */}
        <div className="w-full h-2.5 bg-card-inner rounded-full overflow-hidden flex">
          {stats.approvePct > 0 && (
            <div
              style={{ width: `${stats.approvePct}%` }}
              className="bg-status-success transition-all duration-300"
              title={`Approved: ${stats.approved} (${stats.approvePct.toFixed(1)}%)`}
            />
          )}
          {stats.reviewPct > 0 && (
            <div
              style={{ width: `${stats.reviewPct}%` }}
              className="bg-status-warning transition-all duration-300"
              title={`Needs Review: ${stats.awaitingHuman} (${stats.reviewPct.toFixed(1)}%)`}
            />
          )}
          {stats.rejectPct > 0 && (
            <div
              style={{ width: `${stats.rejectPct}%` }}
              className="bg-rose-deep transition-all duration-300"
              title={`Denied: ${stats.rejected} (${stats.rejectPct.toFixed(1)}%)`}
            />
          )}
          {cases.length === 0 && (
            <div className="w-full bg-border" />
          )}
        </div>

        {/* Legend */}
        <div className="flex flex-wrap items-center gap-4 text-[11px] pt-1">
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-status-success" />
            <span className="text-secondary">Approved:</span>
            <span className="font-semibold text-foreground font-mono tabular-nums">
              {stats.approved} ({stats.approvePct.toFixed(0)}%)
            </span>
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-status-warning" />
            <span className="text-secondary">Needs Review:</span>
            <span className="font-semibold text-foreground font-mono tabular-nums">
              {stats.awaitingHuman} ({stats.reviewPct.toFixed(0)}%)
            </span>
          </div>
          <div className="flex items-center space-x-1.5">
            <span className="w-2 h-2 rounded-full bg-rose-deep" />
            <span className="text-secondary">Denied:</span>
            <span className="font-semibold text-foreground font-mono tabular-nums">
              {stats.rejected} ({stats.rejectPct.toFixed(0)}%)
            </span>
          </div>
          {stats.awaitingInfo > 0 && (
            <div className="flex items-center space-x-1.5 text-muted">
              <span className="w-2 h-2 rounded-full bg-status-info" />
              <span>Awaiting Info:</span>
              <span className="font-mono tabular-nums">{stats.awaitingInfo}</span>
            </div>
          )}
        </div>
      </div>

      {/* Filter Tabs & Search Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 bg-card p-2.5 rounded-card border border-border shadow-xs">
        {/* Status Filter Buttons */}
        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((filter) => {
            const active = activeFilter === filter.value;
            let countBadge: number | null = null;
            if (filter.value === "") countBadge = stats.total;
            else if (filter.value === "AWAITING_HUMAN") countBadge = stats.awaitingHuman;
            else if (filter.value === "AWAITING_INFO") countBadge = stats.awaitingInfo;
            else if (filter.value === "COMPLETED") countBadge = stats.completed;
            else if (filter.value === "FAILED") countBadge = stats.failed;

            return (
              <button
                key={filter.value}
                onClick={() => setActiveFilter(filter.value)}
                className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-btn text-xs transition-all duration-180 active:scale-[0.98] ${
                  active
                    ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/50 shadow-xs"
                    : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
                }`}
              >
                <span>{filter.label}</span>
                {countBadge !== null && (
                  <span
                    className={`px-1.5 py-0.2 rounded-full text-[10px] font-mono tabular-nums transition-colors ${
                      active
                        ? "bg-[#397FA9] text-white font-bold"
                        : "bg-card-inner text-secondary border border-border/60"
                    }`}
                  >
                    {countBadge}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Search Input with Clear Button and Shortcut */}
        <div className="relative w-full md:w-72">
          <Search className="w-3.5 h-3.5 absolute left-3 top-3 text-muted pointer-events-none" />
          <input
            ref={searchInputRef}
            type="text"
            placeholder="Search Case ID or reason... ( / )"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full h-9 bg-card-inner border border-border rounded-btn pl-8 pr-8 py-2 text-xs text-foreground placeholder-muted focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 transition-all shadow-xs"
          />
          {searchQuery ? (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2.5 top-2.5 text-muted hover:text-foreground transition-colors"
              title="Clear search"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          ) : (
            <kbd className="absolute right-2.5 top-2.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-card text-muted border border-border hidden sm:inline-block pointer-events-none">
              /
            </kbd>
          )}
        </div>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="p-3.5 rounded-card bg-status-danger/10 border border-status-danger/20 text-status-danger text-xs flex items-center space-x-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Case Queue Table */}
      <div className="bg-card border border-border rounded-card overflow-hidden shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-card-inner text-muted border-b border-border text-[11px] uppercase tracking-wider font-semibold">
              <tr>
                <th className="py-3 px-4">Case ID</th>
                <th className="py-3 px-4">Lifecycle State</th>
                <th className="py-3 px-4">AI Recommendation</th>
                <th className="py-3 px-4">Escalation Reason</th>
                <th className="py-3 px-4">Confidence</th>
                <th className="py-3 px-4">Docs</th>
                <th className="py-3 px-4">Submitted</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {loading && cases.length === 0 ? (
                // Skeleton loading rows
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="animate-pulse">
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-20" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-24" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-16" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-32" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-12" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-8" />
                    </td>
                    <td className="py-4 px-4">
                      <div className="h-4 bg-card-inner rounded w-16" />
                    </td>
                    <td className="py-4 px-4 text-right">
                      <div className="h-6 bg-card-inner rounded w-14 ml-auto" />
                    </td>
                  </tr>
                ))
              ) : filteredCases.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-14 text-center text-secondary">
                    <div className="max-w-xs mx-auto space-y-2">
                      <FileText className="w-8 h-8 text-muted mx-auto stroke-1" />
                      <p className="font-semibold text-foreground text-sm">No claims match your criteria</p>
                      <p className="text-xs text-muted">
                        Try adjusting your status filter or clearing your search term.
                      </p>
                      {(searchQuery || activeFilter) && (
                        <button
                          onClick={() => {
                            setSearchQuery("");
                            setActiveFilter("");
                          }}
                          className="mt-2 inline-flex items-center px-3 py-1.5 rounded-btn bg-card-inner border border-border text-xs text-foreground hover:bg-border transition font-medium"
                        >
                          Clear all filters
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                filteredCases.map((c) => {
                  const isAwaitingHuman = c.workflow_state === "AWAITING_HUMAN";
                  const isComplete = c.workflow_state === "COMPLETED";
                  const isAwaitingInfo = c.workflow_state === "AWAITING_INFO";
                  const isFailed = c.workflow_state === "FAILED";

                  return (
                    <tr
                      key={c.case_id}
                      onClick={() => router.push(`/cases/${encodeURIComponent(c.case_id)}`)}
                      className="hover:bg-pink-hover/40 transition-colors duration-180 group cursor-pointer"
                    >
                      {/* Case ID */}
                      <td className="py-3.5 px-4 font-mono font-medium text-foreground">
                        <Link
                          href={`/cases/${encodeURIComponent(c.case_id)}`}
                          className="text-foreground group-hover:text-pink-deep font-semibold transition-colors flex items-center space-x-1"
                        >
                          <span className="truncate max-w-[180px]">{c.case_id}</span>
                        </Link>
                      </td>

                      {/* State */}
                      <td className="py-3.5 px-4">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide border ${
                            isAwaitingHuman
                              ? "bg-warning-bg text-warning border-warning-border"
                              : isAwaitingInfo
                              ? "bg-sky-soft text-sky-deep border-sky/30"
                              : isComplete
                              ? "bg-success-bg text-success border-success-border"
                              : isFailed
                              ? "bg-danger-bg text-danger border-danger-border"
                              : "bg-card-inner text-secondary border-border"
                          }`}
                        >
                          {c.workflow_state}
                        </span>
                      </td>

                      {/* AI Recommendation */}
                      <td className="py-3.5 px-4">
                        {c.current_decision === "APPROVE" ? (
                          <span className="inline-flex items-center space-x-1 text-success font-semibold">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span>APPROVE</span>
                          </span>
                        ) : c.current_decision === "REJECT" ? (
                          <span className="inline-flex items-center space-x-1 text-danger font-semibold">
                            <XCircle className="w-3.5 h-3.5" />
                            <span>REJECT</span>
                          </span>
                        ) : c.current_decision === "ESCALATE" ? (
                          <span className="inline-flex items-center space-x-1 text-warning font-semibold">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>ESCALATE</span>
                          </span>
                        ) : (
                          <span className="text-muted italic">Pending</span>
                        )}
                      </td>

                      {/* Sub-reason */}
                      <td className="py-3.5 px-4 font-mono text-[11px] text-secondary">
                        {c.current_sub_reason ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded bg-card-inner text-foreground border border-border max-w-[200px] truncate"
                            title={c.current_sub_reason}
                          >
                            {c.current_sub_reason}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>

                      {/* Confidence */}
                      <td className="py-3.5 px-4 text-secondary">
                        {c.decision_confidence !== null && c.decision_confidence !== undefined ? (
                          <div className="flex items-center space-x-1.5">
                            <span className="font-mono tabular-nums font-medium text-foreground">
                              {(c.decision_confidence * 100).toFixed(0)}%
                            </span>
                            <div className="w-10 h-1 bg-card-inner rounded-full overflow-hidden hidden sm:block">
                              <div
                                style={{ width: `${c.decision_confidence * 100}%` }}
                                className={`h-full ${
                                  c.decision_confidence >= 0.85
                                    ? "bg-success"
                                    : c.decision_confidence >= 0.65
                                    ? "bg-warning"
                                    : "bg-danger"
                                }`}
                              />
                            </div>
                          </div>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>

                      {/* Document count */}
                      <td className="py-3.5 px-4 text-secondary">
                        <span className="inline-flex items-center space-x-1 text-muted">
                          <FileText className="w-3 h-3 text-muted" />
                          <span className="font-mono tabular-nums text-foreground">{c.document_count}</span>
                        </span>
                      </td>

                      {/* Submitted Relative Time */}
                      <td className="py-3.5 px-4 text-secondary text-[11px]" title={formatDateTime(c.created_at)}>
                        <span className="font-mono tabular-nums">{formatRelativeTime(c.created_at)}</span>
                      </td>

                      {/* Action */}
                      <td className="py-3.5 px-4 text-right">
                        <Link
                          href={`/cases/${encodeURIComponent(c.case_id)}`}
                          className={`inline-flex items-center space-x-1 px-3 py-1 rounded-btn text-xs font-semibold transition-all duration-180 active:scale-[0.98] ${
                            isAwaitingHuman
                              ? "bg-[#397FA9] hover:bg-[#2C6688] text-white shadow-xs"
                              : "bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border shadow-xs"
                          }`}
                        >
                          <span>{isAwaitingHuman ? "Review" : "View"}</span>
                        </Link>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Multipart Intake Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150">
          <div className="bg-card border border-border rounded-card max-w-lg w-full p-6 space-y-5 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <h2 className="text-base font-bold text-foreground flex items-center space-x-2">
                <Upload className="w-4 h-4 text-accent" />
                <span>New Case Ingestion Handshake</span>
              </h2>
              <button
                type="button"
                onClick={handleCloseModal}
                className="text-muted hover:text-foreground p-1 rounded-btn hover:bg-card-inner transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleFileUpload} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-foreground mb-1">
                  Claim Type
                </label>
                <select
                  value={claimType}
                  onChange={(e) => setClaimType(e.target.value)}
                  className="w-full bg-card-inner border border-border rounded-btn px-3 py-2 text-xs text-foreground focus:outline-none focus:border-sky focus:ring-1 focus:ring-sky/30 transition-colors"
                >
                  <option value="STANDARD_REIMBURSEMENT_CLAIM">
                    Standard Reimbursement Claim
                  </option>
                  <option value="CASHLESS_PREAUTHORIZATION">
                    Cashless Pre-Authorization
                  </option>
                  <option value="SUPPLEMENTARY_DOCUMENTATION">
                    Supplementary Documentation
                  </option>
                </select>
              </div>

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-medium text-foreground">
                    Claim Documents (PDF, JPEG, PNG, TIFF &bull; Max 25MB each)
                  </label>
                  {stagedFiles.length > 0 && (
                    <button
                      type="button"
                      onClick={handleClearFiles}
                      className="text-[11px] text-rose-deep hover:underline transition"
                    >
                      Clear All
                    </button>
                  )}
                </div>

                {/* Hidden Multi-file input */}
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.png,.jpg,.jpeg,.tiff"
                  onChange={(e) => handleAddFiles(e.target.files)}
                  className="hidden"
                  id="multi-doc-upload"
                />

                {/* Dropzone & Browse Box */}
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                    if (e.dataTransfer.files) {
                      handleAddFiles(e.dataTransfer.files);
                    }
                  }}
                  onClick={() => fileInputRef.current?.click()}
                  className={`border-2 border-dashed rounded-card p-4 text-center cursor-pointer transition-all duration-180 select-none ${
                    isDragging
                      ? "border-sky bg-sky-soft/40"
                      : "border-border hover:border-sky/40 bg-card-inner"
                  }`}
                >
                  <Upload className="w-6 h-6 text-muted mx-auto mb-1.5" />
                  <p className="text-xs text-foreground font-medium">
                    Click to browse or drag & drop files here
                  </p>
                  <p className="text-[11px] text-muted mt-1">
                    Select files from one folder, then click again to add files from other folders.
                  </p>
                </div>

                {/* Staged files roster */}
                {stagedFiles.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    <div className="flex items-center justify-between text-[11px] text-secondary px-1">
                      <span className="font-medium text-foreground">
                        Staged Documents ({stagedFiles.length})
                      </span>
                      <span className="font-mono text-muted">
                        {formatFileSize(stagedFiles.reduce((acc, f) => acc + f.size, 0))}
                      </span>
                    </div>
                    <div className="max-h-36 overflow-y-auto space-y-1.5 pr-1 border border-border rounded-card p-2 bg-card-inner">
                      {stagedFiles.map((file, idx) => (
                        <div
                          key={`${file.name}_${file.size}_${idx}`}
                          className="flex items-center justify-between p-2 rounded-btn bg-card border border-border text-xs transition"
                        >
                          <div className="flex items-center space-x-2 min-w-0 mr-2">
                            <FileText className="w-3.5 h-3.5 text-sky flex-shrink-0" />
                            <span className="text-foreground truncate font-mono text-[11px]" title={file.name}>
                              {file.name}
                            </span>
                            <span className="text-muted text-[10px] flex-shrink-0">
                              ({formatFileSize(file.size)})
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRemoveFile(idx);
                            }}
                            className="text-muted hover:text-rose-deep p-1 rounded transition"
                            title="Remove document"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                    <div className="pt-1 flex justify-end">
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="text-[11px] text-sky-deep hover:underline font-medium flex items-center space-x-1"
                      >
                        <Plus className="w-3 h-3" />
                        <span>Add more files from another folder</span>
                      </button>
                    </div>
                  </div>
                )}

                <p className="text-[11px] text-muted mt-2">
                  Deterministic sub-200ms handshake streams uploads to secure storage and dispatches asynchronous LangGraph worker.
                </p>
              </div>

              {uploadError && (
                <div className="p-3 rounded-card bg-rose-blush border border-rose-med/30 text-rose-deep text-xs">
                  {uploadError}
                </div>
              )}

              <div className="flex items-center justify-end space-x-2.5 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={handleCloseModal}
                  className="px-3.5 py-1.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep text-xs font-medium border border-border hover:border-pink-border transition-all duration-180 active:scale-[0.98]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading || stagedFiles.length === 0}
                  className="px-4 py-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white disabled:opacity-50 disabled:cursor-not-allowed text-xs font-semibold shadow-xs flex items-center space-x-1.5 transition-all duration-180 active:scale-[0.98]"
                >
                  {uploading ? (
                    <>
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      <span>Ingesting ({stagedFiles.length})...</span>
                    </>
                  ) : (
                    <>
                      <Upload className="w-3.5 h-3.5" />
                      <span>
                        Submit Ingestion ({stagedFiles.length})
                      </span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
