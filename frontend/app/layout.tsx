import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ChainScope by MADBOY",
  description: "Read-only history, holdings and profit/loss for Bitcoin, Ethereum, Solana and Cardano addresses.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Rubik:wght@700;800;900&family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
