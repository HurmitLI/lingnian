import type { Metadata } from "next";

import LegalDocument from "@/components/legal-document";

export const metadata: Metadata = {
  title: { absolute: "隐私与数据说明 · 聆年" },
  description: "聆年邀请制内测版的隐私与数据处理说明。",
  robots: { index: false, follow: false },
};

export default function PrivacyPage() {
  return (
    <LegalDocument eyebrow="隐私与数据" title="家庭的记忆，先由家庭做决定" updatedAt="2026 年 9 月 4 日">
      <section><h2>1. 聆年会保存什么</h2><p>只在你主动建立档案、开始采访或上传素材时，保存账号信息、家庭人物关系、录音、转写文字、照片、故事、家人补充和产品操作记录。</p></section>
      <section><h2>2. 谁能看到</h2><p>每个家庭使用独立空间。只有该家庭已登录且未被撤销的成员可访问；平台的新家庭体验码不会让一个家庭进入另一个家庭的档案。</p></section>
      <section><h2>3. AI 会接触什么</h2><p>原始录音不会发给大模型。当你在当次采访明确开启 AI 整理与自然追问时，系统会发送本次转写文字和必要的近期上下文。将整场采访整理成故事时，会再独立请求一次授权。</p></section>
      <section><h2>4. 照片、人物影像和声音</h2><p>默认不向第三方生成服务上传。老照片修复、人物视频或声音复刻需要单独选择服务、确认预算和素材权利；真人演绎还必须得到本人专项授权。</p></section>
      <section><h2>5. 加密、导出与保管</h2><p>正式空间使用加密存储与安全连接。家庭可导出包含文字、原声、照片和校验清单的开放格式传承包，导出包不包含主密钥、恢复口令或模型凭据。</p></section>
      <section><h2>6. 撤销访问与注销</h2><p>家庭管理员可撤销成员访问。普通成员可在家庭管理页注销自己的账号；管理员需先导出档案并交接管理权，避免误删全家庭唯一的记忆。</p></section>
      <aside><strong>内测边界</strong><p>本页是产品内测阶段的数据处理说明，不替代正式上线前由运营主体审定的法律文件。</p></aside>
    </LegalDocument>
  );
}
