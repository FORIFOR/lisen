export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public data?: Record<string, unknown>,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? (init?.body !== undefined ? "POST" : "GET"),
    headers: init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let msg = `通信エラー (${res.status})`;
    let data: Record<string, unknown> | undefined;
    try {
      const j = await res.json();
      data = j;
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join(", ");
    } catch {}
    // a 5xx without our JSON detail means the proxy could not reach the API
    if (res.status >= 500 && typeof data?.detail !== "string") msg = "サーバーに接続できません。Mac で ListenFlow が起動しているか確認してください";
    throw new ApiError(res.status, msg, data);
  }
  return res.json() as Promise<T>;
}

export const audioUrl = (rel?: string | null) => (rel ? `/api/audio/${rel}` : "");

// ---- types --------------------------------------------------------------------

export type Block = {
  kind: string;
  minutes: number;
  label: string;
  description: string;
  elapsed?: number;
  done?: boolean;
  current?: boolean;
};

export type Session = {
  id: string;
  mode: "standard" | "review" | "test";
  minutes: number;
  status: "active" | "paused" | "completed" | "abandoned";
  test_form: string | null;
  blocks: Block[];
  block_index: number;
  study_seconds: number;
  planned_study_minutes: number;
  notes: string[];
};

export type TestStatus = {
  form: string;
  name: string;
  day: number;
  available: boolean;
  taken: boolean;
  score: number | null;
  taken_at: string | null;
};

export type Settings = {
  daily_goal: number;
  break_enabled: boolean;
  break_minutes: number;
  break_every: number;
  playback_rate: number;
  auto_lessons: boolean;
  lesson_engine: "auto" | "gemini" | "local";
  gemini_model: string | null;
};

export type Profile = {
  onboarded: boolean;
  level: number;
  unlocked: number;
  level_name: string;
  settings: Settings;
  day: number;
  curriculum_days: number;
  phase: { name: string; description: string; start: number; end: number | null; focus: string[] };
  today_minutes: number;
  streak: number;
  review_due: number;
  tests: TestStatus[];
  active_session: Session | null;
  vocab: { diagnosed: boolean; active_diagnostic: boolean; due: number };
};

export type SegmentRef = { id: string; audio: string; speaker: string; speakers?: number; index?: number };
export type ClipPublic = {
  id: string;
  level: number;
  kind: string;
  title: string;
  audio: string;
  duration: number;
  speakers: number;
  segments: { id: string; index: number; speaker: string; start: number; end: number; audio: string; text?: string }[];
};

type ItemBase = { item_id: string; block: string; level: number; reason?: string; review?: boolean };
export type ComprehensionItem = ItemBase & {
  type: "comprehension";
  clip: ClipPublic;
  title: string;
  relisten: boolean;
  questions: { q: string; choices: string[]; type: string }[];
};
export type DictationItem = ItemBase & {
  type: "dictation";
  segment: SegmentRef;
  clip_title: string;
  word_count: number;
  context: { speaker: string; text: string }[];
};
export type DiscriminationItem = ItemBase & {
  type: "discrimination";
  segment: SegmentRef;
  clip_title: string;
  options: string[];
  focus: string;
};
export type VocabItem = ItemBase & {
  type: "vocab";
  segment: SegmentRef;
  masked: string;
  letters: number;
  first_letter: string;
};
export type ShadowingItem = ItemBase & { type: "shadowing"; segment: SegmentRef; text: string; clip_title: string };
export type Modality = "text" | "audio" | "context";
export type WordItem = {
  type: "word";
  item_id: string;
  block?: string;
  modality: Modality;
  modality_label: string;
  prompt: string;
  tier: number;
  level: number;
  choices: string[];
  review: boolean;
  reason?: string | null;
  lemma?: string;
  pos?: string;
  audio?: string;
  progress?: { answered: number; max: number };
};
export type Mastery = Record<Modality, number | null>;
export type WordFeedback = {
  type: "word";
  session?: Session;
  duplicate?: boolean;
  // diagnostic: answer is recorded but not revealed
  recorded?: boolean;
  done?: boolean;
  diagnostic?: Diagnostic;
  score?: number;
  confidence?: string;
  // practice / review
  correct?: boolean;
  answer?: number;
  choice?: number | null;
  modality?: Modality;
  lemma?: string;
  meaning?: string;
  pos?: string;
  example?: string;
  form?: string;
  word_audio?: string | null;
  example_audio?: string | null;
  mastery?: Mastery;
  status?: string;
  status_label?: string;
  srs?: { due: string; rating: string } | null;
};
export type WordBrief = { id: string; lemma: string; meaning: string; tier: number; audio?: string | null };
export type DiagnosticResult = {
  estimated_tier: number;
  items: number;
  words: number;
  modality: Partial<Record<Modality, { accuracy: number; n: number }>>;
  tiers: Record<string, { accuracy: number; n: number }>;
  sound_gap: WordBrief[];
  weak: (WordBrief & { failed: Modality[] })[];
  guesses: number;
};
export type Diagnostic = { id: string; status: string; answered: number; max: number; tier: number; result: DiagnosticResult | null; resumed?: boolean };
export type LessonCheck = { check: string; ok: boolean; detail: string; skipped?: boolean };
export type ImportJob = {
  id: string;
  status: "queued" | "processing" | "done" | "failed";
  created_at: string;
  title: string;
  level: number;
  error: string | null;
  info: {
    filename: string;
    duration: number;
    has_script: boolean;
    step: string;
    checks: LessonCheck[];
    clips: { id: string; range: string; lines: number; questions: number; words: string[]; quality: string }[];
    skipped: { part: number; range: string; reason: string }[];
  };
};
export type Lesson = {
  id: string;
  created_at: string;
  engine: "gemini" | "local";
  level: number;
  targets: { id: string; lemma: string; meaning: string }[];
  checks: LessonCheck[];
  heard: boolean;
  cached?: boolean;
  fallback?: string | null;
};
export type GeneratorStatus = {
  engine: "gemini" | "local";
  mode: "auto" | "gemini" | "local";
  gemini_configured: boolean;
  key_source: "env" | "file" | null;
  key_hint: string | null;
  gemini_model: string;
  tts_available: boolean;
  tts_message: string;
};
export type VocabStats = {
  dataset: { name: string; license: string; words: number };
  tested_words: number;
  acquired: number;
  due: number;
  status: { status: string; label: string; count: number }[];
  modality: Record<Modality, { label: string; tested: number; mastery: number | null }>;
  tiers: ({ tier: number; total: number; tested: number; acquired: number } & Record<Modality, number | null>)[];
  estimated_tier: number;
  focus: (WordBrief & { weakest: Modality; weakest_label: string; status: string; status_label: string; mastery: Mastery; next_review_at: string | null })[];
  diagnostic: {
    active: { id: string; answered: number; max: number } | null;
    last: { id: string; completed_at: string; result: DiagnosticResult } | null;
  };
  lessons: Lesson[];
  generator: GeneratorStatus;
  auto_lesson_running: boolean;
};
export const MODALITY_LABEL: Record<Modality, string> = { text: "文字で理解", audio: "音声で認識", context: "会話で理解" };

export type Item = ComprehensionItem | DictationItem | DiscriminationItem | VocabItem | ShadowingItem | WordItem;

export type NextResult =
  | { type: "item"; item: Item; session: Session }
  | { type: "break"; seconds_left: number; session: Session }
  | { type: "done"; session: Session };

export type Token = { text: string; shown: string; status: "correct" | "typo" | "wrong" | "missed"; category: string; typed: string | null };

export type Feedback = {
  type: Item["type"];
  session: Session;
  events?: { event: string; from: number; to: number }[];
  srs?: { due: string; rating: string };
  // comprehension (null: the clip has no questions and is not scored)
  score?: number | null;
  results?: { correct: boolean; given: number | null; answer: number; explain: string }[];
  transcript?: ClipPublic;
  // dictation
  accuracy?: number;
  tokens?: Token[];
  extra?: string[];
  errors?: Record<string, number>;
  error_labels?: Record<string, string>;
  reference?: string;
  moving_on?: boolean;
  // discrimination / vocab
  correct?: boolean;
  answer?: number;
  target?: string;
  distractor?: string;
  category?: string;
  word?: string;
  ja?: string;
  typed?: string;
  rating?: number;
};

export type Summary = {
  session: Session;
  items: number;
  by_kind: Record<string, { count: number; average: number }>;
  errors: { skill: string; label: string; count: number }[];
  new_clips: number;
  events: { event: string; from: number; to: number }[];
  test: null | {
    form: string;
    score: number;
    dictation: number;
    comprehension: number;
    level_estimate: number;
    detail: { levels: Record<string, { dictation: number | null; comprehension: number | null }> };
  };
};

export const SKILL_LABEL: Record<string, string> = {
  weak_forms: "弱形（機能語）",
  contractions: "短縮形",
  linking: "音の連結",
  numbers: "数字",
  endings: "語尾 (-s / -ed)",
  vocabulary: "語彙（内容語）",
  comprehension: "内容理解",
};

export const LEVEL_LABEL: Record<number, string> = { 1: "A2", 2: "B1", 3: "B1+", 4: "B2", 5: "C1" };
