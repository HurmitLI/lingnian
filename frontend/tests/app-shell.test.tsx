import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppShell from "@/components/layout/app-shell";

let pathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

describe("正式 Web 主导航", () => {
  beforeEach(() => {
    pathname = "/";
    window.localStorage.clear();
    delete document.documentElement.dataset.comfortMode;
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

  it("长辈大字模式会持久保存并作用到整页", () => {
    render(<AppShell><p>内容</p></AppShell>);
    fireEvent.click(screen.getByRole("button", { name: /老人模式大字/ }));

    expect(document.documentElement.dataset.comfortMode).toBe("on");
    expect(window.localStorage.getItem("lingnian.comfortMode")).toBe("on");
    expect(screen.getByRole("button", { name: /返回普通模式/ })).toHaveAttribute("aria-pressed", "true");
  });
});
