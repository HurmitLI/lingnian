export type ElderProfile = {
  id: string;
  family_id: string;
  data_classification: "test" | "authorized_non_sensitive" | "authorized_sensitive";
  display_name: string;
  preferred_name: string;
  birth_year: number | null;
  birth_era: string | null;
  native_place: string | null;
  occupation_summary: string | null;
};

export type FamilyPerson = {
  id: string;
  family_id: string;
  role: string;
  display_name: string;
  created_at: string;
};

export type FamilyRelationship = {
  id: string;
  family_id: string;
  from_person_id: string;
  to_person_id: string;
  relationship_type: "parent" | "child" | "spouse" | "sibling" | "grandparent" | "grandchild" | "custom";
  custom_label: string | null;
  confirmed_by: string;
  created_at: string;
};

export type MemorySession = {
  id: string;
  elder_id: string;
  life_stage: string;
  prompt_id: string;
  question_text: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type MediaAsset = {
  id: string;
  kind: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  is_original: boolean;
  content_url: string;
};

export type MediaLink = {
  id: string;
  media_asset_id: string;
  trigger_kind: "photo" | "old_object";
  user_annotation: string | null;
  width: number;
  height: number;
  model_inference: null;
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
  model_consent_event_id: string | null;
};

export type FamilySecurity = {
  family_id: string;
  key_version: number | null;
  encryption_status: "not_initialized" | "key_ready" | "encrypted" | string;
  key_initialized: boolean;
  recovery_package_created_at: string | null;
  recovery_verified_at: string | null;
  activated_at: string | null;
};

export type BackupRecord = {
  id: string;
  backup_version: number;
  archive_sha256: string;
  database_sha256: string;
  asset_count: number;
  status: "ready" | "verified" | "recovery_verified" | "corrupt" | string;
  verified_at: string | null;
  verification_summary: Record<string, unknown>;
  created_at: string;
};

export type ModelConsent = {
  id: string;
  family_id: string;
  session_id: string;
  purpose: "story_organization";
  one_time: true;
  used_at: string | null;
};

export type TopicPreference = {
  id: string;
  elder_id: string;
  topic_key: string;
  preference: "welcome" | "ask_first" | "avoid";
  note: string | null;
  updated_by: string;
  updated_at: string;
};

export type ElderMemoryContext = {
  coverage: Array<{
    life_stage: string;
    session_count: number;
    confirmed_story_count: number;
  }>;
  preferences: TopicPreference[];
  confirmed_facts: Array<{
    id: string;
    story_id: string;
    fact_type: string;
    value_text: string;
    confidence: "confirmed";
  }>;
};

export type Reminder = {
  id: string;
  elder_id: string;
  topic_key: string;
  remind_at: string;
  status: "scheduled" | "due" | "shown_once" | "dismissed" | "paused_by_preference";
  show_count: number;
};

export type MemoryBook = {
  id: string;
  elder_id: string;
  version: number;
  title: string;
  content_sha256: string;
  story_manifest: Array<{ story_id: string; life_stage: string }>;
  created_by: string;
  status: string;
  pdf_status: "not_generated" | "ready" | "failed" | "corrupt";
  pdf_sha256: string | null;
  created_at: string;
};

export type KeepsakeCatalogItem = {
  story_id: string;
  title: string;
  life_stage: string;
  confirmed_at: string;
  has_original_audio: boolean;
  audio_asset_id: string | null;
  image_asset_id: string | null;
  unavailable_reason: string | null;
};

export type KeepsakeAuthorization = {
  id: string;
  elder_id: string;
  actor_label: string;
  story_ids: string[];
  manifest_sha256: string;
  original_voice_authorized: boolean;
  private_family_use: boolean;
  no_impersonation: boolean;
  original_audio_only: boolean;
  decision: string;
  used_at: string | null;
  created_at: string;
};

export type Keepsake = {
  id: string;
  elder_id: string;
  authorization_id: string;
  version: number;
  title: string;
  story_manifest: Array<{
    story_id: string;
    position: number;
    audio_asset_id: string;
    image_asset_id: string | null;
  }>;
  status: "queued" | "rendering" | "ready" | "failed_retryable" | "failed_final" | "corrupt" | string;
  progress: number;
  attempt: number;
  error_code: string | null;
  mime_type: string;
  duration_ms: number | null;
  width: number;
  height: number;
  size_bytes: number | null;
  renderer: string;
  cost_cents: number;
  source_mode: string;
  content_url: string | null;
  created_at: string;
  updated_at: string;
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
