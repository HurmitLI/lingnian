"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, apiDownload } from "@/lib/api";
import type { FamilySecurity } from "@/lib/types";

type Props = {
  familyId: string | null;
};

export default function SecurityPanel({ familyId }: Props) {
  const [security, setSecurity] = useState<FamilySecurity | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [confirmation, setConfirmation] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!familyId) return;
    void api<FamilySecurity>(`/api/v1/families/${familyId}/security`)
      .then((result) => {
        if (!cancelled) setSecurity(result);
      })
      .catch((value: unknown) => {
        if (!cancelled) setError(value instanceof Error ? value.message : "无法读取安全状态。");
      });
    return () => {
      cancelled = true;
    };
  }, [familyId]);

  async function initializeSecurity() {
    if (!familyId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<FamilySecurity>(
        `/api/v1/families/${familyId}/security/initialize`,
        {
          method: "POST",
          body: JSON.stringify({ actor_label: "本机家庭管理员" }),
        },
      );
      setSecurity(result);
      setNotice("家庭主密钥已放入这台 Mac 的钥匙串。现有资料尚未执行加密迁移。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "初始化没有成功。");
    } finally {
      setBusy(false);
    }
  }

  async function exportRecoveryPackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!familyId) return;
    setError("");
    setNotice("");
    if (passphrase !== confirmation) {
      setError("两次输入的恢复口令不一致。");
      return;
    }
    if (passphrase.length < 12) {
      setError("恢复口令至少需要 12 个字符，请使用家人能妥善保管的长口令。");
      return;
    }
    setBusy(true);
    try {
      const download = await apiDownload(
        `/api/v1/families/${familyId}/security/recovery-package`,
        {
          method: "POST",
          body: JSON.stringify({
            actor_label: "本机家庭管理员",
            recovery_passphrase: passphrase,
          }),
        },
      );
      const url = URL.createObjectURL(download.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = download.filename;
      link.click();
      URL.revokeObjectURL(url);
      setPassphrase("");
      setConfirmation("");
      setSecurity((current) => current ? {
        ...current,
        recovery_package_created_at: new Date().toISOString(),
      } : current);
      setNotice("恢复包已下载。请交由可信家人离线保管，并把恢复口令与文件分开存放。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "恢复包生成没有成功。");
    } finally {
      setBusy(false);
    }
  }

  const statusText = !familyId
    ? "请先选择家庭档案"
    : !security
      ? "正在读取"
      : security.encryption_status === "not_initialized"
        ? "尚未初始化"
        : security.encryption_status === "encrypted"
          ? "本机加密已完成"
          : "主密钥已就绪，资料待加密";

  return (
    <section className="card security-card">
      <div className="section-heading">
        <span>02</span>
        <div>
          <h2>家庭档案安全</h2>
          <p>主密钥保存在 Mac 钥匙串；恢复包由家人离线保管，系统不留后门。</p>
        </div>
      </div>

      <div className="security-status">
        <div><span>当前状态</span><strong>{statusText}</strong></div>
        <div><span>恢复包</span><strong>{security?.recovery_package_created_at ? "已生成" : "尚未生成"}</strong></div>
      </div>
      {error && <div className="message error" role="alert">{error}</div>}
      {notice && <div className="message success" role="status">{notice}</div>}

      {security?.encryption_status === "not_initialized" && (
        <div className="security-action">
          <p className="hint">只有点击下面按钮时，系统才会创建家庭主密钥并写入这台 Mac 的钥匙串。</p>
          <button className="button primary" disabled={busy} onClick={initializeSecurity}>
            在 Mac 钥匙串中初始化主密钥
          </button>
        </div>
      )}

      {security?.key_initialized && (
        <form className="recovery-form" onSubmit={exportRecoveryPackage}>
          <div>
            <h3>生成家庭离线恢复包</h3>
            <p className="hint">口令只用于本次加密，不会保存在念念中。遗失主密钥和恢复材料后，档案无法解密。</p>
          </div>
          <label className="field">
            <span>恢复口令（至少 12 个字符）</span>
            <input type="password" autoComplete="new-password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} required />
          </label>
          <label className="field">
            <span>再次输入恢复口令</span>
            <input type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required />
          </label>
          <button className="button secondary" disabled={busy}>生成并下载恢复包</button>
        </form>
      )}
    </section>
  );
}
