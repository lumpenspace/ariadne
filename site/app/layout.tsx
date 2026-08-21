import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Michroma } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetBrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

const michroma = Michroma({
  variable: "--font-michroma",
  weight: "400",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://ariadne.hyperplex.org"),
  title: "ariadne — reconstruct conversations from archives",
  description:
    "Import and search X/Twitter archives, follow known reply and quote links, and render root-to-target conversation branches.",
  icons: {
    icon: "/favicon.svg",
  },
  openGraph: {
    title: "ariadne — reconstruct conversations from archives",
    description:
      "Import and search X/Twitter archives, then reconstruct the conversation branches their reply and quote links can resolve.",
    url: "https://ariadne.hyperplex.org",
    siteName: "ariadne",
    images: [
      {
        url: "/ariadne-thread-v2.png",
        width: 1672,
        height: 941,
        alt: "A golden conversation thread crossing several archives on a Hyperplex blue field.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "ariadne — reconstruct conversations from archives",
    description:
      "Import and search X/Twitter archives, then reconstruct the conversation branches their reply and quote links can resolve.",
    images: ["/ariadne-thread-v2.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetBrainsMono.variable} ${michroma.variable}`}>
        {children}
      </body>
    </html>
  );
}
