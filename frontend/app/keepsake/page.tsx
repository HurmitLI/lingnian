import type { Metadata } from "next";

import KeepsakeApp from "@/features/keepsake/keepsake-app";

export const metadata: Metadata = {
  title: "原声视频念想",
};

export default function KeepsakePage() {
  return <KeepsakeApp />;
}
