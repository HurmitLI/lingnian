"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import ShortSceneReferenceReview from "./short-scene-reference-review";

type Props = { sessionId: string; planId: string; inputSha256: string; referenceMode: "illustrative" | "user_photo" };
type ReferenceJob = { id: string; plan_id: string; status: string; error_code?: string };
type Brief = { brief_sha256: string; brief: { scene: { opening_state: string; action: string } } };
const ACTIVE = new Set(["queued", "preparing", "generating"]);
const LABELS: Record<string, string> = { queued: "参考画面已排队，等待家庭节点", preparing: "家庭节点正在准备参考素材", generating: "家庭节点正在处理参考画面，暂未提供可核实的计算百分比", awaiting_reference_review: "参考画面已回传，请核对后制作视频", interrupted: "连接中断，保留原任务，生成结果尚不确定", failed: "参考准备失败，保留原任务供核对", cancelled: "本次参考任务已取消" };

export default function ShortSceneReference({ sessionId, planId, inputSha256, referenceMode }: Props) {
  const [jobs, setJobs] = useState<ReferenceJob[]>([]);
  const [opened, setOpened] = useState(false);
  const [photo, setPhoto] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const alive = useRef(true);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const base = `/api/v1/memory-sessions/${sessionId}/short-scene-plans/${planId}`;
  const storageKey = `lingnian-reference-attempt:${sessionId}:${planId}`;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function load() {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try {
      const rows = await api<ReferenceJob[]>(`/api/v1/memory-sessions/${sessionId}/short-reference-jobs`);
      if (alive.current) { setJobs(rows.filter(j => j.plan_id === planId)); setOpened(true); setError(""); }
    } catch (e) { if (alive.current) setError(`${e instanceof Error ? e.message : "读取失败"} 下方为上次状态，请查回原任务，不要重复生成。`); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => { loadRef.current = load; });
  const active = jobs.some(j => ACTIVE.has(j.status));
  useEffect(() => {
    if (!active || error) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void loadRef.current(); }, 10000);
    return () => clearInterval(timer);
  }, [active, error]);

  function material() {
    const data = new FormData();
    if (photo) data.append("photo", photo);
    return data;
  }
  const options = { input_sha256: inputSha256, photo_use_authorized: referenceMode === "user_photo", subject_consent: referenceMode === "user_photo" };
  async function prepare() {
    if (lock.current || !consent || (referenceMode === "user_photo" && !photo)) return;
    lock.current = true; setBusy(true); setError("");
    try {
      if (localStorage.getItem(storageKey)) throw new Error("此方案已有提交记录，请查看原参考任务；不会另建请求。");
      const data = material(); data.append("options", JSON.stringify(options));
      const value = await api<Brief>(`${base}/reference-input`, { method: "POST", body: data });
      if (alive.current) setBrief(value);
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "参考准备失败"); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  async function submit() {
    if (lock.current || !brief || !consent) return;
    lock.current = true; setBusy(true); setError("");
    try {
      if (!navigator.locks) throw new Error("当前浏览器无法保护重复提交，请使用支持安全锁的浏览器。");
      await navigator.locks.request(storageKey, { ifAvailable: true }, async browserLock => {
        if (!browserLock || localStorage.getItem(storageKey)) throw new Error("已有提交记录，请查看原参考任务。");
        const key = crypto.randomUUID();
        localStorage.setItem(storageKey, key);
        if (localStorage.getItem(storageKey) !== key) throw new Error("请求记录未能保存，没有发送素材。");
        const data = material(); data.append("consent", JSON.stringify({ ...options, brief_sha256: brief.brief_sha256,
          idempotency_key: key, authorize_reference_generation: true, authorize_node_delivery: true, no_impersonation: true }));
        const job = await api<ReferenceJob>(`${base}/reference-jobs`, { method: "POST", body: data });
        if (alive.current) { setJobs([job]); setBrief(null); setPhoto(null); setConsent(false); }
      });
    } catch (e) { if (alive.current) setError(`${e instanceof Error ? e.message : "提交结果未知"} 请查看原参考任务，不要再次生成。`); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  async function resume(job: ReferenceJob) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const next = await api<ReferenceJob>(`/api/v1/short-reference-jobs/${encodeURIComponent(job.id)}/resume`, {
        method: "POST", body: JSON.stringify({ authorize_resume: true, node_checked_no_gpu_submission: true }),
      });
      if (alive.current) setJobs(previous => previous.map(item => item.id === job.id ? next : item));
    } catch (e) { if (alive.current) setError(`${e instanceof Error ? e.message : "重新排队失败"} 请刷新原任务，不要新建参考。`); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  return <section aria-label="准备参考画面">
    <h4>准备这个瞬间的参考画面</h4>
    <p>{referenceMode === "illustrative" ? "不用准备图片，家庭节点会按这段回忆制作一张示意场景。人物外貌不代表亲人的真实形象。" : "选择一张已获授权的原始照片即可，家庭节点会把它准备成适合这个场景的参考画面。"}</p>
    <button className="button secondary" disabled={busy} onClick={load}>{busy ? "正在核对…" : opened ? "刷新参考任务" : "查看或准备参考画面"}</button>
    {error && <p role="alert">{error}</p>}
    {opened && jobs.length === 0 && <div className="card">
      {referenceMode === "user_photo" && <label className="field"><span>人物照片（PNG或JPEG，32MiB以内）</span><input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={e => {
        const file = e.target.files?.[0] ?? null; setBrief(null); setConsent(false); setError("");
        if (file && (file.size === 0 || file.size > 32 * 1024 ** 2)) { setPhoto(null); setError("照片需在32MiB以内，且不能为空。"); } else setPhoto(file);
      }} /></label>}
      <label style={{ display: "flex", gap: 10, padding: "12px 0" }}><input type="checkbox" checked={consent} disabled={busy} onChange={e => { setConsent(e.target.checked); setBrief(null); }} />
        <span>同意把本次采访文字{referenceMode === "user_photo" ? "和所选照片" : ""}交给已授权的家庭节点准备参考画面；我有权使用素材并已取得所需人物同意，不用示意影像冒充真实历史。</span></label>
      {!brief ? <button className="button" disabled={busy || !consent || (referenceMode === "user_photo" && !photo)} onClick={prepare}>准备参考方案</button> : <>
        <p>开场：{brief.brief.scene.opening_state}</p><p>动作：{brief.brief.scene.action}</p>
        <button className="button" disabled={busy || !consent} onClick={submit}>生成一张参考画面</button>
      </>}
    </div>}
    {jobs.map(job => <article className="card" key={job.id}><p role="status">{LABELS[job.status] ?? "状态待核对，请保留原任务"}</p>
      {job.error_code && <p>失败位置：{job.error_code}</p>}
      {["interrupted", "failed"].includes(job.status) && <button className="button secondary" type="button" disabled={busy || !!error} onClick={() => void resume(job)}>已核对节点未提交显卡，重新排队原任务</button>}
      {job.status === "awaiting_reference_review" && <ShortSceneReferenceReview referenceJobId={job.id} sessionId={sessionId} planId={planId} inputSha256={inputSha256} />}
    </article>)}
  </section>;
}
