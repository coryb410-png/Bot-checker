import type { Metadata } from "next";

// Keep the admin page out of search engines
export const metadata: Metadata = {
  title: "ChainScope admin",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return children;
}
