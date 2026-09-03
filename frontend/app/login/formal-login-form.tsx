"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, KeyRound, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";

type Mode = "login" | "register";
type ApiResult = {
  error?: { message?: string };
  next_path?: string | null;
  platform_role?: "admin" | "user";
};

export function FormalLoginForm() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    const data = new FormData(event.currentTarget);
    const payload = mode === "login"
      ? { username: data.get("username"), password: data.get("password") }
      : {
          invitation_code: data.get("invitationCode"),
          username: data.get("username"),
          display_name: data.get("displayName"),
          password: data.get("password"),
          family_name: data.get("familyName") || "我的家庭",
        };
    try {
      const response = await fetch(`/api/v1/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({})) as ApiResult;
      if (!response.ok) {
        setMessage(result.error?.message || "暂时无法进入，请稍后重试。");
        return;
      }
      const requestedPath = typeof window === "undefined"
        ? null
        : new URLSearchParams(window.location.search).get("next");
      const nextPath = result.next_path?.startsWith("/record?")
        ? result.next_path
        : requestedPath === "/admin" && result.platform_role === "admin"
          ? "/admin"
          : "/";
      router.replace(nextPath);
      router.refresh();
    } catch {
      setMessage("网络连接不稳定，请重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="formal-login-tabs" role="tablist" aria-label="登录方式">
        <button type="button" role="tab" aria-selected={mode === "login"} onClick={() => { setMode("login"); setMessage(""); }}>登录</button>
        <button type="button" role="tab" aria-selected={mode === "register"} onClick={() => { setMode("register"); setMessage(""); }}>邀请码建账</button>
      </div>
      <form className="demo-login-form formal-login-form" onSubmit={handleSubmit}>
        {mode === "register" && (
          <>
            <label htmlFor="displayName">你的称呼</label>
            <div className="demo-code-field"><UserRound size={19} aria-hidden="true" /><input id="displayName" name="displayName" autoComplete="name" minLength={1} maxLength={80} placeholder="例如：小满" required /></div>
            <label htmlFor="familyName">家庭空间名称</label>
            <div className="demo-code-field"><UsersIcon /><input id="familyName" name="familyName" maxLength={80} placeholder="例如：沈家的回忆" /></div>
            <label htmlFor="invitationCode">邀请码</label>
            <div className="demo-code-field"><KeyRound size={19} aria-hidden="true" /><input id="invitationCode" name="invitationCode" type="password" autoComplete="one-time-code" minLength={8} maxLength={128} placeholder="输入家人给你的邀请码" required /></div>
          </>
        )}
        <label htmlFor="username">登录名</label>
        <div className="demo-code-field"><UserRound size={19} aria-hidden="true" /><input id="username" name="username" autoCapitalize="none" autoComplete="username" minLength={3} maxLength={80} placeholder="字母、数字或邮箱" required /></div>
        <label htmlFor="password">密码</label>
        <div className="demo-code-field"><KeyRound size={19} aria-hidden="true" /><input id="password" name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "login" ? 1 : 10} maxLength={200} placeholder={mode === "login" ? "输入密码" : "至少 10 位"} required /></div>
        <p className="demo-login-error" role="alert" aria-live="polite">{message}</p>
        <button className="button primary demo-login-submit" type="submit" disabled={pending}>
          {pending ? "正在进入…" : mode === "login" ? "进入家庭空间" : "建立账号并进入"}
          {!pending && <ArrowRight size={18} aria-hidden="true" />}
        </button>
      </form>
    </>
  );
}

function UsersIcon() {
  return <UserRound size={19} aria-hidden="true" />;
}
