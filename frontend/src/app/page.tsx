"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  FileText,
  Filter,
  Lock,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { getCases, submitIntake, CaseListItem } from "@/lib/api";

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
  const { user, loading: authLoading } = useAuth();
  const [cases, setCases] = useState<CaseListItem[]>([]);
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
  const [claimType, setClaimType] = useState("STANDARD_REIMBURSEMENT_CLAIM");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [isPending, startTransition] = useTransition();

  const loadCases = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getCases({
        state: activeFilter || undefined,
        page: 1,
        page_size: 50,
      });
      setCases(data.items || []);
      setTotalCount(data.total_count || 0);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load cases.");
    } finally {
      setLoading(false);
    }
  }, [activeFilter]);

  useEffect(() => {
    loadCases();
  }, [loadCases]);

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
      await loadCases();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const filteredCases = cases.filter((c) =>
    searchQuery ? c.case_id.toLowerCase().includes(searchQuery.toLowerCase()) : true
  );

  if (!authLoading && user?.role === "CLAIMER") {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center">
        <div className="p-4 bg-cyan-950/40 border border-cyan-800/80 rounded-2xl mb-6 inline-block">
          <ShieldCheck className="w-12 h-12 text-cyan-400 mx-auto" />
        </div>
        <h2 className="text-2xl font-bold text-white mb-2">Claimer Account Detected</h2>
        <p className="text-sm text-slate-400 mb-6 max-w-lg mx-auto leading-relaxed">
          You are authenticated as a <strong>Claimer</strong> (Patient / Provider). Under MediShield RBAC, the internal operational review queue is reserved for licensed claims adjusters.
        </p>
        <Link
          href="/claimer"
          className="inline-flex items-center space-x-2 px-5 py-2.5 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg font-semibold text-sm transition-colors shadow-lg shadow-cyan-950"
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
          <h1 className="text-2xl font-bold tracking-tight text-white">Operations Case Queue</h1>
          <p className="text-sm text-slate-400">
            Real-time human-in-the-loop review queue & automated intake pipeline
          </p>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={() => loadCases()}
            disabled={loading}
            className="flex items-center space-x-1.5 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm border border-slate-700 transition"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
          <button
            onClick={() => setIsModalOpen(true)}
            className="flex items-center space-x-1.5 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold shadow-md shadow-cyan-950 transition"
          >
            <Plus className="w-4 h-4" />
            <span>New Case Intake</span>
          </button>
        </div>
      </div>

      {/* Filter Tabs & Search */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
        <div className="flex flex-wrap gap-1.5">
          {STATUS_FILTERS.map((filter) => {
            const active = activeFilter === filter.value;
            return (
              <button
                key={filter.value}
                onClick={() => setActiveFilter(filter.value)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  active
                    ? "bg-cyan-500 text-slate-950 font-semibold shadow-sm"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/80"
                }`}
              >
                {filter.label}
              </button>
            );
          })}
        </div>

        <div className="relative w-full md:w-72">
          <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
          <input
            type="text"
            placeholder="Search Case ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
          />
        </div>
      </div>

      {/* Error Notice */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-800/60 text-rose-300 text-sm flex items-center space-x-2">
          <AlertTriangle className="w-5 h-5 flex-shrink-0 text-rose-400" />
          <span>{error}</span>
        </div>
      )}

      {/* Case Queue Table */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-950/60 text-slate-400 border-b border-slate-800 text-[11px] uppercase tracking-wider">
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
            <tbody className="divide-y divide-slate-800/60">
              {loading && cases.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2 text-cyan-500" />
                    Loading cases from control plane...
                  </td>
                </tr>
              ) : filteredCases.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500">
                    No cases match the selected filter.
                  </td>
                </tr>
              ) : (
                filteredCases.map((c) => {
                  const isAwaitingHuman = c.workflow_state === "AWAITING_HUMAN";
                  const isComplete = c.workflow_state === "COMPLETED";
                  const isAwaitingInfo = c.workflow_state === "AWAITING_INFO";

                  return (
                    <tr
                      key={c.case_id}
                      className="hover:bg-slate-800/40 transition group"
                    >
                      <td className="py-3.5 px-4 font-mono font-medium text-slate-200">
                        <Link
                          href={`/cases/${encodeURIComponent(c.case_id)}`}
                          className="text-cyan-400 hover:underline flex items-center space-x-1"
                        >
                          <span>{c.case_id}</span>
                        </Link>
                      </td>
                      <td className="py-3.5 px-4">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide border ${
                            isAwaitingHuman
                              ? "bg-amber-950/60 text-amber-300 border-amber-800"
                              : isAwaitingInfo
                              ? "bg-blue-950/60 text-blue-300 border-blue-800"
                              : isComplete
                              ? "bg-emerald-950/60 text-emerald-300 border-emerald-800"
                              : "bg-slate-800 text-slate-300 border-slate-700"
                          }`}
                        >
                          {c.workflow_state}
                        </span>
                      </td>
                      <td className="py-3.5 px-4">
                        {c.current_decision === "APPROVE" ? (
                          <span className="flex items-center space-x-1 text-emerald-400 font-semibold">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span>APPROVE</span>
                          </span>
                        ) : c.current_decision === "REJECT" ? (
                          <span className="flex items-center space-x-1 text-rose-400 font-semibold">
                            <XCircle className="w-3.5 h-3.5" />
                            <span>REJECT</span>
                          </span>
                        ) : c.current_decision === "ESCALATE" ? (
                          <span className="flex items-center space-x-1 text-amber-400 font-semibold">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>ESCALATE</span>
                          </span>
                        ) : (
                          <span className="text-slate-500 italic">Pending</span>
                        )}
                      </td>
                      <td className="py-3.5 px-4 text-slate-300 font-mono text-[11px]">
                        {c.current_sub_reason ? (
                          <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                            {c.current_sub_reason}
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="py-3.5 px-4 text-slate-300">
                        {c.decision_confidence !== null && c.decision_confidence !== undefined ? (
                          <span className="font-mono">
                            {(c.decision_confidence * 100).toFixed(0)}%
                          </span>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="py-3.5 px-4 text-slate-300">
                        <span className="flex items-center space-x-1 text-slate-400">
                          <FileText className="w-3 h-3 text-slate-500" />
                          <span>{c.document_count}</span>
                        </span>
                      </td>
                      <td className="py-3.5 px-4 text-slate-400 text-[11px]">
                        {new Date(c.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </td>
                      <td className="py-3.5 px-4 text-right">
                        <Link
                          href={`/cases/${encodeURIComponent(c.case_id)}`}
                          className={`inline-flex items-center space-x-1 px-3 py-1 rounded-md text-xs font-semibold transition ${
                            isAwaitingHuman
                              ? "bg-amber-600 hover:bg-amber-500 text-slate-950 font-bold"
                              : "bg-slate-800 hover:bg-slate-700 text-slate-200"
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-5 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-lg font-bold text-white flex items-center space-x-2">
                <Upload className="w-5 h-5 text-cyan-400" />
                <span>New Case Ingestion Handshake</span>
              </h2>
              <button
                type="button"
                onClick={handleCloseModal}
                className="text-slate-500 hover:text-slate-300 p-1 rounded-lg hover:bg-slate-800 transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleFileUpload} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Claim Type
                </label>
                <select
                  value={claimType}
                  onChange={(e) => setClaimType(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
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
                  <label className="block text-xs font-medium text-slate-300">
                    Claim Documents (PDF, JPEG, PNG, TIFF &bull; Max 25MB each)
                  </label>
                  {stagedFiles.length > 0 && (
                    <button
                      type="button"
                      onClick={handleClearFiles}
                      className="text-[11px] text-rose-400 hover:text-rose-300 transition"
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
                  className={`border-2 border-dashed rounded-xl p-4 text-center cursor-pointer transition select-none ${
                    isDragging
                      ? "border-cyan-400 bg-cyan-950/30"
                      : "border-slate-800 hover:border-slate-700 bg-slate-950/60 hover:bg-slate-950"
                  }`}
                >
                  <Upload className="w-6 h-6 text-cyan-400 mx-auto mb-1.5" />
                  <p className="text-xs text-slate-200 font-medium">
                    Click to browse or drag & drop files here
                  </p>
                  <p className="text-[11px] text-slate-500 mt-1">
                    Select files from one folder, then click again to add files from other folders.
                  </p>
                </div>

                {/* Staged files roster */}
                {stagedFiles.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    <div className="flex items-center justify-between text-[11px] text-slate-400 px-1">
                      <span className="font-medium text-slate-300">
                        Staged Documents ({stagedFiles.length})
                      </span>
                      <span className="font-mono text-slate-400">
                        {formatFileSize(stagedFiles.reduce((acc, f) => acc + f.size, 0))}
                      </span>
                    </div>
                    <div className="max-h-36 overflow-y-auto space-y-1.5 pr-1 border border-slate-800 rounded-lg p-2 bg-slate-950/40">
                      {stagedFiles.map((file, idx) => (
                        <div
                          key={`${file.name}_${file.size}_${idx}`}
                          className="flex items-center justify-between p-2 rounded-md bg-slate-900 border border-slate-800/80 text-xs hover:border-slate-700 transition"
                        >
                          <div className="flex items-center space-x-2 min-w-0 mr-2">
                            <FileText className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                            <span className="text-slate-200 truncate font-mono text-[11px]" title={file.name}>
                              {file.name}
                            </span>
                            <span className="text-slate-500 text-[10px] flex-shrink-0">
                              ({formatFileSize(file.size)})
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRemoveFile(idx);
                            }}
                            className="text-slate-500 hover:text-rose-400 p-1 rounded transition"
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
                        className="text-[11px] text-cyan-400 hover:text-cyan-300 font-medium flex items-center space-x-1"
                      >
                        <Plus className="w-3 h-3" />
                        <span>Add more files from another folder</span>
                      </button>
                    </div>
                  </div>
                )}

                <p className="text-[11px] text-slate-500 mt-2">
                  Sub-200ms handshake streams uploads to private storage and dispatches background worker task.
                </p>
              </div>

              {uploadError && (
                <div className="p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs">
                  {uploadError}
                </div>
              )}

              <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={handleCloseModal}
                  className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading || stagedFiles.length === 0}
                  className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-semibold shadow flex items-center space-x-1.5"
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

