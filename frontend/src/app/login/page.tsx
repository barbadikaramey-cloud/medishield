"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  KeyRound,
  Layers,
  Loader2,
  Lock,
  ShieldCheck,
  User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { MediShieldLogo } from "@/components/MediShieldLogo";

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const personas = [
    {
      id: "claimer",
      title: "Claimer Portal",
      roleLabel: "CLAIMER",
      subtitle: "Patients & Healthcare Providers",
      description: "Submit multi-file claims, track live processing, and view patient-safe settlement determinations.",
      user: "claimer",
      pass: "claimer123",
      icon: ShieldCheck,
      color: "border-border hover:border-accent bg-card text-foreground",
      target: "/claimer",
    },
    {
      id: "reviewer",
      title: "Reviewer Console",
      roleLabel: "OPERATIONS_REVIEWER",
      subtitle: "Claims Adjusters & Medical Reviewers",
      description: "Adjudicate escalated claims (P4/P5/P7), inspect OCR bounding boxes, and confirm or override decisions.",
      user: "reviewer",
      pass: "reviewer123",
      icon: Layers,
      color: "border-border hover:border-accent bg-card text-foreground",
      target: "/",
    },
    {
      id: "admin",
      title: "Admin Console",
      roleLabel: "ADMINISTRATOR",
      subtitle: "System & Actuarial Governance",
      description: "Manage medical terminology catalogs (CPT/ICD CSVs), ingest master policies, and review STP metrics.",
      user: "admin",
      pass: "admin123",
      icon: Activity,
      color: "border-border hover:border-accent bg-card text-foreground",
      target: "/admin",
    },
  ];

  const handleSelectPreset = (user: string, pass: string) => {
    setUsername(user);
    setPassword(pass);
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError("Please provide both username and password.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const authUser = await login(username, password);
      if (authUser.role === "CLAIMER") {
        router.push("/claimer");
      } else if (authUser.role === "ADMINISTRATOR") {
        router.push("/admin");
      } else {
        router.push("/");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Invalid credentials. Please verify your username and password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      {/* Brand Header */}
      <div className="text-center mb-8 space-y-2">
        <div className="flex justify-center mb-2">
          <MediShieldLogo size="xl" showWordmark={true} showBadge={true} badgeText="Enterprise" />
        </div>
        <p className="text-xs sm:text-sm text-secondary max-w-lg mx-auto">
          Enterprise Role-Based Access Governance. Select a persona preset or enter your credentials.
        </p>
      </div>

      {/* Preset Persona Quick Selector */}
      <div className="mb-8">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted mb-3 text-center">
          Quick Demo Login &bull; Select a Persona
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {personas.map((p) => {
            const Icon = p.icon;
            const isSelected = username === p.user;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => handleSelectPreset(p.user, p.pass)}
                className={`text-left p-4 rounded-card border transition-all duration-180 ${
                  isSelected
                    ? "ring-2 ring-sky/50 border-sky-med bg-sky-soft text-foreground shadow-xs"
                    : "border-border bg-card text-foreground hover:bg-pink-hover/60 hover:border-pink-border shadow-xs hover:shadow-card-hover hover:-translate-y-0.5"
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-bold border ${isSelected ? "bg-[#397FA9] text-white border-transparent" : "bg-card-inner text-muted border-border"}`}>
                    {p.roleLabel}
                  </span>
                  <Icon className={`w-4 h-4 ${isSelected ? "text-sky-deep" : "text-muted"}`} />
                </div>
                <div className="font-bold text-foreground text-sm mb-0.5">{p.title}</div>
                <div className="text-xs text-secondary font-medium mb-1.5">{p.subtitle}</div>
                <div className="text-[11px] text-muted line-clamp-2 leading-relaxed">
                  {p.description}
                </div>
                <div className="mt-3 pt-2 border-t border-border flex items-center justify-between text-xs font-mono text-secondary">
                  <span>User: <strong className="text-foreground">{p.user}</strong></span>
                  <span className="text-accent font-sans flex items-center gap-1 text-[11px] font-semibold">
                    Use Preset <ArrowRight className="w-3 h-3" />
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Login Card */}
      <div className="max-w-md mx-auto bg-card border border-border rounded-card p-6 sm:p-7 shadow-xs">
        <div className="flex items-center space-x-2 text-foreground font-semibold mb-5 pb-3 border-b border-border text-sm">
          <KeyRound className="w-4 h-4 text-accent" />
          <span>Enter Credentials</span>
        </div>

        {error && (
          <div className="mb-5 p-3 bg-rose-blush border border-rose-med/30 rounded-btn flex items-start space-x-2 text-rose-deep text-xs shadow-xs">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-deep" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-foreground mb-1 flex items-center justify-between">
              <span>Username</span>
              <span className="text-[10px] text-muted font-mono">claimer / reviewer / admin</span>
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-muted">
                <User className="w-3.5 h-3.5" />
              </div>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter username"
                required
                className="w-full h-9 pl-9 pr-3 py-1.5 bg-card border border-border rounded-btn text-xs text-foreground placeholder:text-muted focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-foreground mb-1 flex items-center justify-between">
              <span>Password</span>
              <span className="text-[10px] text-muted font-mono">claimer123 / reviewer123 / admin123</span>
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-muted">
                <Lock className="w-3.5 h-3.5" />
              </div>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                required
                className="w-full h-9 pl-9 pr-3 py-1.5 bg-card border border-border rounded-btn text-xs text-foreground placeholder:text-muted focus:outline-none focus:border-sky focus:ring-2 focus:ring-sky/30 shadow-xs transition-colors"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full h-9 py-2 px-4 bg-[#397FA9] hover:bg-[#2C6688] text-white rounded-btn text-xs font-semibold flex items-center justify-center space-x-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-xs"
          >
            {loading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Authenticating...</span>
              </>
            ) : (
              <>
                <span>Sign In to MediShield</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </>
            )}
          </button>
        </form>

        <div className="mt-5 pt-3 border-t border-border text-center">
          <p className="text-[11px] text-muted">
            Protected by MediShield RBAC & HIPAA Authentication Subsystem.
          </p>
        </div>
      </div>
    </div>
  );
}
