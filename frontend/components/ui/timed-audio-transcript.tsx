"use client";

import { Play } from "lucide-react";
import { useRef } from "react";

type UnknownRecord = Record<string, unknown>;

export type TimedCue = {
  text: string;
  startMs: number;
  endMs: number;
};

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validItem(value: unknown): TimedCue | null {
  if (!isRecord(value) || typeof value.text !== "string") return null;
  if (typeof value.start_ms !== "number" || typeof value.end_ms !== "number") return null;
  if (!Number.isFinite(value.start_ms) || !Number.isFinite(value.end_ms) || value.end_ms <= value.start_ms) return null;
  const text = value.text.trim();
  return text ? { text, startMs: value.start_ms, endMs: value.end_ms } : null;
}

function joinTokens(tokens: string[]) {
  return tokens.reduce((result, token) => {
    if (!result) return token;
    const needsSpace = /[A-Za-z0-9]$/.test(result) && /^[A-Za-z0-9]/.test(token);
    return `${result}${needsSpace ? " " : ""}${token}`;
  }, "");
}

function groupTokenCues(cues: TimedCue[]) {
  const groups: TimedCue[] = [];
  let current: TimedCue[] = [];
  const flush = () => {
    if (!current.length) return;
    groups.push({
      text: joinTokens(current.map((item) => item.text)),
      startMs: current[0].startMs,
      endMs: current[current.length - 1].endMs,
    });
    current = [];
  };
  for (const cue of cues) {
    current.push(cue);
    const duration = cue.endMs - current[0].startMs;
    if (current.length >= 12 || duration >= 4500 || /[。！？!?]$/.test(cue.text)) flush();
  }
  flush();
  return groups;
}

function timingCues(value: unknown, baseMs = 0): TimedCue[] {
  if (!isRecord(value) || value.status !== "available" || !Array.isArray(value.items)) return [];
  const cues = value.items.map(validItem).filter((item): item is TimedCue => Boolean(item));
  const grouped = value.granularity === "token" ? groupTokenCues(cues) : cues;
  return grouped.map((cue) => ({ ...cue, startMs: cue.startMs + baseMs, endMs: cue.endMs + baseMs }));
}

export function extractTimedCues(metadata: Record<string, unknown>): TimedCue[] {
  const direct = timingCues(metadata.timing);
  if (direct.length) return direct;

  const interviewTimeline = metadata.interview_timeline;
  if (!isRecord(interviewTimeline) || !Array.isArray(interviewTimeline.segments)) return [];
  const sampleRate = typeof interviewTimeline.sample_rate === "number" && interviewTimeline.sample_rate > 0
    ? interviewTimeline.sample_rate
    : 16000;
  return interviewTimeline.segments.flatMap((segment) => {
    if (!isRecord(segment) || segment.role !== "answer" || typeof segment.start_frame !== "number") return [];
    return timingCues(segment.asr_timing, (segment.start_frame * 1000) / sampleRate);
  });
}

function formatTimestamp(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export default function TimedAudioTranscript({
  audioUrl,
  metadata,
  fallbackText,
  label = "逐句回听原声",
  compact = false,
}: {
  audioUrl: string;
  metadata: Record<string, unknown>;
  fallbackText?: string;
  label?: string;
  compact?: boolean;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cues = extractTimedCues(metadata);

  function seekTo(cue: TimedCue) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = cue.startMs / 1000;
    void audioRef.current.play().catch(() => undefined);
  }

  return (
    <section className={`timed-audio-transcript${compact ? " compact" : ""}`} aria-label={label}>
      <div className="timed-audio-heading">
        <strong>{label}</strong>
        <small>{cues.length ? "点一句，就从当时的原声开始播放" : "可播放完整原声"}</small>
      </div>
      <audio ref={audioRef} controls preload="metadata" src={audioUrl} className="audio-player" />
      {cues.length > 0 ? (
        <div className="timed-cue-list">
          {cues.map((cue, index) => (
            <button
              type="button"
              key={`${cue.startMs}-${index}`}
              onClick={() => seekTo(cue)}
              aria-label={`从 ${formatTimestamp(cue.startMs)} 播放：${cue.text}`}
            >
              <span><Play size={13} fill="currentColor" aria-hidden="true" />{formatTimestamp(cue.startMs)}</span>
              <p>{cue.text}</p>
            </button>
          ))}
        </div>
      ) : fallbackText ? <p className="timed-audio-fallback">{fallbackText}</p> : null}
    </section>
  );
}
