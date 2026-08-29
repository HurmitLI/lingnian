import type { Metadata } from "next";

import WorkspaceApp from "@/features/workspace/workspace-app";

export const metadata: Metadata = {
  title: "回忆档案",
};

export default function ArchivePage() {
  return <WorkspaceApp view="archive" />;
}
