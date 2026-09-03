"use client";

import { CheckCircle2, FileVideo2, LoaderCircle, Upload, XCircle } from "lucide-react";
import { FormEvent, useState } from "react";

import { api, mediaUrl } from "@/lib/api";
import { IS_FORMAL_CLOUD } from "@/lib/runtime";
import type { GenerativeMediaRequest, TimelineItem } from "@/lib/types";

type Props = {
  profileId: string;
  timeline: TimelineItem[];
  requests: GenerativeMediaRequest[];
  onRequestsChange: (requests: GenerativeMediaRequest[]) => void;
  onNotice: (message: string) => void;
  onError: (error: unknown) => void;
};

const STATUS_LABELS: Record<string, string> = {
  awaiting_provider: "等待生成或导入成片",
  queued: "正在等待家用生成节点",
  processing: "家用生成节点正在制作",
  failed: "生成失败，等待处理",
  cancelled: "已取消",
  pending_human_review: "等待家人逐项验收",
  accepted: "验收通过",
  rejected: "验收驳回",
};

const TYPE_LABELS: Record<string, string> = {
  portrait_video: "人物讲述视频",
  scene_video: "故事情景视频",
  photo_restore: "老照片修复副本",
};

async function loadRequests(profileId: string) {
  return api<GenerativeMediaRequest[]>(
    `/api/v1/elder-profiles/${profileId}/generative-media-requests`,
  );
}

export default function GenerationReviewWorkbench({
  profileId,
  timeline,
  requests,
  onRequestsChange,
  onNotice,
  onError,
}: Props) {
  const [busyRequestId, setBusyRequestId] = useState<string | null>(null);

  async function refresh() {
    onRequestsChange(await loadRequests(profileId));
  }

  async function registerTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusyRequestId("new");
    try {
      const maxCostYuan = Number(form.get("maxCostYuan") || 0);
      await api(`/api/v1/elder-profiles/${profileId}/generative-media-requests`, {
        method: "POST",
        body: JSON.stringify({
          story_id: form.get("storyId"),
          generation_type: form.get("generationType"),
          actor_label: form.get("actorLabel") || "家庭管理员",
          subject_consent: form.get("subjectConsent") === "on",
          rights_confirmed: form.get("rightsConfirmed") === "on",
          no_impersonation: form.get("noImpersonation") === "on",
          allow_external_upload: form.get("allowExternalUpload") === "on",
          max_cost_cents: Math.round(maxCostYuan * 100),
        }),
      });
      await refresh();
      formElement.reset();
      onNotice(
        form.get("allowExternalUpload") === "on"
          ? "制作任务已进入家用生成队列。生成完成后仍要由家人逐项验收。"
          : "制作任务已经登记。导入成片后仍必须逐项人工验收，不会自动进入正式展示。",
      );
    } catch (error) {
      onError(error);
    } finally {
      setBusyRequestId(null);
    }
  }

  async function importResult(event: FormEvent<HTMLFormElement>, requestId: string) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const actualCostYuan = Number(form.get("actualCostYuan") || 0);
    form.delete("actualCostYuan");
    form.set("actual_cost_cents", String(Math.round(actualCostYuan * 100)));
    setBusyRequestId(requestId);
    try {
      await api(`/api/v1/generative-media-requests/${requestId}/result`, {
        method: "POST",
        body: form,
        timeoutMs: 10 * 60 * 1000,
      });
      await refresh();
      onNotice(`成片已导入${IS_FORMAL_CLOUD ? "家庭私密空间" : "本机"}，但尚未通过验收，不会出现在正式展示中。`);
    } catch (error) {
      onError(error);
    } finally {
      setBusyRequestId(null);
    }
  }

  async function reviewResult(event: FormEvent<HTMLFormElement>, requestId: string) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const decision = submitter?.value;
    if (decision !== "accepted" && decision !== "rejected") return;
    setBusyRequestId(requestId);
    try {
      await api(`/api/v1/generative-media-requests/${requestId}/review`, {
        method: "PATCH",
        body: JSON.stringify({
          decision,
          reviewed_by: form.get("reviewedBy") || "家庭验收人",
          review_notes: form.get("reviewNotes") || null,
          audio_present: form.get("audioPresent") === "on",
          lip_sync_verified: form.get("lipSyncVerified") === "on",
          pauses_natural: form.get("pausesNatural") === "on",
          expression_natural: form.get("expressionNatural") === "on",
          narrative_consistent: form.get("narrativeConsistent") === "on",
          duration_appropriate: form.get("durationAppropriate") === "on",
          source_preserved: form.get("sourcePreserved") === "on",
          identity_preserved: form.get("identityPreserved") === "on",
        }),
      });
      await refresh();
      onNotice(
        decision === "accepted"
          ? "六项检查全部通过，成片已标记为验收通过。"
          : "成片已驳回并保留问题说明，不会进入正式展示。",
      );
    } catch (error) {
      onError(error);
    } finally {
      setBusyRequestId(null);
    }
  }

  async function cancelTask(requestId: string) {
    if (!window.confirm("确定取消这项生成任务吗？已经完成的本地计算不会进入家庭档案。")) return;
    setBusyRequestId(requestId);
    try {
      await api(`/api/v1/generative-media-requests/${requestId}/cancel`, { method: "POST" });
      await refresh();
      onNotice("生成任务已取消，家用节点不能再回传这项任务的结果。");
    } catch (error) {
      onError(error);
    } finally {
      setBusyRequestId(null);
    }
  }

  return (
    <section className="generation-review-workbench" aria-labelledby="generation-review-title">
      <div className="generation-review-heading">
        <div>
          <span className="card-kicker">成片门禁 · 家庭验收</span>
          <h3 id="generation-review-title">先登记、再导入、最后逐项验收</h3>
          <p>生成成功不等于可以使用。人物讲述必须检查声音、嘴型、停顿、表情、叙事和时长；故事情景视频也必须检查声音、画面自然度、叙事和时长。</p>
        </div>
        <span className="review-gate-badge"><FileVideo2 size={16} aria-hidden="true" />未验收不发布</span>
      </div>

      <form className="generation-task-form" onSubmit={registerTask}>
        <label className="field"><span>制作类型</span><select name="generationType"><option value="photo_restore">老照片修复副本</option><option value="portrait_video">人物讲述视频</option><option value="scene_video">故事情景视频</option></select></label>
        <label className="field"><span>对应故事</span><select name="storyId" required>{timeline.map((item) => <option key={item.story.id} value={item.story.id}>{item.story.title}</option>)}</select></label>
        <label className="field"><span>确认人</span><input name="actorLabel" required defaultValue="家庭管理员" /></label>
        <label className="field"><span>单次预算上限（元）</span><input name="maxCostYuan" type="number" min="0" max="1000" step="0.01" defaultValue="1.00" required /></label>
        <fieldset>
          <legend>制作边界</legend>
          <label><input type="checkbox" name="subjectConsent" required />如涉及仍健在的真人，已经取得本人专项授权</label>
          <label><input type="checkbox" name="rightsConfirmed" required />确认拥有照片、原声和故事的使用权</label>
          <label><input type="checkbox" name="noImpersonation" required />不会冒充本人或误导公众</label>
          <label><input type="checkbox" name="allowExternalUpload" />本次允许把所选素材加密发送到家用生成节点</label>
        </fieldset>
        <button className="button secondary" disabled={busyRequestId !== null || timeline.length === 0}>登记成片验收任务</button>
      </form>

      <div className="generation-request-list">
        {requests.length === 0 && <p className="generation-empty">还没有成片验收任务。下载制作包不会自动上传素材或产生费用。</p>}
        {requests.map((request) => (
          <article className={`generation-request-card status-${request.status}`} key={request.id}>
            <header><div><small>{TYPE_LABELS[request.generation_type] ?? request.generation_type}</small><h4>{STATUS_LABELS[request.status] ?? request.status}</h4></div><span>预算 ¥{(request.max_cost_cents / 100).toFixed(2)}</span></header>

            {(request.status === "queued" || request.status === "processing") && (
              <div className="generation-queue-progress" role="status">
                <LoaderCircle size={18} aria-hidden="true" />
                <div><strong>{request.progress_stage || "等待生成节点"}</strong><span>{request.status === "processing" ? `${request.progress_percent}% · 第 ${request.attempt_count} 次执行` : "家用电脑上线后会自动领取"}</span></div>
                <button className="button quiet" type="button" disabled={busyRequestId !== null} onClick={() => void cancelTask(request.id)}>取消任务</button>
              </div>
            )}

            {request.status === "failed" && (
              <div className="generation-review-result"><strong>这次生成没有完成</strong><p>{request.last_error_message || "可以由平台管理员在生成控制中心重新排队。"}</p></div>
            )}

            {request.status === "awaiting_provider" && (
              <form className="generation-result-form" onSubmit={(event) => importResult(event, request.id)}>
                <p>在后台完成生成后，把 MP4 导入{IS_FORMAL_CLOUD ? "家庭私密空间" : "本机"}。导入只进入待验收区，不会自动发布。</p>
                <label className="field"><span>生成方式</span><input name="provider_key" pattern="[A-Za-z0-9_.-]+" defaultValue="musetalk_manual" required /></label>
                <label className="field"><span>本次实际费用（元）</span><input name="actualCostYuan" type="number" min="0" step="0.01" defaultValue="0" required /></label>
                <label className="field full"><span>选择 MP4 成片</span><input name="video" type="file" accept="video/mp4,.mp4" required /></label>
                <button className="button secondary" disabled={busyRequestId !== null}><Upload size={16} aria-hidden="true" />导入待验收区</button>
              </form>
            )}

            {request.result_content_url && request.generation_type === "photo_restore" && (
              // eslint-disable-next-line @next/next/no-img-element
              <img className="generation-result-image" src={mediaUrl(request.result_content_url) ?? undefined} alt="等待家人验收的老照片修复副本" />
            )}

            {request.result_content_url && request.generation_type !== "photo_restore" && (
              <video controls preload="metadata" src={mediaUrl(request.result_content_url) ?? undefined}>浏览器无法播放这段视频。</video>
            )}

            {request.status === "pending_human_review" && (
              <form className="generation-review-form" onSubmit={(event) => reviewResult(event, request.id)}>
                <p>请完整播放并在说话和停顿位置反复检查。当前类型的必检项目全部通过后，才能接受成片。</p>
                <div className="generation-review-checks">
                  {request.generation_type !== "photo_restore" && <label><input type="checkbox" name="audioPresent" />声音完整且可以听清</label>}
                  {request.generation_type === "portrait_video" && <label><input type="checkbox" name="lipSyncVerified" />嘴型由声音驱动并基本对齐</label>}
                  {request.generation_type === "portrait_video" && <label><input type="checkbox" name="pausesNatural" />停顿时闭嘴，不持续咀嚼</label>}
                  {request.generation_type !== "photo_restore" && <label><input type="checkbox" name="expressionNatural" />{request.generation_type === "portrait_video" ? "表情自然，没有异常抽动" : "画面运动自然，没有异常抽动"}</label>}
                  {request.generation_type !== "photo_restore" && <label><input type="checkbox" name="narrativeConsistent" />人物年龄、画面和故事一致</label>}
                  {request.generation_type !== "photo_restore" && <label><input type="checkbox" name="durationAppropriate" />时长足以承载当前内容</label>}
                  {request.generation_type === "photo_restore" && <label><input type="checkbox" name="sourcePreserved" />原图已保留，修复结果是独立副本</label>}
                  {request.generation_type === "photo_restore" && <label><input type="checkbox" name="identityPreserved" />人物身份、五官和原始构图没有被擅自改写</label>}
                </div>
                <label className="field"><span>验收人</span><input name="reviewedBy" required defaultValue="家庭验收人" /></label>
                <label className="field full"><span>问题或验收说明</span><textarea name="reviewNotes" rows={3} placeholder="驳回时必须写明具体问题，例如第 3 秒停顿仍在咀嚼。" /></label>
                <div className="generation-review-actions"><button className="button danger" name="decision" value="rejected" disabled={busyRequestId !== null}><XCircle size={16} aria-hidden="true" />驳回成片</button><button className="button primary" name="decision" value="accepted" disabled={busyRequestId !== null}><CheckCircle2 size={16} aria-hidden="true" />必检项通过，接受成片</button></div>
              </form>
            )}

            {(request.status === "accepted" || request.status === "rejected") && (
              <div className="generation-review-result">
                <strong>{request.status === "accepted" ? "已通过家庭人工验收" : "已驳回，不会进入正式展示"}</strong>
                <p>{request.review_notes || "没有填写补充说明。"}</p>
                <small>验收人：{request.reviewed_by || "未记录"}{request.actual_cost_cents > 0 ? ` · 实际费用 ¥${(request.actual_cost_cents / 100).toFixed(2)}` : " · 未登记费用"}</small>
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
