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
    } catch (err: any) {

      setSubmitError(err.message || "Failed to submit claim.");
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
    } catch (err: any) {
      setTrackError(err.message || `Claim "${targetId}" not found.`);
      setClaimStatus(null);
    } finally {
      setLoadingStatus(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full">
      {user && user.role !== "CLAIMER" && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-slate-300">
          <div className="flex items-center space-x-2">
            <Info className="w-4 h-4 text-cyan-400 shrink-0" />
            <span>
              You are currently signed in as <strong>{user.username}</strong> ({user.role}). You are previewing the self-service Claimer intake and tracking portal.
            </span>
          </div>
          <Link
            href={user.role === "ADMINISTRATOR" ? "/admin" : "/"}
            className="text-cyan-400 hover:text-cyan-300 underline font-medium shrink-0"
          >
            Switch to {user.role === "ADMINISTRATOR" ? "Admin Console" : "Review Queue"} &rarr;
          </Link>
        </div>
      )}

      {/* Hero Header */}
      <div className="bg-gradient-to-r from-cyan-950/60 via-slate-900 to-slate-900 border border-cyan-800/40 rounded-xl p-6 mb-8 shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2 text-cyan-400 text-xs font-semibold uppercase tracking-wider mb-1">
              <UserCheck className="w-4 h-4" />
              <span>Patient & Member Self-Service</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
              MediShield Claim Portal
            </h1>
            <p className="text-slate-400 text-sm mt-1 max-w-2xl">
              Submit your medical receipts, prescriptions, and health insurance claims with automated verification and real-time status tracking.
            </p>
          </div>
          <div className="flex items-center space-x-2 bg-slate-950/80 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setActiveTab("submit")}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === "submit"
                  ? "bg-cyan-500 text-slate-950 font-bold shadow-md"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Submit Claim
            </button>
            <button
              onClick={() => setActiveTab("track")}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === "track"
                  ? "bg-cyan-500 text-slate-950 font-bold shadow-md"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Track Status
            </button>
          </div>
        </div>
      </div>

      {/* Tab 1: Submit Claim */}
      {activeTab === "submit" && (
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-6 sm:p-8 shadow-2xl">
          {submitSuccessCaseId ? (
            <div className="text-center py-10 px-4">
              <div className="w-16 h-16 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-10 h-10" />
              </div>
              <h2 className="text-2xl font-bold text-white mb-2">Claim Successfully Submitted!</h2>
              <p className="text-slate-400 max-w-md mx-auto mb-6 text-sm">
                Your claim has been securely received by MediShield. Our multi-agent adjudication engine is currently analyzing your documents.
              </p>
              <div className="inline-block bg-slate-950 border border-slate-800 rounded-lg px-6 py-3 font-mono text-cyan-400 font-bold text-lg mb-8">
                Tracking ID: {submitSuccessCaseId}
              </div>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <button
                  onClick={() => {
                    setActiveTab("track");
                    handleTrackClaim(submitSuccessCaseId);
                  }}
                  className="w-full sm:w-auto px-6 py-2.5 bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold rounded-lg transition-colors flex items-center justify-center space-x-2"
                >
                  <span>Track This Claim</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
                <button
                  onClick={() => {
                    setSubmitSuccessCaseId(null);
                  }}
                  className="w-full sm:w-auto px-6 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium rounded-lg transition-colors"
                >
                  Submit Another Claim
                </button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmitClaim} className="space-y-6">
              {submitError && (
                <div className="p-4 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-lg text-sm flex items-start space-x-3">
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <span>{submitError}</span>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
                    Patient Full Name
                  </label>
                  <input
                    type="text"
                    required
                    value={patientName}
                    onChange={(e) => setPatientName(e.target.value)}
                    placeholder="e.g. Jessica Jackson"
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 text-sm"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
                    Member ID / Card Number (Optional)
                  </label>
                  <input
                    type="text"
                    value={memberId}
                    onChange={(e) => setMemberId(e.target.value)}
                    placeholder="e.g. MEM-12345 or PT_99733"
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 text-sm"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
                  Claim Request Type
                </label>
                <select
                  value={claimType}
                  onChange={(e) => setClaimType(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:border-cyan-500 text-sm"
                >
                  <option value="STANDARD_REIMBURSEMENT_CLAIM">Standard Medical Reimbursement Claim</option>
                  <option value="HOSPITAL_CASHLESS_PREAUTH">Inpatient Pre-Authorization / Discharge</option>
                  <option value="POLICY_AMENDMENT_REQUEST">Policy Rider / Amendment Submission</option>
                </select>
              </div>

              {/* Upload Dropzone */}
              <div>
                <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
                  Upload Supporting Documents
                </label>
                <div className="border-2 border-dashed border-slate-700 hover:border-cyan-500/60 rounded-xl p-8 text-center bg-slate-950/60 transition-colors">
                  <Upload className="w-10 h-10 text-cyan-400 mx-auto mb-3" />
                  <p className="text-sm text-slate-200 font-medium mb-1">
                    Drag and drop your claim bill, prescription, and photo ID
                  </p>
                  <p className="text-xs text-slate-500 mb-4">
                    Supported formats: PDF, PNG, JPG (up to 25MB each)
                  </p>
                  <label className="inline-block px-5 py-2 bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-600 rounded-lg text-sm font-semibold cursor-pointer transition-colors">
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
                  <div className="mt-4 space-y-2">
                    <span className="text-xs font-semibold text-slate-400">Attached Documents ({files.length}):</span>
                    <div className="divide-y divide-slate-800 bg-slate-950 rounded-lg border border-slate-800 overflow-hidden">
                      {files.map((f, i) => (
                        <div key={i} className="flex items-center justify-between p-3 text-sm">
                          <div className="flex items-center space-x-2 truncate">
                            <FileText className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                            <span className="text-slate-300 truncate">{f.name}</span>
                            <span className="text-xs text-slate-500">
                              ({(f.size / 1024).toFixed(1)} KB)
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => removeFile(i)}
                            className="text-xs text-rose-400 hover:text-rose-300 font-medium ml-4"
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="pt-4 border-t border-slate-800 flex justify-end">
                <button
                  type="submit"
                  disabled={submitting || files.length === 0}
                  className="px-6 py-3 bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 disabled:cursor-not-allowed text-slate-950 font-bold rounded-lg transition-colors flex items-center space-x-2 text-sm"
                >
                  {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                  <span>{submitting ? "Submitting Claim..." : "Submit Claim for Adjudication"}</span>
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Tab 2: Track Status */}
      {activeTab === "track" && (
        <div className="space-y-6">
          {/* Search Box */}
          <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-6 shadow-xl">
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Enter Claim Tracking ID
            </label>
            <div className="flex flex-col sm:flex-row gap-3">
              <div className="relative flex-1">
                <Search className="w-5 h-5 text-slate-500 absolute left-3 top-3" />
                <input
                  type="text"
                  value={searchCaseId}
                  onChange={(e) => setSearchCaseId(e.target.value)}
                  placeholder="e.g. CASE-20261004-902144 or CASE-20261004-811092"
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg pl-10 pr-4 py-2.5 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 text-sm font-mono"
                />
              </div>
              <button
                onClick={() => handleTrackClaim()}
                disabled={loadingStatus || !searchCaseId.trim()}
                className="px-6 py-2.5 bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-slate-950 font-bold rounded-lg transition-colors flex items-center justify-center space-x-2 text-sm"
              >
                {loadingStatus && <Loader2 className="w-4 h-4 animate-spin" />}
                <span>{loadingStatus ? "Searching..." : "Track Status"}</span>
              </button>
            </div>

            {/* Quick Demo Cases */}
            <div className="mt-3 flex items-center space-x-2 text-xs text-slate-400">
              <span>Try sample cases:</span>
              <button
                type="button"
                onClick={() => handleTrackClaim("CASE-20261004-902144")}
                className="text-cyan-400 hover:underline font-mono"
              >
                CASE-20261004-902144
              </button>
              <span>•</span>
              <button
                type="button"
                onClick={() => handleTrackClaim("CASE-20261004-811092")}
                className="text-cyan-400 hover:underline font-mono"
              >
                CASE-20261004-811092
              </button>
            </div>
          </div>

          {trackError && (
            <div className="p-4 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-xl text-sm flex items-center space-x-3">
              <AlertCircle className="w-5 h-5 flex-shrink-0" />
              <span>{trackError}</span>
            </div>
          )}

          {/* Claim Detail View */}
          {claimStatus && (
            <div className="space-y-6">
              {/* Status Header Banner */}
              <div className={`p-6 rounded-xl border ${
                claimStatus.is_complete && claimStatus.status_label.includes("Approved")
                  ? "bg-emerald-950/40 border-emerald-500/40"
                  : claimStatus.is_complete && claimStatus.status_label.includes("Denied")
                  ? "bg-rose-950/40 border-rose-500/40"
                  : claimStatus.workflow_state === "AWAITING_INFO"
                  ? "bg-amber-950/40 border-amber-500/40"
                  : "bg-cyan-950/40 border-cyan-500/40"
              }`}>
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div>
                    <div className="text-xs uppercase tracking-wider font-semibold text-slate-400">
                      Tracking ID: <span className="font-mono text-white">{claimStatus.case_id}</span>
                    </div>
                    <div className="text-2xl font-extrabold text-white mt-1 flex items-center space-x-2">
                      {claimStatus.is_complete && claimStatus.status_label.includes("Approved") && (
                        <CheckCircle2 className="w-6 h-6 text-emerald-400" />
                      )}
                      {claimStatus.is_complete && claimStatus.status_label.includes("Denied") && (
                        <ShieldAlert className="w-6 h-6 text-rose-400" />
                      )}
                      {!claimStatus.is_complete && (
                        <Clock className="w-6 h-6 text-cyan-400 animate-pulse" />
                      )}
                      <span>{claimStatus.status_label}</span>
                    </div>
                  </div>
                  <div className="text-xs text-slate-400 sm:text-right">
                    <div>Submitted: {new Date(claimStatus.created_at).toLocaleDateString()}</div>
                    <div>Last Updated: {new Date(claimStatus.updated_at).toLocaleTimeString()}</div>
                  </div>
                </div>

                {/* Progress Stepper (5 Stages) */}
                <div className="mt-6 pt-6 border-t border-slate-800/80 grid grid-cols-2 sm:grid-cols-5 gap-3 text-xs">
                  <div className="flex items-center space-x-2 text-emerald-400">
                    <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                    <span className="font-semibold">1. Received</span>
                  </div>
                  <div className={`flex items-center space-x-2 ${
                    ["CLASSIFYING", "ANALYZING", "AWAITING_INFO", "AWAITING_HUMAN", "COMPLETED"].includes(claimStatus.workflow_state)
                      ? "text-emerald-400"
                      : "text-slate-500"
                  }`}>
                    <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                    <span className="font-semibold">2. Validated</span>
                  </div>
                  <div className={`flex items-center space-x-2 ${
                    ["AWAITING_HUMAN", "COMPLETED"].includes(claimStatus.workflow_state)
                      ? "text-emerald-400"
                      : "text-slate-500"
                  }`}>
                    <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                    <span className="font-semibold">3. Policy Checked</span>
                  </div>
                  <div className={`flex items-center space-x-2 ${
                    claimStatus.workflow_state === "COMPLETED"
                      ? "text-emerald-400"
                      : claimStatus.workflow_state === "AWAITING_HUMAN"
                      ? "text-cyan-400"
                      : "text-slate-500"
                  }`}>
                    {claimStatus.workflow_state === "COMPLETED" ? (
                      <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                    ) : claimStatus.workflow_state === "AWAITING_HUMAN" ? (
                      <Clock className="w-4 h-4 flex-shrink-0 animate-spin" />
                    ) : (
                      <Clock className="w-4 h-4 flex-shrink-0" />
                    )}
                    <span className="font-semibold">4. Specialist Review</span>
                  </div>
                  <div className={`flex items-center space-x-2 ${
                    claimStatus.is_complete
                      ? "text-emerald-400"
                      : "text-slate-500"
                  }`}>
                    <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                    <span className="font-semibold">5. Final Determination</span>
                  </div>
                </div>
              </div>

              {/* Informative Review-in-Progress Card for Awaiting Human */}
              {!claimStatus.is_complete && claimStatus.workflow_state === "AWAITING_HUMAN" && (
                <div className="bg-slate-900 border border-cyan-800/60 rounded-xl p-6 shadow-xl relative overflow-hidden">
                  <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-cyan-500 to-blue-500 animate-pulse" />
                  <div className="flex items-start space-x-4">
                    <div className="p-3 bg-cyan-950/60 border border-cyan-800/80 rounded-xl shrink-0 mt-0.5">
                      <Clock className="w-6 h-6 text-cyan-400" />
                    </div>
                    <div>
                      <div className="flex items-center space-x-2">
                        <h3 className="text-base font-bold text-white">Clinical & Policy Specialist Review Active</h3>
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800 font-mono">
                          In Progress
                        </span>
                      </div>
                      <p className="text-sm text-slate-300 mt-1 leading-relaxed">
                        Your claim has passed automated intake and preliminary document checks. A licensed claims specialist is reviewing policy terms and adjudicating the final covered benefits.
                      </p>
                      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs text-slate-400 bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                        <div className="flex items-center space-x-2">
                          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                          <span>Document Extraction & OCR: <strong>Complete</strong></span>
                        </div>
                        <div className="flex items-center space-x-2">
                          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                          <span>Member Identity & Coverage: <strong>Verified</strong></span>
                        </div>
                        <div className="flex items-center space-x-2">
                          <Clock className="w-4 h-4 text-cyan-400 shrink-0" />
                          <span>Adjuster Review: <strong>Under Specialist Evaluation</strong></span>
                        </div>
                        <div className="flex items-center space-x-2">
                          <FileText className="w-4 h-4 text-slate-500 shrink-0" />
                          <span>Settlement Notice & Letter: <strong>Published Upon Completion</strong></span>
                        </div>
                      </div>
                      <p className="text-xs text-slate-500 mt-3">
                        In accordance with healthcare claims standards and MediShield governance (ADR-014), formal decision notices and payment breakdowns are finalized exclusively upon completed human review.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Financial Breakdown (If Adjudicated) */}
              {claimStatus.adjudication && (
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
                  <h3 className="text-base font-bold text-white mb-4 flex items-center space-x-2">
                    <DollarSign className="w-5 h-5 text-cyan-400" />
                    <span>Coverage & Payment Breakdown</span>
                  </h3>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                    <div className="bg-slate-950 p-4 rounded-lg border border-slate-800">
                      <div className="text-xs text-slate-400">Total Billed</div>
                      <div className="text-lg font-mono font-bold text-white mt-1">
                        ${claimStatus.adjudication.total_billed?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-slate-950 p-4 rounded-lg border border-emerald-900/50">
                      <div className="text-xs text-emerald-400 font-medium">Covered by Policy</div>
                      <div className="text-lg font-mono font-bold text-emerald-300 mt-1">
                        ${claimStatus.adjudication.covered_amount?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-slate-950 p-4 rounded-lg border border-slate-800">
                      <div className="text-xs text-slate-400">Deductible Applied</div>
                      <div className="text-lg font-mono font-bold text-slate-300 mt-1">
                        ${claimStatus.adjudication.deductible_applied?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                    <div className="bg-slate-950 p-4 rounded-lg border border-slate-800">
                      <div className="text-xs text-slate-400">Member Responsibility</div>
                      <div className="text-lg font-mono font-bold text-amber-300 mt-1">
                        ${claimStatus.adjudication.member_responsibility?.toFixed(2) ?? "0.00"}
                      </div>
                    </div>
                  </div>

                  {/* Decision Letter */}
                  {claimStatus.adjudication.explanation_letter && (
                    <div className="mt-6 pt-6 border-t border-slate-800">
                      <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                        Official Decision Notice
                      </div>
                      <div className="bg-slate-950 border border-slate-800 rounded-lg p-4 text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
                        {claimStatus.adjudication.explanation_letter}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Submitted Documents Roster */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
                <h3 className="text-base font-bold text-white mb-4 flex items-center space-x-2">
                  <FileText className="w-5 h-5 text-cyan-400" />
                  <span>Submitted File Evidence ({claimStatus.submitted_documents.length})</span>
                </h3>
                <div className="divide-y divide-slate-800 border border-slate-800 rounded-lg overflow-hidden bg-slate-950">
                  {claimStatus.submitted_documents.map((doc, idx) => (
                    <div key={idx} className="p-3.5 flex items-center justify-between text-sm">
                      <div className="flex items-center space-x-3">
                        <FileText className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                        <div>
                          <div className="text-slate-200 font-medium">{doc.file_name}</div>
                          <div className="text-xs text-slate-500">
                            Type: {doc.doc_type} • {(doc.size_bytes / 1024).toFixed(1)} KB
                          </div>
                        </div>
                      </div>
                      <span className="text-xs text-slate-400 font-mono">
                        {new Date(doc.created_at).toLocaleDateString()}
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
