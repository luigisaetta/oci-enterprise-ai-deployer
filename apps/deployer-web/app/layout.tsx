/*
 * Author: L. Saetta
 * Version: 0.1.0
 * Last modified: 2026-04-30
 * License: MIT
 */

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OCI Enterprise AI Deployer",
  description: "Web console for OCI Enterprise AI deployment files.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
