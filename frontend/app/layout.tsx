import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Northwind Expense Pre-Review",
  description: "Finance reviewer copilot for expense submissions",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b bg-white">
          <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-6">
            <Link href="/" className="font-semibold text-slate-900">
              Northwind · Pre-Review
            </Link>
            <nav className="flex items-center gap-4 text-sm text-slate-600">
              <Link href="/" className="hover:text-slate-900">New / Employees</Link>
              <Link href="/history" className="hover:text-slate-900">History</Link>
              <Link href="/qa" className="hover:text-slate-900">Policy Q&amp;A</Link>
            </nav>
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
