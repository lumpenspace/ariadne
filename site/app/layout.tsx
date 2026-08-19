import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://ariadne.hyperplex.org"),
  title: "ariadne",
  description:
    "Archive-first tweet conversation reconstruction for LLM input and Raft-ready retrieval documents.",
  icons: {
    icon: "/og.png",
  },
  openGraph: {
    title: "ariadne",
    description:
      "Rebuild reply branches from archives, hydrate cheaply, and export Raft-ready JSONL.",
    url: "https://ariadne.hyperplex.org",
    siteName: "ariadne",
    images: [
      {
        url: "/og.png",
        width: 1680,
        height: 945,
        alt: "Archived social posts transforming into conversation branches and retrieval documents.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "ariadne",
    description:
      "Archive-first tweet conversation reconstruction for LLM input and Raft-ready retrieval documents.",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
