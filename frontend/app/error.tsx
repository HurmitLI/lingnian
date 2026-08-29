"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("聆年页面异常", error);
  }, [error]);

  return (
    <main className="route-state">
      <div className="route-state-card" role="alert">
        <p className="card-kicker">页面暂时没有打开</p>
        <h1>刚才的操作没有完成</h1>
        <p>已经保存的回忆不会丢失。你可以重新加载当前页面，或回到首页稍后再试。</p>
        <div className="button-row">
          <button className="button primary" type="button" onClick={reset}>
            重新加载
          </button>
          <Link className="button secondary" href="/">
            回到首页
          </Link>
        </div>
      </div>
    </main>
  );
}
