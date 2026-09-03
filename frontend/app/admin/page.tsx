import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";

import { PlatformAdminConsole } from "./platform-admin-console";

export const metadata: Metadata = {
  title: "平台管理后台",
  description: "管理聆年内测账号与新家庭体验码。",
  robots: { index: false, follow: false },
};

export default function AdminPage() {
  return (
    <main className="platform-admin-main">
      <header className="platform-admin-header">
        <Link className="platform-admin-brand" href="/">
          <Image src="/brand/lingnian-mark-v3.png" width={42} height={42} alt="" priority />
          <span><strong>聆年</strong><small>平台管理后台</small></span>
        </Link>
        <Link className="button secondary button-link" href="/">返回家庭空间</Link>
      </header>
      <PlatformAdminConsole />
    </main>
  );
}
