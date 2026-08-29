"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, KeyRound } from "lucide-react";
import { useRouter } from "next/navigation";

type LoginResult = { ok?: boolean; message?: string; redirectTo?: string };

export function DemoLoginForm() {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setMessage("");

    const formData = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/demo-auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inviteCode: formData.get("inviteCode") }),
      });
      const result = await response.json() as LoginResult;
      if (!response.ok || !result.ok) {
        setMessage(result.message || "暂时无法进入，请稍后重试。");
        return;
      }
      router.replace(result.redirectTo || "/showcase");
      router.refresh();
    } catch {
      setMessage("网络连接不稳定，请重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="demo-login-form" onSubmit={handleSubmit}>
      <label htmlFor="inviteCode">邀请码</label>
      <div className="demo-code-field">
        <KeyRound size={19} aria-hidden="true" />
        <input
          id="inviteCode"
          name="inviteCode"
          type="password"
          inputMode="text"
          autoComplete="one-time-code"
          minLength={8}
          maxLength={128}
          placeholder="输入你收到的邀请码"
          required
          autoFocus
        />
      </div>
      <p className="demo-login-error" role="alert" aria-live="polite">{message}</p>
      <button className="button primary demo-login-submit" type="submit" disabled={pending}>
        {pending ? "正在进入…" : "进入体验空间"}
        {!pending && <ArrowRight size={18} aria-hidden="true" />}
      </button>
    </form>
  );
}
