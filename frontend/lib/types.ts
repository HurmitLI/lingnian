export type ElderProfile = {
  id: string;
  family_id: string;
  display_name: string;
  preferred_name: string;
  birth_year: number | null;
  birth_era: string | null;
  native_place: string | null;
  occupation_summary: string | null;
};

export type MemorySession = {
  id: string;
  elder_id: string;
  life_stage: string;
  question_text: string;
  status: string;
};

export type MediaAsset = {
  id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  is_original: boolean;
  content_url: string;
};

export type Transcript = {
  id: string;
  raw_text: string;
  corrected_text: string;
  version: number;
  asr_provider: string;
  asr_model: string;
  asr_metadata: Record<string, unknown>;
};

export type StoryDraft = {
  id: string;
  title: string;
  body: string;
  uncertainties: string[];
  added_facts: string[];
  provider: string;
  model: string;
  status: string;
};

export type WorkflowTask = {
  id: string;
  task_type: string;
  status: string;
  progress: number;
  error_code: string | null;
};

export type SessionDetail = {
  session: MemorySession;
  media_assets: MediaAsset[];
  transcript: Transcript | null;
  story_draft: StoryDraft | null;
  tasks: WorkflowTask[];
};

export type TimelineItem = {
  story: {
    id: string;
    title: string;
    body: string;
    confirmed_by: string;
    confirmed_at: string;
  };
  events: Array<{
    id: string;
    time_expression: string | null;
    normalized_time: string | null;
    confidence: string;
  }>;
  audio_url: string | null;
};

export type Health = {
  status: string;
  environment: string;
  asr_provider: string;
  llm_provider: string;
};

