"use client";

import { useEffect, useState } from "react";
import { Activity, Check, Copy, Cpu, KeyRound, LogOut, RotateCcw, Server, ShieldCheck, UserPlus, Users } from "lucide-react";
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

type GenerationOverview = {
  node_count: number;
  online_node_count: number;
  queued_request_count: number;
  processing_request_count: number;
  review_request_count: number;
  failed_request_count: number;
};

type GenerationNode = {
  id: string;
  display_name: string;
  status: string;
  connection_state: "online" | "offline" | "revoked";
  capabilities: string[];
  software_version: string | null;
  device_summary: string | null;
  last_seen_at: string | null;
  created_at: string;
};

type CreatedGenerationNode = {
  id: string;
  display_name: string;
  connection_token: string;
  capabilities: string[];
  created_at: string;
};

type GenerationQueueItem = {
  id: string;
  generation_type: string;
  status: string;
  assigned_node_id: string | null;
  progress_percent: number;
  progress_stage: string | null;
  attempt_count: number;
  error_code: string | null;
  last_error_message: string | null;
  created_at: string;
  updated_at: string;
};

const invitationStatus = {
  active: "等待使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

const generationTypeLabel: Record<string, string> = {
  photo_restore: "老照片修复",
  portrait_video: "人物讲述视频",
  scene_video: "故事情景视频",
};

const generationStatusLabel: Record<string, string> = {
  awaiting_provider: "等待人工制作",
  queued: "等待家用电脑",
  processing: "正在生成",
  pending_human_review: "等待家庭验收",
  accepted: "验收通过",
  rejected: "验收驳回",
  failed: "生成失败",
  cancelled: "已取消",
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
  const [generationOverview, setGenerationOverview] = useState<GenerationOverview | null>(null);
  const [generationNodes, setGenerationNodes] = useState<GenerationNode[]>([]);
  const [generationQueue, setGenerationQueue] = useState<GenerationQueueItem[]>([]);
  const [createdNode, setCreatedNode] = useState<CreatedGenerationNode | null>(null);
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
    const [summary, accountList, invitationList, generationSummary, nodes, queue] = await Promise.all([
      api<Overview>("/api/v1/auth/platform/overview"),
      api<Account[]>("/api/v1/auth/platform/accounts"),
      api<Invitation[]>("/api/v1/auth/platform-invitations"),
      api<GenerationOverview>("/api/v1/generation-control/overview"),
      api<GenerationNode[]>("/api/v1/generation-control/nodes"),
      api<GenerationQueueItem[]>("/api/v1/generation-control/requests"),
    ]);
    setOverview(summary);
    setAccounts(accountList);
    setInvitations(invitationList);
    setGenerationOverview(generationSummary);
    setGenerationNodes(nodes);
    setGenerationQueue(queue);
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

  async function createGenerationNode() {
    setBusy(true);
    setMessage("");
    setCopied(false);
    try {
      const value = await api<CreatedGenerationNode>("/api/v1/generation-control/nodes", {
        method: "POST",
        body: JSON.stringify({
          display_name: "家用 RTX 5080 生成节点",
          capabilities: ["photo_restore", "portrait_video", "scene_video"],
        }),
      });
      setCreatedNode(value);
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "生成节点连接密钥创建失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function copyNodeSetup() {
    if (!createdNode) return;
    const text = [
      `LINGNIAN_API_BASE=${window.location.origin}`,
      `LINGNIAN_NODE_TOKEN=${createdNode.connection_token}`,
      `LINGNIAN_NODE_ID=${createdNode.id}`,
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setMessage("浏览器没有允许复制，请手动选中连接配置复制。密钥离开本页后不再完整显示。");
    }
  }

  async function revokeGenerationNode(node: GenerationNode) {
    if (!window.confirm(`确定断开“${node.display_name}”吗？正在运行的任务会重新排队。`)) return;
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/generation-control/nodes/${node.id}`, { method: "DELETE" });
      if (createdNode?.id === node.id) setCreatedNode(null);
      await refresh();
      setMessage("生成节点已经断开，原连接密钥立即失效。");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "生成节点暂时无法断开。");
    } finally {
      setBusy(false);
    }
  }

  async function retryGeneration(requestId: string) {
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/v1/generation-control/requests/${requestId}/retry`, { method: "POST" });
      await refresh();
      setMessage("任务已重新排队，家用电脑上线后会自动领取。");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "任务重新排队失败。");
    } finally {
      setBusy(false);
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

      <section className="platform-admin-section platform-generation-center">
        <div className="platform-admin-section-heading">
          <div><p className="eyebrow">本地算力接入</p><h2>家用生成节点</h2><p>家用电脑主动领取任务，云端不会访问家庭网络，也不会把 ComfyUI 端口暴露到公网。</p></div>
          <button className="button primary" type="button" disabled={busy} onClick={createGenerationNode}><Cpu size={18} />生成一次性连接密钥</button>
        </div>

        <div className="generation-admin-stats" aria-label="生成任务概况">
          <article><Server size={18} /><span>在线节点</span><strong>{generationOverview?.online_node_count ?? 0}/{generationOverview?.node_count ?? 0}</strong></article>
          <article><Activity size={18} /><span>排队/生成</span><strong>{(generationOverview?.queued_request_count ?? 0) + (generationOverview?.processing_request_count ?? 0)}</strong></article>
          <article><ShieldCheck size={18} /><span>等待验收</span><strong>{generationOverview?.review_request_count ?? 0}</strong></article>
          <article><RotateCcw size={18} /><span>需要处理</span><strong>{generationOverview?.failed_request_count ?? 0}</strong></article>
        </div>

        {createdNode && (
          <div className="platform-node-secret" aria-live="polite">
            <strong>连接密钥只完整显示这一次</strong>
            <p>在家用电脑配置聆年节点时使用。它不能登录家庭账号，也不能读取没有授权给生成任务的资料。</p>
            <code>{createdNode.connection_token}</code>
            <button className="button secondary" type="button" onClick={copyNodeSetup}>{copied ? <Check size={17} /> : <Copy size={17} />}{copied ? "连接配置已复制" : "复制家用电脑连接配置"}</button>
          </div>
        )}

        <div className="platform-node-list">
          {generationNodes.length === 0 && <p className="platform-empty">还没有生成节点。家用电脑部署完成前，可以先生成连接密钥。</p>}
          {generationNodes.map((node) => (
            <article key={node.id}>
              <span className="platform-node-state" data-state={node.connection_state}>{node.connection_state === "online" ? "在线" : node.connection_state === "revoked" ? "已断开" : "未连接"}</span>
              <div><strong>{node.display_name}</strong><small>{node.device_summary || "等待家用电脑首次连接"}</small></div>
              <div><small>最近连接</small><strong>{localDate(node.last_seen_at)}</strong></div>
              {node.connection_state !== "revoked" && <button type="button" disabled={busy} onClick={() => void revokeGenerationNode(node)}>断开</button>}
            </article>
          ))}
        </div>

        <div className="platform-generation-queue">
          <h3>最近的生成任务</h3>
          {generationQueue.length === 0 && <p className="platform-empty">还没有家庭提交生成任务。</p>}
          {generationQueue.map((item) => (
            <article key={item.id}>
              <div><strong>{generationTypeLabel[item.generation_type] ?? item.generation_type}</strong><small>{item.progress_stage || generationStatusLabel[item.status] || item.status}</small></div>
              <span data-status={item.status}>{generationStatusLabel[item.status] || item.status}{item.status === "processing" ? ` · ${item.progress_percent}%` : ""}</span>
              <small>{localDate(item.updated_at)}</small>
              {(item.status === "failed" || item.status === "rejected") && <button className="button secondary" type="button" disabled={busy} onClick={() => void retryGeneration(item.id)}><RotateCcw size={15} />重新排队</button>}
            </article>
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
