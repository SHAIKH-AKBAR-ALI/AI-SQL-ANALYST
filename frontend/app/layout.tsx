import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HisaabAI Agent",
  description: "Ask questions about your sales in Hindi, Hinglish or English. Preview spreadsheets, confirm imports and explore verified results.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
