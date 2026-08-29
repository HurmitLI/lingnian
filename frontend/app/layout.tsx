import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "念念 · 家庭记忆传家宝",
    template: "%s · 念念",
  },
  description: "把愿意讲的往事，慢慢留给家人。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
