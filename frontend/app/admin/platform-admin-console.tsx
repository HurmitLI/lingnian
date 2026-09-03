"use client";

import { useEffect, useState } from "react";
import { Check, Copy, KeyRound, LogOut, ShieldCheck, UserPlus, Users } from "lucide-react";
import { useRouter } from "next/navigation";

import { api, ApiError } from "@/lib/api";

type AuthUser = {
  user_id: string;
  username: string;
  display_name: string;
  platform_role: "admin" | "user";
};

type Overview = {
  admin_username: string;
  admin_display_name: string;
  family_count: number;
  account_count: number;
  active_account_count: number;
  active_invitation_count: number;
};

type Account = {
  user_id: string;
  username: string;
  display_name: string;
  family_id: string;
  family_name: string;
  family_role: "owner" | "member";
  platform_role: "admin" | "user";
  status: "active" | "disabled";
  created_at: string;
  last_login_at: string | null;
};

type Invitation = {
  id: string;
  expires_at: string;
  max_uses: number;
  use_count: number;
  status: "active" | "used" | "expired" | "revoked";
};

type CreatedInvitation = Invitation & { invitation_code: string };

const invitationStatus = {
  active: "等待使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

function localDate(value: string | null): string {
  if (!value) return "尚未登录";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function PlatformAdminConsole() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [message, setMessage] = useState("");

  async function refresh() {
    const current = await api<AuthUser>("/api/v1/auth/me");
    setUser(current);
    if (current.platform_role !== "admin") {
      setLoading(false);
      return;
    }
    const [summary, accountList, invitationList] = await Promise.all([
      api<Overview>("/api/v1/auth/platform/overview"),
      api<Account[]>("/api/v1/auth/platform/accounts"),
      api<Invitation[]>("/api/v1/auth/platform-invitations"),
    ]);
    setOverview(summary);
    setAccounts(accountList);
    setInvitations(invitationList);
    setLoading(false);
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((error: unknown) => {
        if (error instanceof ApiError && ["AUTH_REQUIRED", "AUTH_EXPIRED"].includes(error.code)) {
          router.replace("/login?next=/admin");
          return;
        }
        setMessage(error instanceof ApiError ? error.message : "暂时无法读取平台管理信息。");
        setLoading(false);
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function createInvitation() {
    setBusy(true);
    setMessage("");
    setCopied(false);
    try {
      const value = await api<CreatedInvitation>("/api/v1/auth/platform-invitations", {
        method: "POST",
        body: JSON.stringify({ expires_in_days: 7 }),
      });
      setCreated(value);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "体验码生成失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyInvitation() {
    if (!created) return;
    const text = `聆年家庭记忆内测邀请\n打开：${window.location.origin}/login\n选择“邀请码建账”\n体验码：${created.invitation_code}\n该码只能使用一次，${new Date(created.expires_at).toLocaleDateString("zh-CN")}前有效。`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setMessage("浏览器没有允许复制，请手动选中体验码复制。");
    }
  }

  async function revokeInvitation(id: string) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/platform-invitations/${id}`, { method: "DELETE" });
      if (created?.id === id) setCreated(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "撤销体验码失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function updateAccount(account: Account) {
    const nextStatus = account.status === "active" ? "disabled" : "active";
    if (nextStatus === "disabled" && !window.confirm(`确定停用“${account.display_name}”的账号吗？停用后已登录设备会立即退出。`)) return;
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/platform/accounts/${account.user_id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: nextStatus }),
      });
      await refresh();
      setMessage(nextStatus === "active" ? "账号已恢复使用。" : "账号已停用，原有登录已失效。");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "账号状态修改失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    await api("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login?next=/admin");
    router.refresh();
  }

  if (loading) return <section className="platform-admin-loading"><p>正在读取平台管理信息……</p></section>;

  if (!user || user.platform_role !== "admin") {
    return (
      <section className="platform-admin-denied">
        <ShieldCheck size={34} aria-hidden="true" />
        <h1>这个账号不是平台管理员</h1>
        <p>家庭管理员只能管理自己的家庭；平台管理后台只向平台运营账号开放。</p>
        <button className="button secondary" type="button" onClick={logout}>换一个账号登录</button>
      </section>
    );
  }

  return (
    <div className="platform-admin-console">
      <section className="platform-admin-hero">
        <div>
          <p className="eyebrow">邀请码内测运营</p>
          <h1>账号和邀请，从这里统一管理</h1>
          <p>当前平台管理员：<strong>{overview?.admin_display_name}</strong>，登录名 <code>{overview?.admin_username}</code>。无需再打开终端。</p>
        </div>
        <button className="button secondary" type="button" disabled={busy} onClick={logout}><LogOut size={17} />退出管理后台</button>
      </section>

      {message && <p className="platform-admin-message" role="status">{message}</p>}

      <section className="platform-admin-stats" aria-label="平台概况">
        <article><Users size={20} /><span>家庭空间</span><strong>{overview?.family_count ?? 0}</strong></article>
        <article><KeyRound size={20} /><span>全部账号</span><strong>{overview?.account_count ?? 0}</strong></article>
        <article><ShieldCheck size={20} /><span>正常账号</span><strong>{overview?.active_account_count ?? 0}</strong></article>
        <article><UserPlus size={20} /><span>有效体验码</span><strong>{overview?.active_invitation_count ?? 0}</strong></article>
      </section>

      <section className="platform-admin-section platform-invite-center">
        <div className="platform-admin-section-heading">
          <div><p className="eyebrow">固定获取渠道</p><h2>新家庭体验码</h2><p>以后固定打开这个页面，点击按钮就能生成。每个码只能建立一个新家庭，有效期 7 天。</p></div>
          <button className="button primary" type="button" disabled={busy} onClick={createInvitation}><UserPlus size={18} />生成新家庭体验码</button>
        </div>
        {created && (
          <div className="platform-created-invite" aria-live="polite">
            <span>新体验码只在本次生成后完整显示，请直接复制邀请说明。</span>
            <code>{created.invitation_code}</code>
            <button className="button secondary" type="button" onClick={copyInvitation}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "已复制" : "复制完整邀请说明"}</button>
          </div>
        )}
        <div className="platform-invite-history">
          {invitations.length === 0 && <p>还没有发放过新家庭体验码。</p>}
          {invitations.map((item) => (
            <div key={item.id}>
              <span><strong>{invitationStatus[item.status]}</strong><small>{new Date(item.expires_at).toLocaleDateString("zh-CN")} 前有效 · 已使用 {item.use_count}/{item.max_uses}</small></span>
              {item.status === "active" && <button type="button" disabled={busy} onClick={() => void revokeInvitation(item.id)}>撤销</button>}
            </div>
          ))}
        </div>
      </section>

      <section className="platform-admin-section">
        <div className="platform-admin-section-heading">
          <div><p className="eyebrow">账号管理</p><h2>已经建立的账号</h2><p>这里只显示账号与家庭归属，不读取任何家庭故事、录音或照片。</p></div>
        </div>
        <div className="platform-account-list">
          {accounts.map((account) => {
            const protectedAccount = account.platform_role === "admin" || account.family_role === "owner";
            return (
              <article key={account.user_id}>
                <div className="platform-account-main">
                  <span className="platform-account-state" data-status={account.status}>{account.status === "active" ? "正常" : "已停用"}</span>
                  <div><strong>{account.display_name}</strong><code>{account.username}</code></div>
                </div>
                <dl>
                  <div><dt>家庭</dt><dd>{account.family_name}</dd></div>
                  <div><dt>权限</dt><dd>{account.platform_role === "admin" ? "平台管理员" : account.family_role === "owner" ? "家庭管理员" : "家庭成员"}</dd></div>
                  <div><dt>最近登录</dt><dd>{localDate(account.last_login_at)}</dd></div>
                </dl>
                {protectedAccount
                  ? <small className="platform-account-protected">{account.platform_role === "admin" ? "平台管理员账号受保护" : "需先在家庭内交接管理权"}</small>
                  : <button className="button secondary" type="button" disabled={busy} onClick={() => void updateAccount(account)}>{account.status === "active" ? "停用账号" : "恢复账号"}</button>}
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
