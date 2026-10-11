import type { Metadata } from "next";
import Link from "next/link";
import { ShieldCheck, Activity, Layers, Lock } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "MediShield | Operations Adjudication Console",
  description: "Enterprise Multi-Agent Document Intake & Adjudication System",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 min-h-screen flex flex-col antialiased">
        {/* Header / Navigation Bar */}
        <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div className="flex items-center space-x-6">
              <Link href="/" className="flex items-center space-x-3 text-cyan-400 font-bold text-lg tracking-tight">
                <ShieldCheck className="w-7 h-7 text-cyan-400" />
                <span className="text-white">Medi<span className="text-cyan-400">Shield</span></span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800 font-mono">
                  v1.0.0
                </span>
              </Link>
              <nav className="flex items-center space-x-4 text-sm font-medium">
                <Link
                  href="/"
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-md hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"
                >
                  <Layers className="w-4 h-4 text-slate-400" />
                  <span>Review Queue</span>
                </Link>
                <Link
                  href="/metrics"
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-md hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"
                >
                  <Activity className="w-4 h-4 text-slate-400" />
                  <span>STP & Metrics</span>
                </Link>
              </nav>
            </div>

            <div className="flex items-center space-x-4">
              <div className="flex items-center space-x-2 text-xs text-slate-400 bg-slate-800/80 px-3 py-1.5 rounded-full border border-slate-700">
                <Lock className="w-3.5 h-3.5 text-emerald-400" />
                <span>Role: <strong className="text-slate-200">OPERATIONS_REVIEWER</strong></span>
              </div>
              <div className="flex items-center space-x-1.5 text-xs text-emerald-400">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                <span>Control Plane Online</span>
              </div>
            </div>
          </div>
        </header>

        {/* Main Content Area */}
        <main className="flex-1 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full">
          {children}
        </main>

        {/* Footer */}
        <footer className="border-t border-slate-800/80 bg-slate-900/40 py-4 text-center text-xs text-slate-500">
          MediShield Multi-Agent Intake System &bull; Canonical Architecture Spec 01–12 &bull; HIPAA Compliance Protected
        </footer>
      </body>
    </html>
  );
}

