"use client";

import { useEffect, useRef, useState } from "react";
import { api, mediaUrl } from "@/lib/api";

type ShortJob = {
  id: string;
  plan_id: string;
  status: string;
  progress_percent: number;
  result_asset_id: string | null;
  error_code?: string | null;
  input_review_available?: boolean;
};
const ACTIVE = new Set(["queued", "preparing", "generating"]);
const CANCELLABLE = new Set([...ACTIVE, "awaiting_input_review", "interrupted", "failed"]);
const LABELS: Record<string, string> = {
  queued: "等待生成节点领取 · 尚未开始生成",
  preparing: "节点正在准备素材 · 尚未确认开始生成",
  generating: "家庭生成服务正在处理",
  awaiting_input_review: "参考画面与场景待核对 · 已暂停",
  interrupted: "节点连接中断 · 生成结果尚不确定",
  failed: "本次制作失败 · 不会自动重试",
  cancelled: "已取消任务授权",
  awaiting_full_playback_review: "已有候选片 · 等待完整观看和试听",
};

// The caller keys this component by interview: no request or confirmation crosses stories.
export default function ShortSceneJobs({ sessionId }: { sessionId: string }) {
  const [jobs, setJobs] = useState<ShortJob[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cancelId, setCancelId] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState("");
  const controller = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  useEffect(() => () => controller.current?.abort(), []);

  async function load() {
    if (lock.current) return;
    lock.current = true;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    try {
      const next = await api<ShortJob[]>(`/api/v1/memory-sessions/${sessionId}/short-scene-jobs`, { signal: request.signal });
      if (!request.signal.aborted) {
        setJobs(next); setError("");
        setCheckedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
        setCancelId((id) => next.some((job) => job.id === id && CANCELLABLE.has(job.status)) ? id : null);
      }
    } catch (value) {
      if (!request.signal.aborted) setError(`${value instanceof Error ? value.message : "暂时未能读取任务。"} 下方保留的是上次状态，不能据此判断仍在生成。请手动刷新，不要重复提交。`);
    } finally {
      if (!request.signal.aborted) setBusy(false);
      lock.current = false;
    }
  }
  useEffect(() => { loadRef.current = load; });
  useEffect(() => {
    const changed = (event: Event) => { if ((event as CustomEvent).detail === sessionId) void loadRef.current(); };
    window.addEventListener("lingnian-short-job-changed", changed);
    return () => window.removeEventListener("lingnian-short-job-changed", changed);
  }, [sessionId]);
  const hasActive = jobs?.some((job) => ACTIVE.has(job.status)) ?? false;
  useEffect(() => {
    if (!hasActive || error) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadRef.current();
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [hasActive, error]);

  async function cancel() {
    if (lock.current || !cancelId) return;
    lock.current = true;
    const id = cancelId;
    const request = new AbortController();
    controller.current = request;
    setBusy(true); setError("");
    try {
      const next = await api<ShortJob>(`/api/v1/short-scene-jobs/${encodeURIComponent(id)}/cancel`, {
        method: "POST", signal: request.signal,
      });
      if (!request.signal.aborted) {
        setJobs((previous) => previous?.map((job) => job.id === id ? next : job) ?? null);
        setCancelId(null);
      }
    } catch (value) {
      if (!request.signal.aborted) {
        setCancelId(null);
        setError(`${value instanceof Error ? value.message : "未能确认取消结果。"} 请刷新任务状态；不会自动再发送取消请求。`);
      }
    } finally {
      if (!request.signal.aborted) setBusy(false);
      lock.current = false;
    }
  }

  async function resume(job: ShortJob, checkedNoGpu = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const next = await api<ShortJob>(`/api/v1/short-scene-jobs/${job.id}/resume`, {
        method: "POST",
        body: JSON.stringify({ authorize_resume: true, node_checked_no_gpu_submission: checkedNoGpu }),
      });
      setJobs(previous => previous?.map(item => item.id === job.id ? next : item) ?? null);
    } catch (value) { setError(`${value instanceof Error ? value.message : "继续请求未确认"} 请刷新原任务，不要重新制作。`); }
    finally { lock.current = false; setBusy(false); }
  }

  return <section aria-label="十秒制作任务">
    <h4>十秒制作任务</h4>
    <p>只查看本次采访的独立十秒任务，不包含旧版长片。排队不代表显卡已经开始工作。</p>
    <button type="button" className="button secondary" disabled={busy} onClick={load}>
      {busy ? "正在核对任务…" : jobs === null ? "查看十秒制作任务" : "刷新十秒任务状态"}
    </button>
    {checkedAt && <p><small>任务列表上次读取：{checkedAt}。进行中的任务仅在本页可见时每10秒查询；查询不是重新生成。</small></p>}
    {error && <p role="alert">{error}</p>}
    {jobs?.length === 0 && <p>这次采访还没有十秒制作任务。选景方案和旧版影片不会自动变成新任务。</p>}
    <div aria-live="polite">{jobs?.map((job, index) => <article className="card" key={job.id}>
      <h5>制作任务 {index + 1}</h5>
      <p>{LABELS[job.status] ?? "任务状态尚不能识别，请先核对，不会自动重试"}</p>
      {job.status === "generating" && <p>处理可能包括本机排队；GPU是否已实际计算、计算百分比和剩余时间尚未核实。完成后会自动回传候选片。</p>}
      {job.status === "awaiting_input_review" && <p>需先核对原文、参考人物、年代与场景。请从上方参考画面核对入口确认；旧版手动上传任务仍需核对原节点记录。</p>}
      {job.status === "awaiting_input_review" && job.input_review_available && <button className="button secondary" disabled={busy || !!error} onClick={() => resume(job)}>按已核对素材继续原任务</button>}
      {job.error_code && <p>失败位置：{job.error_code}</p>}
      {job.status === "interrupted" && <p>需要先核对原节点的生成记录。原计算可能仍在进行，不会自动重复提交。</p>}
      {["interrupted", "failed"].includes(job.status) && <button className="button secondary" type="button" disabled={busy || !!error} onClick={() => resume(job, true)}>已核对节点未提交显卡，重新排队原任务</button>}
      {job.status === "cancelled" && <p>后续素材领取和结果回传已停止授权；不保证已经提交的显卡计算立即停止。</p>}
      {job.status === "awaiting_full_playback_review" && job.result_asset_id && <>
        <p>这是待验收候选，不是合格成片。请完整检查人物面部、手脚、服装、场景延续和原声；不能只看第一帧。</p>
        <video style={{ width: "100%", maxWidth: 720 }} controls preload="none" aria-label={`待验收十秒候选 ${index + 1}`}
          src={mediaUrl(`/api/v1/media-assets/${encodeURIComponent(job.result_asset_id)}/content`) ?? undefined}>
          浏览器无法播放这段候选视频。
        </video>
      </>}
      {CANCELLABLE.has(job.status) && !error && <>
        {cancelId === job.id ? <div role="group" aria-label="确认取消十秒任务">
          <p>取消后不再授权领取素材或回传结果，已有录音不会删除。已经提交的显卡计算不一定立即停止。</p>
          <button className="button secondary" type="button" disabled={busy} onClick={() => setCancelId(null)}>先不取消</button>{" "}
          <button className="button secondary" type="button" disabled={busy} onClick={cancel}>确认取消本次制作</button>
        </div> : <button className="button secondary" type="button" disabled={busy} onClick={() => setCancelId(job.id)}>取消本次制作</button>}
      </>}
    </article>)}</div>
  </section>;
}
