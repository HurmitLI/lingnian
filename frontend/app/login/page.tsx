import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { LockKeyhole, ShieldCheck, Users } from "lucide-react";

import { FormalLoginForm } from "./formal-login-form";

export const metadata: Metadata = {
  title: { absolute: "家庭登录 · 聆年" },
  description: "登录受邀的聆年家庭记忆空间。",
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  return (
    <main className="demo-login-main">
      <section className="demo-login-card formal-login-card" aria-labelledby="formal-login-title">
        <div className="demo-login-visual formal-login-visual">
          <div className="demo-login-visual-shade">
            <div className="demo-login-brand">
              <Image src="/brand/lingnian-mark-v3.png" width={46} height={46} alt="" priority />
              <span><strong>聆年</strong><small>家庭记忆</small></span>
            </div>
            <div className="demo-login-visual-copy">
              <span>受邀家庭空间</span>
              <strong>让说过的话，<br />成为家人能回去的地方。</strong>
              <p>原声、照片、故事与家族脉络，放在同一份档案里。</p>
            </div>
          </div>
        </div>

        <div className="demo-login-panel">
          <div className="demo-login-mobile-brand">
            <Image src="/brand/lingnian-mark-v3.png" width={40} height={40} alt="" />
            <span><strong>聆年</strong><small>家庭记忆</small></span>
          </div>
          <p className="demo-login-eyebrow">家庭私密空间</p>
          <h1 id="formal-login-title">回到家里的记忆</h1>
          <p className="demo-login-lead">已有账号可直接登录；第一次使用，请用家人给你的邀请码建立账号。</p>
          <FormalLoginForm />
          <p className="formal-legal-links">建立账号即表示你已阅读 <Link href="/terms">使用约定</Link> 和 <Link href="/privacy">隐私与数据说明</Link>。</p>
          <ul className="demo-login-trust" aria-label="正式空间说明">
            <li><LockKeyhole size={17} aria-hidden="true" /><span><strong>登录后可见</strong><small>未受邀的人无法打开档案</small></span></li>
            <li><Users size={17} aria-hidden="true" /><span><strong>家人共同补充</strong><small>在同一家庭空间整理回忆</small></span></li>
            <li><ShieldCheck size={17} aria-hidden="true" /><span><strong>资料可追溯</strong><small>原声与确认稿保留来源</small></span></li>
          </ul>
        </div>
      </section>
    </main>
  );
}
