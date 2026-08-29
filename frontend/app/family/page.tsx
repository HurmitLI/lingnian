import type { Metadata } from "next";

import WorkspaceApp from "@/features/workspace/workspace-app";

export const metadata: Metadata = {
  title: "家庭管理",
};

export default function FamilyPage() {
  return <WorkspaceApp view="family" />;
}
