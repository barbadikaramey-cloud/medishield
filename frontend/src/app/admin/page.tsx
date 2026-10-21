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
  const { user, login, loading: authLoading } = useAuth();
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
      setPolicyUploadSuccess(`Draft version ${res.version_tag} for policy ${res.policy_id} uploaded to storage. Staged for Policy Publishing Firewall.`);
      setPolicyPdfFile(null);
      fetchPolicies();
      fetchMetrics();
    } catch (err: unknown) {
      setPolicyUploadError(err instanceof Error ? err.message : "Failed to upload policy PDF.");
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
    } catch (err: unknown) {
      setPublishError(err instanceof Error ? err.message : `Failed to publish policy version ${vTag} for ${pId}.`);
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
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Failed to upload medical codes CSV.");
    } finally {
      setUploadingCsv(false);
    }
  };

  const formatCharge = (charge: number | string | undefined | null) => {
    if (charge === undefined || charge === null || charge === "") return "—";
    const num = typeof charge === "number" ? charge : parseFloat(String(charge));
    return isNaN(num) ? "—" : `$${num.toFixed(2)}`;
  };

  const handleQuickAdminLogin = async () => {
    try {
      await login("admin", "admin123");
    } catch (err) {
      console.error("Failed quick admin login", err);
    }
  };

  if (!authLoading && (!user || user.role !== "ADMINISTRATOR")) {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center">
        <div className="p-4 bg-status-warning/10 border border-status-warning/20 rounded-card mb-6 inline-block">
          <ShieldAlert className="w-10 h-10 text-status-warning mx-auto" />
        </div>
        <h2 className="text-xl font-bold text-foreground mb-2">Administrator Access Required</h2>
        <p className="text-xs sm:text-sm text-secondary mb-6 max-w-lg mx-auto leading-relaxed">
          Under MediShield Three-Tier RBAC, platform terminology uploads and corporate governance are restricted to the <strong>ADMINISTRATOR</strong> persona.
          {user ? (
            <span className="block mt-2 text-xs text-muted">
              You are currently signed in as: <strong className="text-foreground font-mono">{user.username}</strong> ({user.role}).
            </span>
          ) : (
            <span className="block mt-2 text-xs text-muted">
              You are currently not signed in.
            </span>
          )}
        </p>
        <div className="flex flex-col sm:flex-row justify-center gap-2.5">
          <button
            onClick={handleQuickAdminLogin}
            className="px-4 py-2 bg-accent hover:bg-accent-hover text-accent-fg rounded-btn text-xs font-semibold transition-all shadow-xs flex items-center justify-center space-x-1.5"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>1-Click Sign In as Administrator (Demo)</span>
          </button>
          <Link
            href="/login"
            className="px-4 py-2 bg-card-inner hover:bg-border text-foreground border border-border rounded-btn text-xs font-medium transition-colors flex items-center justify-center"
          >
            Go to Login Page
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 w-full space-y-6">
      {/* Top Banner */}
      <div className="bg-card border border-border rounded-card p-5 sm:p-6 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center space-x-2.5 mb-1.5 flex-wrap">
              <h1 className="text-xl sm:text-2xl font-bold text-foreground tracking-tight">Admin & Policy Catalog</h1>
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-sky-soft text-accent border border-sky/30 shadow-xs">
                System Administration
              </span>
            </div>
            <p className="text-secondary text-xs sm:text-sm mt-0.5 max-w-2xl">
              Manage corporate insurance policies, scale-out clinical terminology fee schedules (CPT/ICD CSVs), and inspect executive STP analytics.
            </p>
          </div>

          <div className="flex items-center space-x-1.5 bg-card-inner p-1 rounded-btn border border-border self-start md:self-auto shadow-xs">
            <button
              onClick={() => setActiveTab("dashboard")}
              className={`h-8 px-3.5 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 ${
                activeTab === "dashboard"
                  ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/40 shadow-xs"
                  : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
              }`}
            >
              Executive Dashboard
            </button>
            <button
              onClick={() => setActiveTab("codes")}
              className={`h-8 px-3.5 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 ${
                activeTab === "codes"
                  ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/40 shadow-xs"
                  : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
              }`}
            >
              Medical Codes (CSV)
            </button>
            <button
              onClick={() => setActiveTab("policies")}
              className={`h-8 px-3.5 py-1.5 rounded-btn text-xs font-semibold transition-all duration-180 ${
                activeTab === "policies"
                  ? "bg-sky-soft text-sky-deep font-bold border border-sky-med/40 shadow-xs"
                  : "text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border"
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
            <div className="p-12 text-center text-muted flex items-center justify-center space-x-2 text-xs">
              <Loader2 className="w-4 h-4 animate-spin text-sky-deep" />
              <span>Loading executive metrics...</span>
            </div>
          ) : metrics ? (
            <>
              {/* Metric KPI Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                <div className="bg-card border border-border rounded-card p-4 shadow-xs transition-all duration-180 hover:bg-pink-hover/40 hover:border-success-border hover:shadow-card-hover hover:-translate-y-0.5">
                  <div className="flex items-center justify-between text-muted text-[11px] font-semibold uppercase">
                    <span>Straight-Through Rate (STP)</span>
                    <TrendingUp className="w-3.5 h-3.5 text-success" />
                  </div>
                  <div className="text-2xl font-bold text-success mt-1 font-mono tabular-nums">
                    {metrics.straight_through_processing_rate.toFixed(1)}%
                  </div>
                  <div className="text-[11px] text-muted mt-1">Autonomous P6 clean-pass approvals</div>
                </div>

                <div className="bg-card border border-border rounded-card p-4 shadow-xs transition-all duration-180 hover:bg-pink-hover/40 hover:border-pink-border hover:shadow-card-hover hover:-translate-y-0.5">
                  <div className="flex items-center justify-between text-muted text-[11px] font-semibold uppercase">
                    <span>Total Intake Cases</span>
                    <Activity className="w-3.5 h-3.5 text-sky-deep" />
                  </div>
                  <div className="text-2xl font-bold text-foreground mt-1 font-mono tabular-nums">
                    {metrics.total_cases}
                  </div>
                  <div className="text-[11px] text-muted mt-1">Claims processed across all queues</div>
                </div>

                <div className="bg-card border border-border rounded-card p-4 shadow-xs transition-all duration-180 hover:bg-pink-hover/40 hover:border-pink-border hover:shadow-card-hover hover:-translate-y-0.5">
                  <div className="flex items-center justify-between text-muted text-[11px] font-semibold uppercase">
                    <span>Active Medical Codes</span>
                    <Database className="w-3.5 h-3.5 text-sky-deep" />
                  </div>
                  <div className="text-2xl font-bold text-foreground mt-1 font-mono tabular-nums">
                    {metrics.total_medical_codes.toLocaleString()}
                  </div>
                  <div className="text-[11px] text-muted mt-1">CPT, ICD-10 & HCPCS in DB & cache</div>
                </div>

                <div className="bg-card border border-border rounded-card p-4 shadow-xs transition-all duration-180 hover:bg-pink-hover/40 hover:border-warning-border hover:shadow-card-hover hover:-translate-y-0.5">
                  <div className="flex items-center justify-between text-muted text-[11px] font-semibold uppercase">
                    <span>Active Master Policies</span>
                    <FileCheck className="w-3.5 h-3.5 text-warning" />
                  </div>
                  <div className="text-2xl font-bold text-warning mt-1 font-mono tabular-nums">
                    {metrics.active_policies_count}
                  </div>
                  <div className="text-[11px] text-muted mt-1">Indexed in Qdrant & SQL store</div>
                </div>
              </div>

              {/* Detailed Distributions */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Workflow State Breakdown */}
                <div className="bg-card border border-border rounded-card p-5 shadow-xs">
                  <h3 className="text-xs font-bold text-foreground uppercase tracking-wider mb-3 flex items-center space-x-1.5">
                    <BarChart3 className="w-3.5 h-3.5 text-accent" />
                    <span>Workflow State Breakdown</span>
                  </h3>
                  <div className="space-y-2">
                    {Object.entries(metrics.workflow_distribution).map(([st, count]) => (
                      <div key={st} className="flex items-center justify-between text-xs p-2 bg-card-inner rounded-btn border border-border">
                        <span className="text-secondary font-mono text-[11px]">{st}</span>
                        <span className="font-bold text-foreground font-mono tabular-nums">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Adjudication Decision Breakdown */}
                <div className="bg-card border border-border rounded-card p-5 shadow-xs">
                  <h3 className="text-xs font-bold text-foreground uppercase tracking-wider mb-3 flex items-center space-x-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 text-status-success" />
                    <span>Adjudication Outcomes</span>
                  </h3>
                  <div className="space-y-2">
                    {Object.entries(metrics.decision_distribution).map(([dec, count]) => (
                      <div key={dec} className="flex items-center justify-between text-xs p-2 bg-card-inner rounded-btn border border-border">
                        <span className={`font-semibold ${
                          dec === "APPROVE" ? "text-status-success" : dec === "REJECT" ? "text-rose-deep" : "text-status-warning"
                        }`}>
                          {dec}
                        </span>
                        <span className="font-bold text-foreground font-mono tabular-nums">
                          {count}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="text-muted text-xs">No executive metrics available.</div>
          )}
        </div>
      )}

      {/* Tab 2: Medical Codes (CSV Upload & Search) */}
      {activeTab === "codes" && (
        <div className="space-y-6">
          {/* CSV Upload Section */}
          <div className="bg-card border border-border rounded-card p-5 shadow-xs">
            <div className="flex items-center space-x-2.5 mb-3">
              <FileSpreadsheet className="w-5 h-5 text-accent" />
              <div>
                <h2 className="text-sm font-bold text-foreground">Upload Medical Codes Fee Schedule (CSV)</h2>
                <p className="text-[11px] text-muted">
                  Bulk ingest CPT, HCPCS, ICD-10-CM, or Revenue codes directly into PostgreSQL and Redis cache.
                </p>
              </div>
            </div>

            {uploadError && (
              <div className="p-3 bg-status-danger/10 border border-status-danger/20 text-status-danger rounded-btn text-xs flex items-center space-x-2 mb-3">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                <span>{uploadError}</span>
              </div>
            )}

            {uploadResult && (
              <div className="p-3 bg-status-success/10 border border-status-success/20 text-status-success rounded-btn text-xs flex items-center space-x-2 mb-3">
                <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                <div>
                  <div className="font-bold">{uploadResult.message}</div>
                  <div className="text-[11px] text-status-success mt-0.5">
                    Updated systems: {uploadResult.coding_systems_updated.join(", ")}
                  </div>
                </div>
              </div>
            )}

            <form onSubmit={handleCsvUpload} className="flex flex-col sm:flex-row items-center gap-3 mt-3">
              <input
                type="file"
                accept=".csv,.txt"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                className="block w-full text-xs text-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded-btn file:border-0 file:text-xs file:font-semibold file:bg-card file:text-accent hover:file:bg-sky-pale cursor-pointer border border-border rounded-btn p-1 bg-card-inner transition"
              />
              <button
                type="submit"
                disabled={uploadingCsv || !csvFile}
                className="w-full sm:w-auto h-9 px-4 py-2 bg-[#397FA9] hover:bg-[#2C6688] disabled:opacity-50 text-white font-semibold rounded-btn transition-colors flex items-center justify-center space-x-1.5 text-xs flex-shrink-0 shadow-xs"
              >
                {uploadingCsv && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                <span>{uploadingCsv ? "Ingesting..." : "Upload & Sync Codes"}</span>
              </button>
            </form>
          </div>

          {/* Search & Code Table */}
          <div className="bg-card border border-border rounded-card p-5 shadow-xs">
            <div className="flex flex-col sm:flex-row items-center justify-between gap-3 mb-4">
              <div className="relative w-full sm:w-80">
                <Search className="w-3.5 h-3.5 text-muted absolute left-3 top-3" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && fetchCodes()}
                  placeholder="Search code or description..."
                  className="w-full h-9 bg-card border border-border rounded-btn pl-8 pr-3 py-1.5 text-foreground placeholder:text-muted focus:outline-none focus:border-sky-med focus:ring-2 focus:ring-sky/30 text-xs shadow-xs transition-colors"
                />
              </div>

              <div className="flex items-center space-x-2 w-full sm:w-auto justify-end">
                <select
                  value={systemFilter}
                  onChange={(e) => {
                    setSystemFilter(e.target.value);
                  }}
                  className="h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground text-xs focus:outline-none focus:border-sky-med focus:ring-2 focus:ring-sky/30 shadow-xs"
                >
                  <option value="">All Systems</option>
                  <option value="CPT">CPT</option>
                  <option value="HCPCS">HCPCS</option>
                  <option value="ICD10_CM">ICD-10-CM</option>
                  <option value="REV_CODE">Revenue Codes</option>
                </select>
                <button
                  onClick={fetchCodes}
                  className="h-9 px-3.5 py-1.5 bg-card hover:bg-pink-hover text-secondary hover:text-pink-deep hover:border-pink-border border border-border font-medium rounded-btn text-xs flex items-center space-x-1.5 transition shadow-xs"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <span>Filter</span>
                </button>
              </div>
            </div>

            {/* Table */}
            <div className="border border-border rounded-btn overflow-x-auto bg-card-inner">
              <table className="w-full text-left text-xs text-foreground">
                <thead className="bg-card text-[11px] uppercase font-semibold text-muted border-b border-border">
                  <tr>
                    <th className="px-3.5 py-2.5">Code</th>
                    <th className="px-3.5 py-2.5">System</th>
                    <th className="px-3.5 py-2.5">Description</th>
                    <th className="px-3.5 py-2.5">Fee / Charge</th>
                    <th className="px-3.5 py-2.5">Prior Auth</th>
                    <th className="px-3.5 py-2.5">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {loadingCodes ? (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-muted">
                        <Loader2 className="w-4 h-4 animate-spin mx-auto mb-1.5 text-accent" />
                        Loading codes from catalog...
                      </td>
                    </tr>
                  ) : codesData && codesData.items.length > 0 ? (
                    codesData.items.map((item, idx) => (
                      <tr key={idx} className="hover:bg-card/50">
                        <td className="px-3.5 py-2.5 font-mono font-bold text-accent">{item.code}</td>
                        <td className="px-3.5 py-2.5">
                          <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-card border border-border">
                            {item.coding_system}
                          </span>
                        </td>
                        <td className="px-3.5 py-2.5 max-w-xs truncate" title={item.short_description}>
                          {item.short_description}
                        </td>
                        <td className="px-3.5 py-2.5 font-mono tabular-nums">
                          {formatCharge(item.standard_charge)}
                        </td>
                        <td className="px-3.5 py-2.5">
                          {item.requires_prior_auth ? (
                            <span className="text-status-warning text-xs font-semibold">Yes</span>
                          ) : (
                            <span className="text-muted text-xs">No</span>
                          )}
                        </td>
                        <td className="px-3.5 py-2.5">
                          {item.is_active ? (
                            <span className="text-status-success text-xs font-semibold">Active</span>
                          ) : (
                            <span className="text-status-danger text-xs">Inactive</span>
                          )}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-muted">
                        No medical codes matching current filter. Upload a CSV to populate.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {codesData && (
              <div className="mt-3 text-[11px] text-muted flex justify-between items-center">
                <span>Showing {codesData.items.length} of {codesData.total} codes</span>
                <span>Page 1</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 3: Policies (RAG Knowledge & Policy Publishing Firewall) */}
      {activeTab === "policies" && (
        <div className="space-y-6">
          {/* Top Architecture Notice Banner */}
          <div className="bg-card border border-border rounded-card p-5 shadow-xs">
            <div className="flex items-center space-x-2.5 mb-3">
              <FileCheck className="w-5 h-5 text-status-warning" />
              <div>
                <h2 className="text-sm font-bold text-foreground">Policy Administration & Publishing Firewall</h2>
                <p className="text-[11px] text-muted">
                  Customer submissions are strictly read-only evidence. Only authorized administrators can stage and publish corporate policy PDFs into the Qdrant semantic vector index.
                </p>
              </div>
            </div>

            {policyUploadError && (
              <div className="p-3 bg-status-danger/10 border border-status-danger/20 text-status-danger rounded-btn text-xs flex items-center space-x-2 mb-3">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                <span>{policyUploadError}</span>
              </div>
            )}

            {policyUploadSuccess && (
              <div className="p-3 bg-status-warning/10 border border-status-warning/20 text-status-warning rounded-btn text-xs flex items-center space-x-2 mb-3">
                <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
                <div>
                  <div className="font-bold">Draft Policy Uploaded</div>
                  <div className="text-[11px] mt-0.5">{policyUploadSuccess}</div>
                </div>
              </div>
            )}

            {publishSuccess && (
              <div className="p-3 bg-status-success/10 border border-status-success/20 text-status-success rounded-btn text-xs flex items-center space-x-2 mb-3">
                <ShieldCheck className="w-4 h-4 flex-shrink-0" />
                <div>
                  <div className="font-bold">Policy Publishing Firewall Complete</div>
                  <div className="text-[11px] mt-0.5">{publishSuccess}</div>
                </div>
              </div>
            )}

            {publishError && (
              <div className="p-3 bg-status-danger/10 border border-status-danger/20 text-status-danger rounded-btn text-xs flex items-center space-x-2 mb-3">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                <span>{publishError}</span>
              </div>
            )}

            {/* Upload Draft Form */}
            <form onSubmit={handlePolicyUpload} className="space-y-3 mt-3 bg-card-inner p-4 rounded-card border border-border">
              <div className="text-xs font-bold uppercase tracking-wider text-muted mb-1 flex items-center space-x-1.5">
                <Plus className="w-3.5 h-3.5 text-accent" />
                <span>Stage New Policy Document (Phase 1 Draft)</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Policy Identifier *</label>
                  <input
                    type="text"
                    required
                    value={policyId}
                    onChange={(e) => setPolicyId(e.target.value)}
                    placeholder="e.g. POL-CORP-2026"
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground placeholder:text-muted text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 font-mono shadow-xs transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Plan Display Name *</label>
                  <input
                    type="text"
                    required
                    value={planName}
                    onChange={(e) => setPlanName(e.target.value)}
                    placeholder="e.g. MediShield Gold Health Plan"
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground placeholder:text-muted text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Version Tag *</label>
                  <input
                    type="text"
                    required
                    value={versionTag}
                    onChange={(e) => setVersionTag(e.target.value)}
                    placeholder="e.g. 2026.1"
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground placeholder:text-muted text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 font-mono shadow-xs transition-colors"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Policy Type</label>
                  <select
                    value={policyType}
                    onChange={(e) => setPolicyType(e.target.value)}
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs"
                  >
                    <option value="COMPREHENSIVE_HEALTH">Comprehensive Health</option>
                    <option value="EMERGENCY_ONLY">Emergency Only</option>
                    <option value="HIGH_DEDUCTIBLE">High Deductible Health Plan (HDHP)</option>
                    <option value="MEDICARE_ADVANTAGE">Medicare Advantage</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Effective Start Date *</label>
                  <input
                    type="date"
                    required
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-medium text-secondary mb-1">Effective End Date</label>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="w-full h-9 bg-card border border-border rounded-btn px-2.5 py-1.5 text-foreground text-xs focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-medium text-secondary mb-1">Authoritative Policy PDF Document *</label>
                <input
                  type="file"
                  required
                  accept="application/pdf,.pdf"
                  onChange={(e) => setPolicyPdfFile(e.target.files?.[0] || null)}
                  className="block w-full text-xs text-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded-btn file:border-0 file:text-xs file:font-semibold file:bg-card file:text-accent hover:file:bg-sky-pale cursor-pointer border border-border rounded-btn p-1 bg-card shadow-xs transition"
                />
              </div>

              <div className="flex justify-end pt-1">
                <button
                  type="submit"
                  disabled={uploadingPolicy || !policyPdfFile}
                  className="h-9 px-4 py-2 bg-[#397FA9] hover:bg-[#2C6688] disabled:opacity-50 text-white font-semibold rounded-btn transition-colors flex items-center space-x-1.5 text-xs shadow-xs"
                >
                  {uploadingPolicy ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Staging Document...</span>
                    </>
                  ) : (
                    <>
                      <Upload className="w-3.5 h-3.5" />
                      <span>Upload Draft Policy PDF</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>

          {/* Master Policies and Vector Index Registry Table */}
          <div className="bg-card border border-border rounded-card p-5 shadow-xs">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center space-x-1.5">
                  <Database className="w-3.5 h-3.5 text-accent" />
                  <span>Corporate Policies & Vector Index Registry</span>
                </h3>
                <p className="text-[11px] text-muted mt-0.5">
                  Published versions are indexed in Qdrant and actively queried by the PolicyCoverageAgent.
                </p>
              </div>
              <button
                onClick={fetchPolicies}
                className="px-2.5 py-1 bg-card-inner hover:bg-border text-foreground font-medium rounded-btn text-xs border border-border flex items-center space-x-1 transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                <span>Refresh</span>
              </button>
            </div>

            <div className="border border-border rounded-btn overflow-x-auto bg-card-inner">
              <table className="w-full text-left text-xs text-foreground">
                <thead className="bg-card text-[11px] uppercase font-semibold text-muted border-b border-border">
                  <tr>
                    <th className="px-3.5 py-2.5">Policy ID</th>
                    <th className="px-3.5 py-2.5">Plan Name</th>
                    <th className="px-3.5 py-2.5">Version</th>
                    <th className="px-3.5 py-2.5">Effective Window</th>
                    <th className="px-3.5 py-2.5">RAG Status</th>
                    <th className="px-3.5 py-2.5 text-right">Firewall Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {loadingPolicies ? (
                    <tr>
                      <td colSpan={6} className="text-center py-8 text-muted">
                        <Loader2 className="w-4 h-4 animate-spin mx-auto mb-1.5 text-accent" />
                        Loading corporate policies...
                      </td>
                    </tr>
                  ) : policies.length > 0 ? (
                    policies.map((p, idx) => {
                      const pKey = `${p.policy_id}:${p.version_tag}`;
                      const isPublishingThis = publishingKey === pKey;
                      return (
                        <tr key={idx} className="hover:bg-card/50">
                          <td className="px-3.5 py-2.5 font-mono font-bold text-foreground">{p.policy_id}</td>
                          <td className="px-3.5 py-2.5 max-w-xs truncate font-medium text-foreground" title={p.plan_name}>
                            {p.plan_name}
                          </td>
                          <td className="px-3.5 py-2.5 font-mono text-[11px]">{p.version_tag}</td>
                          <td className="px-3.5 py-2.5 text-[11px] text-muted font-mono">
                            {p.effective_start_date || "—"} to {p.effective_end_date || "Open"}
                          </td>
                          <td className="px-3.5 py-2.5">
                            {p.is_published ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-status-success/10 text-status-success border border-status-success/20">
                                <ShieldCheck className="w-3 h-3" />
                                <span>Published (Vectorized)</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-status-warning/10 text-status-warning border border-status-warning/20">
                                <Lock className="w-3 h-3" />
                                <span>Draft Quarantined</span>
                              </span>
                            )}
                          </td>
                          <td className="px-3.5 py-2.5 text-right">
                            {p.is_published ? (
                              <span className="text-[11px] text-muted font-mono">
                                {p.chunk_count > 0 ? `${p.chunk_count} Chunks` : "Vector Active"}
                              </span>
                            ) : (
                              <button
                                onClick={() => handlePublishPolicy(p.policy_id, p.version_tag)}
                                disabled={isPublishingThis}
                                className="px-2.5 py-1 bg-status-success/15 hover:bg-status-success/25 text-status-success border border-status-success/30 disabled:opacity-50 rounded-btn text-xs font-semibold transition-colors inline-flex items-center space-x-1"
                              >
                                {isPublishingThis ? (
                                  <>
                                    <Loader2 className="w-3 h-3 animate-spin" />
                                    <span>Vectorizing...</span>
                                  </>
                                ) : (
                                  <>
                                    <ShieldCheck className="w-3 h-3" />
                                    <span>Publish to Knowledge Base</span>
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
                      <td colSpan={6} className="text-center py-8 text-muted">
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
