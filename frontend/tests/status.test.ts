import { describe, expect, it } from "vitest";

import {
  hasMeaningfulStoryContent,
  isSessionTerminal,
  memoryWorkflowStep,
  sessionStatusLabel,
  taskErrorMessage,
  taskStatusLabel,
} from "@/lib/workflow/status";

describe("工作流状态文案", () => {
  it("把后端状态转换成家人能理解的文字", () => {
    expect(sessionStatusLabel("TRANSCRIPT_REVIEW")).toBe("等待家人校对");
    expect(taskStatusLabel("running")).toBe("正在处理");
    expect(taskStatusLabel("failed_retryable")).toBe("处理遇到问题，可以重试");
  });

  it("未知状态不会直接暴露技术枚举", () => {
    expect(sessionStatusLabel("SOMETHING_NEW")).toBe("状态待确认，请刷新后再看");
    expect(taskStatusLabel("SOMETHING_NEW")).toBe("状态待确认");
  });

  it("把内容不足转换成可执行提示", () => {
    expect(taskErrorMessage("INSUFFICIENT_STORY_CONTENT")).toContain("补充至少一句实际内容");
    expect(hasMeaningfulStoryContent("啊啊啊哦哦，没了")).toBe(false);
    expect(hasMeaningfulStoryContent("小时候我住在河边，最记得院子里的槐树。 ")).toBe(true);
  });

  it("明确区分采访保存成功和故事生成失败", () => {
    expect(taskErrorMessage("LLM_PROCESSING_FAILED")).toContain("录音和整场采访稿已经保存");
    expect(taskErrorMessage("LLM_PROCESSING_FAILED")).toContain("不需要重新采访");
    expect(taskErrorMessage("LLM_PROCESSING_FAILED")).toContain("点击下方按钮");
  });

  it("只把归档和主动跳过视为会话终态", () => {
    expect(isSessionTerminal("ARCHIVED")).toBe(true);
    expect(isSessionTerminal("SKIPPED")).toBe(true);
    expect(isSessionTerminal("DRAFT_REVIEW")).toBe(false);
  });

  it("把后端状态归到四步记录流程", () => {
    expect(memoryWorkflowStep("PROMPT_READY")).toBe(1);
    expect(memoryWorkflowStep("TRANSCRIBING")).toBe(1);
    expect(memoryWorkflowStep("TRANSCRIPT_REVIEW")).toBe(2);
    expect(memoryWorkflowStep("ORGANIZING")).toBe(2);
    expect(memoryWorkflowStep("DRAFT_REVIEW")).toBe(3);
    expect(memoryWorkflowStep("ARCHIVED")).toBe(3);
    expect(memoryWorkflowStep("SOMETHING_NEW")).toBe(0);
  });
});
