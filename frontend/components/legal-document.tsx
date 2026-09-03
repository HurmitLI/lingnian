import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";

type LegalDocumentProps = {
  eyebrow: string;
  title: string;
  updatedAt: string;
  children: ReactNode;
};

export default function LegalDocument({
  eyebrow,
  title,
  updatedAt,
  children,
}: LegalDocumentProps) {
  return (
    <main className="legal-main">
      <article className="legal-document">
        <header>
          <Link className="legal-brand" href="/login">
            <Image src="/brand/lingnian-mark-v3.png" width={38} height={38} alt="" />
            <span><strong>聆年</strong><small>家庭记忆</small></span>
          </Link>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p className="legal-updated">更新日期：{updatedAt} · 当前为邀请制内测版</p>
        </header>
        <div className="legal-body">{children}</div>
        <footer>
          <Link href="/privacy">隐私与数据说明</Link>
          <Link href="/terms">使用约定</Link>
          <Link href="/login">返回登录</Link>
        </footer>
      </article>
    </main>
  );
}
