import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/layout/app-shell";

let pathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

describe("正式 Web 主导航", () => {
  beforeEach(() => {
    pathname = "/";
  });

  afterEach(() => {
    cleanup();
  });

  it("提供四个有文字名称的主要入口", () => {
    render(<AppShell><p>内容</p></AppShell>);
    const desktopNavigation = screen.getByRole("navigation", { name: "桌面主导航" });
    expect(desktopNavigation).toHaveTextContent("首页");
    expect(desktopNavigation).toHaveTextContent("开始记录");
    expect(desktopNavigation).toHaveTextContent("回忆档案");
    expect(desktopNavigation).toHaveTextContent("家庭管理");
  });

  it("使用 aria-current 标明当前位置", () => {
    pathname = "/archive";
    render(<AppShell><p>内容</p></AppShell>);
    const desktopNavigation = screen.getByRole("navigation", { name: "桌面主导航" });
    expect(desktopNavigation.querySelector('a[aria-current="page"]')).toHaveTextContent("回忆档案");
  });
});
