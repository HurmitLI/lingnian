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
        <div className="demo-login-visual">
          <div className="demo-login-visual-shade">
            <div className="demo-login-brand">
              <Image src="/brand/lingnian-mark-v3.png" width={46} height={46} alt="" priority />
              <span><strong>聆年</strong><small>家庭记忆</small></span>
            </div>
            <div className="demo-login-visual-copy">
              <span>本期家庭记忆</span>
              <strong>包里还装着<br />那天没说完的话</strong>
              <p>沈素琴 · 63 岁 · 浙江湖州</p>
            </div>
          </div>
        </div>

        <div className="demo-login-panel">
          <div className="demo-login-mobile-brand">
            <Image src="/brand/lingnian-mark-v3.png" width={40} height={40} alt="" />
            <span><strong>聆年</strong><small>家庭记忆</small></span>
          </div>
          <p className="demo-login-eyebrow">家庭邀请</p>
          <h1 id="demo-login-title">打开沈家的回忆</h1>
          <p className="demo-login-lead">输入你收到的邀请码，进入一份只读家庭档案，听原声、读故事，也看看照片如何回到记忆里。</p>
          <DemoLoginForm />
          <ul className="demo-login-trust" aria-label="体验空间说明">
            <li><Film size={17} aria-hidden="true" /><span><strong>原声、故事与照片</strong><small>一份完整的回忆档案</small></span></li>
            <li><ShieldCheck size={17} aria-hidden="true" /><span><strong>只读浏览</strong><small>不会写入任何资料</small></span></li>
            <li><LockKeyhole size={17} aria-hidden="true" /><span><strong>独立空间</strong><small>与真实家庭档案隔离</small></span></li>
          </ul>
          <p className="demo-login-footnote">示例档案 · 仅供受邀体验</p>
        </div>
      </section>
    </main>
  );
}
