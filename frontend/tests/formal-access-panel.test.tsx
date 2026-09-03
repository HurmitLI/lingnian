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
      if (path === "/api/v1/auth/invitations" && options?.method === "POST") {
        const request = JSON.parse(String(options.body));
        return Promise.resolve({
          id: "invite-interview",
          invitation_code: "LN-interview-test",
          expires_at: "2026-09-10T10:00:00Z",
          max_uses: 1,
          purpose: request.elder_id ? "interview" : "family_access",
          elder_id: request.elder_id ?? null,
          narrator_person_id: request.narrator_person_id ?? null,
          life_stage: request.life_stage ?? null,
        });
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

  it("管理员可以指定人物、讲述人和话题生成采访邀请", async () => {
    render(<FormalAccessPanel
      profiles={[{
        id: "elder-1",
        person_id: "person-elder",
        family_id: "family-1",
        data_classification: "authorized_sensitive",
        display_name: "测试外公",
        preferred_name: "外公",
        birth_year: null,
        birth_era: null,
        native_place: null,
        occupation_summary: null,
        health_notes: null,
      }]}
      people={[{
        id: "person-mother",
        family_id: "family-1",
        role: "family_member",
        display_name: "妈妈",
        created_at: "2026-09-03T10:00:00Z",
      }]}
    />);

    expect(await screen.findByText("邀请家人补充一段回忆")).toBeVisible();
    fireEvent.change(screen.getByLabelText("先聊哪一段"), { target: { value: "工作" } });
    fireEvent.click(screen.getByRole("button", { name: "生成采访邀请" }));

    await waitFor(() => {
      expect(apiMock).toHaveBeenCalledWith(
        "/api/v1/auth/invitations",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            expires_in_days: 7,
            elder_id: "elder-1",
            narrator_person_id: "person-mother",
            life_stage: "工作",
          }),
        }),
      );
    });
    expect(await screen.findByText("LN-interview-test")).toBeVisible();
  });
});
