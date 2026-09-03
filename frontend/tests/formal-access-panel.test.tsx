import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FormalAccessPanel from "@/app/formal-access-panel";

const apiMock = vi.fn();

vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class ApiError extends Error {},
}));

describe("正式家庭访问管理", () => {
  beforeEach(() => {
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === "/api/v1/auth/me") {
        return Promise.resolve({ display_name: "管理员", family_name: "我的家庭", role: "owner" });
      }
      if (path === "/api/v1/auth/invitations") return Promise.resolve([]);
      if (path === "/api/v1/auth/members") {
        return Promise.resolve([
          { membership_id: "owner-membership", user_id: "owner", username: "owner.account", display_name: "管理员", role: "owner", status: "active", last_login_at: null },
          { membership_id: "member-membership", user_id: "member", username: "member.account", display_name: "受邀家人", role: "member", status: "active", last_login_at: null },
        ]);
      }
      if (path === "/api/v1/auth/members/member-membership" && options?.method === "DELETE") {
        return Promise.resolve(undefined);
      }
      if (path === "/api/v1/auth/password" && options?.method === "POST") {
        return Promise.resolve(undefined);
      }
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  afterEach(() => {
    cleanup();
    apiMock.mockReset();
  });

  it("管理员可以查看并移除受邀家人", async () => {
    render(<FormalAccessPanel />);

    expect(await screen.findByText("受邀家人")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "移除访问" }));

    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith(
        "/api/v1/auth/members/member-membership",
        { method: "DELETE" },
      );
    });
  });

  it("登录用户可以更新自己的密码", async () => {
    render(<FormalAccessPanel />);
    await screen.findByText("已加入的家人");
    fireEvent.click(screen.getByText("修改我的登录密码"));
    fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: "old-password" } });
    fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "new-password-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "更新密码" }));

    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith(
        "/api/v1/auth/password",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
