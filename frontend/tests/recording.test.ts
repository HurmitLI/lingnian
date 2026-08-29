import { describe, expect, it } from "vitest";

import {
  chooseRecordingMimeType,
  formatRecordingDuration,
  recordingErrorMessage,
  recordingExtension,
} from "@/lib/media/recording";

describe("浏览器录音兼容处理", () => {
  it("优先选择浏览器支持的高兼容格式", () => {
    expect(chooseRecordingMimeType((type) => type === "audio/mp4")).toBe("audio/mp4");
    expect(chooseRecordingMimeType(() => false)).toBeUndefined();
    expect(recordingExtension("audio/mp4")).toBe("m4a");
    expect(recordingExtension("audio/webm;codecs=opus")).toBe("webm");
  });

  it("把权限和设备错误转换为可执行提示", () => {
    expect(recordingErrorMessage(new DOMException("denied", "NotAllowedError"))).toContain("地址栏旁允许麦克风");
    expect(recordingErrorMessage(new DOMException("missing", "NotFoundError"))).toContain("没有找到");
  });

  it("显示适合录音中的时长", () => {
    expect(formatRecordingDuration(0)).toBe("00:00");
    expect(formatRecordingDuration(65)).toBe("01:05");
  });
});
