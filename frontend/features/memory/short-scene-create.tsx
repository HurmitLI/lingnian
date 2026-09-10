"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Props = { sessionId: string; planId: string; inputSha256: string; referenceMode: "illustrative" | "user_photo" };
type SavedJob = { id: string; plan_id: string; status: string };
const CHECKS = [
  "我同意导出本场原声和参考图，并发送给已授权的家庭生成节点",
  "我有权使用这张参考图和这段原声，已取得相关人物所需的同意",
  "我不会用示意画面冒充真实历史影像或本人发言",
];

export async function checkReference(file: File) {
  if (file.size === 0 || file.size > 32 * 1024 * 1024) throw new Error("参考PNG需要小于等于32MiB，且不能是空文件。");
  const data = new Uint8Array(await file.slice(0, 24).arrayBuffer());
  if (data.length !== 24 || [137, 80, 78, 71, 13, 10, 26, 10].some((byte, i) => data[i] !== byte)
    || String.fromCharCode(...data.slice(12, 16)) !== "IHDR") throw new Error("请选择PNG场景参考，不要只改文件后缀。");
  const view = new DataView(data.buffer);
  const width = view.getUint32(16), height = view.getUint32(20);
  if (width < 640 || width > 4096 || height < 352 || height > 4096 || Math.abs(width / height - 1280 / 704) >= 0.08)
    throw new Error("需要横向场景参考，建议1280×704。请先重新构图，不要拉伸人物；原始竖版人像不能直接作为场景。");
}

export default function ShortSceneCreate({ sessionId, planId, inputSha256, referenceMode }: Props) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState<string | null>(null);
  const [job, setJob] = useState<SavedJob | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [checks, setChecks] = useState([false, false, false]);
  const lock = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const imageSequence = useRef(0);
  const previewRef = useRef("");
  const storageKey = `lingnian-short-attempt:${sessionId}:${planId}`;
  const base = `/api/v1/memory-sessions/${sessionId}/short-scene-plans/${planId}/jobs`;
  useEffect(() => () => {
    controller.current?.abort(); imageSequence.current += 1;
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  async function prepare() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(""); setReady(false);
    const request = new AbortController(); controller.current = request;
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved && !/^[A-Za-z0-9_-]{8,80}$/.test(saved)) throw new Error("本机请求记录异常，请先核对，不会创建新任务。");
      setAttempt(saved);
      if (saved) {
        const result = await api<SavedJob | null>(`${base}/by-request/${saved}`, { signal: request.signal });
        if (!request.signal.aborted) {
          setJob(result);
          if (!result) setError("此前已尝试提交，但暂未查到已保存任务。请求仍可能在处理中；保留原请求，不会另建任务，请稍后再核对。");
        }
      } else {
        const existing = await api<SavedJob[]>(`/api/v1/memory-sessions/${sessionId}/short-scene-jobs`, { signal: request.signal });
        if (!request.signal.aborted) {
          const result = existing.find((item) => item.plan_id === planId) ?? null;
          setJob(result); setReady(!result);
        }
      }
    } catch (value) {
      if (!request.signal.aborted) setError(`${value instanceof Error ? value.message : "未能准备制作任务。"} 核对完成前不会上传素材。`);
    } finally {
      if (!request.signal.aborted) setBusy(false);
      lock.current = false;
    }
  }

  async function choose(next: File | undefined) {
    const sequence = ++imageSequence.current;
    setFile(null); setChecks([false, false, false]); setError("");
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = ""; setPreview("");
    if (!next) return;
    try {
      await checkReference(next);
      if (sequence !== imageSequence.current) return;
      const url = URL.createObjectURL(next);
      previewRef.current = url; setPreview(url); setFile(next);
    } catch (value) {
      if (sequence === imageSequence.current) setError(value instanceof Error ? value.message : "参考图检查失败。");
    }
  }

  async function submit() {
    if (lock.current || !ready || !file || !checks.every(Boolean) || attempt || job) return;
    lock.current = true; setBusy(true); setError("");
    const request = new AbortController(); controller.current = request;
    try {
      if (!navigator.locks) throw new Error("当前浏览器无法保护多标签页重复提交，请使用支持安全锁的浏览器；本次没有发送。");
      await navigator.locks.request(storageKey, { ifAvailable: true }, async (browserLock) => {
        if (request.signal.aborted) return;
        if (!browserLock) throw new Error("另一个页面正在提交同一方案，请先核对已有任务。");
        const previous = localStorage.getItem(storageKey);
        if (previous) { setAttempt(previous); setReady(false); throw new Error("本方案已有提交记录，请核对原任务，不要重复发送。"); }
        // Store only an opaque request key BEFORE any POST. No audio, photo or text in browser storage.
        const key = crypto.randomUUID();
        localStorage.setItem(storageKey, key);
        if (localStorage.getItem(storageKey) !== key) throw new Error("无法安全保存请求记录，本次没有上传素材。");
        setAttempt(key); setReady(false);
        const data = new FormData();
        data.append("reference", file);
        data.append("consent", JSON.stringify({ input_sha256: inputSha256, idempotency_key: key,
          authorize_material_export: true, authorize_node_delivery: true, reference_rights_confirmed: true,
          subject_consent: true, no_impersonation: true }));
        const result = await api<SavedJob>(base, { method: "POST", body: data, signal: request.signal });
        if (!request.signal.aborted) setJob(result);
      });
    } catch (value) {
      if (!request.signal.aborted) setError(`${value instanceof Error ? value.message : "无法确认本次提交结果。"} 请先核对已有任务，不会自动重新提交。`);
    } finally {
      if (!request.signal.aborted) { setBusy(false); setChecks([false, false, false]); }
      lock.current = false;
    }
  }

  return <section aria-label="准备十秒制作">
    <h4>准备这一个场景的制作任务</h4>
    <p>本方案使用{referenceMode === "user_photo" ? "获授权照片作为人物参考，不等于真实历史影像" : "示意人物，不声称还原亲人长相"}。需要一张符合上方故事的横向场景PNG；自动准备参考画面的入口仍在开发中。</p>
    <button type="button" className="button secondary" disabled={busy} onClick={prepare}>
      {busy ? "正在核对或提交…" : attempt || job ? "核对此前制作请求" : "准备制作素材"}
    </button>
    {error && <p role="alert">{error}</p>}
    {job && <p role="status">已找到这份方案的制作任务。请在下方“十秒制作任务”刷新查看实际状态；这不代表已经开始生成或影片已合格。</p>}
    {ready && !attempt && !job && <div>
      <label className="field"><span>选择已准备的场景参考PNG（仅本机预览）</span>
        <input type="file" accept="image/png,.png" disabled={busy} onChange={(event) => void choose(event.target.files?.[0])} />
      </label>
      {preview && <>
        {/* A local blob is revoked on replacement/unmount; never sent to the image optimizer. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={preview} alt="本次待核对的场景参考，尚未上传" style={{ width: "100%", maxWidth: 640 }} />
        <p>请核对人物、服装、年代和场景是否符合原文。选中图片不等于核对通过，节点仍会停在输入核对。</p>
      </>}
      <fieldset disabled={busy}><legend>仅本次制作的素材授权</legend>
        {CHECKS.map((label, index) => <label key={label} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 0" }}>
          <input type="checkbox" style={{ width: 20, height: 20, flexShrink: 0, marginTop: 3 }} checked={checks[index]}
            onChange={(event) => setChecks((values) => values.map((value, i) => i === index ? event.target.checked : value))} />
          <span>{label}</span>
        </label>)}
      </fieldset>
      <p>与选景时只发送文字不同：本次会把完整采访原声和这张参考图加密交给家庭生成节点，节点按已保存的4～10秒完整原话连续截取；不足10秒的部分保留自然停顿。不会发送给新的付费平台；排队不会绕过输入核对。</p>
      <button type="button" className="button" disabled={busy || !file || !checks.every(Boolean)} onClick={submit}>授权素材投递并创建十秒任务</button>
    </div>}
  </section>;
}
