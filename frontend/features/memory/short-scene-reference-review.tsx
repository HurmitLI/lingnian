"use client";
import { useEffect, useRef, useState } from "react";
import { api, mediaUrl } from "@/lib/api";

type Review = { review_input_sha256: string; source_current: boolean; review_decision: string | null; required_checks: string[];
  reference_image_url: string; source_photo_url: string | null; source_audio_url: string;
  candidate: { text: string; start_frame: number; end_frame: number; sample_rate: number };
  scene: { context_summary: string; opening_state: string; action: string }; answers: { text: string; question?: string }[] };
type Props = { referenceJobId: string; sessionId: string; planId: string; inputSha256: string };

export default function ShortSceneReferenceReview({ referenceJobId, sessionId, planId, inputSha256 }: Props) {
  const [review, setReview] = useState<Review | null>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [video, setVideo] = useState<{ id: string } | null>(null);
  const lock = useRef(false), alive = useRef(true);
  const storageKey = `lingnian-short-attempt:${sessionId}:${planId}`;
  const base = `/api/v1/short-reference-jobs/${referenceJobId}`;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function load() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setChecked(false);
    try {
      const data = await api<Review>(`${base}/review-input`);
      const jobs = await api<{ id: string; plan_id: string }[]>(`/api/v1/memory-sessions/${sessionId}/short-scene-jobs`);
      if (alive.current) { setReview(data); setVideo(jobs.find(j => j.plan_id === planId) ?? null); }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "读取失败"); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  async function decide(accepted: boolean) {
    if (lock.current || !review || !review.source_current || (accepted && !checked)) return;
    lock.current = true; setBusy(true); setError("");
    try {
      await api(`${base}/review`, { method: "POST", body: JSON.stringify({ review_input_sha256: review.review_input_sha256,
        decision: accepted ? "accepted" : "rejected", checks: Object.fromEntries(review.required_checks.map(k => [k, accepted])) }) });
      if (!accepted) { if (alive.current) { setReview({ ...review, review_decision: "rejected" }); setChecked(false); } return; }
      if (!navigator.locks) throw new Error("当前浏览器不支持安全提交锁，参考确认已保存，视频尚未提交。");
      await navigator.locks.request(storageKey, { ifAvailable: true }, async browserLock => {
        if (!browserLock) throw new Error("另一个页面正在提交，请查看原任务。");
        const previous = localStorage.getItem(storageKey);
        if (previous) {
          const saved = await api<{ id: string } | null>(`/api/v1/memory-sessions/${sessionId}/short-scene-plans/${planId}/jobs/by-request/${encodeURIComponent(previous)}`);
          if (!saved) throw new Error("此前请求结果尚不确定，请重新查看，不会另建任务。");
          if (alive.current) setVideo(saved); return;
        }
        const key = crypto.randomUUID(); localStorage.setItem(storageKey, key);
        if (localStorage.getItem(storageKey) !== key) throw new Error("请求记录未能保存，视频未提交。");
        const job = await api<{ id: string }>(`${base}/video-job`, { method: "POST", body: JSON.stringify({
          input_sha256: inputSha256, review_input_sha256: review.review_input_sha256, idempotency_key: key,
          authorize_material_export: true, authorize_node_delivery: true, reference_rights_confirmed: true, subject_consent: true, no_impersonation: true }) });
        if (alive.current) setVideo(job);
        window.dispatchEvent(new CustomEvent("lingnian-short-job-changed", { detail: sessionId }));
      });
    } catch (e) { if (alive.current) setError(`${e instanceof Error ? e.message : "结果尚未确认"} 请查看原任务，避免重复制作。`); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  const start = review ? review.candidate.start_frame / review.candidate.sample_rate : 0;
  const end = review ? review.candidate.end_frame / review.candidate.sample_rate : 0;
  return <section aria-label="核对原声与参考画面">
    <button className="button secondary" disabled={busy} onClick={load}>{busy ? "正在核对…" : "查看原声与参考画面"}</button>
    {error && <p role="alert">{error}</p>}
    {review && <>
      {!review.source_current && <p role="alert">采访素材已变化，旧确认已失效；请返回重新准备方案。</p>}
      <blockquote>{review.candidate.text}</blockquote>
      <audio controls preload="none" aria-label="试听选中的完整原声" src={`${mediaUrl(review.source_audio_url)}#t=${start},${end}`}
        onPlay={e => { if (e.currentTarget.currentTime < start || e.currentTarget.currentTime >= end) e.currentTarget.currentTime = start; }}
        onTimeUpdate={e => { if (e.currentTarget.currentTime >= end) e.currentTarget.pause(); }} />
      {/* Encrypted family images require the authenticated media route, not an image proxy. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={mediaUrl(review.reference_image_url) ?? undefined} alt="本次实际生成的场景参考，待家庭核对" style={{ width: "100%", maxWidth: 720 }} />
      {review.source_photo_url && <details><summary>对照原始照片</summary>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={mediaUrl(review.source_photo_url) ?? undefined} alt="本次已授权原始照片" style={{ maxWidth: "100%", maxHeight: 360 }} /></details>}
      <p>{review.scene.context_summary}</p><p>开场：{review.scene.opening_state}；动作：{review.scene.action}</p>
      <details><summary>对照整场采访原文</summary>{review.answers.map((a, i) => <div key={i}><p>提问：{a.question ?? "未记录"}</p><p>{a.text}</p></div>)}</details>
      {video ? <p role="status">十秒视频任务已保存。下方“十秒制作任务”可查看进度和播放候选片，刷新页面仍能查回。</p> : <>
        {review.review_decision === "rejected" && <p>已记录参考画面不合适，未授权制作视频。</p>}
        <p>请听完整语句，核对画面与整场回忆一致：人物身份和事件年龄、脸与眼睛、手脚、衣物道具、年代地点、开场姿势，以及单一场景和动作。示意人物不代表真实长相。</p>
        <label style={{ display: "flex", gap: 10, padding: "12px 0" }}><input type="checkbox" checked={checked} disabled={busy || !review.source_current} onChange={e => setChecked(e.target.checked)} />
          <span>我已完成上述核对，同意把这段原声和这张参考图交给已授权家庭节点制作10秒视频；我有权使用素材并已取得所需人物同意。成片仍需完整观看验收。</span></label>
        <button className="button" disabled={busy || !checked || !review.source_current} onClick={() => decide(true)}>确认画面并制作10秒视频</button>{" "}
        <button className="button secondary" disabled={busy || !review.source_current} onClick={() => decide(false)}>画面不合适，暂停制作</button>
      </>}
    </>}
  </section>;
}
