const PREFERRED_AUDIO_TYPES = [
  "audio/webm;codecs=opus",
  "audio/mp4",
  "audio/webm",
] as const;

export function chooseRecordingMimeType(
  isSupported: (mimeType: string) => boolean,
): string | undefined {
  return PREFERRED_AUDIO_TYPES.find(isSupported);
}

export function recordingExtension(mimeType: string): "m4a" | "webm" {
  return mimeType.includes("mp4") ? "m4a" : "webm";
}

export function recordingErrorMessage(value: unknown): string {
  const name = value instanceof DOMException
    ? value.name
    : typeof value === "object" && value && "name" in value
      ? String(value.name)
      : "";

  if (name === "NotAllowedError" || name === "SecurityError") {
    return "麦克风权限没有开启。请在浏览器地址栏旁允许麦克风，然后再试一次；也可以直接选择已有音频。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "没有找到可用的麦克风。请连接麦克风，或直接选择已有音频。";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "麦克风正被其他应用占用。请关闭正在录音或通话的应用，再试一次。";
  }
  if (name === "AbortError") {
    return "录音启动被中断了，请确认麦克风仍然连接后重试。";
  }
  return "当前浏览器暂时无法录音。你可以刷新后重试，或直接选择已有音频。";
}

export function formatRecordingDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.max(0, totalSeconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
