"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";

import SecurityPanel from "@/app/security-panel";
import AppShell from "@/components/layout/app-shell";
import { api, ApiError, apiDownload, mediaUrl } from "@/lib/api";
import {
  chooseRecordingMimeType,
  formatRecordingDuration,
  recordingErrorMessage,
  recordingExtension,
} from "@/lib/media/recording";
import { pollWorkflowTask } from "@/lib/workflow/poll-task";
import { isSessionTerminal, sessionStatusLabel, taskStatusLabel } from "@/lib/workflow/status";
import type {
  ElderProfile,
  ElderMemoryContext,
  FamilyPerson,
  FamilyRelationship,
  Health,
  ModelConsent,
  MemoryBook,
  MemorySession,
  MediaLink,
  Reminder,
  SessionDetail,
  TimelineItem,
  WorkflowTask,
} from "@/lib/types";

export type WorkspaceView = "home" | "record" | "archive" | "family";

const LIFE_STAGES = ["童年", "求学", "工作", "婚恋", "育儿", "价值观", "老物件"];
const RELATIONSHIP_LABELS: Record<string, string> = {
  parent: "父母",
  child: "子女",
  spouse: "配偶",
  sibling: "兄弟姐妹",
  grandparent: "祖辈",
  grandchild: "孙辈",
  custom: "自定义",
};

export default function WorkspaceApp({ view }: { view: WorkspaceView }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [profiles, setProfiles] = useState<ElderProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [memoryContext, setMemoryContext] = useState<ElderMemoryContext | null>(null);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [memoryBooks, setMemoryBooks] = useState<MemoryBook[]>([]);
  const [recentSessions, setRecentSessions] = useState<MemorySession[]>([]);
  const [familyPeople, setFamilyPeople] = useState<FamilyPerson[]>([]);
  const [familyRelationships, setFamilyRelationships] = useState<FamilyRelationship[]>([]);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioPreview, setAudioPreview] = useState<string | null>(null);
  const [triggerPreview, setTriggerPreview] = useState<string | null>(null);
  const [correctedText, setCorrectedText] = useState("");
  const [savedCorrectedText, setSavedCorrectedText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [selectedLifeStage, setSelectedLifeStage] = useState(LIFE_STAGES[0]);
  const [cloudConsentChecked, setCloudConsentChecked] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<number | null>(null);

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

  const loadMemoryContext = useCallback(async (profileId: string) => {
    if (!profileId) return;
    const result = await api<ElderMemoryContext>(
      `/api/v1/elder-profiles/${profileId}/memory-context`,
    );
    setMemoryContext(result);
  }, []);

  const loadArchiveTools = useCallback(async (profileId: string) => {
    if (!profileId) return;
    const [reminderResult, bookResult] = await Promise.all([
      api<Reminder[]>(`/api/v1/elder-profiles/${profileId}/reminders`),
      api<MemoryBook[]>(`/api/v1/elder-profiles/${profileId}/memory-books`),
    ]);
    setReminders(reminderResult);
    setMemoryBooks(bookResult);
  }, []);

  const loadRecentSessions = useCallback(async (profileId: string) => {
    if (!profileId) return;
    const result = await api<MemorySession[]>(
      `/api/v1/elder-profiles/${profileId}/memory-sessions?limit=20`,
    );
    setRecentSessions(result);
  }, []);

  const loadFamilyRecords = useCallback(async (familyId: string) => {
    const [peopleResult, relationshipResult] = await Promise.all([
      api<FamilyPerson[]>(`/api/v1/families/${familyId}/people`),
      api<FamilyRelationship[]>(`/api/v1/families/${familyId}/relationships`),
    ]);
    setFamilyPeople(peopleResult);
    setFamilyRelationships(relationshipResult);
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
        const params = new URLSearchParams(window.location.search);
        const requestedProfile = params.get("elder");
        const storedProfile = window.localStorage.getItem("niannian.profileId");
        if (requestedProfile && profileResult.some((item) => item.id === requestedProfile)) {
          setSelectedProfileId(requestedProfile);
        } else if (storedProfile && profileResult.some((item) => item.id === storedProfile)) {
          setSelectedProfileId(storedProfile);
        } else if (profileResult[0]) {
          setSelectedProfileId(profileResult[0].id);
        }

        const requestedSession = params.get("session");
        const storedSession = window.localStorage.getItem("niannian.sessionId");
        const sessionToRestore = requestedSession || storedSession;
        if (sessionToRestore) {
          try {
            const sessionResult = await api<SessionDetail>(
              `/api/v1/memory-sessions/${sessionToRestore}`,
            );
            if (cancelled) return;
            if (isSessionTerminal(sessionResult.session.status)) {
              window.localStorage.removeItem("niannian.sessionId");
              if (requestedSession) {
                const cleanUrl = new URL("/record", window.location.origin);
                cleanUrl.searchParams.set("elder", sessionResult.session.elder_id);
                window.history.replaceState({}, "", `${cleanUrl.pathname}${cleanUrl.search}`);
              }
            } else {
              setDetail(sessionResult);
              setSelectedProfileId(sessionResult.session.elder_id);
              if (sessionResult.transcript) {
                setCorrectedText(sessionResult.transcript.corrected_text);
                setSavedCorrectedText(sessionResult.transcript.corrected_text);
              }
            }
          } catch {
            window.localStorage.removeItem("niannian.sessionId");
          }
        }
      } catch (value) {
        if (!cancelled) showError(value);
      } finally {
        if (!cancelled) setInitialLoading(false);
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
        const [timelineResult, contextResult, reminderResult, bookResult, sessionResult] = await Promise.all([
          api<TimelineItem[]>(`/api/v1/elder-profiles/${selectedProfileId}/timeline`),
          api<ElderMemoryContext>(`/api/v1/elder-profiles/${selectedProfileId}/memory-context`),
          api<Reminder[]>(`/api/v1/elder-profiles/${selectedProfileId}/reminders`),
          api<MemoryBook[]>(`/api/v1/elder-profiles/${selectedProfileId}/memory-books`),
          api<MemorySession[]>(`/api/v1/elder-profiles/${selectedProfileId}/memory-sessions?limit=20`),
        ]);
        if (!cancelled) {
          setTimeline(timelineResult);
          setMemoryContext(contextResult);
          setReminders(reminderResult);
          setMemoryBooks(bookResult);
          setRecentSessions(sessionResult);
        }
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
    if (!selectedProfile?.family_id) return;
    let cancelled = false;
    void Promise.all([
      api<FamilyPerson[]>(`/api/v1/families/${selectedProfile.family_id}/people`),
      api<FamilyRelationship[]>(`/api/v1/families/${selectedProfile.family_id}/relationships`),
    ]).then(([peopleResult, relationshipResult]) => {
      if (!cancelled) {
        setFamilyPeople(peopleResult);
        setFamilyRelationships(relationshipResult);
      }
    }).catch((value: unknown) => {
      if (!cancelled) showError(value);
    });
    return () => { cancelled = true; };
  }, [selectedProfile?.family_id, showError]);

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
      if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
      if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
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

  async function createFamilyMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/families/${selectedProfile.family_id}/people`, {
        method: "POST",
        body: JSON.stringify({
          display_name: form.get("memberName"),
          role: form.get("memberRole"),
        }),
      });
      await loadFamilyRecords(selectedProfile.family_id);
      formElement.reset();
      setNotice("家庭成员已添加。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  function rememberActiveSession(sessionId: string, elderId: string) {
    window.localStorage.setItem("niannian.sessionId", sessionId);
    const url = new URL("/record", window.location.origin);
    url.searchParams.set("elder", elderId);
    url.searchParams.set("session", sessionId);
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  }

  function clearActiveSession(message?: string) {
    window.localStorage.removeItem("niannian.sessionId");
    setDetail(null);
    setAudioFile(null);
    setAudioPreview(null);
    setCorrectedText("");
    setSavedCorrectedText("");
    const url = new URL("/record", window.location.origin);
    if (selectedProfileId) url.searchParams.set("elder", selectedProfileId);
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
    if (message) setNotice(message);
  }

  function leaveSessionForLater() {
    clearActiveSession("当前记录已经保存在本机。以后可以从首页的“继续记录”回来。");
  }

  async function createFamilyRelationship(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const relationshipType = String(form.get("relationshipType"));
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/families/${selectedProfile.family_id}/relationships`, {
        method: "POST",
        body: JSON.stringify({
          from_person_id: form.get("fromPersonId"),
          to_person_id: form.get("toPersonId"),
          relationship_type: relationshipType,
          custom_label: relationshipType === "custom" ? form.get("customRelationship") : null,
          confirmed_by: "本机家庭管理员",
        }),
      });
      await loadFamilyRecords(selectedProfile.family_id);
      formElement.reset();
      setNotice("家庭关系已经人工确认并保存。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function renameFamilyPerson(person: FamilyPerson) {
    const displayName = window.prompt("修改家庭成员的显示名称：", person.display_name)?.trim();
    if (!displayName || displayName === person.display_name || !selectedProfile) return;
    setBusy(true);
    try {
      await api(`/api/v1/people/${person.id}`, {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      });
      await loadFamilyRecords(selectedProfile.family_id);
      await loadProfiles();
      setNotice("家庭成员名称已修改。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function deleteFamilyPerson(person: FamilyPerson) {
    if (!selectedProfile || !window.confirm(`确认删除“${person.display_name}”吗？与其相关的家庭关系也会删除。`)) return;
    setBusy(true);
    try {
      await api<void>(`/api/v1/people/${person.id}`, { method: "DELETE" });
      await loadFamilyRecords(selectedProfile.family_id);
      setNotice("家庭成员与其关系已删除。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function deleteFamilyRelationship(relationship: FamilyRelationship) {
    if (!selectedProfile || !window.confirm("确认删除这条家庭关系吗？")) return;
    setBusy(true);
    try {
      await api<void>(`/api/v1/relationships/${relationship.id}`, { method: "DELETE" });
      await loadFamilyRecords(selectedProfile.family_id);
      setNotice("家庭关系已删除。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function startMemory(lifeStage: string) {
    if (!selectedProfileId) return;
    const needsConfirmation = topicPreference(lifeStage) === "ask_first";
    if (needsConfirmation && !window.confirm(`请先询问讲述者：现在愿意聊“${lifeStage}”吗？\n\n只有对方明确同意后再继续。`)) return;
    setBusy(true);
    setError("");
    try {
      const session = await api<{ id: string }>("/api/v1/memory-sessions", {
        method: "POST",
        body: JSON.stringify({
          elder_id: selectedProfileId,
          life_stage: lifeStage,
          topic_confirmed: needsConfirmation,
        }),
      });
      rememberActiveSession(session.id, selectedProfileId);
      await loadSession(session.id);
      await loadMemoryContext(selectedProfileId);
      await loadRecentSessions(selectedProfileId);
      setAudioFile(null);
      setNotice("回忆问题已准备好，一次只聊一个点。 ");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function startMediaTrigger(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfileId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const file = form.get("triggerImage");
    const triggerKind = String(form.get("triggerKind"));
    if (!(file instanceof File) || !file.size) return;
    const lifeStage = triggerKind === "photo" ? "照片" : "老物件";
    const needsConfirmation = topicPreference(lifeStage) === "ask_first";
    if (needsConfirmation && !window.confirm(`请先询问讲述者：现在愿意用“${lifeStage}”慢慢回想吗？\n\n只有对方明确同意后再继续。`)) return;
    setBusy(true);
    setError("");
    try {
      const session = await api<{ id: string }>("/api/v1/memory-sessions", {
        method: "POST",
        body: JSON.stringify({
          elder_id: selectedProfileId,
          life_stage: lifeStage,
          trigger_kind: triggerKind,
          topic_confirmed: needsConfirmation,
        }),
      });
      const upload = new FormData();
      upload.append("image", file);
      upload.append("trigger_kind", triggerKind);
      upload.append("user_annotation", String(form.get("annotation") ?? ""));
      await api<MediaLink>(`/api/v1/memory-sessions/${session.id}/trigger-image`, {
        method: "POST",
        body: upload,
      });
      rememberActiveSession(session.id, selectedProfileId);
      await loadSession(session.id);
      await loadRecentSessions(selectedProfileId);
      formElement.reset();
      if (triggerPreview) URL.revokeObjectURL(triggerPreview);
      setTriggerPreview(null);
      setNotice("图片已保存在本机。问题只邀请讲述，不会猜测图片中的人物、地点或年代。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function deleteTriggerImage(assetId: string) {
    if (!window.confirm("确认删除这张图片吗？删除后无法从应用内恢复。")) return;
    setBusy(true);
    setError("");
    try {
      await api<void>(`/api/v1/media-assets/${assetId}`, { method: "DELETE" });
      if (detail) await loadSession(detail.session.id);
      setNotice("图片已从本机档案删除。");
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
    setNotice("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("当前浏览器不支持直接录音。请使用最新版 Safari、Chrome 或 Edge，也可以直接选择已有音频。");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const mimeType = chooseRecordingMimeType((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      stream.getAudioTracks().forEach((track) => {
        track.addEventListener("ended", () => {
          if (recorder.state !== "recording") return;
          recorder.stop();
          setIsRecording(false);
          setError("录音因为麦克风断开或权限变化而停止。已录到的部分仍可试听，请确认后再上传。");
        }, { once: true });
      });
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const type = recorder.mimeType || "audio/webm";
        const extension = recordingExtension(type);
        const blob = new Blob(chunksRef.current, { type });
        if (blob.size > 0) {
          const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
          setPreviewFile(new File([blob], `念念录音-${timestamp}.${extension}`, { type }));
          setNotice("录音已停止并暂存在当前浏览器中。请先试听，确认后再上传保存。");
        } else {
          setError("这次没有录到声音。请检查麦克风后再试，或直接选择已有音频。");
        }
        stream.getTracks().forEach((track) => track.stop());
        if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
        mediaRecorderRef.current = null;
        mediaStreamRef.current = null;
      };
      recorder.onerror = () => {
        setError("录音过程中遇到问题，已录到的部分会尽量保留。也可以改为选择已有音频。");
      };
      recorder.start(1000);
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
      setRecordingSeconds(0);
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds((seconds) => seconds + 1);
      }, 1000);
    } catch (value) {
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      setError(recordingErrorMessage(value));
    }
  }

  function stopRecording() {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop();
    if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
    recordingTimerRef.current = null;
    setIsRecording(false);
  }

  function discardAudio() {
    if (audioPreview) URL.revokeObjectURL(audioPreview);
    setAudioPreview(null);
    setAudioFile(null);
    setRecordingSeconds(0);
    setNotice("这段待上传音频已移除，服务器中已保存的内容没有变化。");
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
    return pollWorkflowTask(
      task,
      (taskId) => api<WorkflowTask>(`/api/v1/tasks/${taskId}`),
      {
        isPaused: () => document.visibilityState === "hidden",
        shouldRetryError: (value) => !(value instanceof ApiError) || value.retryable,
        onTemporaryError: () => setNotice("连接暂时中断，任务可能仍在后台处理；念念正在自动重连。"),
        onConnectionRestored: () => setNotice("连接已恢复，正在读取最新处理结果。"),
      },
    );
  }

  async function refreshCurrentSession() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      await loadSession(detail.session.id);
      setNotice("当前记录的处理状态已更新。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
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
      await loadRecentSessions(detail.session.elder_id);
      clearActiveSession("已经跳过。临时音频和转写不会进入故事档案。");
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
      await loadTimeline(selectedProfile.id);
      await loadMemoryContext(selectedProfile.id);
      await loadRecentSessions(selectedProfile.id);
      clearActiveSession("这段故事已由人工确认并归档，可以到“回忆档案”查看。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function updateTopicPreference(
    topicKey: string,
    preference: "welcome" | "ask_first" | "avoid",
  ) {
    if (!selectedProfile) return;
    setBusy(true);
    setError("");
    try {
      await api(
        `/api/v1/elder-profiles/${selectedProfile.id}/topic-preferences/${encodeURIComponent(topicKey)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            topic_key: topicKey,
            preference,
            updated_by: "本机家庭管理员",
          }),
        },
      );
      await loadMemoryContext(selectedProfile.id);
      await loadArchiveTools(selectedProfile.id);
      setNotice(preference === "avoid" ? `以后不会再主动询问“${topicKey}”。` : `“${topicKey}”的话题偏好已更新。`);
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function createLocalReminder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const localTime = String(form.get("remindAt") ?? "");
    if (!localTime) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/elder-profiles/${selectedProfile.id}/reminders`, {
        method: "POST",
        body: JSON.stringify({
          topic_key: form.get("topicKey"),
          remind_at: new Date(localTime).toISOString(),
          idempotency_key: crypto.randomUUID(),
        }),
      });
      await loadArchiveTools(selectedProfile.id);
      formElement.reset();
      setNotice("本机提醒已保存；不会发送微信、短信或系统通知。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function acknowledgeReminder(reminderId: string) {
    if (!selectedProfile) return;
    setBusy(true);
    try {
      await api(`/api/v1/reminders/${reminderId}/shown`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadArchiveTools(selectedProfile.id);
      setNotice("这条提醒已经温和显示一次，不会反复打扰。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function generateMemoryBook() {
    if (!selectedProfile) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/elder-profiles/${selectedProfile.id}/memory-books`, {
        method: "POST",
        body: JSON.stringify({ created_by: "本机家庭管理员" }),
      });
      await loadArchiveTools(selectedProfile.id);
      setNotice("新的文字回忆录版本已生成，只包含人工确认的故事。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function downloadMemoryBook(book: MemoryBook, format: "markdown" | "pdf") {
    setBusy(true);
    setError("");
    try {
      const download = await apiDownload(`/api/v1/memory-books/${book.id}/${format}`);
      const url = URL.createObjectURL(download.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = download.filename;
      link.click();
      URL.revokeObjectURL(url);
      if (selectedProfile) await loadArchiveTools(selectedProfile.id);
      setNotice(`回忆录第 ${book.version} 版 ${format === "pdf" ? "PDF" : "Markdown"} 已下载。`);
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  const latestFailedTask = detail?.tasks.find((task) => task.status === "failed_retryable");
  const processingTask = detail?.tasks.find((task) => ["queued", "running"].includes(task.status));
  const existingAudio = detail?.media_assets.find((asset) => asset.kind === "audio_original");
  const existingTrigger = detail?.media_assets.find((asset) => ["photo_original", "old_object_original"].includes(asset.kind));
  const topicPreference = (stage: string) =>
    memoryContext?.preferences.find((item) => item.topic_key === stage)?.preference ?? "welcome";
  const stageCoverage = (stage: string) =>
    memoryContext?.coverage.find((item) => item.life_stage === stage);
  const availableStages = LIFE_STAGES.filter((stage) => topicPreference(stage) !== "avoid");
  const activeLifeStage = availableStages.includes(selectedLifeStage)
    ? selectedLifeStage
    : (availableStages[0] ?? "");
  const dueReminders = reminders.filter((item) => item.status === "due");
  const openSessions = recentSessions.filter(
    (item) => !["ARCHIVED", "SKIPPED"].includes(item.status),
  );
  const pageMeta: Record<WorkspaceView, { eyebrow: string; title: string; subtitle: string }> = {
    home: {
      eyebrow: "家庭记忆首页",
      title: selectedProfile ? `今天，陪${selectedProfile.preferred_name}聊一点` : "从一位家人开始",
      subtitle: "不用一次讲完。一段声音、一张照片，都可以慢慢成为留给家人的念想。",
    },
    record: {
      eyebrow: "开始记录",
      title: "一次，只聊一个回忆",
      subtitle: "先选择一个愿意谈的话题，再录音、校对并由家人确认。",
    },
    archive: {
      eyebrow: "回忆档案",
      title: selectedProfile ? `${selectedProfile.preferred_name}的故事` : "家人的故事",
      subtitle: "这里只收录经过人工确认的内容，也可以生成长期保存的回忆录。",
    },
    family: {
      eyebrow: "家庭管理",
      title: "把人物、意愿和安全设置好",
      subtitle: "家庭关系、不要再问的话题、加密和恢复材料，都由家人明确决定。",
    },
  };

  return (
    <AppShell>
    <main className="workspace-main">
      <header className="workspace-header">
        <div className="workspace-heading">
          <p className="eyebrow">{pageMeta[view].eyebrow}</p>
          <h1>{pageMeta[view].title}</h1>
          <p className="subtitle">{pageMeta[view].subtitle}</p>
        </div>
        <div className="workspace-controls">
          {profiles.length > 0 && (
            <label className="profile-switcher">
              <span>当前讲述者</span>
              <select value={selectedProfileId} onChange={(event) => { setSelectedProfileId(event.target.value); setCloudConsentChecked(false); }}>
                {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.display_name}</option>)}
              </select>
            </label>
          )}
          <div className="privacy-badge">本机加密 · 仅家人可见</div>
        </div>
      </header>

      {(health?.asr_provider === "mock" || health?.llm_provider === "mock") && (
        <div className="mock-banner" role="status">
          当前使用模拟能力：ASR {health?.asr_provider ?? "…"} / LLM {health?.llm_provider ?? "…"}。可验证流程，不代表真实模型验收。
        </div>
      )}
      {error && <div className="message error" role="alert">{error}</div>}
      {notice && <div className="message success" role="status" aria-live="polite">{notice}</div>}

      {initialLoading && (
        <section className="card loading-card" aria-busy="true" aria-live="polite">
          <div className="loading-line" />
          <div className="loading-line short" />
          <p>正在读取本机家庭档案……</p>
        </section>
      )}

      {!initialLoading && view === "home" && (
        <>
          {selectedProfile ? (
            <section className="home-grid" aria-label="家庭记忆概览">
              <article className="card welcome-card">
                <p className="card-kicker">今天可以做的事</p>
                <h2>留下一小段声音，就很好</h2>
                <p>念念会先在本机保存录音和转写，只有家人校对、确认后，故事才会进入档案。</p>
                <Link className="button primary button-link" href={`/record?elder=${selectedProfile.id}`}>
                  开始记录一段回忆
                </Link>
                {openSessions[0] && (
                  <Link className="continue-link" href={`/record?elder=${selectedProfile.id}&session=${openSessions[0].id}`}>
                    继续上次的“{openSessions[0].life_stage}”记录 · {sessionStatusLabel(openSessions[0].status)}
                  </Link>
                )}
              </article>
              <div className="overview-cards">
                <article className="overview-card"><span>已确认故事</span><strong>{timeline.length}</strong><small>只统计家人确认后的内容</small></article>
                <article className="overview-card"><span>已覆盖阶段</span><strong>{memoryContext?.coverage.filter((item) => item.confirmed_story_count > 0).length ?? 0}<small> / 7</small></strong><small>慢慢来，不需要一次讲完</small></article>
                <article className="overview-card"><span>待继续记录</span><strong>{openSessions.length}</strong><small>刷新页面也能找回来</small></article>
              </div>
            </section>
          ) : (
            <section className="card empty-state-card">
              <p className="card-kicker">还没有家庭档案</p>
              <h2>先建立一位讲述者</h2>
              <p>只需要一个称呼就能开始，其他资料以后再慢慢补充。</p>
              <Link className="button primary button-link" href="/family">建立家庭档案</Link>
            </section>
          )}

          {dueReminders.length > 0 && (
            <section className="card home-reminders" aria-labelledby="home-reminder-title">
              <div><p className="card-kicker">温和提醒</p><h2 id="home-reminder-title">之前约好，可以再问一次了</h2></div>
              {dueReminders.map((item) => <p key={item.id}>“{item.topic_key}” · 只在念念页面提醒，不会自动联系任何人。</p>)}
              <Link className="button secondary button-link" href="/archive">查看提醒与回忆录</Link>
            </section>
          )}

          {selectedProfile && timeline.length > 0 && (
            <section className="card recent-story-card">
              <div className="section-heading"><span>最近</span><div><h2>刚刚保存的故事</h2><p>这些内容都经过家人确认。</p></div></div>
              {timeline.slice(0, 1).map((item) => (
                <article key={item.story.id}>
                  <time>{new Date(item.story.confirmed_at).toLocaleDateString("zh-CN")}</time>
                  <h3>{item.story.title}</h3>
                  <p>{item.story.body}</p>
                </article>
              ))}
              <Link className="text-link" href="/archive">查看全部回忆</Link>
            </section>
          )}
        </>
      )}

      {!initialLoading && (view === "family" || profiles.length === 0) && <section className="card profile-card">
        <div className="section-heading">
          <span>01</span>
          <div>
            <h2>{profiles.length > 0 ? "讲述者档案" : "建立讲述者档案"}</h2>
            <p>{profiles.length > 0 ? "选择当前要记录回忆的家人；资料只用于整理家庭记忆。" : "先用一位家人的称呼开始，其他资料可以以后再补。"}</p>
          </div>
        </div>
        {selectedProfile && <p className="current-profile-line">当前记录：<strong>{selectedProfile.display_name}</strong>。如需切换，使用页面顶部的“当前讲述者”。</p>}
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
        {selectedProfile && (
          <details className="family-panel">
            <summary>维护家庭成员与关系</summary>
            <div className="family-records-grid">
              <div>
                <h3>家庭成员</h3>
                <div className="family-person-list">
                  {familyPeople.map((person) => (
                    <div key={person.id}>
                      <span><strong>{person.display_name}</strong><small>{person.role === "elder" ? "讲述者" : "家庭成员"}</small></span>
                      <span className="record-actions"><button className="button quiet" disabled={busy} onClick={() => renameFamilyPerson(person)}>改名</button>{person.role !== "elder" && <button className="button quiet danger" disabled={busy} onClick={() => deleteFamilyPerson(person)}>删除</button>}</span>
                    </div>
                  ))}
                </div>
                <form className="inline-record-form" onSubmit={createFamilyMember}>
                  <label className="field"><span>显示名称</span><input name="memberName" maxLength={80} required /></label>
                  <label className="field"><span>角色</span><select name="memberRole"><option value="family_member">家庭成员</option><option value="caregiver">照护者</option><option value="archive_manager">档案管理者</option></select></label>
                  <button className="button secondary" disabled={busy}>添加成员</button>
                </form>
              </div>
              <div>
                <h3>已确认关系</h3>
                <div className="relationship-list">
                  {familyRelationships.map((relationship) => {
                    const from = familyPeople.find((person) => person.id === relationship.from_person_id)?.display_name ?? "未知成员";
                    const to = familyPeople.find((person) => person.id === relationship.to_person_id)?.display_name ?? "未知成员";
                    return <div key={relationship.id}><span>{from} <strong>{relationship.custom_label || RELATIONSHIP_LABELS[relationship.relationship_type]}</strong> {to}</span><button className="button quiet danger" disabled={busy} onClick={() => deleteFamilyRelationship(relationship)}>删除</button></div>;
                  })}
                  {!familyRelationships.length && <p className="hint">还没有建立家庭关系。</p>}
                </div>
                {familyPeople.length >= 2 && (
                  <form className="inline-record-form relationship-form" onSubmit={createFamilyRelationship}>
                    <label className="field"><span>从</span><select name="fromPersonId">{familyPeople.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label>
                    <label className="field"><span>关系</span><select name="relationshipType">{Object.entries(RELATIONSHIP_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                    <label className="field"><span>到</span><select name="toPersonId">{[...familyPeople].reverse().map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label>
                    <label className="field"><span>自定义称谓（仅选自定义时）</span><input name="customRelationship" maxLength={80} /></label>
                    <button className="button secondary" disabled={busy}>确认并保存关系</button>
                  </form>
                )}
              </div>
            </div>
          </details>
        )}
      </section>}

      {!initialLoading && view === "family" && selectedProfile && (
        <section className="card preference-card">
          <div className="section-heading">
            <span>02</span>
            <div><h2>讲述意愿</h2><p>默认可以聊。只有想改成“先问我”或“不要再问”时才需要进入设置。</p></div>
          </div>
          <details className="preference-settings">
            <summary>查看或修改 7 个话题意愿</summary>
            <div className="topic-list preference-list">
              {LIFE_STAGES.map((stage) => (
                <label key={stage}>
                  <span>{stage}</span>
                  <select value={topicPreference(stage)} disabled={busy} onChange={(event) => updateTopicPreference(stage, event.target.value as "welcome" | "ask_first" | "avoid")}>
                    <option value="welcome">愿意聊</option>
                    <option value="ask_first">先问我是否愿意</option>
                    <option value="avoid">不要再问</option>
                  </select>
                </label>
              ))}
            </div>
          </details>
        </section>
      )}

      {!initialLoading && view === "family" && <SecurityPanel key={selectedProfile?.family_id ?? "no-family"} familyId={selectedProfile?.family_id ?? null} />}

      {!initialLoading && view === "record" && !detail && <section className="card entry-card">
        <div className="section-heading"><span>01</span><div><h2>今天想从哪一段聊起？</h2><p>只选一个话题，念念会准备一个温和的问题。</p></div></div>
        <div className="stage-grid">
          {LIFE_STAGES.map((stage) => {
            const coverage = stageCoverage(stage);
            const avoided = topicPreference(stage) === "avoid";
            return (
              <button className={`stage-button${avoided ? " avoided" : ""}`} key={stage} disabled={!selectedProfileId || busy || avoided} onClick={() => startMemory(stage)}>
                <span>{stage}</span>
                <small>{avoided ? "不再询问" : coverage ? `${coverage.confirmed_story_count} 篇已确认` : "尚未开始"}</small>
              </button>
            );
          })}
        </div>
        <div className="stage-select-form">
          <label className="field">
            <span>选择一个话题</span>
            <select value={activeLifeStage} onChange={(event) => setSelectedLifeStage(event.target.value)}>
              {availableStages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
            </select>
          </label>
          <button type="button" className="button primary" disabled={!activeLifeStage || !selectedProfileId || busy} onClick={() => activeLifeStage && startMemory(activeLifeStage)}>准备一个问题</button>
        </div>
        {selectedProfile && (
          <details className="secondary-entry">
            <summary>也可以用一张照片或老物件开始</summary>
            <form className="media-trigger-form" onSubmit={startMediaTrigger}>
              <div><h3>用照片或老物件触发回忆</h3><p className="hint">图片只保存在本机；系统不会识别人脸，也不会推断人物、地点、年代或事件。</p></div>
              <label className="field"><span>触发类型</span><select name="triggerKind"><option value="photo">照片</option><option value="old_object">老物件</option></select></label>
              <label className="field"><span>选择图片</span><input name="triggerImage" type="file" accept="image/jpeg,image/png,image/webp" required onChange={(event) => { const file = event.target.files?.[0]; if (triggerPreview) URL.revokeObjectURL(triggerPreview); setTriggerPreview(file ? URL.createObjectURL(file) : null); }} /></label>
              <label className="field"><span>家人明确知道的信息（可选）</span><input name="annotation" maxLength={1000} placeholder="例如：这是外婆明确说过的旧院子" /></label>
              {triggerPreview && <Image unoptimized width={420} height={300} className="trigger-preview" src={triggerPreview} alt="待上传图片预览" />}
              <button className="button secondary" disabled={busy}>保存图片并开始回忆</button>
            </form>
          </details>
        )}
      </section>}

      {!initialLoading && view === "record" && detail && (
        <section className="card memory-card">
          <div className="session-toolbar"><span>当前只完成这一段</span><button type="button" className="button quiet" disabled={busy || isRecording} onClick={leaveSessionForLater}>稍后继续，返回选题</button></div>
          <div className="session-meta"><span>{detail.session.life_stage}</span><strong>{sessionStatusLabel(detail.session.status)}</strong></div>
          <blockquote>{detail.session.question_text}</blockquote>
          {existingTrigger && (
            <div className="saved-trigger">
              <Image unoptimized width={560} height={420} className="trigger-preview" src={mediaUrl(existingTrigger.content_url) ?? ""} alt="本次回忆的触发图片" />
              {detail.session.status !== "ARCHIVED" && <button className="button quiet danger" disabled={busy} onClick={() => deleteTriggerImage(existingTrigger.id)}>删除这张图片</button>}
            </div>
          )}
          {!(["SKIPPED", "ARCHIVED"].includes(detail.session.status)) && <button className="button quiet danger" disabled={busy} onClick={skipSession}>这次不想讲，直接跳过</button>}

          {!(["SKIPPED", "ARCHIVED"].includes(detail.session.status)) && (
            <div className="workflow-block">
              <h3>录音或上传</h3>
              <p className="hint">开始后请慢慢讲。录音停止前只暂存在当前浏览器，点击“确认上传”后才会保存到本机档案。</p>
              <div className="button-row">
                {!isRecording ? <button type="button" className="button secondary" onClick={startRecording} disabled={busy}>开始录音</button> : <button type="button" className="button recording" onClick={stopRecording}>停止录音</button>}
                <label className={`button secondary file-button${isRecording ? " disabled" : ""}`}>选择已有音频<input aria-label="选择已有音频" type="file" accept="audio/*" disabled={busy || isRecording} onChange={(event) => event.target.files?.[0] && setPreviewFile(event.target.files[0])} /></label>
              </div>
              {isRecording && <div className="recording-live" role="status" aria-live="polite"><span aria-hidden="true" /><strong>正在录音 {formatRecordingDuration(recordingSeconds)}</strong><small>讲完后请点“停止录音”</small></div>}
              {audioPreview && <audio controls src={audioPreview} className="audio-player" />}
              {audioFile && <div className="file-line"><span>{audioFile.name}</span><div className="button-row compact-row"><button type="button" className="button quiet danger" disabled={busy} onClick={discardAudio}>移除这段音频</button><button type="button" className="button primary" disabled={busy || isRecording} onClick={uploadAudio}>确认上传到本机档案</button></div></div>}
              {existingAudio && <p className="hint">已保留原始音频：{existingAudio.original_filename}</p>}
              {processingTask && <div className="task-progress" role="status" aria-live="polite"><div><strong>{taskStatusLabel(processingTask.status)}</strong><span>{Math.max(0, Math.min(100, processingTask.progress))}%</span></div><progress max="100" value={Math.max(0, Math.min(100, processingTask.progress))} /><p>可以留在当前页面等待；如果刷新或离开，任务仍会继续。</p><button type="button" className="button secondary" disabled={busy} onClick={refreshCurrentSession}>刷新处理状态</button></div>}
              {detail.session.status === "AUDIO_UPLOADED" && !processingTask && <button type="button" className="button primary" disabled={busy} onClick={() => runTask("transcription")}>开始本地转写</button>}
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

      {!initialLoading && view === "archive" && selectedProfile && (
        <section className="card timeline-card">
          <div className="section-heading"><span>01</span><div><h2>{selectedProfile.preferred_name}的故事</h2><p>这里只显示经过人工确认的内容。</p></div></div>
          {timeline.length === 0 ? <p className="empty">还没有已确认的故事。</p> : <div className="timeline-list">{timeline.map((item) => <article key={item.story.id}><time>{new Date(item.story.confirmed_at).toLocaleDateString("zh-CN")}</time><h3>{item.story.title}</h3><p>{item.story.body}</p>{mediaUrl(item.audio_url) && <audio controls src={mediaUrl(item.audio_url) ?? undefined} />}</article>)}</div>}
        </section>
      )}

      {!initialLoading && view === "archive" && selectedProfile && (
        <section className="card archive-tools-card">
          <div className="section-heading"><span>02</span><div><h2>更多保存工具</h2><p>有需要时，再设提醒或导出文字回忆录。</p></div></div>
          {dueReminders.map((item) => (
            <div className="due-reminder" role="status" key={item.id}>
              <div><strong>可以温和问一次“{item.topic_key}”了</strong><p>这是你之前设定的本机提醒，不会自动联系任何人。</p></div>
              <button className="button secondary" disabled={busy} onClick={() => acknowledgeReminder(item.id)}>我知道了，不再重复提醒</button>
            </div>
          ))}
          <details className="archive-tool-details">
            <summary>管理提醒和导出回忆录</summary>
            <div className="archive-tools-grid">
              <form className="local-reminder-form" onSubmit={createLocalReminder}>
                <h3>添加应用内提醒</h3>
                <label className="field"><span>话题</span><select name="topicKey">{LIFE_STAGES.map((stage) => <option key={stage} value={stage} disabled={topicPreference(stage) === "avoid"}>{stage}{topicPreference(stage) === "avoid" ? "（不要再问）" : ""}</option>)}</select></label>
                <label className="field"><span>提醒时间</span><input name="remindAt" type="datetime-local" required /></label>
                <button className="button secondary" disabled={busy}>保存本机提醒</button>
                <p className="hint">不使用微信、短信、邮件或 macOS 系统通知。</p>
              </form>
              <div className="memory-book-panel">
                <h3>文字回忆录</h3>
                <p className="hint">每次生成一个新版本，旧版本不会被覆盖。Markdown 文件可长期恢复和迁移。</p>
                <button className="button primary" disabled={busy || !memoryContext?.confirmed_facts.length} onClick={generateMemoryBook}>生成新版本</button>
                <div className="book-list">
                  {memoryBooks.length === 0 ? <p className="empty">还没有回忆录版本。</p> : memoryBooks.map((book) => (
                    <div key={book.id}><span>第 {book.version} 版 · {book.story_manifest.length} 篇故事</span><div className="book-actions"><button className="button quiet" disabled={busy} onClick={() => downloadMemoryBook(book, "markdown")}>下载 Markdown</button><button className="button quiet" disabled={busy} onClick={() => downloadMemoryBook(book, "pdf")}>{book.pdf_status === "ready" ? "下载 PDF" : "生成并下载 PDF"}</button></div></div>
                  ))}
                </div>
              </div>
            </div>
          </details>
        </section>
      )}
    </main>
    </AppShell>
  );
}
