"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  RotateCcw,
  Scale,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Unlock,
  UserCheck,
  XCircle,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  ClinicalExplanationViewer,
  formatRuleLabel,
  formatSubReason,
} from "@/components/ClinicalExplanationViewer";
import { LiveAgentTimeline } from "@/components/LiveAgentTimeline";
import {
  acquireReviewLock,
  downloadCaseReport,
  explainCase,
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
import { formatDateTime, formatRelativeTime } from "@/lib/date";
import { useAuth } from "@/lib/auth";

export default function CaseDetailPage() {
  const params = useParams();
  const router = useRouter();
  const caseId = params?.id as string;
  const { user, loading: authLoading } = useAuth();

  const [caseData, setCaseData] = useState<CaseDetailResponse | null>(null);
  const caseVersionRef = useRef<number | undefined>(undefined);
  const [auditData, setAuditData] = useState<AuditVerifyResponse | null>(null);
  const [lockData, setLockData] = useState<ReviewLockResponse | null>(null);
  const [selectedDocIndex, setSelectedDocIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [ocrPayload, setOcrPayload] = useState<any | null>(null);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [docImageUrl, setDocImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [overlayMode, setOverlayMode] = useState<"key_fields" | "all_ocr" | "none">("key_fields");
  const [zoomLevel, setZoomLevel] = useState<number>(1.0);
  const [downloadingReport, setDownloadingReport] = useState(false);

  const handleZoomIn = () => {
    setZoomLevel((prev) => Math.min(3.0, Math.round((prev + 0.25) * 100) / 100));
  };

  const handleZoomOut = () => {
    setZoomLevel((prev) => Math.max(0.5, Math.round((prev - 0.25) * 100) / 100));
  };

  const handleResetZoom = () => {
    setZoomLevel(1.0);
  };

  // Review Override Modal State
  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState(false);
  const [overrideDecision, setOverrideDecision] = useState("APPROVE");
  const [reasonCode, setReasonCode] = useState("SUPERVISORY_EXCEPTION");
  const [remarks, setRemarks] = useState("");
  const [submittingAction, setSubmittingAction] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [pipelineAwaitingReveal, setPipelineAwaitingReveal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Request Info Modal State
  const [isRfiModalOpen, setIsRfiModalOpen] = useState(false);
  const [rfiRemarks, setRfiRemarks] = useState("");

  // AI Decision Explanation State
  const [isExplanationExpanded, setIsExplanationExpanded] = useState(true);
  const [copiedExplanation, setCopiedExplanation] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [explainError, setExplainError] = useState<string | null>(null);
  const [modelSource, setModelSource] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("all");

  const handleCopyExplanation = (text: string) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedExplanation(true);
    setTimeout(() => setCopiedExplanation(false), 2000);
  };

  const openOverrideModal = (suggestedDecision?: string) => {
    setActionError(null);
    if (suggestedDecision) {
      setOverrideDecision(suggestedDecision);
    } else if (caseData?.current_decision === "APPROVE") {
      setOverrideDecision("REJECT");
    } else if (caseData?.current_decision === "REJECT") {
      setOverrideDecision("APPROVE");
    } else {
      setOverrideDecision("APPROVE");
    }
    setIsOverrideModalOpen(true);
  };

  const handleGenerateExplanation = async () => {
    if (!caseId) return;
    setExplaining(true);
    setExplainError(null);
    try {
      const res = await explainCase(caseId);
      setCaseData((prev) => (prev ? {
        ...prev,
        detailed_explanation: res.detailed_explanation,
        justification: res.detailed_explanation,
      } : prev));
      setModelSource(res.model_source);
      setIsExplanationExpanded(true);
    } catch (err: unknown) {
      setExplainError(err instanceof Error ? err.message : "Failed to synthesize LLM explanation.");
    } finally {
      setExplaining(false);
    }
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
      caseVersionRef.current = detail.version;
      setCaseData(detail);
      setAuditData(audit);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load case detail.");
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  const handlePipelineComplete = useCallback(async () => {
    const startingVersion = caseVersionRef.current;
    let latestCase: CaseDetailResponse | null = null;

    for (let attempt = 0; attempt < 12; attempt += 1) {
      try {
        latestCase = await getCaseDetail(caseId);
        const terminalState = ["COMPLETED", "FAILED"].includes(latestCase.workflow_state);
        if (
          startingVersion === undefined ||
          latestCase.version > startingVersion ||
          (!rerunning && terminalState) ||
          attempt === 11
        ) {
          break;
        }
      } catch {
        if (attempt === 11) break;
      }

      await new Promise<void>((resolve) => window.setTimeout(resolve, 250));
    }

    if (latestCase) {
      setCaseData(latestCase);
      setAuditData(await getAuditVerification(caseId).catch(() => null));
    } else {
      await loadData();
    }

    if (rerunning) {
      setRerunning(false);
      setPipelineAwaitingReveal(true);
    }
  }, [caseId, loadData, rerunning]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (!caseData) return;

    const workflowInProgress = ["RECEIVED", "SCANNING", "SPLIT", "CLASSIFYING", "ANALYZING"].includes(
      caseData.workflow_state,
    );
    if (!rerunning && !workflowInProgress) return;

    const startingVersion = caseData.version;
    let isCurrent = true;
    let requestPending = false;
    const intervalId = window.setInterval(async () => {
      if (requestPending) return;
      requestPending = true;
      try {
        const latestCase = await getCaseDetail(caseId);
        const workflowSettled = ["AWAITING_INFO", "AWAITING_HUMAN", "COMPLETED", "FAILED"].includes(
          latestCase.workflow_state,
        );
        if (isCurrent && latestCase.version > startingVersion && workflowSettled) {
          caseVersionRef.current = latestCase.version;
          setCaseData(latestCase);
          setAuditData(await getAuditVerification(caseId).catch(() => null));
          if (rerunning) {
            setRerunning(false);
            setPipelineAwaitingReveal(true);
          }
          window.clearInterval(intervalId);
        }
      } catch {
        // Keep polling while the active workflow is temporarily unavailable.
      } finally {
        requestPending = false;
      }
    }, 1500);

    return () => {
      isCurrent = false;
      window.clearInterval(intervalId);
    };
  }, [caseData, caseId, rerunning]);

  const selectedDocId = caseData?.documents?.[selectedDocIndex]?.document_id;

  useEffect(() => {
    setZoomLevel(1.0);
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
    if (!caseData.current_decision) {
      setActionError("There is no AI recommendation to confirm.");
      return;
    }
    setSubmittingAction(true);
    setActionError(null);
    try {
      await submitConfirmation({
        case_id: caseId,
        expected_case_version: caseData.version,
        new_decision: caseData.current_decision,
        reason_code: "HUMAN_CONFIRMED_RECOMMENDATION",
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
    if (remarks.trim().length < 5) {
      setActionError("Reviewer feedback remarks must be at least 5 characters long.");
      return;
    }
    setSubmittingAction(true);
    setActionError(null);
    try {
      const latestCase = await getCaseDetail(caseId);
      if (latestCase.version !== caseData.version) {
        setCaseData(latestCase);
        setActionError(
          `This case changed from version ${caseData.version} to ${latestCase.version}. The latest case has been loaded; review it before submitting another decision.`,
        );
        return;
      }
      await submitOverride({
        case_id: caseId,
        expected_case_version: latestCase.version,
        new_decision: overrideDecision,
        reason_code: reasonCode,
        remarks: remarks.trim(),
      });
      setIsOverrideModalOpen(false);
      setLockData(null);
      setRemarks("");
      await loadData();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Override submission failed.";
      let latestCase: CaseDetailResponse | null = null;
      try {
        latestCase = await getCaseDetail(caseId);
        setCaseData(latestCase);
      } catch {
        // Keep the original request error when the case cannot be reloaded.
      }

      const latestReview = latestCase?.latest_human_review;
      const overrideWasApplied =
        latestCase !== null &&
        latestCase.version > caseData.version &&
        latestCase.current_decision === overrideDecision &&
        latestReview?.action === "OVERRIDE" &&
        latestReview.new_decision === overrideDecision &&
        latestReview.reason_code === reasonCode &&
        latestReview.remarks === remarks.trim();
      const caseVersionChanged =
        latestCase !== null && latestCase.version !== caseData.version;

      if (overrideWasApplied) {
        setIsOverrideModalOpen(false);
        setLockData(null);
        setRemarks("");
        setActionError(null);
      } else if (message.includes("Optimistic lock conflict") || caseVersionChanged) {
        setActionError("The case changed while you were reviewing it. The latest version is loaded; verify its current decision before submitting another override.");
      } else {
        setActionError(message);
      }
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
        expected_case_version: caseData.version,
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
    setPipelineAwaitingReveal(false);
    setActionError(null);
    try {
      await rerunCase(caseId);
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Workflow rerun failed.");
      setRerunning(false);
    }
  };

  const handleDownloadReport = async () => {
    if (!caseId || downloadingReport) return;
    setDownloadingReport(true);
    try {
      const blob = await downloadCaseReport(caseId);
      const downloadUrl = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = downloadUrl;
      a.download = `MediShield_Case_${caseId}_Report.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(downloadUrl);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "PDF report download failed.");
    } finally {
      setDownloadingReport(false);
    }
  };

  if (!authLoading && user?.role === "CLAIMER") {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center">
        <div className="p-4 bg-status-info/10 border border-status-info/20 rounded-card mb-6 inline-block">
          <ShieldAlert className="w-12 h-12 text-status-info mx-auto" />
        </div>
        <h2 className="text-2xl font-bold text-foreground mb-2">Claimer Account Restricted</h2>
        <p className="text-sm text-secondary mb-6 max-w-lg mx-auto leading-relaxed">
          The Operational Case Workbench contains adjustor-only tools and fraud analytics. Under MediShield RBAC, please track your claim status using your dedicated Claimer Portal.
        </p>
        <Link
          href={`/claimer?case_id=${caseId}`}
          className="inline-flex items-center space-x-2 px-5 py-2.5 bg-accent hover:bg-accent-hover text-accent-fg rounded-btn font-semibold text-sm transition shadow-xs"
        >
          <span>Track Claim in Claimer Portal</span>
        </Link>
      </div>
    );
  }

  if (loading && !caseData) {
    return (
      <div className="py-24 text-center text-muted space-y-3">
        <RefreshCw className="w-8 h-8 animate-spin mx-auto text-accent" />
        <p className="text-sm">Loading Case {caseId} from control plane...</p>
      </div>
    );
  }

  if (error || !caseData) {
    return (
      <div className="py-12 max-w-lg mx-auto text-center space-y-4">
        <AlertTriangle className="w-12 h-12 text-status-danger mx-auto" />
        <h2 className="text-lg font-bold text-foreground">Error Loading Case</h2>
        <p className="text-xs text-status-danger">{error || "Case not found."}</p>
        <Link
          href="/"
          className="inline-flex items-center space-x-1.5 px-4 py-2 rounded-btn bg-card hover:bg-card-inner border border-border text-xs text-foreground transition shadow-xs"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back to Queue</span>
        </Link>
      </div>
    );
  }

  const selectedDoc = (caseData.documents || [])[selectedDocIndex];
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
      <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-4 p-4 rounded-card bg-card border border-border shadow-xs">
        <div className="flex items-center space-x-3 min-w-0">
          <Link
            href="/"
            className="h-8 w-8 inline-flex items-center justify-center rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border transition-all duration-180 shadow-xs active:scale-[0.98] shrink-0"
            title="Back to Operations Queue"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="min-w-0">
            <div className="flex items-center space-x-2.5 flex-wrap">
              <h1 className="text-xl font-bold font-mono text-foreground tracking-tight">
                {caseData.case_id}
              </h1>
              <span className="h-5 inline-flex items-center px-2 rounded text-[10px] font-mono bg-card-inner text-secondary border border-border font-medium">
                v{caseData.version}
              </span>
              <span
                className={`h-5 inline-flex items-center px-2.5 rounded-full text-[10px] font-semibold tracking-wide border ${
                  caseData.workflow_state === "AWAITING_HUMAN"
                    ? "bg-warning-bg text-warning border-warning-border"
                    : caseData.workflow_state === "AWAITING_INFO"
                    ? "bg-sky-soft text-sky-deep border-sky/30"
                    : caseData.workflow_state === "COMPLETED"
                    ? "bg-success-bg text-success border-success-border"
                    : "bg-card-inner text-secondary border-border"
                }`}
              >
                {caseData.workflow_state}
              </span>
            </div>
            <p className="text-xs text-secondary mt-0.5 truncate">
              Workflow Engine: <span className="font-mono text-foreground font-medium">{caseData.workflow_version}</span> &bull; Submitted: {formatDateTime(caseData.created_at)} ({formatRelativeTime(caseData.created_at)})
            </p>
          </div>
        </div>

        {/* Lock Acquisition / Actions */}
        <div className="flex flex-wrap items-center gap-2.5 shrink-0">
          {/* Status Indicators Cluster */}
          <div className="flex items-center gap-2">
            {auditData && (
              <div
                className={`h-8 inline-flex items-center space-x-1.5 px-3 rounded-full border text-xs font-medium shadow-xs ${
                  auditData.chain_intact
                    ? "bg-success-bg border-success-border text-success"
                    : "bg-danger-bg border-danger-border text-danger"
                }`}
              >
                {auditData.chain_intact ? (
                  <ShieldCheck className="w-3.5 h-3.5 text-success shrink-0" />
                ) : (
                  <ShieldAlert className="w-3.5 h-3.5 text-danger shrink-0" />
                )}
                <span className="text-[11px] font-medium">{auditData.chain_intact ? "Audit Chain Intact" : "Audit Compromised"}</span>
              </div>
            )}

            {isAwaitingHuman && (
              <>
                {hasLock ? (
                  <div className="h-8 inline-flex items-center space-x-2 bg-warning-bg border border-warning-border px-2.5 rounded-btn text-xs text-warning font-medium shadow-xs">
                    <Lock className="w-3.5 h-3.5 shrink-0" />
                    <span className="text-[11px]">Lock Active (15m)</span>
                    <button
                      type="button"
                      onClick={handleReleaseLock}
                      className="ml-1 h-5 px-1.5 rounded bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border text-[10px] font-medium transition-all duration-180 active:scale-[0.98]"
                    >
                      Release
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={handleAcquireLock}
                    className="h-8 inline-flex items-center space-x-1.5 px-3 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98]"
                  >
                    <Unlock className="w-3.5 h-3.5" />
                    <span>Acquire Review Lock</span>
                  </button>
                )}
              </>
            )}
          </div>

          {/* Clean Divider between Status and Actions */}
          {(auditData || isAwaitingHuman) && (
            <div className="hidden sm:block h-5 w-px bg-border mx-0.5" />
          )}

          {/* Action Toolbar Cluster */}
          <div className="flex items-center gap-2">
            {/* Rerun Pipeline Button */}
            <button
              type="button"
              onClick={handleRerunCase}
              disabled={rerunning}
              className="h-8 inline-flex items-center space-x-1.5 px-3 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-foreground border border-border hover:border-pink-border text-xs font-medium transition-all duration-180 shadow-xs disabled:opacity-50 active:scale-[0.98]"
              title="Re-execute claims adjudication workflow pipeline with latest models & rules"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${rerunning ? "animate-spin text-sky-deep" : "text-muted"}`} />
              <span>{rerunning ? "Rerunning..." : "Rerun Pipeline"}</span>
            </button>

            {/* Download PDF Case Report Button */}
            <button
              type="button"
              onClick={handleDownloadReport}
              disabled={downloadingReport}
              className="h-8 inline-flex items-center space-x-1.5 px-3 rounded-btn bg-sky-soft hover:bg-pink-hover text-sky-deep hover:text-pink-deep border border-sky/30 hover:border-pink-border text-xs font-semibold transition-all duration-180 shadow-xs active:scale-[0.98] disabled:opacity-50"
              title="Download immutable audit-ready PDF case adjudication report"
            >
              <FileText className={`w-3.5 h-3.5 text-sky-deep ${downloadingReport ? "animate-pulse" : ""}`} />
              <span>{downloadingReport ? "Generating PDF..." : "Download Report"}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Real-Time Agent Execution Stream (ADR-017 Feature 2) */}
      <LiveAgentTimeline
        caseId={caseData.case_id}
        isComplete={caseData.is_complete && !rerunning && !pipelineAwaitingReveal}
        isRerunning={rerunning}
        onPipelineComplete={handlePipelineComplete}
      />

      {/* Main Grid: Document Viewer on Left (6 cols), Decision Console on Right (6 cols) */}
      <div className="grid min-w-0 grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Multi-Document Roster & Viewer */}
        <div className="min-w-0 lg:col-span-6 space-y-4">
          <div className="bg-card border border-border rounded-card overflow-hidden shadow-xs transition-colors">
            <div className="bg-card-inner px-4 py-3 border-b border-border flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <FileText className="w-4 h-4 text-muted" />
                <span className="text-xs font-bold text-foreground uppercase tracking-wider">
                  Submitted Documents ({(caseData.documents || []).length})
                </span>
              </div>
            </div>

            {/* Document Tabs */}
            <div className="flex overflow-x-auto border-b border-border bg-card-inner p-2 gap-2">
              {(caseData.documents || []).map((doc, idx) => (
                <button
                  key={doc.document_id}
                  onClick={() => setSelectedDocIndex(idx)}
                  className={`px-3.5 py-2 rounded-btn text-xs font-mono transition-all duration-180 text-left flex-shrink-0 border active:scale-[0.98] ${
                    selectedDocIndex === idx
                      ? "bg-sky-soft text-sky-deep border-sky-med/60 font-bold shadow-xs"
                      : "bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border-border hover:border-pink-border"
                  }`}
                >
                  <div className="truncate max-w-[160px] font-semibold text-foreground group-hover:text-pink-deep">{doc.file_name}</div>
                  <div className="text-[10px] text-muted">{doc.doc_type || "UNKNOWN"}</div>
                </button>
              ))}
            </div>

            {/* Document Inspector Panel */}
            {selectedDoc ? (
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-card-inner p-3 rounded-card border border-border text-[11px] font-mono shadow-xs">
                  <div className="min-w-0">
                    <span className="text-muted block text-[10px] uppercase tracking-wider">Doc ID:</span>
                    <span className="text-foreground font-semibold truncate block" title={selectedDoc.document_id}>
                      {selectedDoc.document_id}
                    </span>
                  </div>
                  <div className="min-w-0">
                    <span className="text-muted block text-[10px] uppercase tracking-wider">MIME Type:</span>
                    <span className="text-foreground truncate block">{selectedDoc.mime_type}</span>
                  </div>
                  <div className="min-w-0">
                    <span className="text-muted block text-[10px] uppercase tracking-wider">File Size:</span>
                    <span className="text-foreground font-mono truncate block">{((selectedDoc.size_bytes || 0) / 1024).toFixed(1)} KB</span>
                  </div>
                  <div className="min-w-0">
                    <span className="text-muted block text-[10px] uppercase tracking-wider">SHA-256:</span>
                    <span className="text-secondary truncate block font-mono" title={selectedDoc.checksum || ""}>
                      {(selectedDoc.checksum || "").substring(0, 10)}...
                    </span>
                  </div>
                </div>

                {/* Visual Provenance Viewer */}
                <div className="bg-card-inner/60 rounded-card p-4 space-y-3 shadow-xs">
                  <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-2.5 text-xs text-secondary border-b border-border pb-2.5">
                    <div className="flex items-center space-x-2">
                      <span className="font-semibold text-foreground">Extracted Visual Provenance</span>
                      <span className="text-[10px] bg-card px-2 py-0.5 rounded-btn border border-border text-secondary font-mono shadow-xs">
                        {displayedSpans.length} visible
                      </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      {/* Zoom Toolbar & Presets */}
                      <div className="h-8 inline-flex items-center space-x-1 bg-card px-2 rounded-btn border border-border text-[10px] shadow-xs">
                        <button
                          type="button"
                          onClick={handleZoomOut}
                          disabled={zoomLevel <= 0.5}
                          className="h-6 w-6 inline-flex items-center justify-center rounded-btn hover:bg-pink-hover text-secondary hover:text-pink-deep disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-180"
                          title="Zoom Out (Min 50%)"
                        >
                          <ZoomOut className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={handleResetZoom}
                          className="h-6 px-2 inline-flex items-center font-mono text-[11px] font-semibold text-foreground hover:bg-pink-hover hover:text-pink-deep rounded-btn transition-all duration-180"
                          title="Reset to 100%"
                        >
                          {Math.round(zoomLevel * 100)}%
                        </button>
                        <button
                          type="button"
                          onClick={handleZoomIn}
                          disabled={zoomLevel >= 3.0}
                          className="h-6 w-6 inline-flex items-center justify-center rounded-btn hover:bg-pink-hover text-secondary hover:text-pink-deep disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-180"
                          title="Zoom In (Max 300%)"
                        >
                          <ZoomIn className="w-3.5 h-3.5" />
                        </button>
                        <div className="w-px h-3.5 bg-border mx-0.5" />
                        <button
                          type="button"
                          onClick={() => setZoomLevel(1.5)}
                          className={`h-6 px-1.5 inline-flex items-center rounded-btn font-mono text-[10px] transition-all duration-180 ${
                            zoomLevel === 1.5
                              ? "bg-sky-soft text-sky-deep font-semibold"
                              : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                          }`}
                          title="150% Inspection Scale"
                        >
                          150%
                        </button>
                        <button
                          type="button"
                          onClick={() => setZoomLevel(2.0)}
                          className={`h-6 px-1.5 inline-flex items-center rounded-btn font-mono text-[10px] transition-all duration-180 ${
                            zoomLevel === 2.0
                              ? "bg-sky-soft text-sky-deep font-semibold"
                              : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                          }`}
                          title="200% High-Detail Scale"
                        >
                          200%
                        </button>
                        <div className="w-px h-3.5 bg-border mx-0.5" />
                        <button
                          type="button"
                          onClick={handleResetZoom}
                          className="h-6 w-6 inline-flex items-center justify-center rounded-btn hover:bg-pink-hover text-secondary hover:text-pink-deep transition-all duration-180"
                          title="Reset View (100%)"
                        >
                          <RotateCcw className="w-3 h-3" />
                        </button>
                      </div>

                      {/* Overlay Mode Switcher */}
                      <div className="h-8 inline-flex items-center space-x-1 bg-card p-0.5 rounded-btn border border-border text-[10px] shadow-xs">
                        <button
                          type="button"
                          onClick={() => setOverlayMode("key_fields")}
                          className={`h-7 px-2.5 inline-flex items-center space-x-1 rounded-btn transition-all duration-180 active:scale-[0.98] ${
                            overlayMode === "key_fields"
                              ? "bg-sky-soft text-sky-deep font-semibold border border-sky/40 shadow-xs"
                              : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                          }`}
                        >
                          <CheckSquare className="w-3 h-3 text-sky-deep" />
                          <span>Key Fields ({criticalSpans.length})</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setOverlayMode("all_ocr")}
                          className={`h-7 px-2.5 inline-flex items-center space-x-1 rounded-btn transition-all duration-180 active:scale-[0.98] ${
                            overlayMode === "all_ocr"
                              ? "bg-sky-soft text-sky-deep font-semibold border border-sky/40 shadow-xs"
                              : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                          }`}
                        >
                          <Layers className="w-3 h-3 text-sky-deep" />
                          <span>All OCR Tokens ({allOcrSpans.length})</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setOverlayMode("none")}
                          className={`h-7 px-2.5 inline-flex items-center rounded-btn transition-all duration-180 active:scale-[0.98] ${
                            overlayMode === "none"
                              ? "bg-sky-soft text-sky-deep font-semibold border border-sky/40 shadow-xs"
                              : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                          }`}
                        >
                          Hide
                        </button>
                      </div>
                    </div>
                  </div>
                  
                  {/* Stable, Fixed-Size Document Viewport with Scrollable Zoom Canvas */}
                  <div 
                    className={`relative border border-border rounded-card bg-[#F4F8FB] dark:bg-card-inner shadow-xs overflow-auto h-[560px] p-3 select-none flex ${
                      zoomLevel > 1.0 ? "items-start justify-start" : "items-start justify-center"
                    }`}
                  >
                    {!docImageUrl ? (
                      <div className="w-full h-full min-h-[400px] flex flex-col items-center justify-center text-muted text-xs space-y-2">
                        <RefreshCw className="w-6 h-6 animate-spin text-sky-deep" />
                        <span>Loading document image...</span>
                      </div>
                    ) : (
                      <div 
                        className="relative transition-[width] duration-150 ease-out origin-top shadow-sm bg-white dark:bg-card rounded border border-border/60 shrink-0"
                        style={{
                          width: `${Math.round(zoomLevel * 100)}%`,
                          maxWidth: zoomLevel <= 1.0 ? "100%" : "none",
                        }}
                      >
                        <img 
                          src={docImageUrl} 
                          alt="Document Context"
                          className="w-full h-auto block rounded select-none pointer-events-none"
                          draggable={false}
                        />
                        
                        {displayedSpans.filter((span) => span.bbox).map((span, idx) => {
                          const isKey = overlayMode === "key_fields";
                          return (
                            <div
                              key={idx}
                              className={`absolute border transition-colors cursor-crosshair group ${
                                isKey
                                  ? "border-pink-accent/80 bg-pink-blush/30 hover:bg-pink-hover/80"
                                  : "border-border bg-sky-soft/40 hover:bg-sky-soft/70"
                              }`}
                              style={{
                                left: `${span.bbox.x0 * 100}%`,
                                top: `${span.bbox.y0 * 100}%`,
                                width: `${Math.max(0.005, span.bbox.x1 - span.bbox.x0) * 100}%`,
                                height: `${Math.max(0.005, span.bbox.y1 - span.bbox.y0) * 100}%`,
                              }}
                            >
                              <div className="absolute hidden group-hover:block bottom-full left-1/2 -translate-x-1/2 mb-1.5 bg-card border border-border text-foreground text-[11px] p-2 rounded-btn shadow-xl z-20 font-mono whitespace-nowrap min-w-[140px] text-left pointer-events-none">
                                <div className="flex items-center justify-between border-b border-border pb-1 mb-1">
                                  <span className="text-foreground font-bold">
                                    {isKey ? span.key : span.span_id}
                                  </span>
                                  {span.confidence !== undefined && (
                                    <span className="text-[9px] text-muted">
                                      {(span.confidence * 100).toFixed(0)}%
                                    </span>
                                  )}
                                </div>
                                <div className="text-secondary text-xs truncate max-w-[240px]">
                                  {isKey ? span.value : span.text}
                                </div>
                                <div className="text-[9px] text-muted mt-1">
                                  [{span.bbox.x0.toFixed(3)}, {span.bbox.y0.toFixed(3)}, {span.bbox.x1.toFixed(3)}, {span.bbox.y1.toFixed(3)}]
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* Viewport Status & Pan Instructions Footer */}
                  <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between text-[11px] text-secondary gap-1 px-1">
                    <span>
                      {zoomLevel > 1.0
                        ? `Zoomed ${Math.round(zoomLevel * 100)}% • Use scrollbars or trackpad to pan fine details`
                        : `Viewing at ${Math.round(zoomLevel * 100)}% scale • Viewport fixed`}
                    </span>
                    {displayedSpans.length === 0 && overlayMode !== "none" ? (
                      <span className="text-muted italic">No coordinates matched for this display mode.</span>
                    ) : (
                      <span className="font-mono text-[10px] text-muted">
                        Resolution: {zoomLevel > 1.0 ? "Enlarged Inspection Mode" : "Standard Fit"}
                      </span>
                    )}
                  </div>
                </div>

                {/* Extracted vs Expected / Reconciled Adjudication Comparison Panel */}
                <div className="bg-card-inner/60 rounded-card p-4 space-y-3 shadow-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
                    <div className="flex min-w-0 items-center space-x-2">
                      <Scale className="w-4 h-4 text-muted" />
                      <span className="font-semibold text-xs text-foreground uppercase tracking-wider">
                        Extracted Findings vs. Adjudication Comparison
                      </span>
                    </div>
                    <span className="shrink-0 text-[10px] font-mono text-muted">Cross-Document Reconciled</span>
                  </div>

                  {/* Financial Totals Comparison Grid */}
                  <div className="grid min-w-0 grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                    <div className="flex min-h-[112px] min-w-0 flex-col rounded-card border border-border/35 bg-card p-3 shadow-xs">
                      <span className="block min-h-8 text-[10px] font-semibold uppercase leading-4 text-muted">Declared Header Total</span>
                      <div className="mt-1 break-all font-mono text-base font-bold tabular-nums text-foreground">
                        ${Number(caseData.agent_findings?.claims?.claim_amount?.value ?? 0).toFixed(2)}
                      </div>
                      <div className="mt-auto pt-1 text-[10px] text-muted">
                        Conf: {((caseData.agent_findings?.claims?.claim_amount?.confidence ?? 0) * 100).toFixed(0)}%
                      </div>
                    </div>

                    <div className="flex min-h-[112px] min-w-0 flex-col rounded-card border border-border/35 bg-card p-3 shadow-xs">
                      <span className="block min-h-8 text-[10px] font-semibold uppercase leading-4 text-muted">Calculated Line Sum</span>
                      <div className="mt-1 break-all font-mono text-base font-bold tabular-nums text-foreground">
                        ${Number(caseData.agent_findings?.claims?.calculated_lines_sum ?? 0).toFixed(2)}
                      </div>
                      <div className="mt-auto pt-1 text-[10px] text-muted">
                        From {caseData.agent_findings?.claims?.service_lines?.length ?? 0} itemized lines
                      </div>
                    </div>

                    <div className="flex min-h-[112px] min-w-0 flex-col rounded-card border border-border/35 bg-card p-3 shadow-xs">
                      <span className="block min-h-8 text-[10px] font-semibold uppercase leading-4 text-muted">Arithmetic Validation</span>
                      <div className="mt-1 min-w-0">
                        {caseData.agent_findings?.claims?.line_items_sum_valid ? (
                          <span className="inline-flex max-w-full flex-wrap items-center gap-1 rounded bg-status-success/5 px-2 py-0.5 text-[11px] font-semibold text-secondary">
                            <CheckCircle className="h-3.5 w-3.5 shrink-0 text-status-success/75" />
                            <span className="break-words">Totals match</span>
                          </span>
                        ) : (
                          <span className="inline-flex max-w-full flex-wrap items-center gap-1 rounded bg-status-warning/5 px-2 py-0.5 text-[11px] font-semibold text-secondary">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-status-warning/75" />
                            <span className="break-words">Totals differ</span>
                          </span>
                        )}
                      </div>
                      <div className="mt-auto pt-1 text-[9px] text-muted">
                        Billing total check
                      </div>
                    </div>
                  </div>

                  {/* Extracted CPT Procedural Codes */}
                  {caseData.agent_findings?.claims?.cpt_codes && caseData.agent_findings.claims.cpt_codes.length > 0 && (
                    <div className="p-3 bg-card rounded-card border border-border/35 space-y-1.5 shadow-xs">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-muted uppercase font-semibold">Extracted Procedure Codes (CPT)</span>
                        <span className="text-[10px] text-muted">Table-Isolated Extraction</span>
                      </div>
                      <div className="flex flex-wrap gap-2 pt-1">
                        {caseData.agent_findings.claims.cpt_codes.map((cpt: any, i: number) => (
                          <div key={i} className="px-2.5 py-1 rounded bg-card-inner border border-border text-xs font-mono text-foreground flex items-center space-x-1.5">
                            <span className="font-bold">{cpt.value}</span>
                            <span className="text-[9px] text-muted">({(cpt.confidence * 100).toFixed(0)}%)</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Itemized Service Lines Table */}
                  {caseData.agent_findings?.claims?.service_lines && caseData.agent_findings.claims.service_lines.length > 0 && (
                    <div className="overflow-x-auto rounded-card bg-card shadow-xs">
                      <table className="w-full text-left text-[11px] font-mono">
                        <thead className="bg-card-inner/70 text-muted uppercase text-[9px]">
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
                        <tbody className="divide-y divide-black/5 text-secondary">
                          {caseData.agent_findings.claims.service_lines.map((line: any, i: number) => {
                            const cptVal = line.cpt_code?.value || line.hcpcs?.value || "—";
                            const amtVal = typeof line.total_charge?.value === "number" ? line.total_charge.value : (typeof line.amount?.value === "number" ? line.amount.value : null);
                            const unitVal = typeof line.unit_charge?.value === "number" ? line.unit_charge.value : amtVal;
                            const qtyVal = line.quantity?.value || line.units?.value || 1;
                            return (
                              <tr key={i} className="hover:bg-pink-hover/40 transition-colors duration-150">
                                <td className="py-1.5 px-3">{line.line_number}</td>
                                <td className="py-1.5 px-3 text-foreground font-semibold">{cptVal}</td>
                                <td className="py-1.5 px-3 truncate max-w-[160px]">{line.description?.value || "—"}</td>
                                <td className="py-1.5 px-3 text-muted">{line.service_date?.value || "—"}</td>
                                <td className="py-1.5 px-3 text-right">{qtyVal}</td>
                                <td className="py-1.5 px-3 text-right">{unitVal != null ? `$${unitVal.toFixed(2)}` : "—"}</td>
                                <td className="py-1.5 px-3 text-right font-bold text-foreground">{amtVal != null ? `$${amtVal.toFixed(2)}` : "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* VLM Extraction Display */}
                {ocrPayload?.vlm_extraction && Object.keys(ocrPayload.vlm_extraction).length > 0 && (
                  <div className="bg-card-inner rounded-btn border border-border p-4 space-y-2">
                    <div className="flex items-center justify-between text-xs text-secondary border-b border-border pb-2">
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold">VLM Semantic Extraction (Fallback)</span>
                      </div>
                      <span className="text-[10px] font-mono text-muted">Policy Intelligence</span>
                    </div>
                    <div className="p-3 bg-card rounded font-mono text-xs text-foreground border border-border whitespace-pre-wrap leading-relaxed shadow-xs">
                      <pre className="m-0 overflow-x-auto">
                        {JSON.stringify(ocrPayload.vlm_extraction, null, 2)}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="p-8 text-center text-muted text-xs">No documents uploaded.</div>
            )}
          </div>
        </div>

        {/* Right Column: Adjudication Decision & Operator Console */}
        <div className="min-w-0 lg:col-span-6 space-y-4">
          {pipelineAwaitingReveal || rerunning ? (
            <div className="bg-card border border-border rounded-card p-6 sm:p-8 space-y-4 shadow-xs text-center animate-in fade-in duration-200">
              <div className="w-12 h-12 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center mx-auto text-accent">
                <RefreshCw className={`w-6 h-6 ${rerunning ? "animate-spin" : ""}`} />
              </div>
              <div className="space-y-1">
                <h3 className="text-sm font-bold text-foreground">
                  {rerunning ? "Adjudication Pipeline Running..." : "Pipeline Complete — Decision Ready"}
                </h3>
                <p className="text-xs text-secondary max-w-sm mx-auto">
                  {rerunning
                    ? "Multi-agent graph is actively processing documents, verifying identity, cross-checking policies, and auditing math."
                    : "The adjudication pipeline has finished execution. You can return to the Case Queue to review the case in queue order, or reveal the decision here."}
                </p>
              </div>
              {!rerunning && (
                <div className="pt-2 flex flex-col sm:flex-row items-center justify-center gap-2.5">
                  <Link
                    href="/"
                    className="inline-flex items-center space-x-1.5 px-3.5 py-2 rounded-btn bg-card-inner hover:bg-border text-foreground border border-border text-xs font-semibold transition"
                  >
                    <ArrowLeft className="w-3.5 h-3.5" />
                    <span>Back to Case Queue</span>
                  </Link>
                  <button
                    onClick={async () => {
                      setPipelineAwaitingReveal(false);
                      await loadData();
                    }}
                    className="inline-flex items-center space-x-1.5 px-3.5 py-2 rounded-btn bg-accent hover:bg-accent-hover text-accent-fg text-xs font-semibold transition shadow-xs"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Reveal Results Now</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <>
              {/* Adjudication Decision Card */}
              <div className="bg-card border border-border rounded-card p-4 sm:p-5 space-y-4 shadow-xs transition-colors">
              {/* Top Header */}
            <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-border">
              <div className="flex min-w-0 items-center space-x-2">
                <Sparkles className="w-4 h-4 text-muted" />
                <h2 className="text-xs font-bold text-foreground uppercase tracking-wider">
                  Adjudication Decision
                </h2>
              </div>
              <div className="flex max-w-full flex-wrap items-center justify-end gap-2">
                <span className="inline-flex h-7 max-w-full min-w-0 items-center space-x-1 rounded-full border border-border bg-card-inner px-2.5 text-[10px] font-mono text-secondary shadow-xs">
                  <span className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse inline-block" />
                  <span className="max-w-[10rem] truncate">
                    {(modelSource || (caseData.agent_findings?.explanation_model as string) || "Groq LLM")
                      .replace(/\s*\/\s*ADR-\d+\s*Engine/gi, "")
                      .replace(/Clinical Review Standard/gi, "")
                      .trim()}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={handleGenerateExplanation}
                  disabled={explaining}
                  className="h-7 inline-flex items-center space-x-1 px-2.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border text-[10px] font-medium transition-all duration-180 disabled:opacity-50 shadow-xs active:scale-[0.98]"
                  title="Synthesize fresh clinical narrative via Groq LLM"
                >
                  <RefreshCw className={`w-3 h-3 ${explaining ? "animate-spin text-accent" : "text-muted"}`} />
                  <span>{explaining ? "Updating..." : "Refresh"}</span>
                </button>
                {(caseData.detailed_explanation || caseData.justification) && (
                  <button
                    type="button"
                    onClick={() => handleCopyExplanation(caseData.detailed_explanation || caseData.justification || "")}
                    className="h-7 inline-flex items-center space-x-1 px-2.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border text-[10px] font-mono transition-all duration-180 shadow-xs active:scale-[0.98]"
                    title="Copy explanation markdown"
                  >
                    {copiedExplanation ? (
                      <>
                        <Check className="w-3 h-3 text-status-success" />
                        <span className="text-status-success font-sans">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3 h-3 text-muted" />
                        <span className="font-sans">Copy</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            </div>

            {/* Authoritative Human Reviewer Override Card */}
            {(caseData.latest_human_review || (caseData.agent_findings?.override_action === "OVERRIDE")) && (
              <div className="bg-sky-soft border border-sky/40 rounded-card p-4 space-y-3 shadow-xs animate-in fade-in duration-200">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-2.5 border-b border-sky/20">
                  <div className="flex items-center space-x-2">
                    <div className="w-7 h-7 rounded-full bg-sky-pale text-sky-deep flex items-center justify-center shrink-0 border border-sky/40">
                      <UserCheck className="w-4 h-4" />
                    </div>
                    <div>
                      <div className="flex items-center space-x-2">
                        <span className="text-xs font-bold text-foreground">
                          Decision Overridden by Human Reviewer
                        </span>
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-sky-pale text-sky-deep font-semibold border border-sky/40">
                          {caseData.latest_human_review?.reason_code || (caseData.agent_findings?.override_reason as string) || "SUPERVISORY_EXCEPTION"}
                        </span>
                      </div>
                    </div>
                  </div>
                  <span className="text-[11px] text-muted font-mono">
                    {formatDateTime(caseData.latest_human_review?.created_at || (caseData.agent_findings?.overridden_at as string))} ({formatRelativeTime(caseData.latest_human_review?.created_at || (caseData.agent_findings?.overridden_at as string))})
                  </span>
                </div>

                <div className="flex items-center space-x-2 text-xs text-secondary flex-wrap">
                  <span>Authoritative Reviewer:</span>
                  <span className="font-mono font-bold text-foreground">
                    {caseData.latest_human_review?.reviewer_id || (caseData.agent_findings?.overridden_by as string) || "Operations Reviewer"}
                  </span>
                  {caseData.agent_findings?.original_ai_decision && (
                    <span className="text-muted text-[11px]">
                      &bull; Autonomous AI Recommendation Was: <strong className="font-mono text-foreground">{caseData.agent_findings.original_ai_decision}</strong>
                    </span>
                  )}
                </div>

                <div className="bg-card p-3 rounded-btn border border-border text-xs space-y-1">
                  <div className="text-[10px] uppercase tracking-wider font-semibold text-muted">
                    Reviewer Feedback & Rationale:
                  </div>
                  <p className="text-foreground italic font-sans whitespace-pre-wrap text-xs leading-relaxed">
                    &ldquo;{caseData.latest_human_review?.remarks || (caseData.agent_findings?.override_remarks as string) || "Decision authoritative override applied."}&rdquo;
                  </p>
                </div>
              </div>
            )}

            {/* Unified Hero Verdict Bar */}
            <div className={`p-4 rounded-card border flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs transition-all duration-180 ${
              caseData.current_decision === "APPROVE"
                ? "bg-status-success/10 border-status-success/20 text-foreground"
                : caseData.current_decision === "REJECT"
                ? "bg-rose-blush border-rose-med/30 text-foreground"
                : "bg-status-warning/10 border-status-warning/20 text-foreground"
            }`}>
              <div className="flex items-center space-x-3">
                <div className={`p-2 rounded-card border ${
                  caseData.current_decision === "APPROVE"
                    ? "bg-status-success/15 text-status-success border-status-success/30"
                    : caseData.current_decision === "REJECT"
                    ? "bg-rose-blush text-rose-deep border-rose-med/40"
                    : "bg-status-warning/15 text-status-warning border-status-warning/30"
                }`}>
                  {caseData.current_decision === "APPROVE" && <CheckCircle2 className="w-5 h-5" />}
                  {caseData.current_decision === "REJECT" && <XCircle className="w-5 h-5" />}
                  {caseData.current_decision === "ESCALATE" && <AlertTriangle className="w-5 h-5" />}
                  {!["APPROVE", "REJECT", "ESCALATE"].includes(caseData.current_decision || "") && (
                    <Clock className="w-5 h-5 text-muted" />
                  )}
                </div>
                <div>
                  <div className="flex items-center space-x-2 flex-wrap">
                    <span className={`text-base font-bold tracking-wide ${
                      caseData.current_decision === "APPROVE"
                        ? "text-status-success"
                        : caseData.current_decision === "REJECT"
                        ? "text-rose-deep"
                        : caseData.current_decision === "ESCALATE"
                        ? "text-status-warning"
                        : "text-foreground"
                    }`}>
                      {caseData.current_decision === "APPROVE"
                        ? "APPROVED"
                        : caseData.current_decision === "REJECT"
                        ? "DENIED"
                        : caseData.current_decision === "ESCALATE"
                        ? "NEEDS REVIEW"
                        : "IN PROGRESS"}
                    </span>
                    {caseData.rule_id_matched && (
                      <span className="text-xs text-secondary font-medium">
                        &bull; {formatRuleLabel(caseData.rule_id_matched)}
                      </span>
                    )}
                  </div>
                  {caseData.current_sub_reason && (
                    <div className="text-xs text-secondary font-medium mt-0.5">
                      Trigger: {formatSubReason(caseData.current_sub_reason)}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center space-x-3 self-end sm:self-center">
                {caseData.decision_confidence !== null && caseData.decision_confidence !== undefined && (
                  <div className="text-right">
                    <span className="text-[10px] text-muted uppercase tracking-wider font-semibold block">
                      Confidence
                    </span>
                    <span className="text-base font-bold font-mono text-foreground">
                      {(caseData.decision_confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                )}
              </div>
            </div>

            {explainError && (
              <div className="p-3 bg-rose-blush border border-rose-med/30 rounded-btn text-rose-deep text-xs flex items-center space-x-2">
                <AlertCircle className="w-4 h-4 shrink-0 text-rose-deep" />
                <span>{explainError}</span>
              </div>
            )}

            {/* Clinical Narrative & Specialist Tabs */}
            {caseData.detailed_explanation || caseData.justification ? (
              <ClinicalExplanationViewer
                content={caseData.detailed_explanation || caseData.justification || ""}
                caseId={caseData.case_id}
                ruleIdMatched={caseData.rule_id_matched}
                decisionConfidence={caseData.decision_confidence}
                currentDecision={caseData.current_decision}
                modelSource={modelSource || (caseData.agent_findings?.explanation_model as string)}
                agentFindings={caseData.agent_findings}
                onRefresh={handleGenerateExplanation}
                isRefreshing={explaining}
                selectedCategory={selectedCategory}
                onCategoryChange={setSelectedCategory}
              />
            ) : (
              <div className="p-6 bg-card-inner border border-border rounded-card text-center space-y-3">
                <Sparkles className="w-6 h-6 text-muted mx-auto" />
                <div className="text-xs font-semibold text-foreground">No Clinical Explanation Generated Yet</div>
                <p className="text-[11px] text-muted max-w-md mx-auto">
                  Synthesize an authoritative clinical audit narrative grounded in policy clauses and line-item arithmetic using Groq LLM.
                </p>
                <button
                  type="button"
                  onClick={handleGenerateExplanation}
                  disabled={explaining}
                  className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold transition-all duration-180 shadow-xs active:scale-[0.98] disabled:opacity-50"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>{explaining ? "Synthesizing..." : "Synthesize AI Explanation"}</span>
                </button>
              </div>
            )}

            {/* Missing Documents Alert */}
            {caseData.missing_documents && caseData.missing_documents.length > 0 && (
              <div className="p-3 bg-status-warning/10 border border-status-warning/20 rounded-btn text-xs space-y-1">
                <div className="text-status-warning font-semibold flex items-center space-x-1.5">
                  <FileText className="w-4 h-4 flex-shrink-0" />
                  <span>Missing Mandatory Documents:</span>
                </div>
                <ul className="list-disc list-inside text-secondary text-[11px] pl-5">
                  {caseData.missing_documents.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* Operator Action Console */}
          <div className="bg-card border border-border rounded-card p-4 sm:p-5 space-y-4 shadow-xs">
            <h3 className="text-xs font-bold text-foreground uppercase tracking-wider border-b border-border pb-2">
              Human Reviewer Console
            </h3>

            {actionError && (
              <div className="p-3 rounded-btn bg-status-danger/10 border border-status-danger/20 text-status-danger text-xs">
                {actionError}
              </div>
            )}

            {isAwaitingHuman ? (
              hasLock ? (
                <div className="space-y-2.5">
                  <button
                    onClick={handleConfirmRecommendation}
                    disabled={submittingAction}
                    className="w-full h-9 py-2 px-4 rounded-btn bg-accent-hover hover:bg-accent text-white text-xs font-bold shadow-xs flex items-center justify-center space-x-2 transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
                  >
                    <CheckCircle className="w-4 h-4" />
                    <span>Confirm Recommended Decision</span>
                  </button>

                  <div className="grid grid-cols-2 gap-2.5">
                    <button
                      onClick={() => openOverrideModal()}
                      disabled={submittingAction}
                      className="h-9 py-2 px-3 rounded-btn bg-card-inner/70 hover:bg-card-hover text-secondary border border-border/60 text-xs font-semibold shadow-xs flex items-center justify-center space-x-1.5 transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
                    >
                      <AlertTriangle className="w-3.5 h-3.5 text-status-warning/75" />
                      <span>Override Decision</span>
                    </button>

                    <button
                      onClick={() => setIsRfiModalOpen(true)}
                      disabled={submittingAction}
                      className="h-9 py-2 px-3 rounded-btn bg-card-inner/70 hover:bg-card-hover text-secondary border border-border/60 text-xs font-semibold shadow-xs flex items-center justify-center space-x-1.5 transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
                    >
                      <Send className="w-3.5 h-3.5 text-muted" />
                      <span>Request Info (RFI)</span>
                    </button>
                  </div>
                </div>
              ) : (
                <div className="p-4 bg-card-inner rounded-card border border-border text-center space-y-3">
                  <Lock className="w-5 h-5 text-muted mx-auto" />
                  <p className="text-xs text-secondary">
                    Acquire the review lock for exclusive handling, or directly apply an authoritative reviewer override.
                  </p>
                  <div className="flex flex-col sm:flex-row gap-2 justify-center">
                    <button
                      onClick={handleAcquireLock}
                      className="h-8 px-4 py-1.5 rounded-btn bg-accent-hover hover:bg-accent text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98]"
                    >
                      Acquire Review Lock
                    </button>
                    <button
                      onClick={() => openOverrideModal()}
                      className="h-8 px-3.5 py-1.5 rounded-btn bg-card-inner/70 hover:bg-card-hover text-secondary border border-border/60 text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98] flex items-center justify-center space-x-1.5"
                    >
                      <AlertTriangle className="w-3.5 h-3.5 text-status-warning/75" />
                      <span>Override Decision</span>
                    </button>
                  </div>
                </div>
              )
            ) : (
              <div className="p-4 bg-card-inner rounded-card border border-border space-y-3">
                <div className="flex items-center justify-between text-xs pb-2 border-b border-border">
                  <div className="flex items-center space-x-2">
                    {caseData.current_decision === "APPROVE" ? (
                      <CheckCircle2 className="w-4 h-4 text-status-success shrink-0" />
                    ) : caseData.current_decision === "REJECT" ? (
                      <XCircle className="w-4 h-4 text-status-danger shrink-0" />
                    ) : (
                      <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
                    )}
                    <span className="text-secondary">
                      Current Verdict: <strong className="text-foreground">{caseData.current_decision || "PENDING"}</strong>
                    </span>
                  </div>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-card text-muted border border-border">
                    {caseData.workflow_state}
                  </span>
                </div>

                <p className="text-xs text-secondary leading-relaxed">
                  Adjudicators and supervisors may authoritatively override this {caseData.current_decision?.toLowerCase() || ""} decision at any time. All overrides are immutably signed and chained into the audit ledger.
                </p>

                <div className="pt-2">
                  <button
                    onClick={() => openOverrideModal()}
                    disabled={submittingAction}
                    className="w-full h-9 py-2 px-3 rounded-btn bg-card-inner/70 hover:bg-card-hover text-secondary border border-border/60 text-xs font-semibold shadow-xs flex items-center justify-center space-x-2 transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
                  >
                    <AlertTriangle className="w-3.5 h-3.5 text-status-warning/75" />
                    <span>Override Adjudication Decision</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
        </div>
      </div>

      {/* Decision Override Modal */}
      {isOverrideModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150">
          <div className="bg-card border border-border rounded-card max-w-md w-full p-6 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between pb-2 border-b border-border">
              <h3 className="text-sm font-bold text-foreground flex items-center space-x-2">
                <AlertTriangle className="w-4 h-4 text-status-warning" />
                <span>Adjudication Decision Override</span>
              </h3>
              <button
                type="button"
                onClick={() => setIsOverrideModalOpen(false)}
                className="text-muted hover:text-foreground text-xs p-1 rounded hover:bg-card-inner transition"
              >
                ✕
              </button>
            </div>

            {/* Current Decision Pill */}
            <div className="p-2.5 bg-sky-pale rounded-btn border border-border flex items-center justify-between text-xs">
              <span className="text-secondary font-medium">Current Decision:</span>
              <span className={`px-2.5 py-0.5 rounded-full font-bold font-mono text-[11px] border ${
                caseData.current_decision === "APPROVE"
                  ? "bg-status-success/15 text-status-success border-status-success/30"
                  : caseData.current_decision === "REJECT"
                  ? "bg-rose-blush text-rose-deep border-rose-med/40"
                  : "bg-status-warning/15 text-status-warning border-status-warning/30"
              }`}>
                {caseData.current_decision || "PENDING"}
              </span>
            </div>

            {actionError && (
              <div className="p-2.5 rounded-btn bg-rose-blush border border-rose-med/30 text-rose-deep text-xs">
                {actionError}
              </div>
            )}

            <form onSubmit={handleOverrideSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">
                  New Binding Decision
                </label>
                {/* 1-Click Decision Selector Pills */}
                <div className="grid grid-cols-3 gap-2 mb-2">
                  <button
                    type="button"
                    onClick={() => setOverrideDecision("APPROVE")}
                    className={`py-1.5 px-2 rounded-btn text-xs font-semibold border transition-all duration-180 active:scale-[0.98] flex items-center justify-center space-x-1 ${
                      overrideDecision === "APPROVE"
                        ? "bg-status-success/15 text-status-success border-status-success/40 font-bold shadow-xs"
                        : "bg-card text-muted border-border hover:bg-pink-hover hover:text-pink-deep hover:border-pink-border"
                    }`}
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>APPROVE</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setOverrideDecision("REJECT")}
                    className={`py-1.5 px-2 rounded-btn text-xs font-semibold border transition-all duration-180 active:scale-[0.98] flex items-center justify-center space-x-1 ${
                      overrideDecision === "REJECT"
                        ? "bg-rose-blush text-rose-deep border-rose-med/40 font-bold shadow-xs"
                        : "bg-card text-muted border-border hover:bg-pink-hover hover:text-pink-deep hover:border-pink-border"
                    }`}
                  >
                    <XCircle className="w-3.5 h-3.5" />
                    <span>REJECT</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setOverrideDecision("ESCALATE")}
                    className={`py-1.5 px-2 rounded-btn text-xs font-semibold border transition-all duration-180 active:scale-[0.98] flex items-center justify-center space-x-1 ${
                      overrideDecision === "ESCALATE"
                        ? "bg-status-warning/15 text-status-warning border-status-warning/40 font-bold shadow-xs"
                        : "bg-card text-muted border-border hover:bg-pink-hover hover:text-pink-deep hover:border-pink-border"
                    }`}
                  >
                    <AlertTriangle className="w-3.5 h-3.5" />
                    <span>ESCALATE</span>
                  </button>
                </div>
                <select
                  value={overrideDecision}
                  onChange={(e) => setOverrideDecision(e.target.value)}
                  className="w-full bg-card-inner border border-border rounded-btn px-3 py-2 text-xs text-foreground focus:outline-none focus:border-sky-med focus:ring-1 focus:ring-sky/30 transition-colors"
                >
                  <option value="APPROVE">APPROVE (Approve claim for payment)</option>
                  <option value="REJECT">REJECT (Deny claim eligibility/coverage)</option>
                  <option value="ESCALATE">ESCALATE (Escalate for specialized committee)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-foreground mb-1">
                  Reason Code
                </label>
                <select
                  value={reasonCode}
                  onChange={(e) => setReasonCode(e.target.value)}
                  className="w-full bg-card-inner border border-border rounded-btn px-3 py-2 text-xs text-foreground focus:outline-none focus:border-sky-med focus:ring-1 focus:ring-sky/30 transition-colors"
                >
                  <option value="SUPERVISORY_EXCEPTION">SUPERVISORY_EXCEPTION (Supervisory Override Granted)</option>
                  <option value="POLICY_PREAUTHORIZATION_GRANTED">POLICY_PREAUTHORIZATION_GRANTED (Pre-Authorization Verified)</option>
                  <option value="CLINICAL_APPEAL_HONORED">CLINICAL_APPEAL_HONORED (Medical Necessity / Clinical Appeal)</option>
                  <option value="DOCUMENTATION_LEGIBLE_TO_HUMAN">DOCUMENTATION_LEGIBLE_TO_HUMAN (Document Verified Legible by Human)</option>
                  <option value="SUSPECTED_FRAUD_VERIFIED">SUSPECTED_FRAUD_VERIFIED (Suspected Fraud Confirmed)</option>
                  <option value="BILLING_DISPUTE_SETTLED">BILLING_DISPUTE_SETTLED (Fee Schedule / Billing Dispute Settled)</option>
                  <option value="OTHER">OTHER (Other Justified Supervisory Action)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-foreground mb-1">
                  Reviewer Feedback & Rationale * (Min 5 characters)
                </label>
                <textarea
                  rows={3}
                  value={remarks}
                  onChange={(e) => setRemarks(e.target.value)}
                  placeholder="Provide clinical or policy rationale and detailed feedback explaining why the autonomous agent decision is being overridden..."
                  className="w-full bg-card-inner border border-border rounded-btn p-3 text-xs text-foreground placeholder-muted focus:outline-none focus:border-sky-med focus:ring-1 focus:ring-sky/30 transition-colors"
                  required
                />
              </div>

              <div className="flex items-center justify-end space-x-2.5 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setIsOverrideModalOpen(false)}
                  className="px-3.5 py-1.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border text-xs font-medium transition-all duration-180 active:scale-[0.98]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingAction}
                  className="px-4 py-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
                >
                  {submittingAction ? "Submitting..." : "Apply Authoritative Override"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Request Information (RFI) Modal */}
      {isRfiModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-150">
          <div className="bg-card border border-border rounded-card max-w-md w-full p-6 space-y-4 shadow-2xl">
            <h3 className="text-sm font-bold text-foreground flex items-center space-x-2">
              <Send className="w-4 h-4 text-sky-deep" />
              <span>Request Information (RFI)</span>
            </h3>

            <form onSubmit={handleRfiSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-foreground mb-1">
                  Missing Document Details & Instructions
                </label>
                <textarea
                  rows={3}
                  value={rfiRemarks}
                  onChange={(e) => setRfiRemarks(e.target.value)}
                  placeholder="Specify required identity proof or medical bill amendments..."
                  className="w-full bg-card-inner border border-border rounded-btn p-3 text-xs text-foreground placeholder-muted focus:outline-none focus:border-sky-med focus:ring-1 focus:ring-sky/30 transition-colors"
                />
              </div>

              <div className="flex items-center justify-end space-x-2.5 pt-3 border-t border-border">
                <button
                  type="button"
                  onClick={() => setIsRfiModalOpen(false)}
                  className="px-3.5 py-1.5 rounded-btn bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep border border-border hover:border-pink-border text-xs font-medium transition-all duration-180 active:scale-[0.98]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingAction}
                  className="px-4 py-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98] disabled:opacity-50"
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
