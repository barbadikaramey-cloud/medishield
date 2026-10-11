"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Brain,
  Check,
  CheckCircle,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronUp,
  Clock,
  Copy,
  Eye,
  FileText,
  Layers,
  Lock,
  RefreshCw,
  Scale,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Unlock,
  XCircle,
} from "lucide-react";
import {
  acquireReviewLock,
  fetchDocumentOcr,
  fetchDocumentImage,
  getAuditVerification,
  getCaseDetail,
  releaseReviewLock,
  rerunCase,
  submitConfirmation,
  submitOverride,
  requestInformation,
  AuditVerifyResponse,
  CaseDetailResponse,
  ReviewLockResponse,
} from "@/lib/api";

export default function CaseDetailPage() {
  const params = useParams();
  const router = useRouter();
  const caseId = params?.id as string;

  const [caseData, setCaseData] = useState<CaseDetailResponse | null>(null);
  const [auditData, setAuditData] = useState<AuditVerifyResponse | null>(null);
  const [lockData, setLockData] = useState<ReviewLockResponse | null>(null);
  const [selectedDocIndex, setSelectedDocIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [ocrPayload, setOcrPayload] = useState<any | null>(null);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [docImageUrl, setDocImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [overlayMode, setOverlayMode] = useState<"key_fields" | "all_ocr" | "none">("key_fields");

  // Review Override Modal State
  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState(false);
  const [overrideDecision, setOverrideDecision] = useState("APPROVE");
  const [reasonCode, setReasonCode] = useState("SUPERVISORY_EXCEPTION");
  const [remarks, setRemarks] = useState("");
  const [submittingAction, setSubmittingAction] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Request Info Modal State
  const [isRfiModalOpen, setIsRfiModalOpen] = useState(false);
  const [rfiRemarks, setRfiRemarks] = useState("");

  // AI Decision Explanation State (ADR-011)
  const [isExplanationExpanded, setIsExplanationExpanded] = useState(true);
  const [copiedExplanation, setCopiedExplanation] = useState(false);

  const handleCopyExplanation = (text: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedExplanation(true);
    setTimeout(() => setCopiedExplanation(false), 2000);
  };

  const loadData = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, audit] = await Promise.all([
        getCaseDetail(caseId),
        getAuditVerification(caseId).catch(() => null),
      ]);
      setCaseData(detail);
      setAuditData(audit);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load case detail.");
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const selectedDocId = caseData?.documents?.[selectedDocIndex]?.document_id;

  useEffect(() => {
    if (!caseId || !selectedDocId) {
      setOcrPayload(null);
      if (docImageUrl) URL.revokeObjectURL(docImageUrl);
      setDocImageUrl(null);
      return;
    }
    let isCurrent = true;
    setOcrLoading(true);
    
    fetchDocumentOcr(caseId, selectedDocId)
      .then((data) => {
        if (isCurrent) setOcrPayload(data);
      })
      .catch(() => {
        if (isCurrent) setOcrPayload(null);
      })
      .finally(() => {
        if (isCurrent) setOcrLoading(false);
      });
      
    fetchDocumentImage(caseId, selectedDocId).then((url) => {
      if (isCurrent) setDocImageUrl(url);
    });

    return () => {
      isCurrent = false;
    };
  }, [caseId, selectedDocId]);

  const handleAcquireLock = async () => {
    try {
      const res = await acquireReviewLock(caseId);
      setLockData(res);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Lock acquisition failed.");
    }
  };

  const handleReleaseLock = async () => {
    try {
      await releaseReviewLock(caseId);
      setLockData(null);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to release lock.");
    }
  };

  const handleConfirmRecommendation = async () => {
    if (!caseData) return;
    setSubmittingAction(true);
    setActionError(null);
    try {
      await submitConfirmation({
        case_id: caseId,
        expected_case_version: caseData.version,
        remarks: "Human reviewer verified all agent findings and confirmed recommendation.",
      });
      setLockData(null);
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Confirmation failed.");
    } finally {
      setSubmittingAction(false);
    }
  };

  const handleOverrideSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseData) return;
    if (remarks.trim().length < 10) {
      setActionError("Remarks must be at least 10 characters long.");
      return;
    }
    setSubmittingAction(true);
    setActionError(null);
    try {
      await submitOverride({
        case_id: caseId,
        expected_case_version: caseData.version,
        new_decision: overrideDecision,
        reason_code: reasonCode,
        remarks: remarks.trim(),
      });
      setIsOverrideModalOpen(false);
      setLockData(null);
      setRemarks("");
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Override submission failed.");
    } finally {
      setSubmittingAction(false);
    }
  };

  const handleRfiSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!caseData) return;
    if (rfiRemarks.trim().length < 5) {
      setActionError("Remarks must describe the requested information.");
      return;
    }
    setSubmittingAction(true);
    setActionError(null);
    try {
      await requestInformation({
        case_id: caseId,
        missing_doc_types: caseData.missing_documents || ["ID_DOCUMENT"],
        remarks: rfiRemarks.trim(),
      });
      setIsRfiModalOpen(false);
      setLockData(null);
      setRfiRemarks("");
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Request Info failed.");
    } finally {
      setSubmittingAction(false);
    }
  };

  const handleRerunCase = async () => {
    if (!caseId) return;
    setRerunning(true);
    setActionError(null);
    try {
      await rerunCase(caseId);
      await loadData();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Workflow rerun failed.");
    } finally {
      setRerunning(false);
    }
  };

  if (loading && !caseData) {
    return (
      <div className="py-24 text-center text-slate-500 space-y-3">
        <RefreshCw className="w-8 h-8 animate-spin mx-auto text-cyan-400" />
        <p className="text-sm">Loading Case {caseId} from control plane...</p>
      </div>
    );
  }

  if (error || !caseData) {
    return (
      <div className="py-12 max-w-lg mx-auto text-center space-y-4">
        <AlertTriangle className="w-12 h-12 text-rose-400 mx-auto" />
        <h2 className="text-lg font-bold text-white">Error Loading Case</h2>
        <p className="text-xs text-rose-300">{error || "Case not found."}</p>
        <Link
          href="/"
          className="inline-flex items-center space-x-1.5 px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs text-slate-200"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Queue</span>
        </Link>
      </div>
    );
  }

  const selectedDoc = caseData.documents[selectedDocIndex];
  const isAwaitingHuman = caseData.workflow_state === "AWAITING_HUMAN";
  const hasLock = Boolean(lockData);

  const criticalSpans: any[] = [];
  if (caseData?.agent_findings && selectedDoc) {
    const extractProv = (obj: any, parentKey = "") => {
      if (!obj || typeof obj !== "object") return;
      // Array handling
      if (Array.isArray(obj)) {
        obj.forEach((item, idx) => extractProv(item, `${parentKey}[${idx}]`));
        return;
      }
      if (obj.provenance && obj.provenance.document_id === selectedDoc.document_id) {
        let b = obj.provenance.bbox;
        if (!b && obj.provenance.bounding_box) {
          const arr = obj.provenance.bounding_box;
          if (Array.isArray(arr) && arr.length === 4) {
            b = { x0: arr[0], y0: arr[1], x1: arr[2], y1: arr[3] };
          }
        }
        if (b) {
          const x0 = b.x0 ?? b.x_min ?? 0;
          const y0 = b.y0 ?? b.y_min ?? 0;
          const x1 = b.x1 ?? b.x_max ?? 0;
          const y1 = b.y1 ?? b.y_max ?? 0;
          criticalSpans.push({
            key: parentKey,
            value: typeof obj.value === "object" ? JSON.stringify(obj.value) : String(obj.value ?? ""),
            bbox: { x0, y0, x1, y1 },
          });
        }
      } else {
        Object.keys(obj).forEach((k) => {
          if (typeof obj[k] === "object") {
            extractProv(obj[k], k);
          }
        });
      }
    };
    extractProv(caseData.agent_findings, "root");
  }

  // Supplement with extracted OCR key-value pairs from document payload
  if (ocrPayload) {
    const kvList = ocrPayload.key_value_pairs || (ocrPayload.pages && ocrPayload.pages.flatMap((p: any) => p.key_value_pairs || [])) || [];
    if (Array.isArray(kvList) && kvList.length > 0) {
      kvList.forEach((kv: any) => {
        if (kv.bbox) {
          const x0 = kv.bbox.x0 ?? kv.bbox.x_min ?? 0;
          const y0 = kv.bbox.y0 ?? kv.bbox.y_min ?? 0;
          const x1 = kv.bbox.x1 ?? kv.bbox.x_max ?? 0;
          const y1 = kv.bbox.y1 ?? kv.bbox.y_max ?? 0;
          if (!criticalSpans.some((s) => s.key === kv.key)) {
            criticalSpans.push({
              key: kv.key,
              value: String(kv.value ?? ""),
              bbox: { x0, y0, x1, y1 },
              confidence: kv.confidence,
            });
          }
        }
      });
    }
  }

  const allOcrSpans: any[] = [];
  if (ocrPayload) {
    if (Array.isArray(ocrPayload.pages)) {
      ocrPayload.pages.forEach((page: any) => {
        if (Array.isArray(page.spans)) {
          page.spans.forEach((s: any) => {
            const b = s.bbox;
            if (b) {
              const x0 = b.x0 ?? b.x_min ?? 0;
              const y0 = b.y0 ?? b.y_min ?? 0;
              const x1 = b.x1 ?? b.x_max ?? 0;
              const y1 = b.y1 ?? b.y_max ?? 0;
              allOcrSpans.push({
                span_id: s.span_id || `span-p${page.page_number}`,
                text: s.text,
                confidence: s.confidence ?? 0.95,
                bbox: { x0, y0, x1, y1 },
              });
            }
          });
        }
      });
    } else if (Array.isArray(ocrPayload.tokens)) {
      ocrPayload.tokens.forEach((t: any, idx: number) => {
        const b = t.bbox;
        if (b) {
          const x0 = b.x0 ?? b.x_min ?? 0;
          const y0 = b.y0 ?? b.y_min ?? 0;
          const x1 = b.x1 ?? b.x_max ?? 0;
          const y1 = b.y1 ?? b.y_max ?? 0;
          allOcrSpans.push({
            span_id: `token-${idx + 1}`,
            text: t.text,
            confidence: t.confidence ?? 0.95,
            bbox: { x0, y0, x1, y1 },
          });
        }
      });
    }
  }

  const displayedSpans =
    overlayMode === "key_fields"
      ? criticalSpans
      : overlayMode === "all_ocr"
      ? allOcrSpans
      : [];

  return (
    <div className="space-y-6">
      {/* Top Breadcrumb & Lock Control Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
        <div className="flex items-center space-x-4">
          <Link
            href="/"
            className="p-2 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-800 transition"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="flex items-center space-x-3">
              <h1 className="text-xl font-bold font-mono text-white tracking-tight">
                {caseData.case_id}
              </h1>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 border border-slate-700">
                v{caseData.version}
              </span>
              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wide border ${
                  caseData.workflow_state === "AWAITING_HUMAN"
                    ? "bg-amber-950/80 text-amber-300 border-amber-700"
                    : caseData.workflow_state === "AWAITING_INFO"
                    ? "bg-blue-950/80 text-blue-300 border-blue-700"
                    : caseData.workflow_state === "COMPLETED"
                    ? "bg-emerald-950/80 text-emerald-300 border-emerald-700"
                    : "bg-slate-800 text-slate-300 border-slate-700"
                }`}
              >
                {caseData.workflow_state}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Workflow Engine: <span className="font-mono text-cyan-400">{caseData.workflow_version}</span> &bull; Submitted: {new Date(caseData.created_at).toLocaleString()}
            </p>
          </div>
        </div>

        {/* Lock Acquisition / Countdown */}
        <div className="flex items-center space-x-3">
          {auditData && (
            <div
              className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-xs ${
                auditData.chain_intact
                  ? "bg-emerald-950/40 border-emerald-800 text-emerald-300"
                  : "bg-rose-950/60 border-rose-800 text-rose-300"
              }`}
            >
              {auditData.chain_intact ? (
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
              ) : (
                <ShieldAlert className="w-4 h-4 text-rose-400" />
              )}
              <span>{auditData.chain_intact ? "HMAC Audit Chain Intact" : "Audit Chain Compromised"}</span>
            </div>
          )}

          {isAwaitingHuman && (
            <>
              {hasLock ? (
                <div className="flex items-center space-x-2 bg-amber-950/60 border border-amber-800 px-3 py-1.5 rounded-lg text-xs text-amber-300">
                  <Lock className="w-3.5 h-3.5 text-amber-400" />
                  <span>Lock Active (15m TTL)</span>
                  <button
                    onClick={handleReleaseLock}
                    className="ml-2 px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-300 text-[10px]"
                  >
                    Release
                  </button>
                </div>
              ) : (
                <button
                  onClick={handleAcquireLock}
                  className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-slate-950 text-xs font-bold transition shadow"
                >
                  <Unlock className="w-3.5 h-3.5" />
                  <span>Acquire Review Lock</span>
                </button>
              )}
            </>
          )}

          {/* Rerun Pipeline Button */}
          <button
            onClick={handleRerunCase}
            disabled={rerunning}
            className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-indigo-950/70 hover:bg-indigo-900/90 text-indigo-300 border border-indigo-700/80 text-xs font-semibold transition shadow disabled:opacity-50"
            title="Re-execute claims adjudication workflow pipeline with latest models & rules"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${rerunning ? "animate-spin text-indigo-400" : ""}`} />
            <span>{rerunning ? "Rerunning..." : "Rerun Pipeline"}</span>
          </button>
        </div>
      </div>

      {/* Main Grid: Document Viewer on Left (7 cols), Decision Console on Right (5 cols) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Multi-Document Roster & Viewer */}
        <div className="lg:col-span-7 space-y-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
            <div className="bg-slate-950/80 px-4 py-3 border-b border-slate-800 flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FileText className="w-4 h-4 text-cyan-400" />
                <span className="text-xs font-bold text-slate-200 uppercase tracking-wider">
                  Submitted Documents ({caseData.documents.length})
                </span>
              </div>
            </div>

            {/* Document Tabs */}
            <div className="flex overflow-x-auto border-b border-slate-800 bg-slate-950/40 p-2 gap-2">
              {caseData.documents.map((doc, idx) => (
                <button
                  key={doc.document_id}
                  onClick={() => setSelectedDocIndex(idx)}
                  className={`px-3 py-2 rounded-lg text-xs font-mono transition text-left flex-shrink-0 border ${
                    selectedDocIndex === idx
                      ? "bg-slate-800 text-cyan-400 border-cyan-800 font-semibold"
                      : "bg-slate-900/60 text-slate-400 border-slate-800 hover:text-slate-200"
                  }`}
                >
                  <div className="truncate max-w-[140px]">{doc.file_name}</div>
                  <div className="text-[10px] text-slate-500">{doc.doc_type || "UNKNOWN"}</div>
                </button>
              ))}
            </div>

            {/* Document Inspector Panel */}
            {selectedDoc ? (
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-slate-950 p-3 rounded-lg border border-slate-800/80 text-[11px] font-mono">
                  <div>
                    <span className="text-slate-500 block">Doc ID:</span>
                    <span className="text-slate-200">{selectedDoc.document_id}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">MIME Type:</span>
                    <span className="text-slate-200">{selectedDoc.mime_type}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">File Size:</span>
                    <span className="text-slate-200">{(selectedDoc.size_bytes / 1024).toFixed(1)} KB</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">SHA-256:</span>
                    <span className="text-slate-400 truncate block" title={selectedDoc.checksum}>
                      {selectedDoc.checksum.substring(0, 10)}...
                    </span>
                  </div>
                </div>

                {/* Visual Provenance Viewer */}
                <div className="bg-slate-950 rounded-lg border border-slate-800 p-4 space-y-3">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-slate-400 border-b border-slate-800/60 pb-2">
                    <div className="flex items-center space-x-2">
                      <span className="font-semibold text-slate-300">Extracted Visual Provenance</span>
                      <span className="text-[10px] bg-slate-900 px-2 py-0.5 rounded border border-slate-700 text-slate-400 font-mono">
                        {displayedSpans.length} visible
                      </span>
                    </div>

                    {/* Overlay Mode Switcher */}
                    <div className="flex items-center space-x-1 bg-slate-900 p-1 rounded-lg border border-slate-800 text-[10px]">
                      <button
                        type="button"
                        onClick={() => setOverlayMode("key_fields")}
                        className={`px-2 py-0.5 rounded transition flex items-center space-x-1 ${
                          overlayMode === "key_fields"
                            ? "bg-cyan-600 text-white font-semibold shadow"
                            : "text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        <CheckSquare className="w-3 h-3" />
                        <span>Key Fields ({criticalSpans.length})</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setOverlayMode("all_ocr")}
                        className={`px-2 py-0.5 rounded transition flex items-center space-x-1 ${
                          overlayMode === "all_ocr"
                            ? "bg-amber-600 text-slate-950 font-bold shadow"
                            : "text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        <Layers className="w-3 h-3" />
                        <span>All OCR Tokens ({allOcrSpans.length})</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setOverlayMode("none")}
                        className={`px-2 py-0.5 rounded transition ${
                          overlayMode === "none"
                            ? "bg-slate-700 text-slate-200 font-semibold"
                            : "text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        Hide
                      </button>
                    </div>
                  </div>
                  
                  <div className="relative border border-slate-800 rounded overflow-hidden bg-slate-900" style={{ minHeight: "400px" }}>
                    {docImageUrl ? (
                      <img 
                        src={docImageUrl} 
                        alt="Document Context"
                        className="w-full h-auto object-contain opacity-85"
                      />
                    ) : (
                      <div className="w-full h-64 flex items-center justify-center text-slate-500 text-xs">
                        Loading document image...
                      </div>
                    )}
                    
                    {displayedSpans.filter((span) => span.bbox).map((span, idx) => {
                      const isKey = overlayMode === "key_fields";
                      return (
                        <div
                          key={idx}
                          className={`absolute border-2 transition-colors cursor-crosshair group ${
                            isKey
                              ? "border-cyan-400 bg-cyan-400/20 hover:bg-cyan-400/40"
                              : "border-amber-400/80 bg-amber-400/15 hover:bg-amber-400/35"
                          }`}
                          style={{
                            left: `${span.bbox.x0 * 100}%`,
                            top: `${span.bbox.y0 * 100}%`,
                            width: `${Math.max(0.005, span.bbox.x1 - span.bbox.x0) * 100}%`,
                            height: `${Math.max(0.005, span.bbox.y1 - span.bbox.y0) * 100}%`,
                          }}
                        >
                          <div className="absolute hidden group-hover:block bottom-full left-1/2 -translate-x-1/2 mb-1.5 bg-slate-900/95 backdrop-blur text-slate-200 text-[11px] p-2 rounded shadow-2xl z-20 border border-slate-700 font-mono whitespace-nowrap min-w-[140px] text-left pointer-events-none">
                            <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1">
                              <span className={isKey ? "text-cyan-400 font-bold" : "text-amber-400 font-bold"}>
                                {isKey ? span.key : span.span_id}
                              </span>
                              {span.confidence !== undefined && (
                                <span className="text-[9px] text-slate-400">
                                  {(span.confidence * 100).toFixed(0)}%
                                </span>
                              )}
                            </div>
                            <div className="text-white text-xs truncate max-w-[240px]">
                              {isKey ? span.value : span.text}
                            </div>
                            <div className="text-[9px] text-slate-500 mt-1">
                              [{span.bbox.x0.toFixed(3)}, {span.bbox.y0.toFixed(3)}, {span.bbox.x1.toFixed(3)}, {span.bbox.y1.toFixed(3)}]
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {displayedSpans.length === 0 && overlayMode !== "none" && (
                    <div className="text-slate-500 italic py-2 text-center text-xs">
                      No coordinates matched for this display mode.
                    </div>
                  )}
                </div>

                {/* Extracted vs Expected / Reconciled Adjudication Comparison Panel */}
                <div className="bg-slate-950 rounded-lg border border-slate-800 p-4 space-y-3">
                  <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
                    <div className="flex items-center space-x-2">
                      <Scale className="w-4 h-4 text-cyan-400" />
                      <span className="font-semibold text-xs text-slate-200 uppercase tracking-wider">
                        Extracted Findings vs. Adjudication Comparison
                      </span>
                    </div>
                    <span className="text-[10px] font-mono text-cyan-400">Spec 05 / 06 Reconciled</span>
                  </div>

                  {/* Financial Totals Comparison Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                    <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg">
                      <span className="text-[10px] text-slate-500 uppercase font-semibold block">Declared Header Total</span>
                      <div className="text-base font-bold font-mono text-white mt-1">
                        ${(caseData.agent_findings?.claims?.claim_amount?.value ?? 0).toFixed(2)}
                      </div>
                      <div className="text-[10px] text-slate-400 mt-0.5">
                        Conf: {((caseData.agent_findings?.claims?.claim_amount?.confidence ?? 0) * 100).toFixed(0)}%
                      </div>
                    </div>

                    <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg">
                      <span className="text-[10px] text-slate-500 uppercase font-semibold block">Calculated Line Sum</span>
                      <div className="text-base font-bold font-mono text-cyan-400 mt-1">
                        ${(caseData.agent_findings?.claims?.calculated_lines_sum ?? 0).toFixed(2)}
                      </div>
                      <div className="text-[10px] text-slate-400 mt-0.5">
                        From {caseData.agent_findings?.claims?.service_lines?.length ?? 0} itemized lines
                      </div>
                    </div>

                    <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg flex flex-col justify-between">
                      <span className="text-[10px] text-slate-500 uppercase font-semibold block">Arithmetic Validation</span>
                      <div className="mt-1">
                        {caseData.agent_findings?.claims?.line_items_sum_valid ? (
                          <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800">
                            <CheckCircle className="w-3.5 h-3.5" />
                            <span>MATCH (Exact)</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950 text-amber-300 border border-amber-800">
                            <AlertTriangle className="w-3.5 h-3.5" />
                            <span>ARITHMETIC_MISMATCH</span>
                          </span>
                        )}
                      </div>
                      <div className="text-[9px] text-slate-500 mt-0.5">
                        Rule 11 Deterministic Check
                      </div>
                    </div>
                  </div>

                  {/* Extracted CPT Procedural Codes */}
                  {caseData.agent_findings?.claims?.cpt_codes && caseData.agent_findings.claims.cpt_codes.length > 0 && (
                    <div className="p-3 bg-slate-900 border border-slate-800 rounded-lg space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-slate-400 uppercase font-semibold">Extracted Procedure Codes (CPT)</span>
                        <span className="text-[10px] text-slate-500">Table-Isolated Extraction</span>
                      </div>
                      <div className="flex flex-wrap gap-2 pt-1">
                        {caseData.agent_findings.claims.cpt_codes.map((cpt: any, i: number) => (
                          <div key={i} className="px-2.5 py-1 rounded bg-slate-950 border border-cyan-800 text-xs font-mono text-cyan-300 flex items-center space-x-1.5">
                            <span className="font-bold">{cpt.value}</span>
                            <span className="text-[9px] text-slate-500">({(cpt.confidence * 100).toFixed(0)}%)</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Itemized Service Lines Table */}
                  {caseData.agent_findings?.claims?.service_lines && caseData.agent_findings.claims.service_lines.length > 0 && (
                    <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900">
                      <table className="w-full text-left text-[11px] font-mono">
                        <thead className="bg-slate-950 text-slate-400 uppercase text-[9px] border-b border-slate-800">
                          <tr>
                            <th className="py-2 px-3">#</th>
                            <th className="py-2 px-3">CPT</th>
                            <th className="py-2 px-3">Description</th>
                            <th className="py-2 px-3">Date</th>
                            <th className="py-2 px-3 text-right">Qty</th>
                            <th className="py-2 px-3 text-right">Unit ($)</th>
                            <th className="py-2 px-3 text-right">Total ($)</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60 text-slate-300">
                          {caseData.agent_findings.claims.service_lines.map((line: any, i: number) => (
                            <tr key={i} className="hover:bg-slate-800/40">
                              <td className="py-1.5 px-3">{line.line_number}</td>
                              <td className="py-1.5 px-3 text-cyan-300">{line.cpt_code?.value}</td>
                              <td className="py-1.5 px-3 truncate max-w-[160px]">{line.description?.value}</td>
                              <td className="py-1.5 px-3 text-slate-400">{line.service_date?.value}</td>
                              <td className="py-1.5 px-3 text-right">{line.quantity?.value}</td>
                              <td className="py-1.5 px-3 text-right">{line.unit_charge?.value?.toFixed(2)}</td>
                              <td className="py-1.5 px-3 text-right font-bold text-white">{line.total_charge?.value?.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* VLM Extraction Display */}
                {ocrPayload?.vlm_extraction && Object.keys(ocrPayload.vlm_extraction).length > 0 && (
                  <div className="bg-slate-950 rounded-lg border border-indigo-900 p-4 space-y-2">
                    <div className="flex items-center justify-between text-xs text-indigo-300 border-b border-indigo-900/60 pb-2">
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold">VLM Semantic Extraction (Fallback)</span>
                      </div>
                      <span className="text-[10px] font-mono">ADR-009 Enhancement</span>
                    </div>
                    <div className="p-3 bg-indigo-950/20 rounded font-mono text-xs text-indigo-200 whitespace-pre-wrap leading-relaxed">
                      <pre className="m-0 overflow-x-auto">
                        {JSON.stringify(ocrPayload.vlm_extraction, null, 2)}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-8 text-center text-slate-500 text-xs">No documents uploaded.</div>
            )}
          </div>
        </div>

        {/* Right Column: Adjudication Engine & Operator Decision Console */}
        <div className="lg:col-span-5 space-y-4">
          {/* AI Autonomous Verdict Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4 shadow-lg">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                Autonomous Adjudication Engine
              </h2>
              <span className="text-[10px] font-mono text-cyan-400">ADR-001 Priority P1–P8</span>
            </div>

            <div className="flex items-center justify-between bg-slate-950 p-4 rounded-xl border border-slate-800">
              <div>
                <span className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold block">
                  Recommended Outcome
                </span>
                <div className="text-xl font-bold mt-1">
                  {caseData.current_decision === "APPROVE" ? (
                    <span className="text-emerald-400 flex items-center space-x-1.5">
                      <CheckCircle2 className="w-5 h-5" />
                      <span>APPROVE CLAIM</span>
                    </span>
                  ) : caseData.current_decision === "REJECT" ? (
                    <span className="text-rose-400 flex items-center space-x-1.5">
                      <XCircle className="w-5 h-5" />
                      <span>REJECT CLAIM</span>
                    </span>
                  ) : caseData.current_decision === "ESCALATE" ? (
                    <span className="text-amber-400 flex items-center space-x-1.5">
                      <AlertTriangle className="w-5 h-5" />
                      <span>ESCALATE REVIEW</span>
                    </span>
                  ) : (
                    <span className="text-slate-500">In Progress...</span>
                  )}
                </div>
              </div>

              {caseData.decision_confidence !== null && caseData.decision_confidence !== undefined && (
                <div className="text-right">
                  <span className="text-[10px] text-slate-500 uppercase tracking-wider font-semibold block">
                    Confidence
                  </span>
                  <span className="text-lg font-bold font-mono text-cyan-400">
                    {(caseData.decision_confidence * 100).toFixed(0)}%
                  </span>
                </div>
              )}
            </div>

            {/* Escalation Sub-Reason Callout */}
            {caseData.current_sub_reason && (
              <div className="p-3 bg-amber-950/40 border border-amber-800/80 rounded-xl text-xs flex items-center justify-between">
                <div className="text-amber-300 font-semibold flex items-center space-x-1.5">
                  <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
                  <span>Escalation Sub-Reason: {caseData.current_sub_reason}</span>
                </div>
                {caseData.rule_id_matched && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-900/60 text-amber-200 border border-amber-700">
                    {caseData.rule_id_matched}
                  </span>
                )}
              </div>
            )}

            {/* AI Decision Analysis & Clinical Rationale (ADR-011) */}
            {(caseData.detailed_explanation || caseData.justification) && (
              <div className="bg-slate-950/90 border border-indigo-900/70 rounded-xl overflow-hidden shadow-xl space-y-0">
                <div className="bg-gradient-to-r from-indigo-950/90 via-slate-900 to-slate-950 px-3.5 py-2.5 border-b border-indigo-900/40 flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Sparkles className="w-4 h-4 text-cyan-400 animate-pulse flex-shrink-0" />
                    <span className="text-xs font-bold text-slate-100 uppercase tracking-wider flex items-center gap-1.5">
                      <span>AI Decision Analysis & Clinical Rationale</span>
                      {caseData.rule_id_matched && (
                        <span className="hidden sm:inline-block px-1.5 py-0.5 rounded text-[9px] font-mono bg-cyan-950 text-cyan-300 border border-cyan-800">
                          {caseData.rule_id_matched}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="flex items-center space-x-1.5">
                    <button
                      type="button"
                      onClick={() => handleCopyExplanation(caseData.detailed_explanation || caseData.justification || "")}
                      className="flex items-center space-x-1 px-2 py-1 rounded bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white text-[10px] font-mono transition border border-slate-700"
                      title="Copy explanation markdown"
                    >
                      {copiedExplanation ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-400" />
                          <span className="text-emerald-400 font-sans">Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3 text-slate-400" />
                          <span className="font-sans">Copy</span>
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsExplanationExpanded(!isExplanationExpanded)}
                      className="p-1 rounded bg-slate-800/80 hover:bg-slate-700 text-slate-400 hover:text-white transition"
                      title={isExplanationExpanded ? "Collapse section" : "Expand section"}
                    >
                      {isExplanationExpanded ? (
                        <ChevronUp className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronDown className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                </div>

                {isExplanationExpanded && (
                  <div className="p-3.5 bg-slate-950/70 max-h-[460px] overflow-y-auto space-y-2 border-t border-slate-900">
                    <FormattedExplanation
                      content={caseData.detailed_explanation || caseData.justification || ""}
                    />
                  </div>
                )}
              </div>
            )}

            {/* AI Specialist Findings & Diagnostics */}
            {caseData.agent_findings && (
              <div className="p-3.5 bg-slate-950/80 border border-slate-800 rounded-xl space-y-2.5">
                <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
                  <span className="text-[10px] font-bold text-slate-300 uppercase tracking-wider">Multi-Agent Diagnostics</span>
                  <span className="text-[10px] text-cyan-400 font-mono">Spec 05 Pipeline</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  {/* KYC Card */}
                  <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">KYC Identity</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${
                        caseData.agent_findings.kyc?.verdict === "PASS" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" :
                        caseData.agent_findings.kyc?.verdict === "FAIL" ? "bg-rose-950 text-rose-400 border border-rose-800" : "bg-amber-950 text-amber-400 border border-amber-800"
                      }`}>
                        {caseData.agent_findings.kyc?.verdict || "INDETERMINATE"}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400">
                      Member DB: {caseData.agent_findings.kyc?.member_db_matched ? "Matched" : "Unmatched"}
                    </div>
                    {caseData.agent_findings.kyc?.tamper_score !== undefined && (
                      <div className="text-[10px] text-slate-400">
                        Tamper: {(caseData.agent_findings.kyc.tamper_score * 100).toFixed(0)}%
                      </div>
                    )}
                    {caseData.agent_findings.kyc?.flags && caseData.agent_findings.kyc.flags.length > 0 && (
                      <div className="pt-1 flex flex-wrap gap-1">
                        {caseData.agent_findings.kyc.flags.map((f: string) => (
                          <span key={f} className="px-1.5 py-0.5 bg-rose-950/80 text-rose-300 border border-rose-800/80 rounded text-[9px] font-mono">
                            {f}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Claims Card */}
                  <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">Claims Extraction</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${
                        caseData.agent_findings.claims?.verdict === "PASS" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" :
                        caseData.agent_findings.claims?.verdict === "FAIL" ? "bg-rose-950 text-rose-400 border border-rose-800" : "bg-amber-950 text-amber-400 border border-amber-800"
                      }`}>
                        {caseData.agent_findings.claims?.verdict || "INDETERMINATE"}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400">
                      Line Math: {caseData.agent_findings.claims?.line_items_sum_valid ? "Valid" : "Incomplete"}
                    </div>
                    {caseData.agent_findings.claims?.claim_amount !== undefined && (
                      <div className="text-[10px] text-slate-300 font-mono">
                        Total: ${(caseData.agent_findings.claims.claim_amount?.value ?? caseData.agent_findings.claims.claim_amount ?? 0).toLocaleString()}
                      </div>
                    )}
                    {caseData.agent_findings.claims?.validation_errors && caseData.agent_findings.claims.validation_errors.length > 0 && (
                      <div className="pt-1 text-[9px] text-amber-400 font-mono truncate" title={caseData.agent_findings.claims.validation_errors[0]}>
                        {caseData.agent_findings.claims.validation_errors[0]}
                      </div>
                    )}
                  </div>

                  {/* Policy Card */}
                  <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">Policy Coverage</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${
                        caseData.agent_findings.policy?.verdict === "PASS" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" :
                        caseData.agent_findings.policy?.verdict === "FAIL" ? "bg-rose-950 text-rose-400 border border-rose-800" : "bg-amber-950 text-amber-400 border border-amber-800"
                      }`}>
                        {caseData.agent_findings.policy?.verdict || "INDETERMINATE"}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400">
                      Coverage: {caseData.agent_findings.policy?.coverage_percentage ? `${caseData.agent_findings.policy.coverage_percentage}%` : "Pending"}
                    </div>
                  </div>

                  {/* Fraud Card */}
                  <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">Fraud Engine</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold ${
                        (caseData.agent_findings.fraud?.risk_level || "LOW") === "LOW" ? "bg-emerald-950 text-emerald-400 border border-emerald-800" :
                        caseData.agent_findings.fraud?.risk_level === "HIGH" ? "bg-rose-950 text-rose-400 border border-rose-800" : "bg-amber-950 text-amber-400 border border-amber-800"
                      }`}>
                        {caseData.agent_findings.fraud?.risk_level || "LOW"}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400">
                      Fraud Score: {(((caseData.agent_findings.fraud?.fraud_score ?? caseData.agent_findings.fraud?.score) || 0) * 100).toFixed(0)}%
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Missing Documents Alert */}
            {caseData.missing_documents && caseData.missing_documents.length > 0 && (
              <div className="p-3 bg-blue-950/40 border border-blue-800/80 rounded-lg text-xs space-y-1">
                <div className="text-blue-300 font-semibold flex items-center space-x-1.5">
                  <FileText className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                  <span>Missing Mandatory Documents:</span>
                </div>
                <ul className="list-disc list-inside text-blue-200/80 text-[11px] pl-5">
                  {caseData.missing_documents.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* Operator Action Console */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4 shadow-lg">
            <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider border-b border-slate-800 pb-2">
              Human Reviewer Console
            </h3>

            {actionError && (
              <div className="p-3 rounded-lg bg-rose-950/60 border border-rose-800 text-rose-300 text-xs">
                {actionError}
              </div>
            )}

            {isAwaitingHuman ? (
              hasLock ? (
                <div className="space-y-3">
                  <button
                    onClick={handleConfirmRecommendation}
                    disabled={submittingAction}
                    className="w-full py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold shadow flex items-center justify-center space-x-2 transition"
                  >
                    <CheckCircle className="w-4 h-4" />
                    <span>Confirm Recommended Decision</span>
                  </button>

                  <div className="grid grid-cols-2 gap-3">
                    <button
                      onClick={() => setIsOverrideModalOpen(true)}
                      disabled={submittingAction}
                      className="py-2.5 px-4 rounded-lg bg-amber-600 hover:bg-amber-500 text-slate-950 text-xs font-bold shadow flex items-center justify-center space-x-2 transition"
                    >
                      <AlertTriangle className="w-4 h-4" />
                      <span>Override Decision</span>
                    </button>

                    <button
                      onClick={() => setIsRfiModalOpen(true)}
                      disabled={submittingAction}
                      className="py-2.5 px-4 rounded-lg bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-cyan-800 text-xs font-bold shadow flex items-center justify-center space-x-2 transition"
                    >
                      <Send className="w-4 h-4" />
                      <span>Request Info (RFI)</span>
                    </button>
                  </div>
                </div>
              ) : (
                <div className="p-4 bg-slate-950 rounded-xl border border-slate-800 text-center space-y-3">
                  <Lock className="w-6 h-6 text-amber-400 mx-auto" />
                  <p className="text-xs text-slate-400">
                    You must acquire the review lock before submitting adjudication decisions or overrides.
                  </p>
                  <button
                    onClick={handleAcquireLock}
                    className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-slate-950 text-xs font-bold"
                  >
                    Acquire Lock Now
                  </button>
                </div>
              )
            ) : (
              <div className="p-4 bg-slate-950 rounded-xl border border-slate-800 text-center space-y-3">
                <CheckCircle2 className="w-6 h-6 text-emerald-400 mx-auto" />
                <p className="text-xs text-slate-400">
                  Case is currently in state <strong className="text-slate-200">{caseData.workflow_state}</strong>. Human review is closed.
                </p>
                <button
                  onClick={handleRerunCase}
                  disabled={rerunning}
                  className="w-full py-2 px-3 rounded-lg bg-indigo-950 hover:bg-indigo-900 border border-indigo-800 text-indigo-300 text-xs font-semibold flex items-center justify-center space-x-2 transition disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${rerunning ? "animate-spin text-indigo-400" : ""}`} />
                  <span>{rerunning ? "Rerunning Pipeline..." : "Rerun Adjudication Pipeline"}</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Decision Override Modal */}
      {isOverrideModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-base font-bold text-white flex items-center space-x-2">
              <AlertTriangle className="w-5 h-5 text-amber-400" />
              <span>Adjudication Decision Override</span>
            </h3>

            <form onSubmit={handleOverrideSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  New Binding Decision
                </label>
                <select
                  value={overrideDecision}
                  onChange={(e) => setOverrideDecision(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
                >
                  <option value="APPROVE">APPROVE</option>
                  <option value="REJECT">REJECT</option>
                  <option value="ESCALATE">ESCALATE</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Reason Code
                </label>
                <select
                  value={reasonCode}
                  onChange={(e) => setReasonCode(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
                >
                  <option value="SUPERVISORY_EXCEPTION">SUPERVISORY_EXCEPTION</option>
                  <option value="POLICY_PREAUTHORIZATION_GRANTED">POLICY_PREAUTHORIZATION_GRANTED</option>
                  <option value="SUSPECTED_FRAUD_VERIFIED">SUSPECTED_FRAUD_VERIFIED</option>
                  <option value="DOCUMENTATION_LEGIBLE_TO_HUMAN">DOCUMENTATION_LEGIBLE_TO_HUMAN</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Mandatory Remarks (Min 10 characters)
                </label>
                <textarea
                  rows={3}
                  value={remarks}
                  onChange={(e) => setRemarks(e.target.value)}
                  placeholder="Explain why the autonomous decision is being superseded..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsOverrideModalOpen(false)}
                  className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingAction}
                  className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-slate-950 text-xs font-bold"
                >
                  {submittingAction ? "Submitting..." : "Submit Override"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Request Information (RFI) Modal */}
      {isRfiModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-base font-bold text-white flex items-center space-x-2">
              <Send className="w-5 h-5 text-cyan-400" />
              <span>Request Information (RFI)</span>
            </h3>

            <form onSubmit={handleRfiSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Missing Document Details & Instructions
                </label>
                <textarea
                  rows={3}
                  value={rfiRemarks}
                  onChange={(e) => setRfiRemarks(e.target.value)}
                  placeholder="Specify required identity proof or medical bill amendments..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-cyan-500"
                />
              </div>

              <div className="flex items-center justify-end space-x-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsRfiModalOpen(false)}
                  className="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingAction}
                  className="px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-bold"
                >
                  {submittingAction ? "Submitting..." : "Send RFI"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function FormattedExplanation({ content }: { content: string }) {
  if (!content) return null;

  const lines = content.split("\n");
  return (
    <div className="space-y-1.5 text-xs text-slate-300 leading-relaxed font-sans">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={idx} className="h-1" />;
        }
        if (trimmed === "---") {
          return <hr key={idx} className="border-slate-800 my-2" />;
        }
        if (trimmed.startsWith("### ")) {
          return (
            <h3 key={idx} className="text-xs font-bold text-cyan-300 mt-2.5 mb-1 flex items-center space-x-1.5 border-b border-slate-800/80 pb-1">
              <span>{trimmed.replace(/^###\s+/, "")}</span>
            </h3>
          );
        }
        if (trimmed.startsWith("#### ")) {
          return (
            <h4 key={idx} className="text-[11px] font-semibold text-slate-200 uppercase tracking-wider mt-2 mb-0.5">
              {trimmed.replace(/^####\s+/, "")}
            </h4>
          );
        }
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          const bulletContent = trimmed.substring(2);
          return (
            <div key={idx} className="flex items-start space-x-2 pl-1 text-slate-300">
              <span className="text-cyan-400 mt-0.5 text-[10px]">&bull;</span>
              <div className="flex-1">{renderFormattedText(bulletContent)}</div>
            </div>
          );
        }
        if (trimmed.startsWith("  - ")) {
          const subContent = trimmed.substring(4);
          return (
            <div key={idx} className="flex items-start space-x-2 pl-4 text-slate-400 text-[11px]">
              <span className="text-slate-500 mt-0.5 text-[9px]">&bull;</span>
              <div className="flex-1">{renderFormattedText(subContent)}</div>
            </div>
          );
        }
        return (
          <p key={idx} className="text-slate-300">
            {renderFormattedText(trimmed)}
          </p>
        );
      })}
    </div>
  );
}

function renderFormattedText(text: string) {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*.*?\*\*|`.*?`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(
        <strong key={match.index} className="text-slate-100 font-semibold">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(
        <code
          key={match.index}
          className="px-1 py-0.2 rounded bg-slate-900 text-cyan-300 border border-slate-800 font-mono text-[10px]"
        >
          {token.slice(1, -1)}
        </code>
      );
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }
  return parts.length > 0 ? parts : text;
}

