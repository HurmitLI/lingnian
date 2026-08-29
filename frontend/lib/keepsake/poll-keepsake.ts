import type { Keepsake } from "@/lib/types";
import { isKeepsakeTerminal } from "@/lib/keepsake/status";

type PollOptions = {
  maxWaitMs?: number;
  isPaused?: () => boolean;
  isCancelled?: () => boolean;
  shouldRetryError?: (value: unknown) => boolean;
  onTemporaryError?: (value: unknown, attempt: number) => void;
  onUpdate?: (value: Keepsake) => void;
  now?: () => number;
  sleep?: (milliseconds: number) => Promise<void>;
};

export async function pollKeepsake(
  initial: Keepsake,
  fetchKeepsake: (keepsakeId: string) => Promise<Keepsake>,
  options: PollOptions = {},
): Promise<Keepsake> {
  const maxWaitMs = options.maxWaitMs ?? 16 * 60 * 1000;
  const isPaused = options.isPaused ?? (() => false);
  const isCancelled = options.isCancelled ?? (() => false);
  const shouldRetryError = options.shouldRetryError ?? (() => true);
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  }));
  const startedAt = now();
  let latest = initial;
  let interval = 900;
  let consecutiveFailures = 0;

  if (isKeepsakeTerminal(latest.status)) return latest;

  while (now() - startedAt < maxWaitMs) {
    if (isCancelled()) return latest;
    if (isPaused()) {
      await sleep(1000);
      continue;
    }
    try {
      latest = await fetchKeepsake(latest.id);
      consecutiveFailures = 0;
      options.onUpdate?.(latest);
    } catch (value) {
      if (!shouldRetryError(value)) throw value;
      consecutiveFailures += 1;
      options.onTemporaryError?.(value, consecutiveFailures);
      await sleep(Math.min(1000 * (2 ** (consecutiveFailures - 1)), 5000));
      continue;
    }
    if (isKeepsakeTerminal(latest.status)) return latest;
    await sleep(interval);
    interval = Math.min(Math.round(interval * 1.35), 3500);
  }

  throw new Error("视频仍在这台 Mac 上继续合成。稍后刷新本页，会自动恢复当前进度。");
}
