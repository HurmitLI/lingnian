import type { Metadata } from "next";
import "./globals.css";
import "./formal-workspace.css";
import "./memory-design.css";

export const metadata: Metadata = {
  title: {
    default: "聆年 · 家庭记忆传家宝",
    template: "%s · 聆年",
  },
  description: "把愿意讲的往事，慢慢留给家人。",
  icons: {
    icon: "/brand/lingnian-mark-v3.png",
    apple: "/brand/lingnian-mark-v3.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
