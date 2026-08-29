"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, apiDownload } from "@/lib/api";
import type { BackupRecord, FamilySecurity } from "@/lib/types";

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
  const [recoveryFile, setRecoveryFile] = useState<File | null>(null);
  const [verificationPassphrase, setVerificationPassphrase] = useState("");
  const [classification, setClassification] = useState<"authorized_non_sensitive" | "authorized_sensitive">("authorized_non_sensitive");
  const [activationConfirmed, setActivationConfirmed] = useState(false);
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [selectedBackupId, setSelectedBackupId] = useState("");
  const [backupRecoveryFile, setBackupRecoveryFile] = useState<File | null>(null);
  const [backupPassphrase, setBackupPassphrase] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!familyId) return;
    void api<FamilySecurity>(`/api/v1/families/${familyId}/security`)
      .then((result) => {
        if (!cancelled) {
          setSecurity(result);
          if (result.encryption_status === "active_encrypted") {
            void api<BackupRecord[]>("/api/v1/backups")
              .then((items) => {
                if (!cancelled) {
                  setBackups(items);
                  setSelectedBackupId((current) => current || items[0]?.id || "");
                }
              })
              .catch((value: unknown) => {
                if (!cancelled) setError(value instanceof Error ? value.message : "无法读取本机备份。");
              });
          }
        }
      })
      .catch((value: unknown) => {
        if (!cancelled) setError(value instanceof Error ? value.message : "无法读取安全状态。");
      });
    return () => {
      cancelled = true;
    };
  }, [familyId]);

  async function loadBackups() {
    const result = await api<BackupRecord[]>("/api/v1/backups");
    setBackups(result);
    setSelectedBackupId((current) => current || result[0]?.id || "");
  }

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

  async function verifyRecoveryPackage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!familyId || !recoveryFile) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const form = new FormData();
      form.append("package", recoveryFile);
      form.append("recovery_passphrase", verificationPassphrase);
      form.append("actor_label", "本机家庭管理员");
      const result = await api<FamilySecurity>(
        `/api/v1/families/${familyId}/security/verify-recovery`,
        { method: "POST", body: form },
      );
      setSecurity(result);
      setRecoveryFile(null);
      setVerificationPassphrase("");
      setNotice("恢复包已成功还原出同一把主密钥。现在才可以选择是否启用真实资料。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "恢复验证没有成功。");
    } finally {
      setBusy(false);
    }
  }

  async function activateEncryption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!familyId || !activationConfirmed) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<FamilySecurity>(
        `/api/v1/families/${familyId}/security/activate`,
        {
          method: "POST",
          body: JSON.stringify({
            actor_label: "本机家庭管理员",
            data_classification: classification,
          }),
        },
      );
      setSecurity(result);
      setNotice("本机档案已完成静态加密，真实资料模式已启用。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "真实资料模式启用失败。");
    } finally {
      setBusy(false);
    }
  }

  async function createAndVerifyBackup() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<BackupRecord>("/api/v1/backups", {
        method: "POST",
        body: JSON.stringify({ actor_label: "本机家庭管理员" }),
      });
      const verified = await api<BackupRecord>(`/api/v1/backups/${created.id}/verify`, { method: "POST" });
      await loadBackups();
      setSelectedBackupId(verified.id);
      setNotice("备份已生成，并已在新的临时目录中通过数据库与资产完整性验证。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "本机备份没有成功。");
    } finally {
      setBusy(false);
    }
  }

  async function downloadBackup(backup: BackupRecord) {
    setBusy(true);
    setError("");
    try {
      const download = await apiDownload(`/api/v1/backups/${backup.id}/download`);
      const url = URL.createObjectURL(download.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = download.filename;
      link.click();
      URL.revokeObjectURL(url);
      setNotice("备份已下载。备份不包含主密钥、恢复口令或恢复包。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "备份下载没有成功。");
    } finally {
      setBusy(false);
    }
  }

  async function rehearseBackupRecovery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!familyId || !selectedBackupId || !backupRecoveryFile) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const form = new FormData();
      form.append("family_id", familyId);
      form.append("package", backupRecoveryFile);
      form.append("recovery_passphrase", backupPassphrase);
      await api<BackupRecord>(`/api/v1/backups/${selectedBackupId}/rehearse-recovery`, { method: "POST", body: form });
      await loadBackups();
      setBackupRecoveryFile(null);
      setBackupPassphrase("");
      setNotice("完整恢复演练已通过：备份已恢复到新目录，并用恢复包成功解密和校验了其中的文本与媒体。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "完整恢复演练没有成功。");
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
        : security.encryption_status === "active_encrypted"
          ? "本机加密已完成"
          : security.encryption_status === "encryption_failed"
            ? "加密未完成，仍禁止真实资料"
          : "主密钥已就绪，资料待加密";

  return (
    <section className="card security-card">
      <div className="section-heading">
        <span>03</span>
        <div>
          <h2>家庭档案安全</h2>
          <p>主密钥保存在 Mac 钥匙串；恢复包由家人离线保管，系统不留后门。</p>
        </div>
      </div>

      <div className="security-status">
        <div><span>当前状态</span><strong>{statusText}</strong></div>
        <div><span>恢复包</span><strong>{security?.recovery_package_created_at ? "已生成" : "尚未生成"}</strong></div>
        <div><span>恢复验证</span><strong>{security?.recovery_verified_at ? "已通过" : "尚未验证"}</strong></div>
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

      {security?.key_initialized && security.encryption_status !== "active_encrypted" && (
        <form className="recovery-form" onSubmit={exportRecoveryPackage}>
          <div>
            <h3>生成家庭离线恢复包</h3>
            <p className="hint">口令只用于本次加密，不会保存在聆年中。遗失主密钥和恢复材料后，档案无法解密。</p>
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

      {security?.recovery_package_created_at && !security.recovery_verified_at && (
        <form className="recovery-form" onSubmit={verifyRecoveryPackage}>
          <div>
            <h3>用下载的恢复包做一次恢复验证</h3>
            <p className="hint">重新选择刚才下载的 JSON 恢复包并输入口令。验证通过前，后端不会启用真实资料。</p>
          </div>
          <label className="field"><span>恢复包文件</span><input type="file" accept="application/json,.json" required onChange={(event) => setRecoveryFile(event.target.files?.[0] ?? null)} /></label>
          <label className="field"><span>恢复口令</span><input type="password" autoComplete="off" minLength={12} value={verificationPassphrase} onChange={(event) => setVerificationPassphrase(event.target.value)} required /></label>
          <button className="button secondary" disabled={busy || !recoveryFile}>验证恢复包</button>
        </form>
      )}

      {security?.recovery_verified_at && security.encryption_status !== "active_encrypted" && (
        <form className="recovery-form activation-form" onSubmit={activateEncryption}>
          <div>
            <h3>启用真实资料静态加密</h3>
            <p className="hint">系统会加密现有姓名、档案字段、转写、故事、回忆录和媒体。主密钥不会进入数据库或备份。</p>
          </div>
          <label className="field"><span>资料级别</span><select value={classification} onChange={(event) => setClassification(event.target.value as typeof classification)}><option value="authorized_non_sensitive">已授权非敏感家庭资料</option><option value="authorized_sensitive">已授权敏感家庭资料</option></select></label>
          <label className="confirmation-line"><input type="checkbox" checked={activationConfirmed} onChange={(event) => setActivationConfirmed(event.target.checked)} /><span>我已把恢复包交给可信家人离线保管，并理解丢失主密钥和恢复材料后无法解密。</span></label>
          <button className="button primary" disabled={busy || !activationConfirmed}>完成加密并启用真实资料</button>
        </form>
      )}

      {security?.encryption_status === "active_encrypted" && (
        <details className="backup-panel">
          <summary>管理本机备份与恢复演练</summary>
          <div className="backup-panel-content">
            <div>
              <h3>本机备份与完整恢复演练</h3>
              <p className="hint">备份包含 SQLite 快照与媒体清单，但不包含主密钥、恢复口令或恢复包。</p>
            </div>
            <button className="button secondary" disabled={busy} onClick={createAndVerifyBackup}>生成并验证新备份</button>
            <div className="backup-list">
              {backups.map((backup) => (
                <div key={backup.id}><span>{new Date(backup.created_at).toLocaleString("zh-CN")} · {backup.asset_count} 个资产 · {backup.status}</span><button className="button quiet" disabled={busy} onClick={() => downloadBackup(backup)}>下载</button></div>
              ))}
              {!backups.length && <p className="hint">还没有本机备份。</p>}
            </div>
            {backups.length > 0 && (
              <form className="recovery-form" onSubmit={rehearseBackupRecovery}>
                <div><h3>用离线恢复包做完整恢复演练</h3><p className="hint">备份会在全新目录中展开，然后用恢复出的主密钥解密并校验文本和媒体。</p></div>
                <label className="field"><span>选择备份</span><select value={selectedBackupId} onChange={(event) => setSelectedBackupId(event.target.value)}>{backups.map((backup) => <option key={backup.id} value={backup.id}>{new Date(backup.created_at).toLocaleString("zh-CN")} · {backup.status}</option>)}</select></label>
                <label className="field"><span>离线恢复包</span><input type="file" accept="application/json,.json" required onChange={(event) => setBackupRecoveryFile(event.target.files?.[0] ?? null)} /></label>
                <label className="field"><span>恢复口令</span><input type="password" autoComplete="off" minLength={12} value={backupPassphrase} onChange={(event) => setBackupPassphrase(event.target.value)} required /></label>
                <button className="button primary" disabled={busy || !backupRecoveryFile}>开始完整恢复演练</button>
              </form>
            )}
          </div>
        </details>
      )}
    </section>
  );
}
