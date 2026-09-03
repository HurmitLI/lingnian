"use client";

import { FormEvent, useEffect, useState } from "react";
import { Check, Copy, KeyRound, ShieldCheck, UserPlus, X } from "lucide-react";

import { api, ApiError } from "@/lib/api";

type AuthUser = {
  display_name: string;
  family_name: string;
  role: "owner" | "member";
};

type Invitation = {
  id: string;
  expires_at: string;
  max_uses: number;
  use_count: number;
  revoked: boolean;
  status: "active" | "used" | "expired" | "revoked";
};

type CreatedInvitation = {
  id: string;
  invitation_code: string;
  expires_at: string;
  max_uses: number;
};

type FamilyMember = {
  membership_id: string;
  user_id: string;
  username: string;
  display_name: string;
  role: "owner" | "member";
  status: "active" | "revoked";
  last_login_at: string | null;
};

const STATUS_TEXT: Record<Invitation["status"], string> = {
  active: "等待使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

export default function FormalAccessPanel() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function refresh() {
    const me = await api<AuthUser>("/api/v1/auth/me");
    setUser(me);
    if (me.role === "owner") {
      const [invitationResult, memberResult] = await Promise.all([
        api<Invitation[]>("/api/v1/auth/invitations"),
        api<FamilyMember[]>("/api/v1/auth/members"),
      ]);
      setInvitations(invitationResult);
      setMembers(memberResult);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh().catch((error: unknown) => {
        setMessage(error instanceof ApiError ? error.message : "暂时无法读取家庭访问设置。");
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function createInvitation() {
    setBusy(true);
    setMessage("");
    setCopied(false);
    try {
      const result = await api<CreatedInvitation>("/api/v1/auth/invitations", {
        method: "POST",
        body: JSON.stringify({ expires_in_days: 7 }),
      });
      setCreated(result);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "邀请码生成失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyInvitation() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.invitation_code);
      setCopied(true);
    } catch {
      setMessage("浏览器没有允许复制，请手动选中邀请码复制。");
    }
  }

  async function revokeInvitation(id: string) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/invitations/${id}`, { method: "DELETE" });
      if (created?.id === id) setCreated(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "撤销失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function revokeMember(member: FamilyMember) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/members/${member.membership_id}`, { method: "DELETE" });
      setMessage(`已移除${member.display_name}的访问权限，其已登录设备也会失效。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "移除成员失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setMessage("");
    try {
      await api("/api/v1/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: data.get("currentPassword"),
          new_password: data.get("newPassword"),
        }),
      });
      form.reset();
      setMessage("密码已更新，其他设备上的旧登录已退出。");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "密码更新失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card formal-access-card">
      <div className="section-heading">
        <span>03</span>
        <div><h2>家庭访问</h2><p>正式空间只向受邀家人开放。每个邀请码只能使用一次，并会在 7 天后失效。</p></div>
      </div>
      <div className="cloud-security-summary">
        <ShieldCheck size={21} aria-hidden="true" />
        <div><strong>云端加密保存</strong><span>账号会话、家庭资料与媒体分开保护；未登录无法读取。</span></div>
      </div>
      {user && <p className="formal-current-user"><KeyRound size={16} aria-hidden="true" />{user.display_name} · {user.role === "owner" ? "家庭管理员" : "家庭成员"}</p>}
      {user?.role === "owner" && (
        <>
          <button className="button secondary" type="button" disabled={busy} onClick={createInvitation}><UserPlus size={18} aria-hidden="true" />生成一次性邀请码</button>
          {created && (
            <div className="created-invitation" aria-live="polite">
              <span>请现在交给家人，关闭后不会再次显示完整号码。</span>
              <code>{created.invitation_code}</code>
              <button className="button ghost" type="button" onClick={copyInvitation}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "已复制" : "复制"}</button>
            </div>
          )}
          {invitations.length > 0 && (
            <div className="invitation-list">
              {invitations.map((item) => (
                <div key={item.id}>
                  <span><strong>{STATUS_TEXT[item.status]}</strong><small>{new Date(item.expires_at).toLocaleDateString("zh-CN")} 前有效</small></span>
                  {item.status === "active" && <button type="button" disabled={busy} onClick={() => revokeInvitation(item.id)} aria-label="撤销邀请码"><X size={17} /></button>}
                </div>
              ))}
            </div>
          )}
          {members.length > 0 && (
            <div className="formal-member-list">
              <h3>已加入的家人</h3>
              {members.map((member) => (
                <div key={member.membership_id}>
                  <span>
                    <strong>{member.display_name}{member.role === "owner" ? " · 管理员" : ""}</strong>
                    <small>{member.username}{member.status === "revoked" ? " · 已移除" : ""}</small>
                  </span>
                  {member.role === "member" && member.status === "active" && (
                    <button type="button" disabled={busy} onClick={() => void revokeMember(member)}>移除访问</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
      {user?.role === "member" && <p className="hint">如需邀请其他家人，请联系家庭管理员。</p>}
      {user && (
        <details className="formal-password-panel">
          <summary>修改我的登录密码</summary>
          <form onSubmit={changePassword}>
            <label className="field"><span>当前密码</span><input name="currentPassword" type="password" autoComplete="current-password" required /></label>
            <label className="field"><span>新密码</span><input name="newPassword" type="password" autoComplete="new-password" minLength={10} required /></label>
            <button className="button secondary" disabled={busy}>更新密码</button>
          </form>
        </details>
      )}
      {message && <p className="message" role="status">{message}</p>}
    </section>
  );
}
