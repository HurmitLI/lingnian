import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormalLoginForm } from "@/app/login/formal-login-form";

const replace = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));

describe("正式版邀请建账", () => {
  afterEach(() => {
    cleanup();
    replace.mockReset();
    refresh.mockReset();
    vi.unstubAllGlobals();
  });

  it("采访邀请建账后直接进入已分配的采访", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      next_path: "/record?elder=elder-1&session=session-1&source=family-invite",
    }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    })));

    render(<FormalLoginForm />);
    fireEvent.click(screen.getByRole("tab", { name: "邀请码建账" }));
    fireEvent.change(screen.getByLabelText("你的称呼"), { target: { value: "妈妈" } });
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "LN-interview-test" } });
    fireEvent.change(screen.getByLabelText("登录名"), { target: { value: "mother.account" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secure-password" } });
    fireEvent.click(screen.getByRole("button", { name: /建立账号并进入/ }));

    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith(
        "/record?elder=elder-1&session=session-1&source=family-invite",
      );
    });
    expect(refresh).toHaveBeenCalled();
  });
});
