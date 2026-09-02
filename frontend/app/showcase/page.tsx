import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import {
  Archive,
  BookOpenText,
  Check,
  Film,
  Home,
  ImageIcon,
  LockKeyhole,
  Mic2,
  Quote,
  ShieldCheck,
  Type,
  Users,
} from "lucide-react";
import {
  DEMO_SESSION_COOKIE,
  getDemoSessionSecret,
  isDemoModeEnabled,
  verifyDemoSessionToken,
} from "@/lib/demo-auth/session";
import { DemoLogoutButton } from "./logout-button";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { absolute: "沈家的回忆 · 聆年" },
  description: "一段由口述、文字、照片和原声影像共同保存的家庭记忆。",
  robots: { index: false, follow: false },
};

const STORY_PARAGRAPHS = [
  "这个蓝布包，我一直没舍得扔。1982 年春天，我十九岁，第一次一个人坐火车去无锡的纺织厂。出门前，母亲没说多少话，只把两个煮鸡蛋、一个搪瓷缸和一块手帕塞进包里。手帕边上，她用红线缝了四针。她说，到了宿舍，先给家里写信。",
  "火车开的时候，我隔着窗看见她还站在月台上。她没有追，只抬了一下手。我那时候年轻，心里全是远方，甚至觉得她站在那里有些多余。",
  "后来我也做了母亲。女儿第一次离家去读书，我在站外等到车开远，才突然明白那一下抬手是什么意思。不是舍不得你走，是想让你放心走。",
  "这个包陪我搬过三次家，拉链早坏了，提手也换过。去年女儿整理柜子，问我为什么还留着。我说，包里早就没东西了。其实不是。母亲那天没说出口的话，还在里面。",
  "要是她现在还在，我想告诉她：那两个鸡蛋，我一个没舍得在车上吃；到了无锡才发现都压裂了。可那是我这一辈子吃过最香的两个鸡蛋。",
];

const MEMORY_STEPS = [
  { icon: Mic2, label: "原声讲述", detail: "保留语气和停顿" },
  { icon: Type, label: "家人校对", detail: "确认每一句原话" },
  { icon: BookOpenText, label: "整理故事", detail: "不补写未知事实" },
  { icon: Film, label: "家庭影像", detail: "照片、原声和字幕" },
] as const;

export default async function ShowcasePage() {
  const demoMode = isDemoModeEnabled();
  if (demoMode) {
    const secret = getDemoSessionSecret();
    const session = (await cookies()).get(DEMO_SESSION_COOKIE)?.value;
    if (!secret || !await verifyDemoSessionToken(session, secret)) redirect("/demo-login");
  }

  return (
    <div className="demo-app-shell">
      <aside className="demo-app-sidebar">
        <Link className="demo-app-brand" href={demoMode ? "/showcase" : "/"} aria-label="聆年家庭记忆">
          <Image src="/brand/lingnian-mark-v3.png" width={42} height={42} alt="" preload />
          <span><strong>聆年</strong><small>家庭记忆</small></span>
        </Link>

        <nav className="demo-app-nav" aria-label="体验导航">
          <span><Home size={19} aria-hidden="true" />首页</span>
          <span className="is-active" aria-current="page"><Archive size={19} aria-hidden="true" />回忆档案</span>
          <span><Users size={19} aria-hidden="true" />家庭成员</span>
        </nav>

        <div className="demo-app-sidebar-note">
          <LockKeyhole size={17} aria-hidden="true" />
          <span><strong>受邀只读体验</strong><small>不会写入或读取真实家庭资料</small></span>
        </div>
      </aside>

      <div className="demo-app-stage">
        <header className="demo-app-header">
          <div>
            <p>家庭空间</p>
            <h1>沈家的回忆</h1>
          </div>
          <div className="demo-app-header-actions">
            <span className="demo-app-private"><LockKeyhole size={15} aria-hidden="true" />仅受邀人可见</span>
            {demoMode && <DemoLogoutButton compact />}
          </div>
        </header>

        <main className="demo-app-main">
          <div className="demo-app-grid">
            <div className="demo-app-feed">
              <section className="demo-memory-card" aria-labelledby="memory-title">
                <div className="demo-card-heading">
                  <div>
                    <p>最近整理</p>
                    <h2 id="memory-title">包里还装着那天没说完的话</h2>
                  </div>
                  <span className="demo-confirmed"><Check size={14} aria-hidden="true" />家人已确认</span>
                </div>

                <div className="demo-video-frame">
                  <video
                    controls
                    playsInline
                    preload="metadata"
                    poster="/showcase/fictional-grandmother-source.png"
                    aria-label="播放家庭影像：包里还装着那天没说完的话"
                  >
                    <source src="/showcase/shen-suqin-portrait-story.mp4?v=2" type="video/mp4" />
                    当前浏览器不支持视频播放。
                  </video>
                </div>

                <div className="demo-memory-meta">
                  <span><Film size={15} aria-hidden="true" />人物讲述 · 00:08</span>
                  <span>离家 · 母亲 · 1982 年</span>
                </div>
              </section>

              <section className="demo-story-card" aria-labelledby="story-title">
                <div className="demo-section-heading">
                  <div>
                    <p>口述故事</p>
                    <h2 id="story-title">不是舍不得你走，是想让你放心走。</h2>
                  </div>
                  <BookOpenText size={22} aria-hidden="true" />
                </div>

                <article>
                  <Quote size={25} strokeWidth={1.5} aria-hidden="true" />
                  {STORY_PARAGRAPHS.slice(0, 2).map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                  <details>
                    <summary>继续读完整故事</summary>
                    {STORY_PARAGRAPHS.slice(2).map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                  </details>
                </article>

                <footer><span>沈素琴 口述</span><span>女儿校对</span><time dateTime="2026-08-28">2026 年 8 月整理</time></footer>
              </section>

              <section className="demo-photo-card" aria-labelledby="photo-title">
                <div className="demo-section-heading">
                  <div><p>关联照片</p><h2 id="photo-title">照片和故事放在一起</h2></div>
                  <ImageIcon size={22} aria-hidden="true" />
                </div>
                <div className="demo-photo-grid">
                  <figure>
                    <div className="demo-photo-image"><Image src="/showcase/shen-suqin-station-1982.png" fill unoptimized sizes="(max-width: 760px) 100vw, 45vw" alt="1982 年春，年轻的沈素琴在县城火车站抱着蓝布包" /></div>
                    <figcaption><time dateTime="1982-03">1982 年春</time><strong>第一次离开湖州</strong><span>去无锡纺织厂工作的那天</span></figcaption>
                  </figure>
                  <figure>
                    <div className="demo-photo-image"><Image src="/showcase/shen-suqin-blue-bag.png" fill unoptimized sizes="(max-width: 760px) 100vw, 35vw" alt="旧蓝布包里放着搪瓷缸、手帕和两个鸡蛋" /></div>
                    <figcaption><time>留存至今</time><strong>那只旧蓝布包</strong><span>母亲没说出口的话还在里面</span></figcaption>
                  </figure>
                </div>
              </section>
            </div>

            <aside className="demo-app-aside" aria-label="档案概览">
              <section className="demo-profile-card">
                <div className="demo-profile-cover">
                  <Image src="/showcase/fictional-grandmother-source.png" fill unoptimized sizes="320px" alt="沈素琴坐在家中准备讲述往事" />
                </div>
                <div className="demo-profile-copy">
                  <span className="demo-profile-avatar">沈</span>
                  <p>讲述者</p>
                  <h2>沈素琴</h2>
                  <span>63 岁 · 浙江湖州</span>
                  <dl>
                    <div><dt>故事</dt><dd>1</dd></div>
                    <div><dt>照片</dt><dd>2</dd></div>
                    <div><dt>影像</dt><dd>1</dd></div>
                  </dl>
                </div>
              </section>

              <section className="demo-process-card" aria-labelledby="process-title">
                <div className="demo-aside-title"><span><ShieldCheck size={17} aria-hidden="true" /></span><div><p>这段回忆</p><h2 id="process-title">已经完成整理</h2></div></div>
                <ol>
                  {MEMORY_STEPS.map(({ icon: Icon, label, detail }) => (
                    <li key={label}>
                      <span><Icon size={17} aria-hidden="true" /></span>
                      <div><strong>{label}</strong><small>{detail}</small></div>
                      <Check size={15} aria-label="已完成" />
                    </li>
                  ))}
                </ol>
              </section>

              <section className="demo-readonly-card">
                <LockKeyhole size={19} aria-hidden="true" />
                <div><strong>只读体验空间</strong><p>这里展示的是一份示例档案。你可以阅读和播放，但不会改动任何内容。</p></div>
              </section>
            </aside>
          </div>

          <footer className="demo-app-footer">
            <span>示例档案，不对应真实家庭</span>
            {demoMode ? <DemoLogoutButton /> : <Link className="button secondary button-link" href="/">返回家庭空间</Link>}
          </footer>
        </main>

        <nav className="demo-app-mobile-nav" aria-label="手机体验导航">
          <span><Home size={20} aria-hidden="true" /><small>首页</small></span>
          <span className="is-active" aria-current="page"><Archive size={20} aria-hidden="true" /><small>回忆</small></span>
          <span><Users size={20} aria-hidden="true" /><small>家人</small></span>
        </nav>
      </div>
    </div>
  );
}
