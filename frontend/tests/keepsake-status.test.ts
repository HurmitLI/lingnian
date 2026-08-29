import { describe, expect, it } from "vitest";

import {
  isKeepsakeTerminal,
  keepsakeErrorLabel,
  keepsakeStatusLabel,
} from "@/lib/keepsake/status";

describe("原声视频念想状态", () => {
  it("把内部状态转换成家人可理解的说明", () => {
    expect(keepsakeStatusLabel("rendering")).toContain("这台 Mac");
    expect(keepsakeStatusLabel("ready")).toBe("原声视频已生成");
    expect(keepsakeErrorLabel("PROCESS_INTERRUPTED")).toContain("原始故事和录音都还在");
  });

  it("只把可安全停止轮询的状态视为结束", () => {
    expect(isKeepsakeTerminal("queued")).toBe(false);
    expect(isKeepsakeTerminal("rendering")).toBe(false);
    expect(isKeepsakeTerminal("ready")).toBe(true);
    expect(isKeepsakeTerminal("failed_retryable")).toBe(true);
    expect(isKeepsakeTerminal("corrupt")).toBe(true);
  });
});
