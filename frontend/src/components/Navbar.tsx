"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Layers,
  Lock,
  LogOut,
  Moon,
  ShieldCheck,
  Sparkles,
  Sun,
  User,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";

export function Navbar() {
  const pathname = usePathname();
  const { user, logout, loading } = useAuth();
  const { theme, setTheme } = useTheme();

  const isClaimer = user?.role === "CLAIMER";
  const isReviewer = user?.role === "OPERATIONS_REVIEWER" || user?.role === "OPERATIONS_MANAGER";
  const isAdmin = user?.role === "ADMINISTRATOR";

  return (
    <header className="border-b border-border bg-card/90 backdrop-blur-md sticky top-0 z-50 shadow-xs transition-colors duration-150">
      <div className="max-w-[1680px] mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        {/* Left: Brand & Dynamic Role Navigation */}
        <div className="flex items-center space-x-6">
          <Link
            href={isClaimer ? "/claimer" : isAdmin ? "/admin" : "/"}
            className="flex items-center space-x-2.5 font-bold text-lg tracking-tight group"
          >
            <div className="p-1.5 rounded-btn bg-accent text-accent-fg shadow-xs transition">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <span className="font-extrabold tracking-tight text-foreground">
              MediShield
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-status-info/10 text-status-info border border-status-info/20 font-mono font-semibold">
              v1.0.0
            </span>
          </Link>

          {/* Role-Gated Navigation Links */}
          <nav className="hidden md:flex items-center space-x-1.5 text-sm font-medium">
            {/* CLAIMER NAVIGATION */}
            {isClaimer && (
              <Link
                href="/claimer"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                  pathname === "/claimer"
                    ? "bg-card-inner text-foreground border border-border font-semibold shadow-xs"
                    : "text-secondary hover:text-foreground hover:bg-card-inner"
                }`}
              >
                <ShieldCheck className={`w-4 h-4 ${pathname === "/claimer" ? "text-accent" : "text-muted"}`} />
                <span>Claimer Portal</span>
              </Link>
            )}

            {/* REVIEWER NAVIGATION */}
            {isReviewer && (
              <Link
                href="/"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                  pathname === "/"
                    ? "bg-card-inner text-foreground border border-border font-semibold shadow-xs"
                    : "text-secondary hover:text-foreground hover:bg-card-inner"
                }`}
              >
                <Layers className={`w-4 h-4 ${pathname === "/" ? "text-accent" : "text-muted"}`} />
                <span>Review Queue</span>
              </Link>
            )}

            {/* ADMINISTRATOR NAVIGATION */}
            {isAdmin && (
              <>
                <Link
                  href="/admin"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                    pathname === "/admin"
                      ? "bg-card-inner text-foreground border border-border font-semibold shadow-xs"
                      : "text-secondary hover:text-foreground hover:bg-card-inner"
                  }`}
                >
                  <Activity className={`w-4 h-4 ${pathname === "/admin" ? "text-accent" : "text-muted"}`} />
                  <span>Admin Console</span>
                </Link>
                <Link
                  href="/metrics"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                    pathname === "/metrics"
                      ? "bg-card-inner text-foreground font-semibold border border-border shadow-xs"
                      : "text-secondary hover:text-foreground hover:bg-card-inner"
                  }`}
                >
                  <Activity className={`w-4 h-4 ${pathname === "/metrics" ? "text-accent" : "text-muted"}`} />
                  <span>STP & Metrics</span>
                </Link>
                <Link
                  href="/"
                  className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                    pathname === "/"
                      ? "bg-card-inner text-foreground font-semibold border border-border shadow-xs"
                      : "text-secondary hover:text-foreground hover:bg-card-inner"
                  }`}
                >
                  <Layers className={`w-4 h-4 ${pathname === "/" ? "text-accent" : "text-muted"}`} />
                  <span>Review Queue</span>
                </Link>
              </>
            )}

            {/* 30-CASE BENCHMARK PORTAL */}
            {(isReviewer || isAdmin || !user) && (
              <Link
                href="/benchmark"
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-btn transition-all ${
                  pathname === "/benchmark"
                    ? "bg-card-inner text-foreground border border-border font-semibold shadow-xs"
                    : "text-secondary hover:text-foreground hover:bg-card-inner"
                }`}
              >
                <Sparkles className={`w-4 h-4 ${pathname === "/benchmark" ? "text-accent" : "text-muted"}`} />
                <span>30-Case Benchmark</span>
              </Link>
            )}
          </nav>
        </div>

        {/* Right: Auth Profile Status & Sign In/Out Controls */}
        <div className="flex items-center space-x-3">
          {/* Dual Segmented Theme Switcher */}
          <div className="flex items-center bg-card-inner p-0.5 rounded-btn border border-border text-xs shadow-xs">
            <button
              type="button"
              onClick={() => setTheme("light")}
              className={`flex items-center space-x-1 px-2.5 py-1 rounded-md text-[11px] font-semibold transition ${
                theme === "light"
                  ? "bg-card text-foreground shadow-xs border border-border"
                  : "text-muted hover:text-foreground"
              }`}
              title="Switch to Light Mode"
            >
              <Sun className="w-3.5 h-3.5 text-status-warning" />
              <span>Light</span>
            </button>
            <button
              type="button"
              onClick={() => setTheme("dark")}
              className={`flex items-center space-x-1 px-2.5 py-1 rounded-md text-[11px] font-semibold transition ${
                theme === "dark"
                  ? "bg-card text-foreground shadow-xs border border-border"
                  : "text-muted hover:text-foreground"
              }`}
              title="Switch to Dark Mode"
            >
              <Moon className="w-3.5 h-3.5 text-accent" />
              <span>Dark</span>
            </button>
          </div>

          {loading ? (
            <div className="text-xs text-muted animate-pulse">Verifying session...</div>
          ) : user ? (
            <>
              {/* Role Pill */}
              <div className="hidden sm:flex items-center space-x-2 text-xs px-3 py-1.5 rounded-full border bg-card-inner text-foreground border-border">
                <Lock className="w-3.5 h-3.5 shrink-0 text-muted" />
                <span>
                  Role: <strong className="font-mono">{user.role}</strong>
                </span>
              </div>

              {/* Username Badge */}
              <div className="flex items-center space-x-1.5 text-xs text-foreground bg-card-inner px-2.5 py-1.5 rounded-btn border border-border">
                <User className="w-3.5 h-3.5 text-muted" />
                <span className="font-medium font-mono">{user.username}</span>
              </div>

              {/* Sign Out Button */}
              <button
                type="button"
                onClick={logout}
                className="flex items-center space-x-1 text-xs text-secondary hover:text-status-danger bg-card-inner hover:bg-border px-3 py-1.5 rounded-btn border border-border transition-colors"
                title="Sign out of current account"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Sign Out</span>
              </button>
            </>
          ) : (
            <Link
              href="/login"
              className="flex items-center space-x-1.5 text-xs font-semibold text-accent-fg bg-accent hover:bg-accent-hover px-3.5 py-1.5 rounded-btn transition shadow-xs"
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
