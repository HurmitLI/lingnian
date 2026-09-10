"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import ShortSceneCreate from "./short-scene-reference";

type Input = {
  status: string;
  input_sha256?: string;
  payload?: {
    answers: { text: string; question?: string | null }[];
    interview_context?: { subject_label: string | null; narrator_label: string | null; narrator_is_subject: boolean | null };
  };
};
type Plan = {
  id: string;
  input_sha256: string;
  status: string;
  reference_mode?: "illustrative" | "user_photo";
  result?: {
    reason?: string;
    candidate?: { text: string; duration_seconds: number };
    selection?: { reason: string; scene: {
      context_summary: string; opening_state: string; action: string;
      illustrative_details: string[];
      facts: { field: string; status: string; value: string | null }[];
    } };
  };
};
const FACT_LABELS: Record<string, string> = { character: "人物", location: "地点", era: "年代", wardrobe: "衣着", prop: "道具" };

export default function ShortSceneSelection({ sessionId }: { sessionId: string }) {
  const [input, setInput] = useState<Input | null>(null);
  const [referenceMode, setReferenceMode] = useState<"illustrative" | "user_photo">("illustrative");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const key = useRef("");
  const lock = useRef(false);
  useEffect(() => () => controller.current?.abort(), []);
  const base = `/api/v1/memory-sessions/${sessionId}`;

  async function prepare() {
    if (lock.current) return;
    lock.current = true;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setBusy(true); setError(""); setConsent(false); setInput(null); setPlan(null);
    try {
      const [next, saved] = await Promise.all([
        api<Input>(`${base}/short-scene-selection-input${referenceMode === "user_photo" ? "?reference_mode=user_photo" : ""}`, { signal: request.signal }),
        api<Plan[]>(`${base}/short-scene-plans`, { signal: request.signal }),
      ]);
      if (!request.signal.aborted) {
        setInput(next);
        setPlan(saved.find((item) => item.input_sha256 === next.input_sha256) ?? null);
        key.current = crypto.randomUUID();
      }
    } catch (value) {
      if (!request.signal.aborted) {
        setInput(null);
        setError(value instanceof Error ? value.message : "未能读取选景依据。");
      }
    } finally {
      if (!request.signal.aborted) setBusy(false);
      lock.current = false;
    }
  }

  async function submit() {
    if (lock.current || !input?.input_sha256 || !consent || plan) return;
    lock.current = true; setBusy(true); setError("");
    const request = new AbortController();
    controller.current = request;
    try {
      const result = await api<Plan>(`${base}/short-scene-plans`, {
        method: "POST", signal: request.signal,
        body: JSON.stringify({ reference_mode: referenceMode, input_sha256: input.input_sha256,
          idempotency_key: key.current, authorize_text_send: true }),
      });
      if (!request.signal.aborted) setPlan(result);
    } catch (value) {
      if (!request.signal.aborted) setError(`${value instanceof Error ? value.message : "请求未完成。"} 请先点击“查看选景结果”，不要反复发送。`);
    } finally {
      if (!request.signal.aborted) { setBusy(false); setConsent(false); }
      lock.current = false;
    }
  }

  const scene = plan?.result?.selection?.scene;
  return <section aria-label="单场景方案">
    <h4>先选一个讲得清楚的瞬间</h4>
    <p>理解整场采访，选择一段完整原声，形成一个场景。本步不生成视频、不上传录音或照片。</p>
    <label className="field"><span>人物参考方式</span><select value={referenceMode} disabled={busy} onChange={(event) => {
      setReferenceMode(event.target.value as typeof referenceMode); setInput(null); setPlan(null); setConsent(false); setError("");
    }}><option value="illustrative">没有照片，使用示意人物</option><option value="user_photo">有获授权照片，用作人物参考</option></select></label>
    <button className="button secondary" type="button" disabled={busy} onClick={prepare}>
      {busy ? "正在处理选景…" : input || plan ? "查看选景结果" : "准备单场景方案"}
    </button>
    {error && <p role="alert">{error}</p>}
    {input && !input.input_sha256 && <p>这次采访还缺少可用的完整原声时间段，暂不发送文字。</p>}
    {input?.input_sha256 && !plan && <div className="card">
      <p>同意后，会把本场采访的提问、回答文字及档案对象和讲述者称呼发送给千问，用来理解背景和选择一个场景。{referenceMode === "illustrative" ? "只作示意设计，不声称还原未提供的长相或历史细节。" : "本步只说明将使用获授权照片，不发送照片本身，也不把参考画面当成真实历史影像。"}</p>
      <details><summary>查看本次发送的采访文字</summary>
        {input.payload?.interview_context && <p>记录对象：{input.payload.interview_context.subject_label ?? "未记录"}；讲述者：{input.payload.interview_context.narrator_label ?? "未记录"}；
          {input.payload.interview_context.narrator_is_subject === true ? "本人讲述" : input.payload.interview_context.narrator_is_subject === false ? "由其他人讲述，不自动当作亲历" : "身份关系未记录，不自动当作本人讲述"}。</p>}
        {input.payload?.answers.map((answer, index) => <div key={index}>
          <p>提问（不是事实依据）：{answer.question ?? "未记录"}</p><p>回答：{answer.text}</p>
        </div>)}
      </details>
      <label style={{ display: "flex", alignItems: "flex-start", gap: 10, minHeight: 44, padding: "12px 0", cursor: "pointer" }}>
        <input type="checkbox" style={{ width: 20, height: 20, flexShrink: 0, marginTop: 3 }} checked={consent} disabled={busy} onChange={(e) => setConsent(e.target.checked)} />
        <span>我同意本次发送采访文字用于选景</span>
      </label>
      <button className="button" type="button" disabled={busy || !consent} onClick={submit}>同意并选择一个场景</button>
    </div>}
    {plan && <div aria-live="polite" className="card">
      {plan.status === "dispatching" && <p>选景请求已保存，尚未取得结果。可以稍后查看，不需要再次发送。</p>}
      {["outcome_unknown", "invalid_proposal", "not_dispatched"].includes(plan.status) && <p role="alert">
        {plan.status === "invalid_proposal" ? "模型方案没有通过原文依据校验，不会拿它生成影片。" : plan.status === "not_dispatched" ? "请求没有安全保存，本次未发送文字。" : "尚未能确认这次模型请求的结果，已保留记录。"}
        不会自动重发或继续生成，需先排查原因。
      </p>}
      {plan.status === "no_suitable_scene" && <p>这段采访暂不适合单场景短片：{plan.result?.reason}</p>}
      {scene && <>
        <h4>待核对的单场景方案 · 尚未生成影片</h4>
        <blockquote>{plan.result?.candidate?.text}</blockquote>
        <p>为什么选它：{plan.result?.selection?.reason}</p>
        <p>整段背景：{scene.context_summary}</p>
        <p>开场：{scene.opening_state}</p><p>唯一动作：{scene.action}</p>
        <dl>{scene.facts.map((fact) => <div key={fact.field}><dt>{FACT_LABELS[fact.field] ?? fact.field}</dt><dd>{fact.status === "known" ? fact.value : "原文未说明，不能当成已知事实"}</dd></div>)}</dl>
        {scene.illustrative_details.length > 0 && <details><summary>需要示意设计的细节</summary>{scene.illustrative_details.map((detail, index) => <p key={index}>{detail}</p>)}</details>}
        <p>目前只校验了格式和逐字出处。人物关系、画面连贯性和完整视听仍需验收，不能把此方案当成合格成片。</p>
        {plan.status === "awaiting_scene_context_review" && plan.reference_mode && <ShortSceneCreate
          key={plan.id} sessionId={sessionId} planId={plan.id} inputSha256={plan.input_sha256} referenceMode={plan.reference_mode} />}
      </>}
    </div>}
  </section>;
}
