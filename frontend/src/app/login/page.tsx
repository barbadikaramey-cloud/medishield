"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  FileText,
  KeyRound,
  Layers,
  Loader2,
  Lock,
  ShieldCheck,
  User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";

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
      color: "border-cyan-500/40 hover:border-cyan-400 bg-cyan-950/20 text-cyan-400",
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
      color: "border-emerald-500/40 hover:border-emerald-400 bg-emerald-950/20 text-emerald-400",
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
      color: "border-purple-500/40 hover:border-purple-400 bg-purple-950/20 text-purple-400",
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
    } catch (err: any) {
      setError(err.message || "Invalid credentials. Please verify your username and password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      {/* Brand Header */}
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center p-3 bg-cyan-950/40 border border-cyan-800/60 rounded-2xl mb-4 shadow-lg">
          <ShieldCheck className="w-10 h-10 text-cyan-400" />
        </div>
        <h1 className="text-3xl font-extrabold text-white tracking-tight sm:text-4xl">
          Medi<span className="text-cyan-400">Shield</span> Access Control
        </h1>
        <p className="mt-2 text-sm text-slate-400 max-w-lg mx-auto">
          Enterprise Role-Based Access Governance. Select a user persona or enter your enterprise credentials below.
        </p>
      </div>

      {/* Preset Persona Quick Selector */}
      <div className="mb-8">
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3 text-center">
          Quick Demo Login &bull; Select a Persona
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {personas.map((p) => {
            const Icon = p.icon;
            const isSelected = username === p.user;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => handleSelectPreset(p.user, p.pass)}
                className={`text-left p-4 rounded-xl border transition-all ${p.color} ${
                  isSelected ? "ring-2 ring-cyan-400 shadow-lg scale-[1.02]" : "opacity-90 hover:opacity-100"
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs px-2 py-0.5 rounded-full bg-slate-900 font-mono font-bold">
                    {p.roleLabel}
                  </span>
                  <Icon className="w-5 h-5" />
                </div>
                <div className="font-bold text-white text-base mb-1">{p.title}</div>
                <div className="text-xs text-slate-300 font-medium mb-2">{p.subtitle}</div>
                <div className="text-xs text-slate-400 line-clamp-2 leading-relaxed">
                  {p.description}
                </div>
                <div className="mt-3 pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono text-slate-300">
                  <span>User: <strong className="text-white">{p.user}</strong></span>
                  <span className="text-cyan-400 font-sans flex items-center gap-1 text-[11px]">
                    Use Preset <ArrowRight className="w-3 h-3" />
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Login Card */}
      <div className="max-w-md mx-auto bg-slate-900/90 border border-slate-800 rounded-2xl p-6 sm:p-8 shadow-2xl backdrop-blur">
        <div className="flex items-center space-x-2 text-white font-semibold mb-6 pb-3 border-b border-slate-800">
          <KeyRound className="w-5 h-5 text-cyan-400" />
          <span>Enter Credentials</span>
        </div>

        {error && (
          <div className="mb-6 p-3 bg-red-950/40 border border-red-800/80 rounded-lg flex items-start space-x-2 text-red-300 text-xs">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5 flex items-center justify-between">
              <span>Username</span>
              <span className="text-[11px] text-slate-500 font-mono">claimer / reviewer / admin</span>
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                <User className="w-4 h-4" />
              </div>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter username"
                required
                className="w-full pl-10 pr-4 py-2.5 bg-slate-950 border border-slate-800 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5 flex items-center justify-between">
              <span>Password</span>
              <span className="text-[11px] text-slate-500 font-mono">claimer123 / reviewer123 / admin123</span>
            </label>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
                <Lock className="w-4 h-4" />
              </div>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                required
                className="w-full pl-10 pr-4 py-2.5 bg-slate-950 border border-slate-800 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 transition-colors"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-sm font-semibold flex items-center justify-center space-x-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-md shadow-cyan-950"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Authenticating...</span>
              </>
            ) : (
              <>
                <span>Sign In to MediShield</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </form>

        <div className="mt-6 pt-4 border-t border-slate-800/80 text-center">
          <p className="text-[11px] text-slate-500">
            Protected by MediShield RBAC & HIPAA Authentication Subsystem.
          </p>
        </div>
      </div>
    </div>
  );
}

