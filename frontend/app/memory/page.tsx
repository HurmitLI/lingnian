import type { Metadata } from "next";

import MemoryHubApp from "@/features/memory/memory-hub-app";

export const metadata: Metadata = {
  title: { absolute: "家族记忆 · 聆年" },
};

export default function MemoryPage() {
  return <MemoryHubApp />;
}
