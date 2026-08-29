import type { Metadata } from "next";
import Image from "next/image";
import { Film, LockKeyhole, ShieldCheck } from "lucide-react";
import { DemoLoginForm } from "./login-form";

export const metadata: Metadata = {
  title: { absolute: "邀请体验 · 聆年" },
  description: "通过邀请码进入聆年只读体验空间。",
  robots: { index: false, follow: false },
};

export default function DemoLoginPage() {
  return (
    <main className="demo-login-main">
      <section className="demo-login-card" aria-labelledby="demo-login-title">
        <div className="demo-login-brand">
          <Image src="/brand/lingnian-mark-v3.png" width={52} height={52} alt="" priority />
          <span><strong>聆年</strong><small>家庭记忆</small></span>
        </div>
        <p className="showcase-kicker">邀请体验</p>
        <h1 id="demo-login-title">请进，来听一段<br />被好好留下的往事。</h1>
        <p className="demo-login-lead">这是一个独立的只读空间。你可以看到一段口述，如何变成文字、故事和家庭影像。</p>
        <DemoLoginForm />
        <ul className="demo-login-trust" aria-label="体验空间说明">
          <li><Film size={18} aria-hidden="true" /><span><strong>完整功能样片</strong><small>原声、故事与影像</small></span></li>
          <li><ShieldCheck size={18} aria-hidden="true" /><span><strong>只读浏览</strong><small>不会写入任何资料</small></span></li>
          <li><LockKeyhole size={18} aria-hidden="true" /><span><strong>与真实档案隔离</strong><small>不读取家庭内容</small></span></li>
        </ul>
      </section>
    </main>
  );
}
