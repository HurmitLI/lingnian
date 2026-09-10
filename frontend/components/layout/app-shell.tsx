"use client";

import Link from "next/link";
import Image from "next/image";
import { ALargeSmall, BookHeart, Home, LibraryBig, LogOut, Mic2, ShieldCheck, Users } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useComfortMode, setComfortMode } from "@/lib/use-comfort-mode";

const ELDER_NAV_ITEMS = [
  { href: "/", label: "首页", icon: Home },
  { href: "/record", label: "聊聊往事", icon: Mic2 },
  { href: "/archive", label: "听听回忆", icon: BookHeart },
];

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
  const formalCloudMode = process.env.NEXT_PUBLIC_FORMAL_AUTH_REQUIRED === "true";
  const comfortMode = useComfortMode();
  const desktopItems = comfortMode ? ELDER_NAV_ITEMS : DESKTOP_NAV_ITEMS;
  const mobileItems = comfortMode ? ELDER_NAV_ITEMS : NAV_ITEMS;

  useEffect(() => {
    document.documentElement.dataset.comfortMode = comfortMode ? "on" : "off";
  }, [comfortMode]);

  function toggleComfortMode() {
    setComfortMode(!comfortMode);
  }

  async function logout() {
    await fetch("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.replace("/login");
  }

  return (
    <div className="app-shell">
      <aside className="desktop-sidebar" aria-label="主导航">
        <Link className="brand-mark" href="/" aria-label="聆年首页">
          <span className="brand-symbol" aria-hidden="true"><Image src="/brand/lingnian-mark-v3.png" width={42} height={42} alt="" preload /></span>
          <span className="brand-copy"><strong>聆年</strong><small>家庭记忆</small></span>
        </Link>
        <p className="nav-caption">我们的记忆空间</p>
        <nav className="primary-nav" aria-label="桌面主导航">
          {desktopItems.map((item) => {
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
        <button className="comfort-toggle desktop-comfort-toggle" type="button" aria-pressed={comfortMode} onClick={toggleComfortMode}>
          <ALargeSmall size={20} aria-hidden="true" /><span><strong>{comfortMode ? "返回普通模式" : "老人模式"}</strong><small>{comfortMode ? "查看完整功能" : "大字 · 操作更简单"}</small></span>
        </button>
        <div className="sidebar-note"><ShieldCheck size={20} strokeWidth={1.8} aria-hidden="true" /><span><strong>家庭私密空间</strong><small>{formalCloudMode ? "加密连接 · 受邀家人可见" : "资料保存在这台 Mac"}</small></span></div>
        {formalCloudMode && <div className="sidebar-legal"><Link href="/privacy">隐私</Link><Link href="/terms">使用约定</Link></div>}
        {formalCloudMode && <button className="sidebar-logout" type="button" onClick={logout}><LogOut size={17} aria-hidden="true" />退出登录</button>}
      </aside>

      <div className="app-content">
        <header className="workspace-topbar"><span>聆年 <i>/</i> {desktopItems.find((item) => item.href === pathname)?.label ?? "家庭记忆"}</span><span><ShieldCheck size={14} aria-hidden="true" />只与在意的人分享</span><button className="comfort-toggle mobile-comfort-toggle" type="button" aria-pressed={comfortMode} onClick={toggleComfortMode}>
        <ALargeSmall size={18} aria-hidden="true" />{comfortMode ? "普通模式" : "老人模式"}
      </button></header>
        {children}
        <footer className="workspace-footer">聆年 · 把平凡的日子，留成珍贵的回忆</footer>
      </div>

      <nav className="mobile-nav" aria-label="手机主导航">
        {mobileItems.map((item) => {
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
