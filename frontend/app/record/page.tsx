import type { Metadata } from "next";

import WorkspaceApp from "@/features/workspace/workspace-app";

export const metadata: Metadata = {
  title: "开始记录",
};

export default function RecordPage() {
  return <WorkspaceApp view="record" />;
}
