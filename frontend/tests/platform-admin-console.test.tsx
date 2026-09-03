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
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  afterEach(() => {
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
});
