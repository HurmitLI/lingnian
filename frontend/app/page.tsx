import type { Metadata } from "next";

import WorkspaceApp from "@/features/workspace/workspace-app";

export const metadata: Metadata = {
  title: { absolute: "首页 · 聆年" },
};

export default function HomePage() {
  return <WorkspaceApp view="home" />;
}
