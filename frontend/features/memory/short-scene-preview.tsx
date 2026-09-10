"use client";

import { useEffect, useRef, useState } from "react";

import { api, mediaUrl } from "@/lib/api";
import type { TimelineItem } from "@/lib/types";
import ShortSceneSelection from "./short-scene-selection";
import ShortSceneJobs from "./short-scene-jobs";

type Candidate = {
  id: string;
  text: string;
  start_frame: number;
  end_frame: number;
  sample_rate: number;
  duration_seconds: number;
};
type Preview = { status: string; generation_ready: false; candidates: Candidate[] };

function Excerpts({ story }: { story: TimelineItem }) {
  const [result, setResult] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function load() {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const preview = await api<Preview>(`/api/v1/memory-sessions/${story.source_session_id}/short-scene-preview`, { signal: current.signal });
      if (!current.signal.aborted) setResult(preview);
    } catch (value) {
      if (!current.signal.aborted) setError(value instanceof Error ? value.message : "暂时未能读取原声片段。");
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }

  return <div>
    <button className="button secondary" type="button" disabled={loading} onClick={load}>
      {loading ? "正在检查原声时间…" : "查看约10秒原声候选"}
    </button>
    {error && <p role="alert">{error}</p>}
    {result && <div aria-live="polite">
      {result.status === "needs_source_timing" && <p>这段旧录音缺少可靠的语句时间轴。需要先补做对齐，不能按字数猜时间，也不需要你重新采访。</p>}
      {result.status === "no_complete_excerpt" && <p>暂时没有找到4～10秒的完整语句，不会强行截断。需要进一步检查原声的自然停顿。</p>}
      {result.candidates.length > 0 && <p>以下按时长接近10秒排序；较短的完整原话会在10秒画面中保留自然停顿。尚未判断哪一段最适合单场景画面，不代表影片已经生成。</p>}
      {result.candidates.map((candidate) => {
        const start = candidate.start_frame / candidate.sample_rate;
        const end = candidate.end_frame / candidate.sample_rate;
        return <article key={candidate.id} className="card">
          <h4>原录音 {start.toFixed(1)}～{end.toFixed(1)} 秒 · {candidate.duration_seconds} 秒</h4>
          <p>{candidate.text}</p>
          {story.audio_url && <audio controls preload="metadata" aria-label={`试听原声 ${start.toFixed(1)} 至 ${end.toFixed(1)} 秒`}
            src={`${mediaUrl(story.audio_url)}#t=${start},${end}`}
            onPlay={(event) => { const audio = event.currentTarget; if (audio.currentTime < start || audio.currentTime >= end) audio.currentTime = start; }}
            onTimeUpdate={(event) => { if (event.currentTarget.currentTime >= end) event.currentTarget.pause(); }} />}
        </article>;
      })}
      {result.candidates.length > 0 && <ShortSceneSelection key={story.source_session_id} sessionId={story.source_session_id} />}
    </div>}
    <ShortSceneJobs key={story.source_session_id} sessionId={story.source_session_id} />
  </div>;
}

export default function ShortScenePreview({ timeline }: { timeline: TimelineItem[] }) {
  const [selected, setSelected] = useState("");
  const story = timeline.find((item) => item.story.id === selected) ?? timeline[0];
  return <section className="card" aria-label="10秒回忆片段">
    <span className="card-kicker">新方向 · 开发验证中</span>
    <h3>把回忆里的一个瞬间，变成10秒画面</h3>
    <p>先理解整段回忆，再选完整原声；只表现一个场景和简单动作。有照片作为参考，没有照片使用示意人物。</p>
    <p>选好原声后准备参考画面，集中核对并确认制作。页面会保留原任务，成片回传后可在这里播放。</p>
    {story ? <>
      <label className="field"><span>选择回忆原声</span><select value={story.story.id} onChange={(event) => setSelected(event.target.value)}>
        {timeline.map((item) => <option key={item.story.id} value={item.story.id}>{item.story.title}</option>)}
      </select></label>
      <Excerpts key={story.source_session_id} story={story} />
    </> : <p>完成一次采访并保存故事后，可以在这里查看原声候选。</p>}
  </section>;
}
