"use client";

import { useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock,
  DollarSign,
  FileText,
  HelpCircle,
  Info,
  Loader2,
  Search,
  ShieldAlert,
  ShieldCheck,
  Upload,
  UserCheck,
} from "lucide-react";
import {
  ClaimerStatusResponse,
  getClaimerStatus,
  submitIntake,
} from "@/lib/api";
import { formatDate, formatTime } from "@/lib/date";
import { useAuth } from "@/lib/auth";

export default function ClaimerPortalPage() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<"submit" | "track">("submit");

  // Track State
  const [searchCaseId, setSearchCaseId] = useState("");
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [claimStatus, setClaimStatus] = useState<ClaimerStatusResponse | null>(null);
  const [trackError, setTrackError] = useState<string | null>(null);

  // Submit State
  const [files, setFiles] = useState<File[]>([]);
  const [claimType, setClaimType] = useState("STANDARD_REIMBURSEMENT_CLAIM");
  const [patientName, setPatientName] = useState("");
  const [memberId, setMemberId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitSuccessCaseId, setSubmitSuccessCaseId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
    }
  };

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmitClaim = async (e: React.FormEvent) => {
    e.preventDefault();
    if (files.length === 0) {
      setSubmitError("Please upload at least one document (claim form or medical bill).");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const formData = new FormData();
      for (const f of files) {
        formData.append("files", f);
      }
      formData.append("claim_type", claimType);
      formData.append(
        "metadata",
        JSON.stringify({ patient_name: patientName, member_id: memberId })
      );
      const res = (await submitIntake(formData)) as { case_id: string };
      setSubmitSuccessCaseId(res.case_id);
      setFiles([]);
      setPatientName("");
      setMemberId("");
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "Failed to submit claim.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleTrackClaim = async (caseIdToTrack?: string) => {
    const targetId = (caseIdToTrack || searchCaseId).trim();
    if (!targetId) return;
    setLoadingStatus(true);
    setTrackError(null);
    try {
      const data = await getClaimerStatus(targetId);
      setClaimStatus(data);
      setSearchCaseId(targetId);
    } catch (err: unknown) {
      setTrackError(err instanceof Error ? err.message : `Claim "${targetId}" not found.`);
      setClaimStatus(null);
    } finally {
      setLoadingStatus(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 w-full space-y-6">
      {user && user.role !== "CLAIMER" && (
        <div className="bg-card border border-border rounded-card p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-secondary shadow-xs">
          <div className="flex items-center space-x-2">
            <Info className="w-4 h-4 text-accent shrink-0" />
            <span>
              You are currently signed in as <strong>{user.username}</strong> ({user.role}). You are previewing the self-service Claimer intake and tracking portal.
            </span>
          </div>
          <Link
            href={user.role === "ADMINISTRATOR" ? "/admin" : "/"}
            className="text-accent hover:underline font-medium shrink-0"
          >
            Switch to {user.role === "ADMINISTRATOR" ? "Admin Console" : "Review Queue"} &rarr;
          </Link>
        </div>
      )}

      {/* Hero Header */}
      <div className="bg-card border border-border rounded-card p-5 sm:p-6 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2.5 mb-1.5 flex-wrap">
              <h1 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight">Claimer Portal</h1>
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-sky-soft text-accent border border-sky/30 shadow-xs">
                Patient & Provider Intake
              </span>
            </div>
            <p className="text-secondary text-xs sm:text-sm mt-0.5 max-w-2xl">
              Submit medical receipts, prescriptions, and health insurance claims with automated verification and real-time status tracking.
            </p>
          </div>
          <div className="flex items-center space-x-1.5 bg-card-inner p-1 rounded-btn border border-border self-start md:self-auto shadow-xs">
            <button
              onClick={() => setActiveTab("submit")}
              className={`h-8 px-3.5 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 ${
                activeTab === "submit"
                  ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/40 shadow-xs"
                  : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
              }`}
            >
              Submit Claim
            </button>
            <button
              onClick={() => setActiveTab("track")}
              className={`h-8 px-3.5 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 ${
                activeTab === "track"
                  ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/40 shadow-xs"
                  : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
              }`}
            >
              Track Status
            </button>
          </div>
        </div>
      </div>

      {/* Tab 1: Submit Claim */}
      {activeTab === "submit" && (
        <div className="bg-card border border-border rounded-card p-5 sm:p-6 shadow-xs">
          {submitSuccessCaseId ? (
            <div className="text-center py-8 px-4">
              <div className="w-14 h-14 bg-status-success/10 text-status-success border border-status-success/20 rounded-full flex items-center justify-center mx-auto mb-3">
                <CheckCircle2 className="w-8 h-8" />
              </div>
              <h2 className="text-xl font-bold text-foreground mb-1.5">Claim Successfully Submitted!</h2>
              <p className="text-secondary max-w-md mx-auto mb-5 text-xs sm:text-sm leading-relaxed">
                Your claim has been securely received by MediShield. Our multi-agent adjudication engine is currently analyzing your documents.
              </p>
              <div className="inline-block bg-card-inner border border-border rounded-btn px-5 py-2 font-mono text-sky-deep font-bold text-base mb-6">
                Tracking ID: {submitSuccessCaseId}
              </div>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-2.5">
                <button
                  onClick={() => {
                    setActiveTab("track");
                    handleTrackClaim(submitSuccessCaseId);
                  }}
                  className="w-full sm:w-auto h-9 px-5 py-2 bg-[#397FA9] hover:bg-[#2C6688] text-white font-semibold rounded-btn transition-colors flex items-center justify-center space-x-2 text-xs shadow-xs"
                >
                  <span>Track This Claim</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => {
                    setSubmitSuccessCaseId(null);
                  }}
                  className="w-full sm:w-auto h-9 px-5 py-2 bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border font-medium rounded-btn transition-colors border border-border text-xs shadow-xs"
                >
                  Submit Another Claim
                </button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmitClaim} className="space-y-4">
              {submitError && (
                <div className="p-3 bg-rose-blush border border-rose-med/30 text-rose-deep rounded-btn text-xs flex items-start space-x-2">
                  <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>{submitError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-foreground mb-1">
                    Patient Full Name
                  </label>
                  <input
                    type="text"
                    required
                    value={patientName}
                    onChange={(e) => setPatientName(e.target.value)}
                    placeholder="e.g. Jessica Jackson"
                    className="w-full h-9 bg-card border border-border rounded-btn px-3 py-1.5 text-foreground placeholder:text-muted focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 text-xs shadow-xs transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-foreground mb-1">
                    Member ID / Card Number (Optional)
                  </label>
                  <input
                    type="text"
                    value={memberId}
                    onChange={(e) => setMemberId(e.target.value)}
                    placeholder="e.g. MEM-12345 or PT_99733"
                    className="w-full h-9 bg-card border border-border rounded-btn px-3 py-1.5 text-foreground placeholder:text-muted focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 text-xs font-mono shadow-xs transition-colors"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-foreground mb-1">
                  Claim Request Type
                </label>
                <select
                  value={claimType}
                  onChange={(e) => setClaimType(e.target.value)}
                  className="w-full h-9 bg-card border border-border rounded-btn px-3 py-1.5 text-foreground focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 text-xs shadow-xs"
                >
                  <option value="STANDARD_REIMBURSEMENT_CLAIM">Standard Medical Reimbursement Claim</option>
                  <option value="HOSPITAL_CASHLESS_PREAUTH">Inpatient Pre-Authorization / Discharge</option>
                  <option value="POLICY_AMENDMENT_REQUEST">Policy Rider / Amendment Submission</option>
                </select>
              </div>

              {/* Upload Dropzone */}
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">
                  Upload Supporting Documents
                </label>
                <div className="border-2 border-dashed border-border hover:border-sky/60 rounded-card p-6 text-center bg-card-inner transition-colors">
                  <Upload className="w-8 h-8 text-accent mx-auto mb-2" />
                  <p className="text-xs text-foreground font-medium mb-0.5">
                    Drag and drop your claim bill, prescription, and photo ID
                  </p>
                  <p className="text-[11px] text-muted mb-3">
                    Supported formats: PDF, PNG, JPG (up to 25MB each)
                  </p>
                  <label className="h-8 inline-flex items-center px-4 py-1.5 bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border border border-border rounded-btn text-xs font-semibold cursor-pointer transition shadow-xs">
                    <span>Browse Files</span>
                    <input
                      type="file"
                      multiple
                      accept="application/pdf,image/png,image/jpeg"
                      onChange={handleFileChange}
                      className="hidden"
                    />
                  </label>
                </div>

                {/* Uploaded Files Roster */}
                {files.length > 0 && (
                  <div className="mt-3 space-y-1.5">
                    <span className="text-[11px] font-semibold text-secondary">Attached Documents ({files.length}):</span>
                    <div className="divide-y divide-border bg-card-inner rounded-btn border border-border overflow-hidden">
                      {files.map((f, i) => (
                        <div key={i} className="flex items-center justify-between p-2.5 text-xs">
                          <div className="flex items-center space-x-2 truncate">
                            <FileText className="w-3.5 h-3.5 text-sky-deep flex-shrink-0" />
                            <span className="text-foreground truncate font-mono text-[11px]">{f.name}</span>
                            <span className="text-[10px] text-muted font-mono">
                              ({(f.size / 1024).toFixed(1)} KB)
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeFile(i)}
                            className="text-[11px] text-danger hover:underline font-medium ml-3"
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="pt-3 border-t border-border flex justify-end">
                <button
                  type="submit"
                  disabled={submitting || files.length === 0}
                  className="h-9 px-4 py-2 bg-[#397FA9] hover:bg-[#2C6688] disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded-btn transition-colors flex items-center space-x-1.5 text-xs shadow-xs"
                >
                  {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>{submitting ? "Submitting Claim..." : "Submit Claim for Adjudication"}</span>
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Tab 2: Track Status */}
      {activeTab === "track" && (
        <div className="space-y-4">
          {/* Search Box */}
          <div className="bg-card border border-border rounded-card p-4 sm:p-5 shadow-xs">
            <label className="block text-xs font-semibold text-foreground uppercase tracking-wider mb-2">
              Enter Claim Tracking ID
            </label>
            <div className="flex flex-col sm:flex-row gap-2.5">
              <div className="relative flex-1">
                <Search className="w-4 h-4 text-muted absolute left-3 top-2.5" />
                <input
                  type="text"
                  value={searchCaseId}
                  onChange={(e) => setSearchCaseId(e.target.value)}
                  placeholder="e.g. CASE-20261004-902144 or CASE-20261004-811092"
                  className="w-full h-9 bg-card border border-border rounded-btn pl-9 pr-3 py-1.5 text-foreground placeholder:text-muted focus:outline-none focus:border-sky-med focus:ring-2 focus:ring-sky/30 text-xs font-mono shadow-xs transition-colors"
                />
              </div>
              <button
                onClick={() => handleTrackClaim()}
                disabled={loadingStatus || !searchCaseId.trim()}
                className="h-9 px-4 py-2 bg-[#397FA9] hover:bg-[#2C6688] disabled:opacity-50 text-white font-semibold rounded-btn transition-colors flex items-center justify-center space-x-1.5 text-xs shadow-xs"
              >
                {loadingStatus && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                <span>{loadingStatus ? "Searching..." : "Track Status"}</span>
              </button>
            </div>

            {/* Quick Demo Cases */}
            <div className="mt-2.5 flex items-center space-x-2 text-[11px] text-muted">
              <span>Try sample cases:</span>
              <button
                type="button"
                onClick={() => handleTrackClaim("CASE-20261004-902144")}
                className="text-accent hover:underline font-mono"
              >
                CASE-20261004-902144
              </button>
              <span>•</span>
              <button
                type="button"
                onClick={() => handleTrackClaim("CASE-20261004-811092")}
                className="text-accent hover:underline font-mono"
              >
                CASE-20261004-811092
              </button>
            </div>
          </div>

          {trackError && (
            <div className="p-3.5 bg-status-danger/10 border border-status-danger/20 text-status-danger rounded-card text-xs flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{trackError}</span>
            </div>
          )}

          {/* Claim Detail View */}
          {claimStatus && (
            <div className="space-y-4">
              {/* Status Header Banner */}
              <div className={`p-5 rounded-card border ${
                claimStatus.is_complete && claimStatus.status_label.includes("Approved")
                  ? "bg-status-success/10 border-status-success/20"
                  : claimStatus.is_complete && claimStatus.status_label.includes("Denied")
                  ? "bg-rose-blush border-rose-med/30"
                  : claimStatus.workflow_state === "AWAITING_INFO"
                  ? "bg-status-warning/10 border-status-warning/20"
                  : "bg-accent/10 border-accent/20"
              }`}>
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] uppercase tracking-wider font-semibold text-secondary">
                      Tracking ID: <span className="font-mono text-foreground font-bold">{claimStatus.case_id}</span>
                    </div>
                    <div className="text-xl font-bold text-foreground mt-1 flex items-center space-x-2">
                      {claimStatus.is_complete && claimStatus.status_label.includes("Approved") && (
                        <CheckCircle2 className="w-5 h-5 text-status-success" />
                      )}
                      {claimStatus.is_complete && claimStatus.status_label.includes("Denied") && (
                        <ShieldAlert className="w-5 h-5 text-rose-deep" />
                      )}
                      {!claimStatus.is_complete && (
                        <Clock className="w-5 h-5 text-accent animate-pulse" />
                      )}
                      <span>{claimStatus.status_label}</span>
                    </div>
                  </div>
                  <div className="text-[11px] text-muted sm:text-right font-mono">
                    <div>Submitted: {formatDate(claimStatus.created_at)}</div>
                    <div>Updated: {formatTime(claimStatus.updated_at)}</div>
                  </div>
                </div>

                {/* Progress Stepper (5 Stages) */}
                <div className="mt-4 pt-4 border-t border-border grid grid-cols-2 sm:grid-cols-5 gap-2 text-[11px]">
                  <div className="flex items-center space-x-1.5 text-status-success">
                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                    <span className="font-semibold">1. Received</span>
                  </div>
                  <div className={`flex items-center space-x-1.5 ${
                    ["CLASSIFYING", "ANALYZING", "AWAITING_INFO", "AWAITING_HUMAN", "COMPLETED"].includes(claimStatus.workflow_state)
                      ? "text-status-success"
                      : "text-muted"
                  }`}>
                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                    <span className="font-semibold">2. Validated</span>
                  </div>
                  <div className={`flex items-center space-x-1.5 ${
                    ["AWAITING_HUMAN", "COMPLETED"].includes(claimStatus.workflow_state)
                      ? "text-status-success"
                      : "text-muted"
                  }`}>
                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                    <span className="font-semibold">3. Policy Checked</span>
                  </div>
                  <div className={`flex items-center space-x-1.5 ${
                    claimStatus.workflow_state === "COMPLETED"
                      ? "text-status-success"
                      : claimStatus.workflow_state === "AWAITING_HUMAN"
                      ? "text-accent"
                      : "text-muted"
                  }`}>
                    {claimStatus.workflow_state === "COMPLETED" ? (
                      <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                    ) : claimStatus.workflow_state === "AWAITING_HUMAN" ? (
                      <Clock className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />
                    ) : (
                      <Clock className="w-3.5 h-3.5 flex-shrink-0" />
                    )}
                    <span className="font-semibold">4. Specialist Review</span>
                  </div>
                  <div className={`flex items-center space-x-1.5 ${
                    claimStatus.is_complete
                      ? "text-status-success"
                      : "text-muted"
                  }`}>
                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />
                    <span className="font-semibold">5. Final Determination</span>
                  </div>
                </div>
              </div>

              {/* Informative Review-in-Progress Card for Awaiting Human */}
              {!claimStatus.is_complete && claimStatus.workflow_state === "AWAITING_HUMAN" && (
                <div className="bg-card border border-border rounded-card p-5 shadow-xs relative overflow-hidden">
                  <div className="flex items-start space-x-3.5">
                    <div className="p-2.5 bg-accent/10 border border-accent/20 rounded-btn shrink-0 mt-0.5">
                      <Clock className="w-5 h-5 text-accent" />
                    </div>
                    <div>
                      <div className="flex items-center space-x-2">
                        <h3 className="text-sm font-bold text-foreground">Clinical & Policy Specialist Review Active</h3>
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20 font-mono font-semibold">
                          In Progress
                        </span>
                      </div>
                      <p className="text-xs text-secondary mt-1 leading-relaxed">
                        Your claim has passed automated intake and preliminary document checks. A licensed claims specialist is reviewing policy terms and adjudicating the final covered benefits.
                      </p>
                      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-secondary bg-card-inner p-3 rounded-btn border border-border">
                        <div className="flex items-center space-x-1.5">
                          <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0" />
                          <span>Extraction & OCR: <strong className="text-foreground">Complete</strong></span>
                        </div>
                        <div className="flex items-center space-x-1.5">
                          <CheckCircle2 className="w-3.5 h-3.5 text-status-success shrink-0" />
                          <span>Identity & Coverage: <strong className="text-foreground">Verified</strong></span>
                        </div>
                        <div className="flex items-center space-x-1.5">
                          <Clock className="w-3.5 h-3.5 text-accent shrink-0" />
                          <span>Adjuster Review: <strong className="text-foreground">Under Evaluation</strong></span>
                        </div>
                        <div className="flex items-center space-x-1.5">
                          <FileText className="w-3.5 h-3.5 text-muted shrink-0" />
                          <span>Settlement Notice: <strong className="text-foreground">Upon Completion</strong></span>
                        </div>
                      </div>
                      <p className="text-[11px] text-muted mt-2">
                        In accordance with healthcare claims standards and MediShield governance, formal decision notices and payment breakdowns are finalized exclusively upon completed human review.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Financial Breakdown (If Adjudicated) */}
              {claimStatus.adjudication && (
                <div className="bg-card border border-border rounded-card p-5 shadow-xs space-y-4">
                  <h3 className="text-sm font-bold text-foreground flex items-center space-x-1.5">
                    <DollarSign className="w-4 h-4 text-accent" />
                    <span>Coverage & Payment Breakdown</span>
                  </h3>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="bg-card-inner p-3 rounded-btn border border-border">
                      <div className="text-[11px] text-muted">Total Billed</div>
                      <div className="text-base font-mono font-bold text-foreground mt-0.5 tabular-nums">
                        ${claimStatus.adjudication.total_billed?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-card-inner p-3 rounded-btn border border-status-success/30">
                      <div className="text-[11px] text-status-success font-medium">Covered by Policy</div>
                      <div className="text-base font-mono font-bold text-status-success mt-0.5 tabular-nums">
                        ${claimStatus.adjudication.covered_amount?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-card-inner p-3 rounded-btn border border-border">
                      <div className="text-[11px] text-muted">Deductible Applied</div>
                      <div className="text-base font-mono font-bold text-foreground mt-0.5 tabular-nums">
                        ${claimStatus.adjudication.deductible_applied?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-card-inner p-3 rounded-btn border border-border">
                      <div className="text-[11px] text-muted">Member Responsibility</div>
                      <div className="text-base font-mono font-bold text-status-warning mt-0.5 tabular-nums">
                        ${claimStatus.adjudication.member_responsibility?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                  </div>

                  {/* Decision Letter */}
                  {claimStatus.adjudication.explanation_letter && (
                    <div className="pt-3 border-t border-border">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted mb-1.5">
                        Official Decision Notice
                      </div>
                      <div className="bg-card-inner border border-border rounded-btn p-3 text-secondary text-xs leading-relaxed whitespace-pre-wrap font-sans">
                        {claimStatus.adjudication.explanation_letter}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Submitted Documents Roster */}
              <div className="bg-card border border-border rounded-card p-5 shadow-xs">
                <h3 className="text-sm font-bold text-foreground mb-3 flex items-center space-x-1.5">
                  <FileText className="w-4 h-4 text-accent" />
                  <span>Submitted File Evidence ({claimStatus.submitted_documents.length})</span>
                </h3>
                <div className="divide-y divide-border border border-border rounded-btn overflow-hidden bg-card-inner">
                  {claimStatus.submitted_documents.map((doc, idx) => (
                    <div key={idx} className="p-3 flex items-center justify-between text-xs">
                      <div className="flex items-center space-x-2.5">
                        <FileText className="w-3.5 h-3.5 text-accent flex-shrink-0" />
                        <div>
                          <div className="text-foreground font-medium font-mono text-[11px]">{doc.file_name}</div>
                          <div className="text-[10px] text-muted">
                            Type: {doc.doc_type} &bull; {(doc.size_bytes / 1024).toFixed(1)} KB
                          </div>
                        </div>
                      </div>
                      <span className="text-[10px] text-muted font-mono">
                        {formatDate(doc.created_at)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
