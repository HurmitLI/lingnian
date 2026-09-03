import type { Metadata } from "next";

import LegalDocument from "@/components/legal-document";

export const metadata: Metadata = {
  title: { absolute: "使用约定 · 聆年" },
  description: "聆年邀请制内测版的使用约定。",
  robots: { index: false, follow: false },
};

export default function TermsPage() {
  return (
    <LegalDocument eyebrow="内测使用约定" title="记忆属于人，技术只负责帮忙" updatedAt="2026 年 9 月 4 日">
      <section><h2>1. 产品用途</h2><p>聆年用于家庭口述史的采访、整理、归档、共同补充和长期保存，不用于身份证明、医疗判断、遗嘱认定或其他需要法律效力的场景。</p></section>
      <section><h2>2. 邀请与家庭权限</h2><p>请只将一次性邀请码交给你信任的家人。家庭管理员负责邀请、撤销成员、交接管理权和导出家庭档案。</p></section>
      <section><h2>3. 内容真实性与来源</h2><p>回忆可能不完整，不同家人也可能有不同记忆。系统会尽量保留讲述人、原声和家人确认状态，但不保证历史事实已被外部证实。</p></section>
      <section><h2>4. 他人素材与真人生成</h2><p>上传录音、照片或视频前，请确认你有权在家庭范围内使用。不得用生成影像或复刻声音冒充他人、误导公众、骗取信任或进行商业欺诈。</p></section>
      <section><h2>5. 不可用的内容</h2><p>不得上传违法内容、未经授权的高度敏感信息，或使用产品侵害他人隐私、名誉、肖像、声音和知识产权。</p></section>
      <section><h2>6. 内测稳定性</h2><p>内测期可能因升级、容量限制或第三方服务故障暂时不可用。重要档案请定期下载开放格式传承包，不把任何单一在线服务作为唯一副本。</p></section>
      <aside><strong>内测边界</strong><p>正式扩大邀请前，仍需补充运营主体、联系方式、服务地域等正式条款信息并完成人工法务复核。</p></aside>
    </LegalDocument>
  );
}
