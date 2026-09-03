import { describe, expect, it } from "vitest";

import {
  audioRms,
  SILENCE_TO_SUBMIT_MS,
  shouldSubmitAfterSilence,
} from "@/lib/media/silence";

describe("连续采访静音检测", () => {
  it("没有听到说话前不会误提交", () => {
    expect(shouldSubmitAfterSilence({
      heardSpeech: false,
      recordingStartedAt: 0,
      lastVoiceAt: 0,
      holdUntil: 0,
      now: 30_000,
    })).toBe(false);
  });

  it("说完并持续静音后提交", () => {
    expect(shouldSubmitAfterSilence({
      heardSpeech: true,
      recordingStartedAt: 0,
      lastVoiceAt: 2_000,
      holdUntil: 0,
      now: 2_000 + SILENCE_TO_SUBMIT_MS,
    })).toBe(true);
  });

  it("句间停顿三秒不会误以为已经说完", () => {
    expect(shouldSubmitAfterSilence({
      heardSpeech: true,
      recordingStartedAt: 0,
      lastVoiceAt: 2_000,
      holdUntil: 0,
      now: 5_000,
    })).toBe(false);
  });

  it("点击还在想时延后静音提交", () => {
    expect(shouldSubmitAfterSilence({
      heardSpeech: true,
      recordingStartedAt: 0,
      lastVoiceAt: 2_000,
      holdUntil: 20_000,
      now: 8_000,
    })).toBe(false);
  });

  it("正确计算音量均方根", () => {
    expect(audioRms(new Float32Array([0.5, -0.5]))).toBeCloseTo(0.5);
  });
});
