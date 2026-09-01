"use client";

import Link from "next/link";
import Image from "next/image";
import { BookHeart, Home, LibraryBig, Mic2, ShieldCheck, Users } from "lucide-react";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV_ITEMS = [
  { href: "/", label: "首页", icon: Home },
  { href: "/record", label: "开始记录", icon: Mic2 },
  { href: "/archive", label: "回忆档案", icon: BookHeart },
  { href: "/family", label: "家庭管理", icon: Users },
] as const;

const DESKTOP_NAV_ITEMS = [
  NAV_ITEMS[0],
  NAV_ITEMS[1],
  NAV_ITEMS[2],
  { href: "/memory", label: "家族记忆", icon: LibraryBig },
  NAV_ITEMS[3],
] as const;

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <aside className="desktop-sidebar" aria-label="主导航">
        <Link className="brand-mark" href="/" aria-label="聆年首页">
          <span className="brand-symbol" aria-hidden="true"><Image src="/brand/lingnian-mark-v3.png" width={42} height={42} alt="" preload /></span>
          <span className="brand-copy"><strong>聆年</strong><small>家庭记忆</small></span>
        </Link>
        <nav className="primary-nav" aria-label="桌面主导航">
          {DESKTOP_NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}>
                <Icon size={21} strokeWidth={1.8} aria-hidden="true" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-note"><ShieldCheck size={20} strokeWidth={1.8} aria-hidden="true" /><span><strong>家庭私密空间</strong><small>资料保存在这台 Mac</small></span></div>
      </aside>

      <div className="app-content">{children}</div>

      <nav className="mobile-nav" aria-label="手机主导航">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href || (["/keepsake", "/memory"].includes(pathname) && item.href === "/archive");
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined}>
              <Icon size={22} strokeWidth={1.8} aria-hidden="true" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
