"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Database,
  FileCheck2,
  FileText,
  Layers,
  Lock,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Shield,
  ShieldCheck,
  Sparkles,
  Sun,
  User,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { MediShieldLogo } from "@/components/MediShieldLogo";

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout, loading: authLoading } = useAuth();
  const { theme, setTheme } = useTheme();

  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  // Restore sidebar collapse state
  useEffect(() => {
    try {
      const stored = localStorage.getItem("medishield_sidebar_collapsed");
      if (stored === "true") {
        setIsCollapsed(true);
      }
    } catch {}
  }, []);

  const toggleCollapse = () => {
    const next = !isCollapsed;
    setIsCollapsed(next);
    try {
      localStorage.setItem("medishield_sidebar_collapsed", String(next));
    } catch {}
  };

  const isClaimer = user?.role === "CLAIMER";
  const isReviewer = user?.role === "OPERATIONS_REVIEWER" || user?.role === "OPERATIONS_MANAGER";
  const isAdmin = user?.role === "ADMINISTRATOR";

  // Build breadcrumb and active title
  const getPageInfo = () => {
    if (pathname === "/") return { title: "Operations Case Queue", subtitle: "Real-time human-in-the-loop review queue & automated intake pipeline" };
    if (pathname.startsWith("/cases/")) return { title: "Claim Reviewer", subtitle: "Line-by-line clinical alignment & deterministic adjudication" };
    if (pathname === "/benchmark") return { title: "30-Case Benchmark Suite", subtitle: "Cluster dataset accuracy & autonomous evaluation" };
    if (pathname === "/metrics") return { title: "Operational STP & Telemetry", subtitle: "Real-time throughput, Straight-Through-Processing & latency" };
    if (pathname === "/admin") return { title: "Admin & Policy Catalog", subtitle: "Medical code governance & RAG vector policy firewall" };
    if (pathname === "/claimer") return { title: "Patient & Provider Portal", subtitle: "Multi-document claims intake & live adjudication tracking" };
    if (pathname === "/login") return { title: "Authentication Portal", subtitle: "Role-based access control (RBAC) sign in" };
    return { title: "MediShield Console", subtitle: "Enterprise Adjudication Platform" };
  };

  const pageInfo = getPageInfo();

  // Navigation Items
  interface NavItem {
    label: string;
    href: string;
    icon: React.ComponentType<{ className?: string }>;
    visible: boolean;
    badge?: string;
  }

  const navItems: NavItem[] = [
    {
      label: "Case Queue",
      href: "/",
      icon: Layers,
      visible: !isClaimer,
    },
    {
      label: "30-Case Benchmark",
      href: "/benchmark",
      icon: Sparkles,
      visible: !isClaimer,
    },
    {
      label: "STP & Metrics",
      href: "/metrics",
      icon: Activity,
      visible: isReviewer || isAdmin || !user,
    },
    {
      label: "Admin & Catalog",
      href: "/admin",
      icon: Database,
      visible: isAdmin || !user,
    },
    {
      label: "Claimer Portal",
      href: "/claimer",
      icon: FileText,
      visible: isClaimer || !user,
    },
  ];

  const visibleNav = navItems.filter((i) => i.visible);

  return (
    <div className="min-h-screen flex flex-col bg-page text-foreground transition-colors">
      {/* Mobile Backdrop Drawer */}
      {isMobileOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs md:hidden"
          onClick={() => setIsMobileOpen(false)}
        >
          <div
            className="fixed inset-y-0 left-0 w-72 bg-card border-r border-border p-5 flex flex-col justify-between"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <Link
                  href={isClaimer ? "/claimer" : "/"}
                  onClick={() => setIsMobileOpen(false)}
                  className="group flex items-center"
                >
                  <MediShieldLogo size="md" showWordmark={true} showBadge={true} badgeText="v1.0" />
                </Link>
                <button
                  type="button"
                  onClick={() => setIsMobileOpen(false)}
                  className="p-1 rounded-btn text-muted hover:text-foreground"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Mobile Navigation List */}
              <nav className="space-y-1">
                {visibleNav.map((item) => {
                  const isActive = pathname === item.href;
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setIsMobileOpen(false)}
                      className={`flex items-center space-x-3 px-3 py-2.5 rounded-btn text-xs transition-all duration-180 active:scale-[0.99] ${
                        isActive
                          ? "bg-sky-soft text-sky-deep font-bold border-l-[3px] border-sky-med shadow-xs"
                          : "text-secondary hover:text-pink-deep hover:bg-pink-hover border-l-[3px] border-transparent hover:border-pink-border"
                      }`}
                    >
                      <Icon className={`w-4 h-4 shrink-0 transition-colors ${isActive ? "text-sky-deep" : "text-muted group-hover:text-pink-accent"}`} />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
              </nav>
            </div>

            {/* Mobile Footer Status */}
            <div className="pt-4 border-t border-border space-y-3">
              <div className="flex items-center space-x-2 text-[11px] text-foreground-muted">
                <span className="w-2 h-2 rounded-full bg-status-success animate-pulse" />
                <span>Pipeline Online · HIPAA Safe</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Layout Container */}
      <div className="flex-1 flex">
        {/* Desktop Collapsible Sidebar */}
        <aside
          className={`hidden md:flex flex-col justify-between border-r border-border bg-card transition-all duration-200 shrink-0 sticky top-0 h-screen z-30 ${
            isCollapsed ? "w-18" : "w-64"
          }`}
        >
          {/* Top Brand & Links */}
          <div className="p-4 space-y-6">
            {/* Brand Logo */}
            <div className={`flex items-center ${isCollapsed ? "justify-center" : "justify-start"}`}>
              <Link
                href={isClaimer ? "/claimer" : "/"}
                className="group flex items-center"
                title="MediShield Enterprise Adjudication"
              >
                <MediShieldLogo
                  size={isCollapsed ? "sm" : "md"}
                  showWordmark={!isCollapsed}
                  showBadge={!isCollapsed}
                  badgeText="v1.0"
                />
              </Link>
            </div>

            {/* Nav List */}
            <nav className="space-y-1">
              {!isCollapsed && (
                <div className="text-[10px] uppercase tracking-wider font-semibold text-muted px-3 pb-1.5">
                  Workspace
                </div>
              )}
              {visibleNav.map((item) => {
                const isActive = pathname === item.href;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`flex items-center ${
                      isCollapsed ? "justify-center px-2 py-2 rounded-btn" : "space-x-3 px-3 py-2 rounded-r-btn rounded-l-none"
                    } text-xs transition-all duration-180 active:scale-[0.99] group ${
                      isActive
                        ? "bg-sky-soft text-sky-deep font-bold border-l-[3px] border-sky-med shadow-xs"
                        : "text-secondary hover:text-pink-deep hover:bg-pink-hover border-l-[3px] border-transparent hover:border-pink-border"
                    }`}
                    title={isCollapsed ? item.label : undefined}
                  >
                    <Icon className={`w-4 h-4 shrink-0 transition-colors ${isActive ? "text-sky-deep" : "text-muted group-hover:text-pink-accent"}`} />
                    {!isCollapsed && <span className="truncate">{item.label}</span>}
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* Bottom Sidebar Utility Area */}
          <div className="p-3 border-t border-border space-y-2 bg-page/50">
            {/* System Status indicator */}
            {!isCollapsed ? (
              <div className="flex items-center justify-between px-2.5 py-1.5 rounded-btn bg-card border border-border text-[11px] shadow-xs">
                <div className="flex items-center space-x-2 min-w-0">
                  <span className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse shrink-0" />
                  <span className="text-secondary truncate font-medium">Pipeline Active</span>
                </div>
                <span className="text-[10px] font-mono text-muted shrink-0">HIPAA Safe</span>
              </div>
            ) : (
              <div className="flex justify-center py-1" title="Pipeline Active · HIPAA Protected">
                <span className="w-2 h-2 rounded-full bg-status-success animate-pulse" />
              </div>
            )}

            {/* Collapse / Expand Toggle Button */}
            <button
              type="button"
              onClick={toggleCollapse}
              className={`w-full flex items-center ${
                isCollapsed ? "justify-center" : "justify-between px-2.5"
              } py-1.5 rounded-btn text-xs text-secondary hover:text-pink-deep hover:bg-pink-hover border border-transparent hover:border-pink-border transition-all duration-180 active:scale-[0.98]`}
              title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {!isCollapsed && <span className="text-[11px] font-medium">Collapse Sidebar</span>}
              {isCollapsed ? (
                <PanelLeftOpen className="w-4 h-4 text-muted group-hover:text-pink-accent" />
              ) : (
                <PanelLeftClose className="w-4 h-4 text-muted group-hover:text-pink-accent" />
              )}
            </button>
          </div>
        </aside>

        {/* Content Pane Wrapper */}
        <div className="flex-1 flex flex-col min-w-0 overflow-x-hidden">
          {/* Top Header Bar */}
          <header className="border-b border-border bg-card/90 backdrop-blur-md sticky top-0 z-20 h-16 shrink-0 px-4 sm:px-6 lg:px-8 flex items-center justify-between gap-4">
            {/* Left: Mobile Toggle, MediShield Brand Logo & Context Breadcrumbs */}
            <div className="flex items-center space-x-3.5 min-w-0">
              <button
                type="button"
                onClick={() => setIsMobileOpen(true)}
                className="h-8 w-8 inline-flex items-center justify-center rounded-btn md:hidden text-secondary hover:text-pink-deep hover:bg-pink-hover border border-border hover:border-pink-border transition-colors"
                aria-label="Open navigation menu"
              >
                <Menu className="w-4 h-4" />
              </button>

              {/* MediShield Signature Brand Anchor - Mobile Only (Desktop sidebar already anchors brand) */}
              <Link
                href={isClaimer ? "/claimer" : "/"}
                className="group flex items-center shrink-0 md:hidden"
                title="MediShield Adjudication Console"
              >
                <MediShieldLogo size="sm" showWordmark={true} showBadge={false} />
              </Link>

              {/* Vertical Divider - Mobile Only */}
              <div className="h-5 w-px bg-border shrink-0 md:hidden" />

              {/* Page Breadcrumb Context */}
              <div className="min-w-0 flex items-center space-x-1.5 text-xs">
                <span className="font-semibold text-foreground text-sm truncate">{pageInfo.title}</span>
                {pageInfo.subtitle && (
                  <span className="hidden xl:inline text-secondary truncate">
                    &bull; {pageInfo.subtitle}
                  </span>
                )}
              </div>
            </div>

            {/* Right: Security Pill, Segmented Theme Switcher & Auth */}
            <div className="flex items-center space-x-2 sm:space-x-3 shrink-0">
              {/* Deterministic Safety Gate Badge */}
              <div className="hidden lg:inline-flex items-center space-x-1.5 h-8 px-3 rounded-full text-[11px] font-medium bg-sky-soft text-sky-deep border border-sky/30 shadow-xs">
                <Shield className="w-3.5 h-3.5 text-status-success shrink-0" />
                <span>Deterministic Safety Gate</span>
              </div>

              {/* Segmented Dual Theme Switcher */}
              <div className="h-8 inline-flex items-center bg-card-inner p-0.5 rounded-btn border border-border text-xs shadow-xs">
                <button
                  type="button"
                  onClick={() => setTheme("light")}
                  className={`h-7 inline-flex items-center space-x-1 px-2.5 rounded-btn text-[11px] font-semibold transition-all duration-180 ${
                    theme === "light"
                      ? "bg-card text-foreground border border-border shadow-xs"
                      : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                  }`}
                  title="Switch to Light Mode"
                >
                  <Sun className="w-3 h-3 text-status-warning" />
                  <span className="hidden sm:inline">Light</span>
                </button>
                <button
                  type="button"
                  onClick={() => setTheme("dark")}
                  className={`h-7 inline-flex items-center space-x-1 px-2.5 rounded-btn text-[11px] font-semibold transition-all duration-180 ${
                    theme === "dark"
                      ? "bg-card text-foreground border border-border shadow-xs"
                      : "text-muted hover:text-pink-deep hover:bg-pink-hover"
                  }`}
                  title="Switch to Dark Mode"
                >
                  <Moon className="w-3 h-3 text-sky-med" />
                  <span className="hidden sm:inline">Dark</span>
                </button>
              </div>

              {/* Role & User State */}
              {authLoading ? (
                <div className="h-8 inline-flex items-center text-[11px] text-muted animate-pulse">Checking auth...</div>
              ) : user ? (
                <div className="flex items-center space-x-2">
                  <div className="hidden sm:inline-flex items-center space-x-1 h-8 px-2.5 rounded-full text-[10px] font-mono bg-card-inner text-secondary border border-border shadow-xs">
                    <Lock className="w-3 h-3 text-muted shrink-0" />
                    <span>{user.role}</span>
                  </div>

                  <div className="h-8 inline-flex items-center space-x-1.5 text-xs text-foreground bg-card-inner px-2.5 rounded-btn border border-border shadow-xs">
                    <User className="w-3.5 h-3.5 text-muted" />
                    <span className="font-mono font-medium text-[11px]">{user.username}</span>
                  </div>

                  <button
                    type="button"
                    onClick={logout}
                    className="h-8 w-8 inline-flex items-center justify-center rounded-btn text-muted hover:text-rose-deep hover:bg-rose-blush border border-transparent hover:border-rose-med/30 transition-all duration-180 active:scale-[0.98]"
                    title="Sign Out"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                  </button>
                </div>
              ) : (
                <Link
                  href="/login"
                  className="h-8 px-3 inline-flex items-center space-x-1.5 rounded-btn bg-[#397FA9] hover:bg-[#2C6688] text-white text-xs font-semibold shadow-xs transition-all duration-180 active:scale-[0.98]"
                >
                  <Lock className="w-3.5 h-3.5" />
                  <span>Sign In</span>
                </Link>
              )}
            </div>
          </header>

          {/* Page Body Viewport */}
          <main className="flex-1 max-w-[1680px] w-full mx-auto p-4 sm:p-6 lg:p-8">
            {children}
          </main>

          {/* Understated Minimal Enterprise Footer */}
          <footer className="border-t border-border bg-card py-3.5 px-6 text-center text-xs text-foreground-muted flex flex-col sm:flex-row items-center justify-between gap-2">
            <div>
              MediShield &bull; Canonical Multi-Agent Intake & Adjudication Platform &bull; HIPAA Compliant
            </div>
            <div className="font-mono text-[11px] text-foreground-muted">
              ADR-015 Spec Standard &bull; Audit Trail Intact
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}

