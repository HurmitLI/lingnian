import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformAdminConsole } from "@/app/admin/platform-admin-console";

const apiMock = vi.fn();
const replace = vi.fn();
const refreshRouter = vi.fn();

vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class ApiError extends Error {},
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh: refreshRouter }),
}));

describe("平台管理后台", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_LINGNIAN_WORKER_API_BASE = "https://formal-api.example.com/";
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/auth/me") return Promise.resolve({
        user_id: "admin-1",
        username: "platform.admin",
        display_name: "平台管理员",
        platform_role: "admin",
      });
      if (path === "/api/v1/auth/platform/overview") return Promise.resolve({
        admin_username: "platform.admin",
        admin_display_name: "平台管理员",
        family_count: 2,
        account_count: 3,
        active_account_count: 3,
        active_invitation_count: 0,
      });
      if (path === "/api/v1/auth/platform/accounts") return Promise.resolve([
        {
          user_id: "admin-1",
          username: "platform.admin",
          display_name: "平台管理员",
          family_id: "family-1",
          family_name: "运营家庭",
          family_role: "owner",
          platform_role: "admin",
          status: "active",
          created_at: "2026-09-04T00:00:00Z",
          last_login_at: "2026-09-04T01:00:00Z",
        },
      ]);
      if (path === "/api/v1/auth/platform-invitations" && options?.method === "POST") return Promise.resolve({
        id: "invite-1",
        invitation_code: "LN-F-browser-channel",
        expires_at: "2026-09-11T00:00:00Z",
        max_uses: 1,
      });
      if (path === "/api/v1/auth/platform-invitations") return Promise.resolve([]);
      if (path === "/api/v1/generation-control/overview") return Promise.resolve({
        node_count: 0,
        online_node_count: 0,
        queued_request_count: 0,
        processing_request_count: 0,
        review_request_count: 0,
        failed_request_count: 0,
      });
      if (path === "/api/v1/generation-control/nodes" && options?.method === "POST") return Promise.resolve({
        id: "node-1",
        display_name: "家用 RTX 5080 生成节点",
        connection_token: "ln_node_once-only-token",
        capabilities: ["photo_restore", "portrait_video", "scene_video"],
        created_at: "2026-09-04T02:00:00Z",
      });
      if (path === "/api/v1/generation-control/nodes") return Promise.resolve([
        {
          id: "node-online",
          display_name: "家用 RTX 5080 生成节点",
          status: "active",
          connection_state: "online",
          capabilities: ["scene_video"],
          software_version: "lingnian-worker/2.0.0",
          device_summary: "Windows 11 · NVIDIA GeForce RTX 5080 / 16303MB",
          last_seen_at: "2026-09-04T06:53:58Z",
          created_at: "2026-09-04T02:00:00Z",
        },
      ]);
      if (path === "/api/v1/generation-control/requests") return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  afterEach(() => {
    delete process.env.NEXT_PUBLIC_LINGNIAN_WORKER_API_BASE;
    cleanup();
    apiMock.mockReset();
    replace.mockReset();
    refreshRouter.mockReset();
  });

  it("明确显示管理员登录名并可在网页生成体验码", async () => {
    render(<PlatformAdminConsole />);

    expect(await screen.findByText("账号和邀请，从这里统一管理")).toBeVisible();
    expect(screen.getAllByText("platform.admin").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "生成新家庭体验码" }));

    expect(await screen.findByText("LN-F-browser-channel")).toBeVisible();
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/auth/platform-invitations",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("可以在网页生成家用节点的一次性连接密钥", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<PlatformAdminConsole />);

    expect(await screen.findByText("家用生成节点")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "生成一次性连接密钥" }));

    expect(await screen.findByText("ln_node_once-only-token")).toBeVisible();
    expect(screen.getByText("连接密钥只完整显示这一次")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "复制家用电脑连接配置" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(
      "LINGNIAN_BACKEND_URL=https://formal-api.example.com",
    ));
  });

  it("显示在线节点的软件版本，便于确认家里电脑是否升级", async () => {
    render(<PlatformAdminConsole />);

    expect(await screen.findByText("程序版本：lingnian-worker/2.0.0")).toBeVisible();
    expect(screen.getByText("Windows 11 · NVIDIA GeForce RTX 5080 / 16303MB")).toBeVisible();
  });
});
