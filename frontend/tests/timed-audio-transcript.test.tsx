import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TimedAudioTranscript, { extractTimedCues } from "@/components/ui/timed-audio-transcript";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("原声逐句回听", () => {
  it("从完整采访时间线提取回答时间并换算到整段录音", () => {
    expect(extractTimedCues({
      interview_timeline: {
        sample_rate: 16000,
        segments: [
          { role: "question", start_frame: 0 },
          {
            role: "answer",
            start_frame: 32000,
            asr_timing: {
              status: "available",
              granularity: "sentence",
              items: [{ text: "那时候我十六岁。", start_ms: 500, end_ms: 2600 }],
            },
          },
        ],
      },
    })).toEqual([{ text: "那时候我十六岁。", startMs: 2500, endMs: 4600 }]);
  });

  it("点击文字会从对应原声时间开始播放", () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    render(
      <TimedAudioTranscript
        audioUrl="/memory.wav"
        metadata={{
          timing: {
            status: "available",
            granularity: "sentence",
            items: [{ text: "我第一次去上海。", start_ms: 3200, end_ms: 5600 }],
          },
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /从 0:03 播放/ }));
    expect(screen.getByRole("button", { name: /从 0:03 播放/ })).toHaveTextContent("我第一次去上海");
    expect(document.querySelector("audio")?.currentTime).toBe(3.2);
    expect(play).toHaveBeenCalledOnce();
  });
});
