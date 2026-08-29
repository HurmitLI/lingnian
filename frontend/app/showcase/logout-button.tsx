"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

export function DemoLogoutButton({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function logout() {
    setPending(true);
    try {
      const response = await fetch("/api/demo-auth/logout", { method: "POST" });
      const result = await response.json() as { redirectTo?: string };
      router.replace(result.redirectTo || "/demo-login");
      router.refresh();
    } catch {
      router.replace("/demo-login");
      router.refresh();
    }
  }

  return (
    <button
      className={compact ? "showcase-logout-compact" : "button secondary"}
      type="button"
      onClick={logout}
      disabled={pending}
    >
      <LogOut size={16} aria-hidden="true" />{pending ? "正在退出…" : "退出体验"}
    </button>
  );
}
