// 老年人说话音量可能偏轻、句间停顿也更长，因此这里宁可多等一会儿。
export const SPEECH_RMS_THRESHOLD = 0.01;
export const SILENCE_TO_SUBMIT_MS = 6_000;
export const MIN_RECORDING_MS = 4_000;
export const THINKING_EXTENSION_MS = 20_000;

export function audioRms(samples: Float32Array): number {
  if (!samples.length) return 0;
  let squareSum = 0;
  for (const sample of samples) squareSum += sample * sample;
  return Math.sqrt(squareSum / samples.length);
}

export function shouldSubmitAfterSilence(input: {
  heardSpeech: boolean;
  recordingStartedAt: number;
  lastVoiceAt: number;
  holdUntil: number;
  now: number;
}): boolean {
  if (!input.heardSpeech) return false;
  if (input.now < input.holdUntil) return false;
  if (input.now - input.recordingStartedAt < MIN_RECORDING_MS) return false;
  return input.now - input.lastVoiceAt >= SILENCE_TO_SUBMIT_MS;
}
