"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Database,
  FileCheck,
  FileSpreadsheet,
  FileText,
  Filter,
  Loader2,
  Lock,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
  Upload,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  AdminDashboardMetrics,
  CodeListResponse,
  CodeUploadSummary,
  MedicalCodeItem,
  PolicyVersionItem,
  getAdminDashboardMetrics,
  listMedicalCodes,
  listPolicies,
  publishPolicyVersion,
  uploadMedicalCodesCsv,
  uploadPolicyPdf,
} from "@/lib/api";

export default function AdminPortalPage() {
  const { user, loading: authLoading } = useAuth();
  const [activeTab, setActiveTab] = useState<"dashboard" | "codes" | "policies">("dashboard");

  // Dashboard State
  const [metrics, setMetrics] = useState<AdminDashboardMetrics | null>(null);
  const [loadingMetrics, setLoadingMetrics] = useState(true);

  // Terminology State
  const [codesData, setCodesData] = useState<CodeListResponse | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [systemFilter, setSystemFilter] = useState("");
  const [loadingCodes, setLoadingCodes] = useState(false);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [uploadingCsv, setUploadingCsv] = useState(false);
  const [uploadResult, setUploadResult] = useState<CodeUploadSummary | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Policies (RAG Firewall) State
  const [policies, setPolicies] = useState<PolicyVersionItem[]>([]);
  const [loadingPolicies, setLoadingPolicies] = useState(false);
  const [policyId, setPolicyId] = useState("");
  const [planName, setPlanName] = useState("");
  const [versionTag, setVersionTag] = useState("2026.1");
  const [policyType, setPolicyType] = useState("COMPREHENSIVE_HEALTH");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2026-12-31");
  const [policyPdfFile, setPolicyPdfFile] = useState<File | null>(null);
  const [uploadingPolicy, setUploadingPolicy] = useState(false);
  const [policyUploadSuccess, setPolicyUploadSuccess] = useState<string | null>(null);
  const [policyUploadError, setPolicyUploadError] = useState<string | null>(null);
  const [publishingKey, setPublishingKey] = useState<string | null>(null);
  const [publishSuccess, setPublishSuccess] = useState<string | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  // Load Dashboard Metrics
  const fetchMetrics = useCallback(async () => {
    setLoadingMetrics(true);
    try {
      const data = await getAdminDashboardMetrics();
      setMetrics(data);
    } catch (err) {
      console.error("Failed to load admin metrics", err);
    } finally {
      setLoadingMetrics(false);
    }
  }, []);

  // Load Codes
  const fetchCodes = useCallback(async () => {
    setLoadingCodes(true);
    try {
      const data = await listMedicalCodes(searchQuery, systemFilter, 50, 0);
      setCodesData(data);
    } catch (err) {
      console.error("Failed to list medical codes", err);
    } finally {
      setLoadingCodes(false);
    }
  }, [searchQuery, systemFilter]);

  // Load Policies
  const fetchPolicies = useCallback(async () => {
    setLoadingPolicies(true);
    try {
      const data = await listPolicies();
      setPolicies(data);
    } catch (err) {
      console.error("Failed to list corporate policies", err);
    } finally {
      setLoadingPolicies(false);
    }
  }, []);

  useEffect(() => {
    fetchMetrics();
    fetchCodes();
    fetchPolicies();
  }, [fetchMetrics, fetchCodes, fetchPolicies]);

  const handlePolicyUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!policyPdfFile || !policyId.trim() || !planName.trim()) {
      setPolicyUploadError("Please provide Policy ID, Plan Name, and a valid PDF file.");
      return;
    }
    setUploadingPolicy(true);
    setPolicyUploadError(null);
    setPolicyUploadSuccess(null);
    try {
      const formData = new FormData();
      formData.append("policy_id", policyId.trim());
      formData.append("version_tag", versionTag.trim());
      formData.append("plan_name", planName.trim());
      formData.append("policy_type", policyType);
      formData.append("effective_start_date", startDate);
      if (endDate) formData.append("effective_end_date", endDate);
      formData.append("file", policyPdfFile);

      const res = await uploadPolicyPdf(formData);
      setPolicyUploadSuccess(`Draft version ${res.version_tag} for policy ${res.policy_id} uploaded to storage. Staged for ADR-006 Publishing Firewall.`);
      setPolicyPdfFile(null);
      fetchPolicies();
      fetchMetrics();
    } catch (err: any) {
      setPolicyUploadError(err.message || "Failed to upload policy PDF.");
    } finally {
      setUploadingPolicy(false);
    }
  };

  const handlePublishPolicy = async (pId: string, vTag: string) => {
    const key = `${pId}:${vTag}`;
    setPublishingKey(key);
    setPublishError(null);
    setPublishSuccess(null);
    try {
      const res = await publishPolicyVersion(pId, vTag, "Corporate administration publishing firewall promotion");
      setPublishSuccess(`Policy ${res.policy_id} (${res.version_tag}) published successfully! Vector collection '${res.vector_collection_name}' is now active in Qdrant.`);
      fetchPolicies();
      fetchMetrics();
    } catch (err: any) {
      setPublishError(err.message || `Failed to publish policy version ${vTag} for ${pId}.`);
    } finally {
      setPublishingKey(null);
    }
  };


  const handleCsvUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvFile) return;
    setUploadingCsv(true);
    setUploadError(null);
    setUploadResult(null);
    try {
      const res = await uploadMedicalCodesCsv(csvFile);
      setUploadResult(res);
      setCsvFile(null);
      fetchCodes();
      fetchMetrics();
    } catch (err: any) {
      setUploadError(err.message || "Failed to upload medical codes CSV.");
    } finally {
      setUploadingCsv(false);
    }
  };

  if (!authLoading && (!user || user.role !== "ADMINISTRATOR")) {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center">
        <div className="p-4 bg-purple-950/40 border border-purple-800/80 rounded-2xl mb-6 inline-block">
          <ShieldAlert className="w-12 h-12 text-purple-400 mx-auto" />
        </div>
        <h2 className="text-2xl font-bold text-white mb-2">Administrator Access Required</h2>
        <p className="text-sm text-slate-400 mb-6 max-w-lg mx-auto leading-relaxed">
          Under MediShield Three-Tier RBAC (ADR-012, ADR-013), platform terminology uploads and corporate governance are restricted to the <strong>ADMINISTRATOR</strong> persona.
          {user ? (
            <span className="block mt-2 text-xs text-slate-300">
              You are currently signed in as: <strong className="text-white font-mono">{user.username}</strong> ({user.role}).
            </span>
          ) : (
            <span className="block mt-2 text-xs text-slate-300">
              You are currently not signed in.
            </span>
          )}
        </p>
        <div className="flex justify-center gap-3">
          {user && (
            <Link
              href={user.role === "CLAIMER" ? "/claimer" : "/"}
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-sm font-semibold transition-colors"
            >
              Return to My Workspace
            </Link>
          )}
          <Link
            href="/login"
            className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-semibold transition-colors shadow-lg shadow-purple-950"
          >
            Sign In as Administrator
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full">
      {/* Top Banner */}
      <div className="bg-gradient-to-r from-cyan-950 via-slate-900 to-slate-900 border border-cyan-800/50 rounded-xl p-6 mb-8 shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2 text-cyan-400 text-xs font-semibold uppercase tracking-wider mb-1">
              <ShieldCheck className="w-4 h-4" />
              <span>Platform Administration & Terminology</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-white tracking-tight">
              MediShield Control Center
            </h1>
            <p className="text-slate-400 text-sm mt-1 max-w-2xl">
              Manage corporate insurance policies, scale-out clinical terminology fee schedules (CPT/ICD CSVs), and inspect executive STP analytics.
            </p>
          </div>

          <div className="flex items-center space-x-2 bg-slate-950/80 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setActiveTab("dashboard")}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === "dashboard"
                  ? "bg-cyan-500 text-slate-950 font-bold shadow-md"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Executive Dashboard
            </button>
            <button
              onClick={() => setActiveTab("codes")}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === "codes"
                  ? "bg-cyan-500 text-slate-950 font-bold shadow-md"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Medical Codes (CSV)
            </button>
            <button
              onClick={() => setActiveTab("policies")}
              className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                activeTab === "policies"
                  ? "bg-cyan-500 text-slate-950 font-bold shadow-md"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Policies (RAG Firewall)
            </button>
          </div>
        </div>
      </div>

      {/* Tab 1: Executive Dashboard */}
      {activeTab === "dashboard" && (
        <div className="space-y-6">
          {loadingMetrics ? (
            <div className="p-12 text-center text-slate-400 flex items-center justify-center space-x-3">
              <Loader2 className="w-6 h-6 animate-spin text-cyan-400" />
              <span>Loading executive metrics...</span>
            </div>
          ) : metrics ? (
            <>
              {/* Metric KPI Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
                  <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase">
                    <span>Straight-Through Rate (STP)</span>
                    <TrendingUp className="w-4 h-4 text-emerald-400" />
                  </div>
                  <div className="text-3xl font-extrabold text-emerald-400 mt-2 font-mono">
                    {metrics.straight_through_processing_rate.toFixed(1)}%
                  </div>
                  <div className="text-xs text-slate-500 mt-1">Autonomous P6 clean-pass approvals</div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
                  <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase">
                    <span>Total Intake Cases</span>
                    <Activity className="w-4 h-4 text-cyan-400" />
                  </div>
                  <div className="text-3xl font-extrabold text-white mt-2 font-mono">
                    {metrics.total_cases}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">Claims processed across all queues</div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
                  <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase">
                    <span>Active Medical Codes</span>
                    <Database className="w-4 h-4 text-purple-400" />
                  </div>
                  <div className="text-3xl font-extrabold text-purple-300 mt-2 font-mono">
                    {metrics.total_medical_codes.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">CPT, ICD-10 & HCPCS in DB & cache</div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
                  <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase">
                    <span>Active Master Policies</span>
                    <FileCheck className="w-4 h-4 text-amber-400" />
                  </div>
                  <div className="text-3xl font-extrabold text-amber-300 mt-2 font-mono">
                    {metrics.active_policies_count}
                  </div>
                  <div className="text-xs text-slate-500 mt-1">Indexed in Qdrant & SQL store</div>
                </div>
              </div>

              {/* Detailed Distributions */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Workflow State Breakdown */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
                  <h3 className="text-sm font-bold text-slate-200 uppercase tracking-wider mb-4 flex items-center space-x-2">
                    <BarChart3 className="w-4 h-4 text-cyan-400" />
                    <span>Workflow State Breakdown</span>
                  </h3>
                  <div className="space-y-3">
                    {Object.entries(metrics.workflow_distribution).map(([st, count]) => (
                      <div key={st} className="flex items-center justify-between text-sm">
                        <span className="text-slate-400 font-mono text-xs">{st}</span>
                        <span className="font-bold text-white bg-slate-950 px-3 py-1 rounded border border-slate-800 font-mono">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Adjudication Decision Breakdown */}
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
                  <h3 className="text-sm font-bold text-slate-200 uppercase tracking-wider mb-4 flex items-center space-x-2">
                    <ShieldCheck className="w-4 h-4 text-emerald-400" />
                    <span>Adjudication Outcomes</span>
                  </h3>
                  <div className="space-y-3">
                    {Object.entries(metrics.decision_distribution).map(([dec, count]) => (
                      <div key={dec} className="flex items-center justify-between text-sm">
                        <span className={`font-semibold ${
                          dec === "APPROVE" ? "text-emerald-400" : dec === "REJECT" ? "text-rose-400" : "text-amber-400"
                        }`}>
                          {dec}
                        </span>
                        <span className="font-bold text-white bg-slate-950 px-3 py-1 rounded border border-slate-800 font-mono">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="text-slate-400 text-sm">No executive metrics available.</div>
          )}
        </div>
      )}

      {/* Tab 2: Medical Codes (CSV Upload & Search) */}
      {activeTab === "codes" && (
        <div className="space-y-8">
          {/* CSV Upload Section */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <div className="flex items-center space-x-3 mb-3">
              <FileSpreadsheet className="w-6 h-6 text-cyan-400" />
              <div>
                <h2 className="text-lg font-bold text-white">Upload Medical Codes Fee Schedule (CSV)</h2>
                <p className="text-xs text-slate-400">
                  Bulk ingest CPT, HCPCS, ICD-10-CM, or Revenue codes directly into PostgreSQL and Redis cache (ADR-012).
                </p>
              </div>
            </div>

            {uploadError && (
              <div className="p-4 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <AlertCircle className="w-5 h-5 flex-shrink-0" />
                <span>{uploadError}</span>
              </div>
            )}

            {uploadResult && (
              <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
                <div>
                  <div className="font-bold">{uploadResult.message}</div>
                  <div className="text-xs text-emerald-400 mt-0.5">
                    Updated systems: {uploadResult.coding_systems_updated.join(", ")}
                  </div>
                </div>
              </div>
            )}

            <form onSubmit={handleCsvUpload} className="flex flex-col sm:flex-row items-center gap-4 mt-4">
              <input
                type="file"
                accept=".csv,.txt"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                className="block w-full text-sm text-slate-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-slate-800 file:text-cyan-400 hover:file:bg-slate-700 cursor-pointer"
              />
              <button
                type="submit"
                disabled={uploadingCsv || !csvFile}
                className="w-full sm:w-auto px-6 py-2.5 bg-cyan-500 hover:bg-cyan-400 disabled:opacity-50 text-slate-950 font-bold rounded-lg transition-colors flex items-center justify-center space-x-2 text-sm flex-shrink-0"
              >
                {uploadingCsv && <Loader2 className="w-4 h-4 animate-spin" />}
                <span>{uploadingCsv ? "Ingesting..." : "Upload & Sync Codes"}</span>
              </button>
            </form>
          </div>

          {/* Search & Code Table */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mb-6">
              <div className="relative w-full sm:w-80">
                <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && fetchCodes()}
                  placeholder="Search code or description..."
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg pl-9 pr-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 text-sm"
                />
              </div>

              <div className="flex items-center space-x-3 w-full sm:w-auto justify-end">
                <select
                  value={systemFilter}
                  onChange={(e) => {
                    setSystemFilter(e.target.value);
                  }}
                  className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-500"
                >
                  <option value="">All Systems</option>
                  <option value="CPT">CPT</option>
                  <option value="HCPCS">HCPCS</option>
                  <option value="ICD10_CM">ICD-10-CM</option>
                  <option value="REV_CODE">Revenue Codes</option>
                </select>
                <button
                  onClick={fetchCodes}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium rounded-lg text-sm flex items-center space-x-2"
                >
                  <RefreshCw className="w-4 h-4" />
                  <span>Filter</span>
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="border border-slate-800 rounded-lg overflow-x-auto bg-slate-950">
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="bg-slate-900/90 text-xs uppercase font-semibold text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="px-4 py-3">Code</th>
                    <th className="px-4 py-3">System</th>
                    <th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3">Fee / Charge</th>
                    <th className="px-4 py-3">Prior Auth</th>
                    <th className="px-4 py-3">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {loadingCodes ? (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-slate-500">
                        <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2 text-cyan-400" />
                        Loading codes from catalog...
                      </td>
                    </tr>
                  ) : codesData && codesData.items.length > 0 ? (
                    codesData.items.map((item, idx) => (
                      <tr key={idx} className="hover:bg-slate-900/50">
                        <td className="px-4 py-3 font-mono font-bold text-cyan-400">{item.code}</td>
                        <td className="px-4 py-3">
                          <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 border border-slate-700">
                            {item.coding_system}
                          </span>
                        </td>
                        <td className="px-4 py-3 max-w-xs truncate" title={item.short_description}>
                          {item.short_description}
                        </td>
                        <td className="px-4 py-3 font-mono">
                          {item.standard_charge !== undefined && item.standard_charge !== null
                            ? `$${item.standard_charge.toFixed(2)}`
                            : "—"}
                        </td>
                        <td className="px-4 py-3">
                          {item.requires_prior_auth ? (
                            <span className="text-amber-400 text-xs font-semibold">Yes</span>
                          ) : (
                            <span className="text-slate-500 text-xs">No</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {item.is_active ? (
                            <span className="text-emerald-400 text-xs font-semibold">Active</span>
                          ) : (
                            <span className="text-rose-400 text-xs">Inactive</span>
                          )}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-slate-500">
                        No medical codes matching current filter. Upload a CSV to populate.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {codesData && (
              <div className="mt-3 text-xs text-slate-500 flex justify-between items-center">
                <span>Showing {codesData.items.length} of {codesData.total} codes</span>
                <span>Page 1</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 3: Policies (RAG Knowledge & ADR-006 Firewall) */}
      {activeTab === "policies" && (
        <div className="space-y-8">
          {/* Top Architecture Notice Banner */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <div className="flex items-center space-x-3 mb-3">
              <FileCheck className="w-6 h-6 text-amber-400" />
              <div>
                <h2 className="text-lg font-bold text-white">Policy Administration & Publishing Firewall (ADR-006)</h2>
                <p className="text-xs text-slate-400">
                  Customer submissions are strictly read-only evidence. Only authorized administrators can stage and publish corporate policy PDFs into the Qdrant semantic vector index.
                </p>
              </div>
            </div>

            {policyUploadError && (
              <div className="p-4 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <AlertCircle className="w-5 h-5 flex-shrink-0" />
                <span>{policyUploadError}</span>
              </div>
            )}

            {policyUploadSuccess && (
              <div className="p-4 bg-amber-500/10 border border-amber-500/30 text-amber-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
                <div>
                  <div className="font-bold">Draft Policy Uploaded</div>
                  <div className="text-xs text-amber-400 mt-0.5">{policyUploadSuccess}</div>
                </div>
              </div>
            )}

            {publishSuccess && (
              <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <ShieldCheck className="w-5 h-5 flex-shrink-0" />
                <div>
                  <div className="font-bold">ADR-006 Publishing Firewall Complete</div>
                  <div className="text-xs text-emerald-400 mt-0.5">{publishSuccess}</div>
                </div>
              </div>
            )}

            {publishError && (
              <div className="p-4 bg-rose-500/10 border border-rose-500/30 text-rose-300 rounded-lg text-sm flex items-center space-x-3 mb-4">
                <AlertCircle className="w-5 h-5 flex-shrink-0" />
                <span>{publishError}</span>
              </div>
            )}

            {/* Upload Draft Form */}
            <form onSubmit={handlePolicyUpload} className="space-y-4 mt-4 bg-slate-950/80 p-5 rounded-lg border border-slate-800">
              <div className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2 flex items-center space-x-1.5">
                <Plus className="w-3.5 h-3.5 text-cyan-400" />
                <span>Stage New Policy Document (Phase 1 Draft)</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Policy Identifier *</label>
                  <input
                    type="text"
                    required
                    value={policyId}
                    onChange={(e) => setPolicyId(e.target.value)}
                    placeholder="e.g. POL-CORP-2026"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-cyan-500 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Plan Display Name *</label>
                  <input
                    type="text"
                    required
                    value={planName}
                    onChange={(e) => setPlanName(e.target.value)}
                    placeholder="e.g. MediShield Gold Health Plan"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-cyan-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Version Tag *</label>
                  <input
                    type="text"
                    required
                    value={versionTag}
                    onChange={(e) => setVersionTag(e.target.value)}
                    placeholder="e.g. 2026.1"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-cyan-500 font-mono"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Policy Type</label>
                  <select
                    value={policyType}
                    onChange={(e) => setPolicyType(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-500"
                  >
                    <option value="COMPREHENSIVE_HEALTH">Comprehensive Health</option>
                    <option value="EMERGENCY_ONLY">Emergency Only</option>
                    <option value="HIGH_DEDUCTIBLE">High Deductible Health Plan (HDHP)</option>
                    <option value="MEDICARE_ADVANTAGE">Medicare Advantage</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Effective Start Date *</label>
                  <input
                    type="date"
                    required
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-500"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Effective End Date</label>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Authoritative Policy PDF Document *</label>
                <input
                  type="file"
                  required
                  accept="application/pdf,.pdf"
                  onChange={(e) => setPolicyPdfFile(e.target.files?.[0] || null)}
                  className="block w-full text-sm text-slate-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-slate-800 file:text-cyan-400 hover:file:bg-slate-700 cursor-pointer"
                />
              </div>

              <div className="flex justify-end pt-2">
                <button
                  type="submit"
                  disabled={uploadingPolicy || !policyPdfFile}
                  className="px-6 py-2.5 bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-slate-950 font-bold rounded-lg transition-colors flex items-center space-x-2 text-sm shadow-md"
                >
                  {uploadingPolicy ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Staging Document...</span>
                    </>
                  ) : (
                    <>
                      <Upload className="w-4 h-4" />
                      <span>Upload Draft Policy PDF</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>

          {/* Master Policies and Vector Index Registry Table */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center space-x-2">
                  <Database className="w-4 h-4 text-purple-400" />
                  <span>Corporate Policies & Vector Index Registry</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Published versions are indexed in Qdrant and actively queried by the PolicyCoverageAgent.
                </p>
              </div>
              <button
                onClick={fetchPolicies}
                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium rounded-lg text-xs flex items-center space-x-1.5 transition-colors"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Refresh</span>
              </button>
            </div>

            <div className="border border-slate-800 rounded-lg overflow-x-auto bg-slate-950">
              <table className="w-full text-left text-sm text-slate-300">
                <thead className="bg-slate-900/90 text-xs uppercase font-semibold text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="px-4 py-3">Policy ID</th>
                    <th className="px-4 py-3">Plan Name</th>
                    <th className="px-4 py-3">Version</th>
                    <th className="px-4 py-3">Effective Window</th>
                    <th className="px-4 py-3">RAG Status</th>
                    <th className="px-4 py-3 text-right">Firewall Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {loadingPolicies ? (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-slate-500">
                        <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2 text-cyan-400" />
                        Loading corporate policies...
                      </td>
                    </tr>
                  ) : policies.length > 0 ? (
                    policies.map((p, idx) => {
                      const pKey = `${p.policy_id}:${p.version_tag}`;
                      const isPublishingThis = publishingKey === pKey;
                      return (
                        <tr key={idx} className="hover:bg-slate-900/50">
                          <td className="px-4 py-3 font-mono font-bold text-amber-400">{p.policy_id}</td>
                          <td className="px-4 py-3 max-w-xs truncate font-medium text-white" title={p.plan_name}>
                            {p.plan_name}
                          </td>
                          <td className="px-4 py-3 font-mono text-xs">{p.version_tag}</td>
                          <td className="px-4 py-3 text-xs text-slate-400 font-mono">
                            {p.effective_start_date || "—"} to {p.effective_end_date || "Open"}
                          </td>
                          <td className="px-4 py-3">
                            {p.is_published ? (
                              <span className="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                                <ShieldCheck className="w-3.5 h-3.5" />
                                <span>Published (Vectorized)</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                                <Lock className="w-3.5 h-3.5" />
                                <span>Draft Quarantined</span>
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right">
                            {p.is_published ? (
                              <span className="text-xs text-slate-500 font-mono">
                                {p.chunk_count > 0 ? `${p.chunk_count} Chunks` : "Vector Active"}
                              </span>
                            ) : (
                              <button
                                onClick={() => handlePublishPolicy(p.policy_id, p.version_tag)}
                                disabled={isPublishingThis}
                                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded text-xs font-semibold transition-colors inline-flex items-center space-x-1.5 shadow-md shadow-emerald-950"
                              >
                                {isPublishingThis ? (
                                  <>
                                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                    <span>Vectorizing...</span>
                                  </>
                                ) : (
                                  <>
                                    <ShieldCheck className="w-3.5 h-3.5" />
                                    <span>Publish to RAG (ADR-006)</span>
                                  </>
                                )}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-slate-500">
                        No policy versions registered. Stage a draft policy above to get started.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
