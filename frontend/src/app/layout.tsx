import type { Metadata } from "next";
import { AuthProvider } from "@/lib/auth";
import { Navbar } from "@/components/Navbar";
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
        <AuthProvider>
          {/* Dynamic Role-Gated Navigation Bar */}
          <Navbar />

          {/* Main Content Area */}
          <main className="flex-1 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 w-full">
            {children}
          </main>

          {/* Footer */}
          <footer className="border-t border-slate-800/80 bg-slate-900/40 py-4 text-center text-xs text-slate-500">
            MediShield Multi-Agent Intake System &bull; Canonical Architecture Spec 01–13 &bull; HIPAA Compliance Protected
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
