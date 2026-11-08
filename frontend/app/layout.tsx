import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ChainScope by MADBOY",
  description: "Read-only history, holdings and profit/loss for Bitcoin, Ethereum, Solana, Cardano and XRP addresses.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bungee&family=Rubik:wght@400;500;700&family=IBM+Plex+Mono:wght@400;600&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
