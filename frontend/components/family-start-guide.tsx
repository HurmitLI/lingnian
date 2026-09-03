import Link from "next/link";

type FamilyStartGuideProps = {
  hasProfile: boolean;
  hasNarrator: boolean;
  hasStory: boolean;
  hasInvitedAccount: boolean;
};

const steps = [
  { key: "profile", title: "建立第一位家人的档案", description: "先写一个家里常用的称呼，其他资料以后再补。", href: "/family", action: "去建档" },
  { key: "narrator", title: "添加一位可以讲故事的家人", description: "回忆对象和实际讲述人可以不是同一个人。", href: "/family", action: "添加讲述人" },
  { key: "story", title: "完成第一段语音采访", description: "聆年负责提问和整理，家人只在最后确认。", href: "/record", action: "开始采访" },
  { key: "invite", title: "邀请第一位家人加入", description: "可以只邀请加入，也可以直接分配一段采访。", href: "/family#family-access", action: "生成邀请" },
] as const;

export default function FamilyStartGuide({ hasProfile, hasNarrator, hasStory, hasInvitedAccount }: FamilyStartGuideProps) {
  const completed = { profile: hasProfile, narrator: hasNarrator, story: hasStory, invite: hasInvitedAccount };
  const completedCount = Object.values(completed).filter(Boolean).length;
  const nextStep = steps.find((step) => !completed[step.key]);

  return (
    <section className="card family-start-guide" aria-labelledby="family-start-title">
      <div className="family-start-heading">
        <div><p className="card-kicker">家庭空间刚开始</p><h2 id="family-start-title">按这四步，就能留下第一份家庭记忆</h2><p>不用一次填完所有资料，先完成下一小步即可。</p></div>
        <span aria-label={`已完成 ${completedCount} 项，共 4 项`}>{completedCount}/4</span>
      </div>
      <ol className="family-start-steps">
        {steps.map((step, index) => {
          const done = completed[step.key];
          const current = nextStep?.key === step.key;
          return (
            <li key={step.key} data-state={done ? "done" : current ? "current" : "upcoming"}>
              <span className="family-start-number" aria-hidden="true">{done ? "✓" : index + 1}</span>
              <div><strong>{step.title}</strong><small>{step.description}</small></div>
              {done ? <span className="family-start-status">已完成</span> : current ? <Link href={step.href}>{step.action}</Link> : <span className="family-start-status">稍后</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
