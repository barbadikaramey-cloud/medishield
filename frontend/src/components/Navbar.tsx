"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Layers,
  Lock,
  LogOut,
  ShieldCheck,
  Sparkles,
  User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";

export function Navbar() {
  const pathname = usePathname();
  const { user, logout, loading } = useAuth();

  const isClaimer = user?.role === "CLAIMER";
  const isReviewer = user?.role === "OPERATIONS_REVIEWER" || user?.role === "OPERATIONS_MANAGER";
  const isAdmin = user?.role === "ADMINISTRATOR";

  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Left: Brand & Dynamic Role Navigation */}
        <div className="flex items-center space-x-6">
          <Link
            href={isClaimer ? "/claimer" : isAdmin ? "/admin" : "/"}
            className="flex items-center space-x-3 text-cyan-400 font-bold text-lg tracking-tight"
          >
            <ShieldCheck className="w-7 h-7 text-cyan-400" />
            <span className="text-white">
              Medi<span className="text-cyan-400">Shield</span>
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800 font-mono">
              v1.0.0
            </span>
          </Link>

          {/* Role-Gated Navigation Links */}
          <nav className="hidden md:flex items-center space-x-2 text-sm font-medium">
            {/* CLAIMER NAVIGATION */}
            {isClaimer && (
              <Link
                href="/claimer"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                  pathname === "/claimer"
                    ? "bg-cyan-950/80 text-cyan-400 border border-cyan-800/60 font-semibold"
                    : "hover:bg-slate-800 text-slate-300 hover:text-white"
                }`}
              >
                <ShieldCheck className="w-4 h-4 text-cyan-400" />
                <span>Claimer Portal</span>
              </Link>
            )}

            {/* REVIEWER NAVIGATION */}
            {isReviewer && (
              <Link
                href="/"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                  pathname === "/"
                    ? "bg-emerald-950/80 text-emerald-400 border border-emerald-800/60 font-semibold"
                    : "hover:bg-slate-800 text-slate-300 hover:text-white"
                }`}
              >
                <Layers className="w-4 h-4 text-emerald-400" />
                <span>Review Queue</span>
              </Link>
            )}

            {/* ADMINISTRATOR NAVIGATION */}
            {isAdmin && (
              <>
                <Link
                  href="/admin"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                    pathname === "/admin"
                      ? "bg-purple-950/80 text-purple-400 border border-purple-800/60 font-semibold"
                      : "hover:bg-slate-800 text-slate-300 hover:text-white"
                  }`}
                >
                  <Activity className="w-4 h-4 text-purple-400" />
                  <span>Admin Console</span>
                </Link>
                <Link
                  href="/metrics"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                    pathname === "/metrics"
                      ? "bg-slate-800 text-white font-semibold"
                      : "hover:bg-slate-800 text-slate-300 hover:text-white"
                  }`}
                >
                  <Activity className="w-4 h-4 text-slate-400" />
                  <span>STP & Metrics</span>
                </Link>
                <Link
                  href="/"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                    pathname === "/"
                      ? "bg-slate-800 text-white font-semibold"
                      : "hover:bg-slate-800 text-slate-300 hover:text-white"
                  }`}
                >
                  <Layers className="w-4 h-4 text-slate-400" />
                  <span>Review Queue</span>
                </Link>
              </>
            )}

            {/* 30-CASE BENCHMARK PORTAL */}
            {(isReviewer || isAdmin || !user) && (
              <Link
                href="/benchmark"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md transition-colors ${
                  pathname === "/benchmark"
                    ? "bg-cyan-950/80 text-cyan-400 border border-cyan-800/60 font-semibold"
                    : "hover:bg-slate-800 text-slate-300 hover:text-white"
                }`}
              >
                <Sparkles className="w-4 h-4 text-cyan-400" />
                <span>30-Case Benchmark</span>
              </Link>
            )}
          </nav>
        </div>

        {/* Right: Auth Profile Status & Sign In/Out Controls */}
        <div className="flex items-center space-x-3">
          {loading ? (
            <div className="text-xs text-slate-500 animate-pulse">Verifying session...</div>
          ) : user ? (
            <>
              {/* Role Pill */}
              <div
                className={`hidden sm:flex items-center space-x-2 text-xs px-3 py-1.5 rounded-full border ${
                  isClaimer
                    ? "bg-cyan-950/40 text-cyan-300 border-cyan-800/60"
                    : isAdmin
                    ? "bg-purple-950/40 text-purple-300 border-purple-800/60"
                    : "bg-emerald-950/40 text-emerald-300 border-emerald-800/60"
                }`}
              >
                <Lock className="w-3.5 h-3.5 shrink-0" />
                <span>
                  Role: <strong className="font-mono">{user.role}</strong>
                </span>
              </div>

              {/* Username Badge */}
              <div className="flex items-center space-x-1.5 text-xs text-slate-300 bg-slate-800 px-2.5 py-1.5 rounded-lg border border-slate-700">
                <User className="w-3.5 h-3.5 text-slate-400" />
                <span className="font-medium font-mono">{user.username}</span>
              </div>

              {/* Sign Out Button */}
              <button
                type="button"
                onClick={logout}
                className="flex items-center space-x-1 text-xs text-slate-400 hover:text-red-400 bg-slate-900 hover:bg-slate-800 px-3 py-1.5 rounded-lg border border-slate-800 transition-colors"
                title="Sign out of current account"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Sign Out</span>
              </button>
            </>
          ) : (
            <Link
              href="/login"
              className="flex items-center space-x-1.5 text-xs font-semibold text-white bg-cyan-600 hover:bg-cyan-500 px-3.5 py-1.5 rounded-lg transition-colors shadow-md shadow-cyan-950"
            >
              <Lock className="w-3.5 h-3.5" />
              <span>Sign In</span>
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}

