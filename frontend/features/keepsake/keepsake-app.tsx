"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import AppShell from "@/components/layout/app-shell";
import { api, apiDownload, mediaUrl } from "@/lib/api";
import { pollKeepsake } from "@/lib/keepsake/poll-keepsake";
import {
  isKeepsakeTerminal,
  keepsakeErrorLabel,
  keepsakeStatusLabel,
} from "@/lib/keepsake/status";
import { IS_FORMAL_CLOUD } from "@/lib/runtime";
import type {
  ElderProfile,
  Keepsake,
  KeepsakeAuthorization,
  KeepsakeCatalogItem,
} from "@/lib/types";

type AuthorizationChecks = {
  originalVoiceAuthorized: boolean;
  privateFamilyUse: boolean;
  noImpersonation: boolean;
  originalAudioOnly: boolean;
};

const EMPTY_CHECKS: AuthorizationChecks = {
  originalVoiceAuthorized: false,
  privateFamilyUse: false,
  noImpersonation: false,
  originalAudioOnly: false,
};

function formatBytes(value: number | null): string {
  if (!value) return "";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(value: number | null): string {
  if (!value) return "";
  const seconds = Math.round(value / 1000);
  return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
}

export default function KeepsakeApp() {
  const [profiles, setProfiles] = useState<ElderProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [catalog, setCatalog] = useState<KeepsakeCatalogItem[]>([]);
  const [history, setHistory] = useState<Keepsake[]>([]);
  const [activeKeepsake, setActiveKeepsake] = useState<Keepsake | null>(null);
  const [selectedStoryIds, setSelectedStoryIds] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [actorLabel, setActorLabel] = useState("家庭成员");
  const [checks, setChecks] = useState<AuthorizationChecks>(EMPTY_CHECKS);
  const [initialLoading, setInitialLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [temporaryConnectionIssue, setTemporaryConnectionIssue] = useState(false);
  const [pendingAuthorizationId, setPendingAuthorizationId] = useState<string | null>(null);
  const [pendingIdempotencyKey, setPendingIdempotencyKey] = useState<string | null>(null);
  const pollGenerationRef = useRef(0);

  const selectedProfile = profiles.find((item) => item.id === selectedProfileId) ?? null;
  const allChecksConfirmed = Object.values(checks).every(Boolean);
  const canSubmit = Boolean(
    selectedStoryIds.length > 0 &&
      selectedStoryIds.length <= 10 &&
      title.trim() &&
      actorLabel.trim() &&
      allChecksConfirmed &&
      !busy,
  );

  const updateUrl = useCallback((elderId: string, keepsakeId?: string) => {
    const url = new URL("/keepsake", window.location.origin);
    if (elderId) url.searchParams.set("elder", elderId);
    if (keepsakeId) url.searchParams.set("keepsake", keepsakeId);
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  }, []);

  const refreshHistory = useCallback(async (elderId: string) => {
    const result = await api<Keepsake[]>(`/api/v1/elder-profiles/${elderId}/keepsakes`);
    setHistory(result);
    return result;
  }, []);

  const watchKeepsake = useCallback(async (initial: Keepsake) => {
    const generation = ++pollGenerationRef.current;
    setActiveKeepsake(initial);
    updateUrl(initial.elder_id, initial.id);
    if (isKeepsakeTerminal(initial.status)) return initial;
    try {
      const final = await pollKeepsake(
        initial,
        (id) => api<Keepsake>(`/api/v1/keepsakes/${id}`),
        {
          isPaused: () => document.hidden,
          isCancelled: () => generation !== pollGenerationRef.current,
          onUpdate: (item) => {
            if (generation === pollGenerationRef.current) setActiveKeepsake(item);
          },
          onTemporaryError: () => setTemporaryConnectionIssue(true),
        },
      );
      if (generation !== pollGenerationRef.current) return final;
      setTemporaryConnectionIssue(false);
      setActiveKeepsake(final);
      await refreshHistory(final.elder_id);
      if (final.status === "ready") setNotice("原声视频已在这台 Mac 上生成，可以播放或下载。");
      return final;
    } catch (value) {
      if (generation === pollGenerationRef.current) {
        setError(value instanceof Error ? value.message : "暂时无法读取合成进度，请刷新后再看。");
      }
      return initial;
    }
  }, [refreshHistory, updateUrl]);

  const loadProfileData = useCallback(async (elderId: string, requestedKeepsakeId?: string | null) => {
    const [catalogResult, historyResult] = await Promise.all([
      api<KeepsakeCatalogItem[]>(`/api/v1/elder-profiles/${elderId}/keepsake-catalog`),
      api<Keepsake[]>(`/api/v1/elder-profiles/${elderId}/keepsakes`),
    ]);
    setCatalog(catalogResult);
    setHistory(historyResult);
    setSelectedStoryIds([]);
    setChecks(EMPTY_CHECKS);
    setPendingAuthorizationId(null);
    setPendingIdempotencyKey(null);
    const requested = requestedKeepsakeId
      ? await api<Keepsake>(`/api/v1/keepsakes/${requestedKeepsakeId}`).catch(() => null)
      : null;
    const restorable = requested?.elder_id === elderId
      ? requested
      : historyResult.find((item) => ["queued", "rendering", "failed_retryable"].includes(item.status));
    if (restorable) void watchKeepsake(restorable);
    else {
      setActiveKeepsake(null);
      updateUrl(elderId);
    }
  }, [updateUrl, watchKeepsake]);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        const result = await api<ElderProfile[]>("/api/v1/elder-profiles");
        if (cancelled) return;
        setProfiles(result);
        const params = new URLSearchParams(window.location.search);
        const requestedProfile = params.get("elder");
        const storedProfile = window.localStorage.getItem("niannian.profileId");
        const profileId = [requestedProfile, storedProfile]
          .find((candidate) => candidate && result.some((item) => item.id === candidate)) ?? result[0]?.id;
        if (profileId) {
          setSelectedProfileId(profileId);
          const profile = result.find((item) => item.id === profileId);
          setTitle(`${profile?.preferred_name ?? "家人"}的声音念想`);
          await loadProfileData(profileId, params.get("keepsake"));
        }
      } catch (value) {
        if (!cancelled) setError(value instanceof Error ? value.message : IS_FORMAL_CLOUD ? "暂时无法读取家庭私密空间。" : "暂时无法读取本机档案。");
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    }
    void initialize();
    return () => {
      cancelled = true;
      pollGenerationRef.current += 1;
    };
  }, [loadProfileData]);

  async function changeProfile(profileId: string) {
    pollGenerationRef.current += 1;
    setSelectedProfileId(profileId);
    window.localStorage.setItem("niannian.profileId", profileId);
    const profile = profiles.find((item) => item.id === profileId);
    setTitle(`${profile?.preferred_name ?? "家人"}的声音念想`);
    setActiveKeepsake(null);
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await loadProfileData(profileId);
    } catch (value) {
      setError(value instanceof Error ? value.message : "暂时无法读取这位家人的档案。");
    } finally {
      setBusy(false);
    }
  }

  function toggleStory(item: KeepsakeCatalogItem) {
    if (!item.has_original_audio) return;
    setPendingAuthorizationId(null);
    setPendingIdempotencyKey(null);
    setSelectedStoryIds((current) => current.includes(item.story_id)
      ? current.filter((id) => id !== item.story_id)
      : current.length < 10 ? [...current, item.story_id] : current);
  }

  async function createKeepsake(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile || !canSubmit) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let authorizationId = pendingAuthorizationId;
      let idempotencyKey = pendingIdempotencyKey;
      if (!authorizationId) {
        const authorization = await api<KeepsakeAuthorization>(
          `/api/v1/elder-profiles/${selectedProfile.id}/keepsake-authorizations`,
          {
            method: "POST",
            body: JSON.stringify({
              story_ids: selectedStoryIds,
              actor_label: actorLabel.trim(),
              original_voice_authorized: checks.originalVoiceAuthorized,
              private_family_use: checks.privateFamilyUse,
              no_impersonation: checks.noImpersonation,
              original_audio_only: checks.originalAudioOnly,
            }),
          },
        );
        authorizationId = authorization.id;
        idempotencyKey = crypto.randomUUID();
        setPendingAuthorizationId(authorizationId);
        setPendingIdempotencyKey(idempotencyKey);
      }
      const created = await api<Keepsake>(
        `/api/v1/elder-profiles/${selectedProfile.id}/keepsakes`,
        {
          method: "POST",
          body: JSON.stringify({
            authorization_id: authorizationId,
            title: title.trim(),
            idempotency_key: idempotencyKey,
          }),
        },
      );
      setPendingAuthorizationId(null);
      setPendingIdempotencyKey(null);
      setNotice("制作任务已受理，素材只在这台 Mac 上处理。");
      await refreshHistory(selectedProfile.id);
      void watchKeepsake(created);
    } catch (value) {
      setError(value instanceof Error ? value.message : "没有成功开始制作，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function retryKeepsake() {
    if (!activeKeepsake) return;
    setBusy(true);
    setError("");
    try {
      const retried = await api<Keepsake>(`/api/v1/keepsakes/${activeKeepsake.id}/retry`, {
        method: "POST",
      });
      setNotice(IS_FORMAL_CLOUD ? "已重新开始私密合成，原始素材没有被改动。" : "已重新开始本机合成，原始素材没有被改动。");
      void watchKeepsake(retried);
    } catch (value) {
      setError(value instanceof Error ? value.message : "重试没有成功，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  async function downloadKeepsake(item: Keepsake) {
    if (!item.content_url) return;
    setBusy(true);
    setError("");
    try {
      const result = await apiDownload(item.content_url, { timeoutMs: 20 * 60 * 1000 });
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
      setNotice("MP4 已下载到浏览器的下载目录。");
    } catch (value) {
      setError(value instanceof Error ? value.message : "视频下载没有成功，请重试。");
    } finally {
      setBusy(false);
    }
  }

  const hasReadyResult = activeKeepsake?.status === "ready" && activeKeepsake.content_url;
  const eligibleCount = catalog.filter((item) => item.has_original_audio).length;

  return (
    <AppShell>
      <main className="workspace-main keepsake-main">
        <header className="workspace-header">
          <div className="workspace-heading">
            <p className="eyebrow">原声视频念想</p>
            <h1>把亲口讲过的故事，留成一段视频</h1>
            <p className="subtitle">使用已确认故事和原始录音，{IS_FORMAL_CLOUD ? "在家庭私密空间内合成，不克隆声音，也不发送到外部生成模型。" : "在这台 Mac 上合成，不克隆声音，也不上传第三方。"}</p>
          </div>
          <div className="workspace-controls">
            {profiles.length > 0 && (
              <label className="profile-switcher">
                <span className="profile-switcher-label"><i aria-hidden="true">{selectedProfile?.preferred_name.slice(0, 1) ?? "家"}</i><b>当前讲述者</b></span>
                <select value={selectedProfileId} onChange={(event) => void changeProfile(event.target.value)}>
                  {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.display_name}</option>)}
                </select>
              </label>
            )}
            <div className="privacy-badge">{IS_FORMAL_CLOUD ? "私密合成 · 原声留存" : "本机合成 · 原声留存"}</div>
          </div>
        </header>

        <section className="keepsake-guide" aria-label="制作说明">
          <ol className="keepsake-journey">
            <li data-active={!activeKeepsake ? "true" : undefined}><span>1</span><strong>选择故事</strong></li>
            <li><span>2</span><strong>确认授权</strong></li>
            <li data-active={activeKeepsake && !hasReadyResult ? "true" : undefined}><span>3</span><strong>{IS_FORMAL_CLOUD ? "私密制作" : "本机制作"}</strong></li>
            <li data-active={hasReadyResult ? "true" : undefined}><span>4</span><strong>播放保存</strong></li>
          </ol>
          <div className="keepsake-boundaries" aria-label="制作边界">
            <span>{IS_FORMAL_CLOUD ? "素材只在家庭空间处理" : "素材不离开本机"}</span><span>只用原始录音</span><span>不克隆声线</span><span>{IS_FORMAL_CLOUD ? "不调用生成式视频模型" : "新增费用 0 元"}</span>
          </div>
        </section>
        {error && <div className="message error" role="alert">{error}</div>}
        {notice && <div className="message success" role="status" aria-live="polite">{notice}</div>}
        {temporaryConnectionIssue && <div className="message" role="status">暂时读不到进度，视频仍在{IS_FORMAL_CLOUD ? "私密空间" : "本机"}处理，连接恢复后会继续显示。</div>}

        {initialLoading && (
          <section className="card loading-card" aria-busy="true" aria-live="polite">
            <div className="loading-line" /><div className="loading-line short" />
            <p>正在读取可制作的家庭故事……</p>
          </section>
        )}

        {!initialLoading && profiles.length === 0 && (
          <section className="card empty-state-card">
            <p className="card-kicker">还没有家庭档案</p><h2>先建立一位讲述者</h2>
            <p>完成记录并人工确认故事后，才能制作原声视频念想。</p>
            <Link className="button primary button-link" href="/family">建立家庭档案</Link>
          </section>
        )}

        {!initialLoading && activeKeepsake && (
          <section className={`card keepsake-result-card${hasReadyResult ? " ready" : ""}`} aria-live="polite">
            <div className="section-heading"><span>当前</span><div><h2>{activeKeepsake.title}</h2><p>第 {activeKeepsake.version} 版 · {keepsakeStatusLabel(activeKeepsake.status)}</p></div></div>
            {!isKeepsakeTerminal(activeKeepsake.status) && (
              <div className="keepsake-progress" aria-label={`制作进度 ${activeKeepsake.progress}%`}>
                <div className="progress-track"><span style={{ width: `${activeKeepsake.progress}%` }} /></div>
                <p>{activeKeepsake.progress}% · 第 {activeKeepsake.attempt} 次处理</p>
              </div>
            )}
            {hasReadyResult && (
              <div className="keepsake-player">
                <video controls preload="metadata" src={mediaUrl(activeKeepsake.content_url) ?? undefined}>
                  你的浏览器暂时不能播放这个 MP4，可以直接下载保存。
                </video>
                <p>家庭记忆整理 · 原始录音 · 非实时影像</p>
                <div className="keepsake-result-meta">
                  <span>{formatDuration(activeKeepsake.duration_ms)}</span>
                  <span>{activeKeepsake.width} × {activeKeepsake.height}</span>
                  <span>{formatBytes(activeKeepsake.size_bytes)}</span>
                </div>
                <button className="button primary" disabled={busy} onClick={() => void downloadKeepsake(activeKeepsake)}>下载 MP4 到本地</button>
              </div>
            )}
            {["failed_retryable", "failed_final", "corrupt"].includes(activeKeepsake.status) && (
              <div className="keepsake-failure">
                <p>{keepsakeErrorLabel(activeKeepsake.error_code)}</p>
                {activeKeepsake.status === "failed_retryable" && <button className="button primary" disabled={busy} onClick={() => void retryKeepsake()}>重试{IS_FORMAL_CLOUD ? "视频" : "本机"}合成</button>}
              </div>
            )}
          </section>
        )}

        {!initialLoading && selectedProfile && (!hasReadyResult ? (
          <section className="card keepsake-create-card">
            <div className="section-heading"><span>01</span><div><h2>选择这次要留下的故事</h2><p>只能选择已经人工确认并保留原始录音的故事，最多 10 篇。</p></div></div>
            {catalog.length === 0 ? (
              <div className="keepsake-empty"><p className="empty">还没有可制作的故事。先完成一段录音、人工校对和归档。</p><Link className="button primary button-link" href={`/record?elder=${selectedProfile.id}`}>去记录一段回忆</Link></div>
            ) : (
              <form onSubmit={createKeepsake}>
                <fieldset className="keepsake-story-fieldset">
                  <legend className="sr-only">选择故事</legend>
                  {catalog.map((item) => (
                    <label className={`keepsake-story-option${item.has_original_audio ? "" : " unavailable"}`} key={item.story_id}>
                      <input type="checkbox" checked={selectedStoryIds.includes(item.story_id)} disabled={!item.has_original_audio} onChange={() => toggleStory(item)} />
                      <span><strong>{item.title}</strong><small>{item.life_stage} · {new Date(item.confirmed_at).toLocaleDateString("zh-CN")}{item.image_asset_id ? " · 有照片" : " · 使用故事卡"}</small>{item.unavailable_reason && <em>{item.unavailable_reason}</em>}</span>
                    </label>
                  ))}
                </fieldset>
                <p className="selection-count" role="status">已选择 {selectedStoryIds.length} / 10 篇 · 当前有 {eligibleCount} 篇可用</p>
                <div className="keepsake-form-section">
                  <div className="keepsake-form-intro"><span>02</span><div><h3>写下视频名称</h3><p>这只是家庭内部看到的名称，可以随时制作新版本。</p></div></div>
                  <div className="keepsake-form-grid">
                  <label className="field"><span>视频名称</span><input value={title} maxLength={200} required onChange={(event) => setTitle(event.target.value)} /></label>
                  <label className="field"><span>本次确认人</span><input value={actorLabel} maxLength={80} required onChange={(event) => setActorLabel(event.target.value)} /></label>
                  </div>
                </div>
                <fieldset className="keepsake-authorization">
                  <legend><span>03</span> 请逐项确认本次原声使用</legend>
                  <label><input type="checkbox" checked={checks.originalVoiceAuthorized} onChange={(event) => setChecks((current) => ({ ...current, originalVoiceAuthorized: event.target.checked }))} /><span>我确认有权使用这次所选的原始录音。</span></label>
                  <label><input type="checkbox" checked={checks.privateFamilyUse} onChange={(event) => setChecks((current) => ({ ...current, privateFamilyUse: event.target.checked }))} /><span>这份视频只用于家庭记忆保存。</span></label>
                  <label><input type="checkbox" checked={checks.noImpersonation} onChange={(event) => setChecks((current) => ({ ...current, noImpersonation: event.target.checked }))} /><span>不会把视频用于仿冒、误导或冒充本人。</span></label>
                  <label><input type="checkbox" checked={checks.originalAudioOnly} onChange={(event) => setChecks((current) => ({ ...current, originalAudioOnly: event.target.checked }))} /><span>本次只使用亲口说过的原始声音，不生成新语音。</span></label>
                </fieldset>
                <div className="keepsake-submit-row">
                  <button className="button primary" disabled={!canSubmit}>{busy ? "正在提交……" : pendingAuthorizationId ? `继续提交${IS_FORMAL_CLOUD ? "私密" : "本机"}制作` : `确认授权并开始${IS_FORMAL_CLOUD ? "私密" : "本机"}制作`}</button>
                  <p>合成可能需要几分钟；可以离开或刷新本页，任务不会丢失。</p>
                </div>
              </form>
            )}
          </section>
        ) : (
          <details className="card keepsake-secondary-create">
            <summary>再制作一个新版本</summary>
            <p>旧版本不会覆盖。打开后可重新选择故事并完成一次新授权。</p>
            <button className="button secondary" onClick={() => { setActiveKeepsake(null); updateUrl(selectedProfile.id); setSelectedStoryIds([]); setChecks(EMPTY_CHECKS); }}>开始选择新版本</button>
          </details>
        ))}

        {!initialLoading && selectedProfile && history.length > 0 && (
          <details className="card keepsake-history">
            <summary>查看历史版本（{history.length}）</summary>
            <div className="keepsake-history-list">
              {history.map((item) => (
                <button key={item.id} onClick={() => void watchKeepsake(item)} aria-current={activeKeepsake?.id === item.id ? "true" : undefined}>
                  <span><strong>第 {item.version} 版 · {item.title}</strong><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></span>
                  <em>{keepsakeStatusLabel(item.status)}</em>
                </button>
              ))}
            </div>
          </details>
        )}

        {!initialLoading && <Link className="back-to-archive" href={`/archive${selectedProfile ? `?elder=${selectedProfile.id}` : ""}`}>返回回忆档案</Link>}
      </main>
    </AppShell>
  );
}
