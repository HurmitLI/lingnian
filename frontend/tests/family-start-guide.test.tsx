import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import FamilyStartGuide from "@/components/family-start-guide";

describe("家庭启动引导", () => {
  afterEach(cleanup);

  it("只把当前下一步显示为可执行操作", () => {
    render(<FamilyStartGuide hasProfile hasNarrator={false} hasStory={false} hasInvitedAccount={false} />);
    expect(screen.getByLabelText("已完成 1 项，共 4 项")).toBeVisible();
    expect(screen.getByRole("link", { name: "添加讲述人" })).toHaveAttribute("href", "/family");
    expect(screen.queryByRole("link", { name: "开始采访" })).not.toBeInTheDocument();
    expect(screen.getAllByText("稍后")).toHaveLength(2);
  });

  it("已有故事后引导管理员邀请家人", () => {
    render(<FamilyStartGuide hasProfile hasNarrator hasStory hasInvitedAccount={false} />);
    expect(screen.getByRole("link", { name: "生成邀请" })).toHaveAttribute("href", "/family#family-access");
  });

  it("四项完成后显示家庭空间已启动", () => {
    render(<FamilyStartGuide hasProfile hasNarrator hasStory hasInvitedAccount />);
    expect(screen.getByLabelText("已完成 4 项，共 4 项")).toBeVisible();
    expect(screen.getAllByText("已完成")).toHaveLength(4);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
