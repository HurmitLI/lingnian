"use client";

import Image from "next/image";
import Link from "next/link";
import {
  Archive,
  AudioLines,
  BookOpenText,
  CircleHelp,
  Download,
  MapPin,
  MessageCircleQuestion,
  Mic2,
  NotebookTabs,
  Route,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import AppShell from "@/components/layout/app-shell";
import { api, apiDownload, mediaUrl } from "@/lib/api";
import type {
  ArchiveAnswer,
  ElderProfile,
  FamilyPerson,
  GenerativeMediaCapability,
  GenerativeMediaRequest,
  LegacyPlan,
  TimelineItem,
} from "@/lib/types";

type HubTab = "ask" | "listen" | "journey" | "family" | "legacy" | "studio";

const TABS: Array<{ key: HubTab; label: string; icon: typeof CircleHelp }> = [
  { key: "ask", label: "问问家人", icon: MessageCircleQuestion },
  { key: "listen", label: "声音故事", icon: AudioLines },
  { key: "journey", label: "人生轨迹", icon: Route },
  { key: "family", label: "家人补充", icon: Users },
  { key: "legacy", label: "传家保存", icon: Archive },
  { key: "studio", label: "影像实验室", icon: Sparkles },
];

const CONTRIBUTION_LABELS = {
  context: "补充背景",
  correction: "更正线索",
  question: "继续追问",
  alternate_memory: "另一种记忆",
} as const;

function displayYear(item: TimelineItem): string {
  if (item.detail?.event_year) return String(item.detail.event_year);
  const normalized = item.events.find((event) => event.normalized_time)?.normalized_time;
  if (normalized) return normalized;
  return new Date(item.story.confirmed_at).toLocaleDateString("zh-CN");
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function MemoryHubApp() {
  const router = useRouter();
  const [profiles, setProfiles] = useState<ElderProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [people, setPeople] = useState<FamilyPerson[]>([]);
  const [legacyPlan, setLegacyPlan] = useState<LegacyPlan | null>(null);
  const [capabilities, setCapabilities] = useState<GenerativeMediaCapability[]>([]);
  const [generationRequests, setGenerationRequests] = useState<GenerativeMediaRequest[]>([]);
  const [tab, setTab] = useState<HubTab>("ask");
  const [answer, setAnswer] = useState<ArchiveAnswer | null>(null);
  const [question, setQuestion] = useState("");
  const [activeStoryId, setActiveStoryId] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedProfile = profiles.find((item) => item.id === selectedProfileId) ?? null;
  const activeStory = timeline.find((item) => item.story.id === activeStoryId) ?? timeline[0] ?? null;
  const audioStories = useMemo(() => timeline.filter((item) => item.audio_url), [timeline]);

  const showError = useCallback((value: unknown) => {
    setNotice("");
    setError(value instanceof Error ? value.message : "操作没有成功，请稍后重试。");
  }, []);

  const loadProfileData = useCallback(async (profile: ElderProfile) => {
    const [timelineResult, peopleResult, planResult, capabilityResult, requestResult] = await Promise.all([
      api<TimelineItem[]>(`/api/v1/elder-profiles/${profile.id}/timeline`),
      api<FamilyPerson[]>(`/api/v1/families/${profile.family_id}/people`),
      api<LegacyPlan | null>(`/api/v1/families/${profile.family_id}/legacy-plan`),
      api<GenerativeMediaCapability[]>("/api/v1/generative-media/capabilities"),
      api<GenerativeMediaRequest[]>(`/api/v1/elder-profiles/${profile.id}/generative-media-requests`),
    ]);
    setTimeline(timelineResult);
    setPeople(peopleResult);
    setLegacyPlan(planResult);
    setCapabilities(capabilityResult);
    setGenerationRequests(requestResult);
    setActiveStoryId((current) => timelineResult.some((item) => item.story.id === current) ? current : timelineResult[0]?.story.id ?? "");
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      try {
        const result = await api<ElderProfile[]>("/api/v1/elder-profiles");
        if (cancelled) return;
        setProfiles(result);
        const stored = window.localStorage.getItem("niannian.profileId");
        const selected = result.find((item) => item.id === stored) ?? result[0];
        if (selected) setSelectedProfileId(selected.id);
      } catch (value) {
        if (!cancelled) showError(value);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initialize();
    return () => { cancelled = true; };
  }, [showError]);

  useEffect(() => {
    const profile = selectedProfile;
    if (!profile) return;
    const profileId = profile.id;
    const familyId = profile.family_id;
    let cancelled = false;
    window.localStorage.setItem("niannian.profileId", profileId);
    async function syncProfile() {
      try {
        const [timelineResult, peopleResult, planResult, capabilityResult, requestResult] = await Promise.all([
          api<TimelineItem[]>(`/api/v1/elder-profiles/${profileId}/timeline`),
          api<FamilyPerson[]>(`/api/v1/families/${familyId}/people`),
          api<LegacyPlan | null>(`/api/v1/families/${familyId}/legacy-plan`),
          api<GenerativeMediaCapability[]>("/api/v1/generative-media/capabilities"),
          api<GenerativeMediaRequest[]>(`/api/v1/elder-profiles/${profileId}/generative-media-requests`),
        ]);
        if (cancelled) return;
        setTimeline(timelineResult);
        setPeople(peopleResult);
        setLegacyPlan(planResult);
        setCapabilities(capabilityResult);
        setGenerationRequests(requestResult);
        setActiveStoryId((current) => timelineResult.some((item) => item.story.id === current) ? current : timelineResult[0]?.story.id ?? "");
      } catch (value) {
        if (!cancelled) showError(value);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void syncProfile();
    return () => { cancelled = true; };
  }, [selectedProfile, showError]);

  async function askArchive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile || !question.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<ArchiveAnswer>(
        `/api/v1/elder-profiles/${selectedProfile.id}/archive-questions`,
        { method: "POST", body: JSON.stringify({ question: question.trim(), max_citations: 3 }) },
      );
      setAnswer(result);
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function createFollowUp() {
    if (!selectedProfile || !answer?.follow_up_question) return;
    setBusy(true);
    setError("");
    try {
      const session = await api<{ id: string }>(
        `/api/v1/elder-profiles/${selectedProfile.id}/archive-gaps`,
        {
          method: "POST",
          body: JSON.stringify({
            question: answer.follow_up_question,
            actor_label: "家庭成员",
            life_stage: "家人提问",
          }),
        },
      );
      router.push(`/record?elder=${selectedProfile.id}&session=${session.id}`);
    } catch (value) {
      showError(value);
      setBusy(false);
    }
  }

  async function updateStoryDetail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeStory || !selectedProfile) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/stories/${activeStory.story.id}/detail`, {
        method: "PUT",
        body: JSON.stringify({
          event_year: form.get("eventYear") ? Number(form.get("eventYear")) : null,
          place_name: form.get("placeName") || null,
          theme_tags: String(form.get("themeTags") ?? "").split(/[，,]/).map((item) => item.trim()).filter(Boolean),
          summary: form.get("summary") || null,
          updated_by: form.get("updatedBy") || "家庭成员",
        }),
      });
      await loadProfileData(selectedProfile);
      setNotice("故事的时间、地点和主题已经保存。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function addContribution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeStory || !selectedProfile) return;
    const element = event.currentTarget;
    const form = new FormData(element);
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/stories/${activeStory.story.id}/contributions`, {
        method: "POST",
        body: JSON.stringify({
          contributor_person_id: form.get("personId") || null,
          contributor_label: form.get("contributorLabel"),
          contribution_type: form.get("contributionType"),
          body: form.get("body"),
        }),
      });
      element.reset();
      await loadProfileData(selectedProfile);
      setNotice("这条家人补充已经和原故事并列保存，不会覆盖老人的原话。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function addPhotoPersonTag(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeStory?.image_asset_id || !selectedProfile) return;
    const element = event.currentTarget;
    const form = new FormData(element);
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/media-assets/${activeStory.image_asset_id}/person-tags`, {
        method: "POST",
        body: JSON.stringify({
          person_id: form.get("personId"),
          tagged_by: form.get("taggedBy") || "家庭成员",
          note: form.get("note") || null,
        }),
      });
      element.reset();
      await loadProfileData(selectedProfile);
      setNotice("照片人物已由家人手动确认并标注。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function saveLegacyPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProfile) return;
    const form = new FormData(event.currentTarget);
    const successors = form.getAll("successors").map(String);
    setBusy(true);
    setError("");
    try {
      const result = await api<LegacyPlan>(`/api/v1/families/${selectedProfile.family_id}/legacy-plan`, {
        method: "PUT",
        body: JSON.stringify({
          successor_person_ids: successors,
          access_policy: form.get("accessPolicy"),
          steward_label: form.get("stewardLabel"),
          note: form.get("note") || null,
        }),
      });
      setLegacyPlan(result);
      setNotice("传承安排已经记录。系统不会根据这份记录自动转移权限。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  async function downloadHeritagePackage() {
    if (!selectedProfile) return;
    setBusy(true);
    setError("");
    try {
      const result = await apiDownload(`/api/v1/elder-profiles/${selectedProfile.id}/heritage-package`, {
        method: "POST",
        body: JSON.stringify({ actor_label: "家庭成员" }),
        timeoutMs: 10 * 60 * 1000,
      });
      saveBlob(result.blob, result.filename);
      setNotice("开放格式传承包已经生成并下载，请至少保存两份。");
    } catch (value) {
      showError(value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <main className="workspace-main memory-hub-main">
        <header className="workspace-header memory-hub-header">
          <div className="workspace-heading">
            <p className="eyebrow">家庭记忆 · 持续生长</p>
            <h1>让后来的人，不只看到一份文件</h1>
            <p className="subtitle">可以听见原声、追溯出处、补充不同记忆，也可以继续提出下一次要问的问题。</p>
          </div>
          <div className="workspace-controls">
            <label className="profile-switcher">
              <span className="profile-switcher-label"><i>{selectedProfile?.preferred_name.slice(0, 1) || "家"}</i><span><small>当前讲述者</small><strong>切换档案</strong></span></span>
              <select value={selectedProfileId} onChange={(event) => setSelectedProfileId(event.target.value)}>
                {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.preferred_name}</option>)}
              </select>
            </label>
            <span className="privacy-badge">本机家庭档案</span>
          </div>
        </header>

        {error && <div className="banner error" role="alert">{error}</div>}
        {notice && <div className="banner notice" role="status">{notice}</div>}

        <nav className="memory-hub-tabs" aria-label="家族记忆功能">
          {TABS.map((item) => {
            const Icon = item.icon;
            return <button key={item.key} type="button" aria-current={tab === item.key ? "page" : undefined} onClick={() => setTab(item.key)}><Icon size={18} aria-hidden="true" /><span>{item.label}</span></button>;
          })}
        </nav>

        {loading && <section className="card memory-hub-loading">正在读取家庭记忆…</section>}
        {!loading && !selectedProfile && <section className="card memory-hub-empty"><h2>先建立一位讲述者档案</h2><Link className="button primary button-link" href="/family">前往家庭管理</Link></section>}

        {!loading && selectedProfile && tab === "ask" && (
          <section className="memory-ask-layout">
            <div className="card memory-ask-card">
              <div className="section-heading"><span>01</span><div><h2>问问{selectedProfile.preferred_name}</h2><p>只从家人已经确认的故事中寻找答案，不替家人猜测。</p></div></div>
              <form onSubmit={askArchive}>
                <label className="field"><span>你想知道什么？</span><textarea rows={3} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：奶奶年轻时做过什么工作？" required /></label>
                <div className="memory-question-suggestions" aria-label="问题示例">
                  {["小时候家里是什么样的？", "第一份工作是怎么找到的？", "家里为什么搬到这里？"].map((item) => <button type="button" key={item} onClick={() => setQuestion(item)}>{item}</button>)}
                </div>
                <button className="button primary" disabled={busy || !question.trim()}>从家庭档案里找答案</button>
              </form>
            </div>
            <aside className="card memory-answer-card" aria-live="polite">
              {!answer ? <div className="memory-answer-empty"><MessageCircleQuestion size={34} aria-hidden="true" /><h2>答案要有出处</h2><p>找到故事时会同时显示原文和原声；没有记录时，会把问题留给下一次采访。</p></div> : <>
                <div className="memory-answer-status"><ShieldCheck size={17} aria-hidden="true" /><span>{answer.status === "grounded" ? "来自已确认档案" : "暂时没有可靠记录"}</span></div>
                <p className="memory-answer-text">{answer.answer}</p>
                {answer.citations.map((citation) => <article className="memory-citation" key={citation.story_id}>
                  <div><span>{citation.life_stage}</span><h3>{citation.title}</h3></div><p>{citation.excerpt}</p>
                  {citation.audio_url && <audio controls preload="metadata" src={mediaUrl(citation.audio_url) ?? undefined} />}
                </article>)}
                {answer.follow_up_question && <div className="memory-gap"><strong>把空白变成下一次采访</strong><p>{answer.follow_up_question}</p><button className="button secondary" disabled={busy} onClick={createFollowUp}>带着这个问题去记录</button></div>}
              </>}
            </aside>
          </section>
        )}

        {!loading && selectedProfile && tab === "listen" && (
          <section className="card sound-library">
            <div className="section-heading"><span>02</span><div><h2>{selectedProfile.preferred_name}的声音故事馆</h2><p>保留真实语气和停顿，不把原声替换成合成声音。</p></div><strong>{audioStories.length} 集</strong></div>
            {audioStories.length === 0 ? <div className="memory-hub-empty"><Mic2 size={30} aria-hidden="true" /><h3>还没有可以播放的原声故事</h3><Link className="button primary button-link" href={`/record?elder=${selectedProfile.id}`}>记录第一段声音</Link></div> : <div className="sound-library-grid">
              <div className="sound-feature">
                {activeStory?.image_url ? <Image unoptimized width={900} height={600} src={mediaUrl(activeStory.image_url) ?? ""} alt={activeStory.image_annotation || activeStory.story.title} /> : <div className="sound-cover"><AudioLines size={42} aria-hidden="true" /><span>{activeStory?.life_stage}</span></div>}
                <div><span className="card-kicker">正在聆听</span><h2>{activeStory?.story.title}</h2><p>{activeStory?.story.body}</p>{activeStory?.audio_url && <audio key={activeStory.audio_url} controls autoPlay={false} preload="metadata" src={mediaUrl(activeStory.audio_url) ?? undefined} />}</div>
              </div>
              <div className="sound-episodes" role="list" aria-label="声音故事列表">
                {audioStories.map((item, index) => <button type="button" role="listitem" key={item.story.id} aria-current={activeStory?.story.id === item.story.id ? "true" : undefined} onClick={() => setActiveStoryId(item.story.id)}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.story.title}</strong><small>{item.life_stage}{item.detail?.place_name ? ` · ${item.detail.place_name}` : ""}</small></div><AudioLines size={17} aria-hidden="true" /></button>)}
              </div>
            </div>}
          </section>
        )}

        {!loading && selectedProfile && tab === "journey" && (
          <section className="card life-journey">
            <div className="section-heading"><span>03</span><div><h2>人生轨迹</h2><p>把年份、地点、人物和故事放回一生的脉络里；不确定的信息继续保留为待核实。</p></div></div>
            {timeline.length === 0 ? <p className="empty">还没有已确认故事。</p> : <div className="life-route">
              {[...timeline].sort((a, b) => (a.detail?.event_year ?? 9999) - (b.detail?.event_year ?? 9999)).map((item) => <article key={item.story.id}>
                <div className="life-route-marker"><span>{displayYear(item)}</span></div>
                <div className="life-route-story"><div className="story-route-meta"><span>{item.life_stage}</span>{item.detail?.place_name && <span><MapPin size={14} aria-hidden="true" />{item.detail.place_name}</span>}</div><h3>{item.story.title}</h3><p>{item.detail?.summary || item.story.body}</p><div className="tags">{item.detail?.theme_tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div>
              </article>)}
            </div>}
          </section>
        )}

        {!loading && selectedProfile && tab === "family" && (
          <section className="memory-family-layout">
            <div className="card memory-story-picker">
              <div className="section-heading"><span>04</span><div><h2>选择一篇故事</h2><p>补充内容与原故事并列保存，不会静默改写老人的原话。</p></div></div>
              <div className="memory-story-list">{timeline.map((item) => <button type="button" key={item.story.id} aria-current={activeStory?.story.id === item.story.id ? "true" : undefined} onClick={() => setActiveStoryId(item.story.id)}><strong>{item.story.title}</strong><small>{item.life_stage} · {item.contributions.length} 条补充</small></button>)}</div>
            </div>
            {activeStory && <div className="memory-family-editor">
              <section className="card"><span className="card-kicker">原故事</span><h2>{activeStory.story.title}</h2><p className="story-body">{activeStory.story.body}</p>{activeStory.audio_url && <audio controls preload="metadata" src={mediaUrl(activeStory.audio_url) ?? undefined} />}</section>
              <section className="card"><h3>补充时间、地点和主题</h3><form className="memory-detail-form" key={activeStory.detail?.updated_at ?? activeStory.story.id} onSubmit={updateStoryDetail}><label className="field"><span>年份（可选）</span><input name="eventYear" type="number" min="1800" max="2100" defaultValue={activeStory.detail?.event_year ?? ""} /></label><label className="field"><span>地点（家人确认）</span><input name="placeName" maxLength={160} defaultValue={activeStory.detail?.place_name ?? ""} /></label><label className="field full"><span>主题标签，用逗号分隔</span><input name="themeTags" defaultValue={activeStory.detail?.theme_tags.join("，") ?? ""} placeholder="工作，迁居，老物件" /></label><label className="field full"><span>一句话说明</span><textarea name="summary" rows={2} defaultValue={activeStory.detail?.summary ?? ""} /></label><label className="field"><span>确认人</span><input name="updatedBy" required defaultValue="家庭成员" /></label><button className="button secondary" disabled={busy}>保存故事资料</button></form></section>
              <section className="card"><h3>家人补充与不同记忆</h3>{activeStory.contributions.length > 0 && <div className="contribution-list">{activeStory.contributions.map((item) => <article key={item.id}><span>{CONTRIBUTION_LABELS[item.contribution_type]}</span><strong>{item.contributor_label}</strong><p>{item.body}</p></article>)}</div>}<form className="contribution-form" onSubmit={addContribution}><label className="field"><span>家庭成员（可选）</span><select name="personId"><option value="">不绑定成员</option>{people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label><label className="field"><span>显示称呼</span><input name="contributorLabel" required maxLength={80} defaultValue="家庭成员" /></label><label className="field"><span>补充类型</span><select name="contributionType">{Object.entries(CONTRIBUTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field full"><span>补充内容</span><textarea name="body" required rows={4} /></label><button className="button primary" disabled={busy}>保存家人补充</button></form></section>
              {activeStory.image_url && activeStory.image_asset_id && <section className="card photo-people-card"><h3>这张照片里有谁？</h3><div className="photo-people-grid"><Image unoptimized width={720} height={520} src={mediaUrl(activeStory.image_url) ?? ""} alt={activeStory.image_annotation || activeStory.story.title} /><div>{activeStory.person_tags.length > 0 && <div className="tags">{activeStory.person_tags.map((tag) => <span key={tag.id}>{tag.person_name}</span>)}</div>}<form onSubmit={addPhotoPersonTag}><label className="field"><span>家人确认的人物</span><select name="personId" required><option value="">请选择</option>{people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label><label className="field"><span>标注人</span><input name="taggedBy" required defaultValue="家庭成员" /></label><label className="field"><span>说明（可选）</span><input name="note" maxLength={500} /></label><button className="button secondary" disabled={busy}>添加人物标注</button></form></div></div></section>}
            </div>}
          </section>
        )}

        {!loading && selectedProfile && tab === "legacy" && (
          <section className="memory-legacy-layout">
            <div className="card heritage-export-card"><BookOpenText size={34} aria-hidden="true" /><span className="card-kicker">开放格式传承包</span><h2>把故事、原声和照片一起带走</h2><p>生成 ZIP 文件，内含可直接打开的家庭故事网页、结构化 JSON、原始媒体和校验清单，不绑定聆年软件。</p><button className="button primary" disabled={busy || timeline.length === 0} onClick={downloadHeritagePackage}><Download size={17} aria-hidden="true" />生成并下载传承包</button><small>不会包含主密钥、恢复口令、API Key 或模型凭据。</small></div>
            <div className="card legacy-plan-card"><div className="section-heading"><span>05</span><div><h2>指定家人接管</h2><p>这里只保存家庭安排，不检测离世状态，也不会自动转移权限。</p></div></div><form key={legacyPlan?.updated_at ?? selectedProfile.family_id} onSubmit={saveLegacyPlan}><fieldset><legend>未来由谁共同保管？</legend>{people.map((person) => <label key={person.id}><input type="checkbox" name="successors" value={person.id} defaultChecked={legacyPlan?.successor_person_ids.includes(person.id)} />{person.display_name}</label>)}</fieldset><label className="field"><span>交接方式</span><select name="accessPolicy" defaultValue={legacyPlan?.access_policy ?? "manual_handoff"}><option value="manual_handoff">由家庭线下确认后手动交接</option><option value="joint_family_review">由多位家人共同复核</option><option value="designated_steward">由指定保管人负责</option></select></label><label className="field"><span>本次确认人</span><input name="stewardLabel" required defaultValue={legacyPlan?.steward_label ?? "家庭档案管理员"} /></label><label className="field"><span>家庭说明（可选）</span><textarea name="note" rows={3} defaultValue={legacyPlan?.note ?? ""} /></label><button className="button secondary" disabled={busy}>保存传承安排</button></form></div>
          </section>
        )}

        {!loading && selectedProfile && tab === "studio" && (
          <section className="card generation-studio">
            <div className="section-heading"><span>06</span><div><h2>影像与声音实验室</h2><p>生成式能力独立于家庭档案；未选择服务、预算和真人授权前，不会上传任何素材。</p></div></div>
            <div className="generation-capabilities">{capabilities.map((item) => <article key={item.generation_type}><div><span className="generation-status">{item.available ? "可使用" : "尚未启用"}</span><h3>{item.label}</h3></div><p>{item.unavailable_reason}</p><dl><div><dt>真人授权</dt><dd>{item.requires_subject_consent ? "必须" : "按素材判断"}</dd></div><div><dt>外部上传</dt><dd>{item.requires_external_upload ? "启用前逐次确认" : "不需要"}</dd></div><div><dt>预计费用</dt><dd>{item.estimated_cost_cents === null ? "选择供应商后显示" : `¥${(item.estimated_cost_cents / 100).toFixed(2)}`}</dd></div></dl><button className="button secondary" disabled>等待配置与预算确认</button></article>)}</div>
            <div className="generation-current"><NotebookTabs size={23} aria-hidden="true" /><div><strong>现在仍可使用零费用的快速影像导出</strong><p>它只把原始录音、照片和文字合成 MP4，不是人物视频，已经从主导航降级为辅助工具。</p></div><Link className="button quiet button-link" href={`/keepsake?elder=${selectedProfile.id}`}>打开快速影像导出</Link></div>
            {generationRequests.length > 0 && <details><summary>查看已登记的生成准备记录（{generationRequests.length}）</summary>{generationRequests.map((request) => <p key={request.id}>{request.generation_type} · {request.status} · 未产生费用</p>)}</details>}
          </section>
        )}
      </main>
    </AppShell>
  );
}
