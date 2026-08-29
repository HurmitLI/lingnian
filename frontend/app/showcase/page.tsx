import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import {
  ArrowLeft,
  BookOpenText,
  Check,
  Film,
  LockKeyhole,
  Mic2,
  Quote,
  Sparkles,
  Type,
} from "lucide-react";

export const metadata: Metadata = {
  title: { absolute: "一段完整的家庭记忆 · 聆年" },
  description: "从一段讲述，到一篇故事和一支家庭影像。",
};

const STORY_PARAGRAPHS = [
  "这个蓝布包，我一直没舍得扔。1982 年春天，我十九岁，第一次一个人坐火车去无锡的纺织厂。出门前，母亲没说多少话，只把两个煮鸡蛋、一个搪瓷缸和一块手帕塞进包里。手帕边上，她用红线缝了四针。她说，到了宿舍，先给家里写信。",
  "火车开的时候，我隔着窗看见她还站在月台上。她没有追，只抬了一下手。我那时候年轻，心里全是远方，甚至觉得她站在那里有些多余。",
  "后来我也做了母亲。女儿第一次离家去读书，我在站外等到车开远，才突然明白那一下抬手是什么意思。不是舍不得你走，是想让你放心走。",
  "这个包陪我搬过三次家，拉链早坏了，提手也换过。去年女儿整理柜子，问我为什么还留着。我说，包里早就没东西了。其实不是。母亲那天没说出口的话，还在里面。",
  "要是她现在还在，我想告诉她：那两个鸡蛋，我一个没舍得在车上吃；到了无锡才发现都压裂了。可那是我这一辈子吃过最香的两个鸡蛋。",
];

const OUTPUT_STEPS = [
  { icon: Mic2, label: "留下讲述", detail: "保留原声与停顿" },
  { icon: Type, label: "转成文字", detail: "家人可以逐字校对" },
  { icon: BookOpenText, label: "整理成故事", detail: "不添加没有说过的事实" },
  { icon: Film, label: "生成家庭影像", detail: "声音、照片与字幕合成" },
] as const;

export default function ShowcasePage() {
  return (
    <div className="showcase-shell">
      <header className="showcase-topbar">
        <Link className="showcase-brand" href="/" aria-label="返回聆年首页">
          <Image src="/brand/lingnian-mark-v3.png" width={40} height={40} alt="" preload />
          <span><strong>聆年</strong><small>家庭记忆</small></span>
        </Link>
        <div className="showcase-access"><LockKeyhole size={16} aria-hidden="true" />邀请码体验 · 只读空间</div>
      </header>

      <main className="showcase-main">
        <Link className="showcase-back" href="/"><ArrowLeft size={17} aria-hidden="true" />返回家庭空间</Link>

        <section className="showcase-intro" aria-labelledby="showcase-title">
          <div>
            <p className="showcase-kicker">一段完整的家庭记忆</p>
            <h1 id="showcase-title">包里还装着<br />那天没说完的话</h1>
            <p className="showcase-lead">从一次口述开始，聆年把原声、文字和老照片，整理成家人愿意反复打开的故事。</p>
          </div>
          <aside className="showcase-person" aria-label="讲述者信息">
            <span className="showcase-avatar">沈</span>
            <div><small>本期讲述者</small><strong>沈素琴</strong><span>63 岁 · 浙江湖州</span></div>
          </aside>
        </section>

        <section className="showcase-film" aria-labelledby="film-title">
          <div className="showcase-film-frame">
            <h2 className="sr-only" id="film-title">包里还装着那天没说完的话</h2>
            <video
              controls
              playsInline
              preload="metadata"
              poster="/showcase/shen-suqin-home.png"
              aria-label="播放家庭影像：包里还装着那天没说完的话"
            >
              <source src="/showcase/shen-suqin-story.mp4?v=4" type="video/mp4" />
              当前浏览器不支持视频播放。
            </video>
            <span className="showcase-film-label">家庭影像 · 01:29</span>
          </div>
        </section>

        <ol className="showcase-pipeline" aria-label="一段家庭记忆的生成过程">
          {OUTPUT_STEPS.map(({ icon: Icon, label, detail }, index) => (
            <li key={label}>
              <span className="showcase-step-icon"><Icon size={20} strokeWidth={1.8} aria-hidden="true" /></span>
              <div><small>0{index + 1}</small><strong>{label}</strong><span>{detail}</span></div>
              <Check className="showcase-step-check" size={17} aria-label="已完成" />
            </li>
          ))}
        </ol>

        <section className="showcase-story" aria-labelledby="story-title">
          <div className="showcase-story-heading">
            <p className="showcase-kicker">由讲述整理的故事</p>
            <h2 id="story-title">不是舍不得你走，<br />是想让你放心走。</h2>
            <div className="showcase-story-meta"><span>沈素琴 口述</span><span>家人校对</span><span>童年与离家</span></div>
          </div>
          <article>
            <Quote size={30} strokeWidth={1.4} aria-hidden="true" />
            {STORY_PARAGRAPHS.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          </article>
        </section>

        <section className="showcase-gallery" aria-labelledby="gallery-title">
          <div className="showcase-gallery-heading">
            <p className="showcase-kicker">照片进入故事</p>
            <h2 id="gallery-title">不只是把照片排成幻灯片</h2>
            <p>聆年会把照片放回它对应的语境里：哪一年、在哪里、为什么一直被留下。</p>
          </div>
          <figure className="showcase-photo showcase-photo-wide">
            <Image src="/showcase/shen-suqin-station-1982.png" fill sizes="(max-width: 760px) 100vw, 60vw" alt="1982 年春天，年轻的沈素琴在县城火车站抱着蓝布包" />
            <figcaption><span>1982 年春</span>第一次离开湖州，去无锡工作</figcaption>
          </figure>
          <figure className="showcase-photo">
            <Image src="/showcase/shen-suqin-blue-bag.png" fill sizes="(max-width: 760px) 100vw, 40vw" alt="旧蓝布包里放着搪瓷缸、手帕和两个鸡蛋" />
            <figcaption><span>一只旧蓝布包</span>留下来的不是物件，是当时没说出口的话</figcaption>
          </figure>
        </section>

        <section className="showcase-boundary" aria-label="演示空间说明">
          <div><LockKeyhole size={22} aria-hidden="true" /><span><strong>这是独立的只读空间</strong><small>不会读取、修改或展示任何真实家庭档案。</small></span></div>
          <Link className="button secondary button-link" href="/">回到聆年首页</Link>
        </section>
        <p className="showcase-creation-note"><Sparkles size={13} aria-hidden="true" />本页人物与故事为创作内容，声音为合成演绎。</p>
      </main>
    </div>
  );
}
