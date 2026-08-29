import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "念念 · 家庭记忆传家宝",
  description: "第一阶段本机测试版",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

