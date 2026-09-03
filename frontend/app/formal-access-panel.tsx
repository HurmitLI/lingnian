"use client";

import { FormEvent, useEffect, useState } from "react";
import { Check, Copy, KeyRound, ShieldCheck, UserPlus, X } from "lucide-react";
import Link from "next/link";

import { api, ApiError } from "@/lib/api";
import type { ElderProfile, FamilyPerson } from "@/lib/types";

const LIFE_STAGES = ["童年", "求学", "工作", "婚恋", "育儿", "价值观", "老物件"];

type AuthUser = {
  display_name: string;
  family_name: string;
  role: "owner" | "member";
  platform_role: "admin" | "user";
};

type Invitation = {
  id: string;
  expires_at: string;
  max_uses: number;
  use_count: number;
  revoked: boolean;
  status: "active" | "used" | "expired" | "revoked";
  purpose: "family_access" | "interview";
  elder_id: string | null;
  narrator_person_id: string | null;
  life_stage: string | null;
  session_id: string | null;
  interview_status: string | null;
};

type CreatedInvitation = {
  id: string;
  invitation_code: string;
  expires_at: string;
  max_uses: number;
  purpose: "family_access" | "interview";
  elder_id: string | null;
  narrator_person_id: string | null;
  life_stage: string | null;
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

type PlatformInvitation = {
  id: string;
  expires_at: string;
  max_uses: number;
  use_count: number;
  status: "active" | "used" | "expired" | "revoked";
};

type CreatedPlatformInvitation = {
  id: string;
  invitation_code: string;
  expires_at: string;
  max_uses: number;
};

const STATUS_TEXT: Record<Invitation["status"], string> = {
  active: "等待使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

const INTERVIEW_STATUS_TEXT: Record<string, string> = {
  pending: "等待建账",
  claimed: "已接受",
  INTERVIEWING: "采访中",
  TRANSCRIBING: "整理中",
  TRANSCRIPT_REVIEW: "待整理",
  ORGANIZING: "整理中",
  DRAFT_REVIEW: "待家人确认",
  CONFIRMED: "待归档",
  ARCHIVED: "已归档",
  SKIPPED: "已跳过",
};

type FormalAccessPanelProps = {
  profiles?: ElderProfile[];
  people?: FamilyPerson[];
};

export default function FormalAccessPanel({
  profiles = [],
  people = [],
}: FormalAccessPanelProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [platformInvitations, setPlatformInvitations] = useState<PlatformInvitation[]>([]);
  const [created, setCreated] = useState<CreatedInvitation | null>(null);
  const [createdPlatform, setCreatedPlatform] = useState<CreatedPlatformInvitation | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function refresh() {
    const me = await api<AuthUser>("/api/v1/auth/me");
    setUser(me);
    if (me.role === "owner") {
      const requests: [Promise<Invitation[]>, Promise<FamilyMember[]>, Promise<PlatformInvitation[]> | null] = [
        api<Invitation[]>("/api/v1/auth/invitations"),
        api<FamilyMember[]>("/api/v1/auth/members"),
        me.platform_role === "admin"
          ? api<PlatformInvitation[]>("/api/v1/auth/platform-invitations")
          : null,
      ];
      const [invitationResult, memberResult, platformResult] = await Promise.all([
        requests[0],
        requests[1],
        requests[2] ?? Promise.resolve([]),
      ]);
      setInvitations(invitationResult);
      setMembers(memberResult);
      setPlatformInvitations(platformResult);
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

  async function createPlatformInvitation() {
    setBusy(true);
    setMessage("");
    setCopied(false);
    try {
      const result = await api<CreatedPlatformInvitation>("/api/v1/auth/platform-invitations", {
        method: "POST",
        body: JSON.stringify({ expires_in_days: 7 }),
      });
      setCreatedPlatform(result);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "新家庭体验码生成失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyPlatformInvitation() {
    if (!createdPlatform) return;
    try {
      await navigator.clipboard.writeText(
        `请打开 ${window.location.origin}/login，选择“邀请码建账”，输入新家庭体验码 ${createdPlatform.invitation_code}。请为自己的家庭设置独立的家庭空间名称。`,
      );
      setCopied(true);
    } catch {
      setMessage("浏览器没有允许复制，请手动选中体验码复制。");
    }
  }

  async function revokePlatformInvitation(id: string) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/platform-invitations/${id}`, { method: "DELETE" });
      if (createdPlatform?.id === id) setCreatedPlatform(null);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "撤销失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function createInterviewInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setMessage("");
    setCopied(false);
    try {
      const result = await api<CreatedInvitation>("/api/v1/auth/invitations", {
        method: "POST",
        body: JSON.stringify({
          expires_in_days: 7,
          elder_id: data.get("elderId"),
          narrator_person_id: data.get("narratorPersonId"),
          life_stage: data.get("lifeStage"),
        }),
      });
      setCreated(result);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "采访邀请生成失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyInvitation() {
    if (!created) return;
    try {
      const elder = profiles.find((item) => item.id === created.elder_id);
      const narrator = people.find((item) => item.id === created.narrator_person_id);
      const content = created.purpose === "interview"
        ? `请打开 ${window.location.origin}/login，选择“邀请码建账”，输入邀请码 ${created.invitation_code}。建账后会直接进入采访，由${narrator?.display_name ?? "你"}补充${elder?.preferred_name ?? "这位家人"}的“${created.life_stage ?? "回忆"}”。`
        : created.invitation_code;
      await navigator.clipboard.writeText(content);
      setCopied(true);
    } catch {
      setMessage("浏览器没有允许复制，请手动选中邀请码复制。");
    }
  }

  function invitationSummary(item: Invitation): string {
    if (item.purpose !== "interview") return "加入家庭空间";
    const elder = profiles.find((profile) => profile.id === item.elder_id);
    const narrator = people.find((person) => person.id === item.narrator_person_id);
    const progress = INTERVIEW_STATUS_TEXT[item.interview_status ?? ""];
    return `${narrator?.display_name ?? "受邀家人"}讲${elder?.preferred_name ?? "家人"}的${item.life_stage ?? "回忆"}${progress ? ` · ${progress}` : ""}`;
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

  async function transferOwnership(member: FamilyMember) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/auth/members/${member.membership_id}/make-owner`, { method: "PATCH" });
      setMessage(`已把家庭管理权交给${member.display_name}。`);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "管理权交接失败，请重试。");
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

  async function deleteAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setMessage("");
    try {
      await api("/api/v1/auth/account", {
        method: "DELETE",
        body: JSON.stringify({
          password: data.get("password"),
          confirmation: data.get("confirmation"),
        }),
      });
      window.location.replace("/login");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "账号注销没有完成，请重试。");
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
          {user.platform_role === "admin" && (
            <div className="interview-invite-card platform-invite-card">
              <div><h3>平台管理后台</h3><p>新家庭体验码和全部账号统一放在独立后台，不需要再从终端获取。</p></div>
              <Link className="button primary button-link" href="/admin">进入平台管理后台</Link>
              <div><h3>临时快速发码</h3><p>这个码会建立全新、独立加密的家庭空间，不会进入你的家庭档案。</p></div>
              <button className="button secondary" type="button" disabled={busy} onClick={createPlatformInvitation}><UserPlus size={18} aria-hidden="true" />生成新家庭体验码</button>
              {createdPlatform && (
                <div className="created-invitation" aria-live="polite">
                  <span>只显示这一次，复制后发给另一个家庭的管理员。</span>
                  <code>{createdPlatform.invitation_code}</code>
                  <button className="button ghost" type="button" onClick={copyPlatformInvitation}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "已复制" : "复制体验说明"}</button>
                </div>
              )}
              {platformInvitations.length > 0 && (
                <div className="invitation-list">
                  {platformInvitations.map((item) => (
                    <div key={item.id}>
                      <span><strong>新家庭体验码 · {STATUS_TEXT[item.status]}</strong><small>{new Date(item.expires_at).toLocaleDateString("zh-CN")} 前有效</small></span>
                      {item.status === "active" && <button type="button" disabled={busy} onClick={() => void revokePlatformInvitation(item.id)} aria-label="撤销新家庭体验码"><X size={17} /></button>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="interview-invite-card">
            <div><h3>邀请家人补充一段回忆</h3><p>选好讲谁、谁来讲和话题。家人用邀请码建账后，会直接进入这次语音采访。</p></div>
            {profiles.length > 0 && people.length > 0 ? (
              <form className="interview-invite-form" onSubmit={createInterviewInvitation}>
                <label><span>讲谁的故事</span><select name="elderId">{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.preferred_name}</option>)}</select></label>
                <label><span>请谁来讲</span><select name="narratorPersonId">{people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label>
                <label><span>先聊哪一段</span><select name="lifeStage">{LIFE_STAGES.map((stage) => <option key={stage} value={stage}>{stage}</option>)}</select></label>
                <button className="button primary" type="submit" disabled={busy}><UserPlus size={18} aria-hidden="true" />生成采访邀请</button>
              </form>
            ) : <p className="hint">先在上方建立人物档案和家庭成员，就可以发起采访。</p>}
          </div>
          <div className="access-invite-divider"><span>或者只邀请家人加入空间</span></div>
          <button className="button secondary" type="button" disabled={busy} onClick={createInvitation}><UserPlus size={18} aria-hidden="true" />生成一次性邀请码</button>
          {created && (
            <div className="created-invitation" aria-live="polite">
              <span>{created.purpose === "interview" ? "复制后发给家人，其中包含打开方式、邀请码和本次采访目标。" : "请现在交给家人，关闭后不会再次显示完整号码。"}</span>
              <code>{created.invitation_code}</code>
              <button className="button ghost" type="button" onClick={copyInvitation}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "已复制" : created.purpose === "interview" ? "复制邀请说明" : "复制"}</button>
            </div>
          )}
          {invitations.length > 0 && (
            <div className="invitation-list">
              {invitations.map((item) => (
                <div key={item.id}>
                  <span><strong>{item.purpose === "interview" ? "采访邀请" : "家庭邀请"} · {STATUS_TEXT[item.status]}</strong><small>{invitationSummary(item)} · {new Date(item.expires_at).toLocaleDateString("zh-CN")} 前有效</small></span>
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
                    <span className="formal-member-actions">
                      <button type="button" disabled={busy} onClick={() => void transferOwnership(member)}>设为管理员</button>
                      <button type="button" disabled={busy} onClick={() => void revokeMember(member)}>移除访问</button>
                    </span>
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
      {user && (
        <details className="formal-password-panel formal-account-delete">
          <summary>注销我的账号</summary>
          <form onSubmit={deleteAccount}>
            <p className="hint">家庭管理员需先导出档案并交接管理权；普通家庭成员可直接注销自己的账号。</p>
            <label className="field"><span>注销确认密码</span><input name="password" type="password" autoComplete="current-password" required /></label>
            <label className="field"><span>输入“注销我的账号”</span><input name="confirmation" required /></label>
            <button className="button secondary" disabled={busy}>确认注销</button>
          </form>
        </details>
      )}
      {message && <p className="message" role="status">{message}</p>}
    </section>
  );
}
