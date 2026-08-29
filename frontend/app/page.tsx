"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import SecurityPanel from "@/app/security-panel";
import { api, mediaUrl } from "@/lib/api";
import type {
  ElderProfile,
  Health,
  ModelConsent,
  SessionDetail,
  TimelineItem,
  WorkflowTask,
} from "@/lib/types";

const LIFE_STAGES = ["童年", "求学", "工作", "婚恋", "育儿", "价值观", "老物件"];

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    PROMPT_READY: "问题已准备",
    AUDIO_UPLOADED: "音频已保存",
    TRANSCRIBING: "正在转写",
    TRANSCRIPT_REVIEW: "等待校对",
    ORGANIZING: "正在整理",
    DRAFT_REVIEW: "等待确认",
    ARCHIVED: "已归档",
    SKIPPED: "已跳过",
    FAILED_RETRYABLE: "处理失败，可重试",
  };
  return labels[status] ?? status;
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [profiles, setProfiles] = useState<ElderProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioPreview, setAudioPreview] = useState<string | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [savedCorrectedText, setSavedCorrectedText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [cloudConsentChecked, setCloudConsentChecked] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const selectedProfile = profiles.find((item) => item.id === selectedProfileId) ?? null;
  const unsaved = Boolean(detail?.transcript && correctedText !== savedCorrectedText);
  const requiresCloudConsent = Boolean(
    selectedProfile &&
      selectedProfile.data_classification !== "test" &&
      health?.llm_provider === "qwen",
  );

  const showError = useCallback((value: unknown) => {
    setNotice("");
    setError(value instanceof Error ? value.message : "操作没有成功，请重试。");
  }, []);

  const loadProfiles = useCallback(async () => {
    const result = await api<ElderProfile[]>("/api/v1/elder-profiles");
    setProfiles(result);
    const stored = window.localStorage.getItem("niannian.profileId");
    if (stored && result.some((item) => item.id === stored)) {
      setSelectedProfileId(stored);
    } else if (result[0]) {
      setSelectedProfileId(result[0].id);
    }
  }, []);

  const loadTimeline = useCallback(async (profileId: string) => {
    if (!profileId) return;
    const result = await api<TimelineItem[]>(`/api/v1/elder-profiles/${profileId}/timeline`);
    setTimeline(result);
  }, []);

  const loadSession = useCallback(async (sessionId: string) => {
    const result = await api<SessionDetail>(`/api/v1/memory-sessions/${sessionId}`);
    setDetail(result);
    if (result.transcript) {
      setCorrectedText(result.transcript.corrected_text);
      setSavedCorrectedText(result.transcript.corrected_text);
    }
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        const [healthResult, profileResult] = await Promise.all([
          api<Health>("/api/v1/health"),
          api<ElderProfile[]>("/api/v1/elder-profiles"),
        ]);
        if (cancelled) return;
        setHealth(healthResult);
        setProfiles(profileResult);
        const storedProfile = window.localStorage.getItem("niannian.profileId");
        if (storedProfile && profileResult.some((item) => item.id === storedProfile)) {
          setSelectedProfileId(storedProfile);
        } else if (profileResult[0]) {
          setSelectedProfileId(profileResult[0].id);
        }

        const storedSession = window.localStorage.getItem("niannian.sessionId");
        if (storedSession) {
          try {
            const sessionResult = await api<SessionDetail>(
              `/api/v1/memory-sessions/${storedSession}`,
            );
            if (cancelled) return;
            setDetail(sessionResult);
            if (sessionResult.transcript) {
              setCorrectedText(sessionResult.transcript.corrected_text);
              setSavedCorrectedText(sessionResult.transcript.corrected_text);
            }
          } catch {
            window.localStorage.removeItem("niannian.sessionId");
          }
        }
      } catch (value) {
        if (!cancelled) showError(value);
      }
    }
    void initialize();
    return () => {
      cancelled = true;
    };
  }, [showError]);

  useEffect(() => {
    if (!selectedProfileId) return;
    window.localStorage.setItem("niannian.profileId", selectedProfileId);
    let cancelled = false;
    async function syncTimeline() {
      try {
        const result = await api<TimelineItem[]>(
          `/api/v1/elder-profiles/${selectedProfileId}/timeline`,
        );
        if (!cancelled) setTimeline(result);
      } catch (value) {
        if (!cancelled) showError(value);
      }
    }
    void syncTimeline();
    return () => {
      cancelled = true;
    };
  }, [selectedProfileId, showError]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!unsaved) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  useEffect(() => {
    return () => {
      if (audioPreview) URL.revokeObjectURL(audioPreview);
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, [audioPreview]);

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setBusy(true);
    setError("");
    const form = new FormData(formElement);
    try {
      const family = await api<{ id: string }>("/api/v1/families", {
        method: "POST",
        body: JSON.stringify({
          display_name: form.get("familyName"),
          idempotency_key: crypto.randomUUID(),
        }),
      });
      const profile = await api<ElderProfile>("/api/v1/elder-profiles", {
        method: "POST",
        body: JSON.stringify({
          family_id: family.id,
          display_name: form.get("displayName"),
          preferred_name: form.get("preferredName"),
          birth_year: form.get("birthYear") ? Number(form.get("birthYear")) : null,
          native_place: form.get("nativePlace") || null,
          occupation_summary: form.get("occupation") || null,
        }),
      });
      await loadProfiles();
      setSelectedProfileId(profile.id);
      setNotice("虚构测试档案已保存。");
      formElement.reset();
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function startMemory(lifeStage: string) {
    if (!selectedProfileId) return;
    setBusy(true);
    setError("");
    try {
      const session = await api<{ id: string }>("/api/v1/memory-sessions", {
        method: "POST",
        body: JSON.stringify({ elder_id: selectedProfileId, life_stage: lifeStage }),
      });
      window.localStorage.setItem("niannian.sessionId", session.id);
      await loadSession(session.id);
      setAudioFile(null);
      setNotice("回忆问题已准备好，一次只聊一个点。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  function setPreviewFile(file: File) {
    if (audioPreview) URL.revokeObjectURL(audioPreview);
    setAudioFile(file);
    setAudioPreview(URL.createObjectURL(file));
  }

  async function startRecording() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const preferredTypes = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];
      const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const type = recorder.mimeType || "audio/webm";
        const extension = type.includes("mp4") ? "m4a" : "webm";
        const blob = new Blob(chunksRef.current, { type });
        setPreviewFile(new File([blob], `memory.${extension}`, { type }));
        stream.getTracks().forEach((track) => track.stop());
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      setError("无法使用麦克风。请允许录音权限，或改为上传音频文件。");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
  }

  async function uploadAudio() {
    if (!detail || !audioFile) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("audio", audioFile);
    try {
      await api(`/api/v1/memory-sessions/${detail.session.id}/audio`, {
        method: "POST",
        body: form,
      });
      await loadSession(detail.session.id);
      setNotice("原始音频已安全保留，可以开始转写。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function waitForTask(task: WorkflowTask) {
    let latest = task;
    for (let index = 0; index < 180; index += 1) {
      latest = await api<WorkflowTask>(`/api/v1/tasks/${latest.id}`);
      if (["succeeded", "failed_retryable", "failed_final"].includes(latest.status)) return latest;
      await new Promise((resolve) => window.setTimeout(resolve, 750));
    }
    throw new Error("处理时间超过预期，请刷新页面查看任务状态。 ");
  }

  async function runTask(kind: "transcription" | "organization") {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      const path = kind === "transcription" ? "transcription-tasks" : "organization-tasks";
      let body: Record<string, string> = {};
      if (kind === "organization" && requiresCloudConsent) {
        if (!cloudConsentChecked) {
          throw new Error("请先勾选本次授权，确认只把当前人工校对稿发送给千问整理。");
        }
        const consent = await api<ModelConsent>(
          `/api/v1/memory-sessions/${detail.session.id}/model-consents`,
          {
            method: "POST",
            body: JSON.stringify({ actor_label: "本机家庭管理员" }),
          },
        );
        body = { consent_event_id: consent.id };
      }
      const task = await api<WorkflowTask>(
        `/api/v1/memory-sessions/${detail.session.id}/${path}`,
        { method: "POST", body: JSON.stringify(body) },
      );
      const result = await waitForTask(task);
      await loadSession(detail.session.id);
      if (result.status !== "succeeded") throw new Error(`处理失败（${result.error_code ?? "未知错误"}），可以稍后重试。`);
      setNotice(kind === "transcription" ? "转写已完成，请先人工校对。" : "故事草稿已生成，请逐句核对后再确认。 ");
    } catch (value) {
      showError(value);
    } finally {
      if (kind === "organization") setCloudConsentChecked(false);
      setBusy(false);
    }
  }

  async function retryTask(task: WorkflowTask) {
    setBusy(true);
    try {
      const retried = await api<WorkflowTask>(`/api/v1/tasks/${task.id}/retry`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await waitForTask(retried);
      if (detail) await loadSession(detail.session.id);
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function saveTranscript() {
    if (!detail?.transcript) return;
    setBusy(true);
    try {
      const result = await api<{ corrected_text: string }>(
        `/api/v1/transcripts/${detail.transcript.id}`,
        { method: "PATCH", body: JSON.stringify({ corrected_text: correctedText }) },
      );
      setSavedCorrectedText(result.corrected_text);
      await loadSession(detail.session.id);
      setNotice("人工校对稿已保存，原始转写没有被覆盖。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function skipSession() {
    if (!detail) return;
    setBusy(true);
    try {
      await api(`/api/v1/memory-sessions/${detail.session.id}/skip`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadSession(detail.session.id);
      setNotice("已经跳过。临时音频和转写不会进入故事档案。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function rejectDraft() {
    if (!detail?.story_draft) return;
    setBusy(true);
    try {
      await api(`/api/v1/story-drafts/${detail.story_draft.id}/reject`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadSession(detail.session.id);
      setNotice("草稿已退回，可以继续修改校对稿。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDraft() {
    if (!detail?.story_draft || !selectedProfile) return;
    setBusy(true);
    try {
      await api(`/api/v1/story-drafts/${detail.story_draft.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirmed_by: "测试子女" }),
      });
      await loadSession(detail.session.id);
      await loadTimeline(selectedProfile.id);
      setNotice("这段故事已由人工确认并归档。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  const latestFailedTask = detail?.tasks.find((task) => task.status === "failed_retryable");
  const existingOriginal = detail?.media_assets.find((asset) => asset.is_original);

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">第二阶段 · 本机开发版</p>
          <h1>念念</h1>
          <p className="subtitle">把愿意讲的往事，慢慢留给家人。</p>
        </div>
        <div className="privacy-badge">仅限虚构或授权测试资料</div>
      </header>

      {(health?.asr_provider === "mock" || health?.llm_provider === "mock") && (
        <div className="mock-banner" role="status">
          当前使用模拟能力：ASR {health?.asr_provider ?? "…"} / LLM {health?.llm_provider ?? "…"}。可验证流程，不代表真实模型验收。
        </div>
      )}
      {error && <div className="message error" role="alert">{error}</div>}
      {notice && <div className="message success" role="status">{notice}</div>}

      <section className="card profile-card">
        <div className="section-heading">
          <span>01</span>
          <div><h2>测试档案</h2><p>先用虚构资料建立一位讲述者。</p></div>
        </div>
        {profiles.length > 0 && (
          <label className="field compact">
            <span>当前讲述者</span>
            <select value={selectedProfileId} onChange={(event) => { setSelectedProfileId(event.target.value); setCloudConsentChecked(false); }}>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.display_name}</option>)}
            </select>
          </label>
        )}
        <details className="create-panel" open={profiles.length === 0}>
          <summary>{profiles.length ? "再建一个测试档案" : "创建测试档案"}</summary>
          <form onSubmit={createProfile} className="form-grid">
            <label className="field"><span>虚构家庭名称</span><input name="familyName" required placeholder="例如：虚构的林家" /></label>
            <label className="field"><span>档案显示名</span><input name="displayName" required placeholder="例如：林奶奶（虚构）" /></label>
            <label className="field"><span>希望怎么称呼</span><input name="preferredName" required placeholder="例如：林奶奶" /></label>
            <label className="field"><span>出生年份（可选）</span><input name="birthYear" type="number" min="1900" max="2100" /></label>
            <label className="field"><span>籍贯（可选）</span><input name="nativePlace" /></label>
            <label className="field"><span>职业摘要（可选）</span><input name="occupation" /></label>
            <button className="button primary" disabled={busy}>保存测试档案</button>
          </form>
        </details>
      </section>

      <SecurityPanel key={selectedProfile?.family_id ?? "no-family"} familyId={selectedProfile?.family_id ?? null} />

      <section className="card">
        <div className="section-heading"><span>03</span><div><h2>选一个回忆入口</h2><p>一次一个问题，不催促，也可以直接跳过。</p></div></div>
        <div className="stage-grid">
          {LIFE_STAGES.map((stage) => <button className="stage-button" key={stage} disabled={!selectedProfileId || busy} onClick={() => startMemory(stage)}>{stage}</button>)}
        </div>
      </section>

      {detail && (
        <section className="card memory-card">
          <div className="session-meta"><span>{detail.session.life_stage}</span><strong>{statusLabel(detail.session.status)}</strong></div>
          <blockquote>{detail.session.question_text}</blockquote>
          {!(["SKIPPED", "ARCHIVED"].includes(detail.session.status)) && <button className="button quiet danger" disabled={busy} onClick={skipSession}>这次不想讲，直接跳过</button>}

          {!(["SKIPPED", "ARCHIVED"].includes(detail.session.status)) && (
            <div className="workflow-block">
              <h3>录音或上传</h3>
              <div className="button-row">
                {!isRecording ? <button className="button secondary" onClick={startRecording} disabled={busy}>开始录音</button> : <button className="button recording" onClick={stopRecording}>停止录音</button>}
                <label className="button secondary file-button">选择音频<input type="file" accept="audio/*" onChange={(event) => event.target.files?.[0] && setPreviewFile(event.target.files[0])} /></label>
              </div>
              {audioPreview && <audio controls src={audioPreview} className="audio-player" />}
              {audioFile && <div className="file-line"><span>{audioFile.name}</span><button className="button primary" disabled={busy} onClick={uploadAudio}>确认上传</button></div>}
              {existingOriginal && <p className="hint">已保留原始音频：{existingOriginal.original_filename}</p>}
              {detail.session.status === "AUDIO_UPLOADED" && <button className="button primary" disabled={busy} onClick={() => runTask("transcription")}>开始本地转写</button>}
              {latestFailedTask && (!requiresCloudConsent || latestFailedTask.task_type === "transcription") && <button className="button secondary" disabled={busy} onClick={() => retryTask(latestFailedTask)}>重试失败任务</button>}
            </div>
          )}

          {detail.transcript && (
            <div className="workflow-block">
              <div className="block-title"><h3>人工校对</h3><span>版本 {detail.transcript.version}</span></div>
              <div className="evidence-grid">
                <div><h4>ASR 原始转写</h4><p className="evidence-text">{detail.transcript.raw_text}</p></div>
                <label><h4>人工校对稿</h4><textarea value={correctedText} onChange={(event) => setCorrectedText(event.target.value)} rows={8} /></label>
              </div>
              {unsaved && <p className="unsaved">有尚未保存的修改</p>}
              <div className="button-row">
                <button className="button secondary" disabled={busy || !unsaved} onClick={saveTranscript}>保存校对稿</button>
                {detail.session.status === "TRANSCRIPT_REVIEW" && !requiresCloudConsent && <button className="button primary" disabled={busy || unsaved} onClick={() => runTask("organization")}>按原话整理故事</button>}
              </div>
              {detail.session.status === "TRANSCRIPT_REVIEW" && requiresCloudConsent && (
                <div className="cloud-consent">
                  <strong>本次发送授权</strong>
                  <p>原始录音不会发送。只有当前已保存的人工校对稿会发送给千问，用于生成这一版故事草稿；修改文字或再次整理都要重新授权。</p>
                  <label>
                    <input type="checkbox" checked={cloudConsentChecked} onChange={(event) => setCloudConsentChecked(event.target.checked)} />
                    我确认并授权本次发送当前人工校对稿
                  </label>
                  <button className="button primary" disabled={busy || unsaved || !cloudConsentChecked} onClick={() => runTask("organization")}>授权本次发送并整理故事</button>
                </div>
              )}
            </div>
          )}

          {detail.story_draft && ["DRAFT_REVIEW", "ARCHIVED"].includes(detail.session.status) && (
            <div className="workflow-block">
              <div className="block-title"><h3>故事草稿</h3><span>{detail.story_draft.provider} / {detail.story_draft.model}</span></div>
              <h4 className="draft-title">{detail.story_draft.title}</h4>
              <p className="story-body">{detail.story_draft.body}</p>
              {detail.story_draft.added_facts.length > 0 && <div className="review-warning"><strong>发现需要人工核对的新增信息</strong>{detail.story_draft.added_facts.map((item) => <p key={item}>{item}</p>)}</div>}
              {detail.story_draft.uncertainties.length > 0 && <div className="review-warning"><strong>待核实</strong>{detail.story_draft.uncertainties.map((item) => <p key={item}>{item}</p>)}</div>}
              {detail.session.status === "DRAFT_REVIEW" && <div className="button-row"><button className="button secondary" disabled={busy} onClick={rejectDraft}>退回修改</button><button className="button primary" disabled={busy} onClick={confirmDraft}>人工确认并归档</button></div>}
            </div>
          )}
        </section>
      )}

      {selectedProfile && (
        <section className="card timeline-card">
          <div className="section-heading"><span>04</span><div><h2>{selectedProfile.preferred_name}的时间线</h2><p>这里只显示经过人工确认的故事。</p></div></div>
          {timeline.length === 0 ? <p className="empty">还没有已确认的故事。</p> : <div className="timeline-list">{timeline.map((item) => <article key={item.story.id}><time>{new Date(item.story.confirmed_at).toLocaleDateString("zh-CN")}</time><h3>{item.story.title}</h3><p>{item.story.body}</p>{mediaUrl(item.audio_url) && <audio controls src={mediaUrl(item.audio_url) ?? undefined} />}</article>)}</div>}
        </section>
      )}
    </main>
  );
}
