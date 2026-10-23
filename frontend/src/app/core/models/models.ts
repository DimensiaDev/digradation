export interface ChatCreated {
  session_id: string;
}

export interface ChatAttachmentApi {
  id: number;
  name: string;
}

export interface ChatUploadParams {
  attachment_id: number;
  params: { url: string; fields: Record<string, string> };
}

export type ChatAttachmentStatus = 'uploading' | 'processing' | 'completed' | 'error';

/** Same states as `ChatAttachmentStatus`, plus the case where the status key has expired. */
export type ChatSessionAttachmentStatus = ChatAttachmentStatus | 'unknown';

/** A file currently on a chat session, whether or not a message references it yet. */
export interface ChatSessionAttachment {
  id: number;
  filename: string;
  status: ChatSessionAttachmentStatus;
  ready: boolean;
  chat_message_id: number | null;
  timestamp: string;
}

export interface ChatAttachmentUrl {
  attachment_url: string;
  filename: string;
}

export interface ChatMessageApi {
  id: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  attachments: ChatAttachmentApi[];
  timestamp: string;
}

export interface UserChat {
  session_id: string;
  last_message: ChatMessageApi;
  name: string;
}

/** Envelope every paginated list endpoint returns: the page itself plus the total. */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  items_per_page: number;
}

export interface ChatResultContent {
  type: 'error' | 'success';
  content: string;
  timestamp: string;
  attachment_id: number | null;
  update_name: string | null;
}

export interface ChatResult {
  running: boolean;
  result: ChatResultContent | null;
}

export const USER_ROLES = ['user', 'admin', 'superuser'] as const;

export type UserRole = (typeof USER_ROLES)[number];

export const ROLE_LABELS: Record<UserRole, string> = {
  user: 'Пользователь',
  admin: 'Администратор',
  superuser: 'Суперпользователь'
};

export const isAdminRole = (role: UserRole | undefined): boolean =>
  role === 'admin' || role === 'superuser';

export interface User {
  id: number;
  username: string;
  first_name: string | null;
  last_name: string | null;
  role: UserRole;
}

export interface S3AvatarUrl {
  avatar_url: string | null;
}

export interface AdminUser {
  id: number;
  username: string;
  first_name: string | null;
  last_name: string | null;
  role: UserRole;
  expires_at: string | null;
}

export interface AdminUserPayload {
  username: string;
  password?: string;
  first_name: string | null;
  last_name: string | null;
  expires_at: string | null;
  role?: UserRole;
}

export interface MessageResponse {
  message: string;
}

export interface InstanceSettings {
  prompt_extension: string;
  last_update_by: number | null;
  last_update_at: string | null;
}

export interface InstanceSettingsUpdate {
  prompt_extension?: string;
}

export type UploadState = 'idle' | 'uploading' | 'completed';

export type UploadItemStatus = 'queued' | 'uploading' | 'done' | 'error';

export interface UploadItem {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly extension: string;
  readonly file: File;
  readonly status: UploadItemStatus;
  readonly uploaded: number;
  /** Server-side id, assigned once the backend has registered the file. Needed to delete it. */
  readonly attachmentId?: number;
}

export interface UploadSpeedSample {
  readonly time: number;
  readonly bytes: number;
}

export const MAX_CONCURRENT_UPLOADS = 3;

export const MAX_FILE_SIZE = 30 * 1024 * 1024;

export const MAX_MESSAGE_LENGTH = 5000;

export const MAX_ANSWER_LENGTH = 4000;

/** The backend caps the notes of a revision request at 2000 characters. */
export const MAX_REVISION_NOTES_LENGTH = 2000;

export const VISIBLE_FILES_LIMIT = 5;

export type ProgramViewStatus =
  | 'none'
  | 'draft'
  | 'generating'
  | 'awaiting_answers'
  | 'pending_approval'
  | 'approved'
  | 'verified'
  | 'failed';

export type ProgramStatus = ProgramViewStatus;

export type ProgramKind = 'professional_development' | 'general_development';

export type ProgramMode = 'new' | 'actualization' | 'adaptation';

export type ProgramTheme = 'it' | 'analytics' | 'soft-skills' | 'agile' | 'management' | 'security';

export interface ProgramModule {
  id: string;
  title: string;
  summary: string;
  hours: number;
}

export interface Program {
  id: string;
  title: string;
  type: string;
  direction: string;
  hours: number;
  status: ProgramStatus;
  theme: ProgramTheme;
  updatedAt: string;
  description: string;
  goals: readonly string[];
  /** Real curriculum modules, when the programme detail has been loaded. */
  modules?: readonly ProgramModule[];
  /** Real competency statements, when the programme detail has been loaded. */
  competencyStatements?: readonly string[];
  /** Live hint from the programme endpoint: a worker is working on the run. */
  busy?: boolean;
  error?: string | null;
}

export interface ProgramApiItem {
  session_id: string;
  title: string;
  status: ProgramViewStatus;
  updated_at: string | null;
}

export interface ProgramPageApi {
  items: ProgramApiItem[];
  total: number;
  page: number;
  items_per_page: number;
}

export interface ProgramHoursApi {
  lecture?: number;
  practice?: number;
  lab?: number;
  self_study?: number;
}

export interface ProgramTopicApi {
  id: string;
  title: string;
  hours: ProgramHoursApi;
}

export interface ProgramModuleApi {
  id: string;
  title: string;
  topics: readonly ProgramTopicApi[];
}

export interface ProgramCurriculumApi {
  modules: readonly ProgramModuleApi[];
  /** Hours of the final attestation, counted into the programme's volume. */
  attestation_hours?: number;
  /** Hours delivered remotely; absent when the programme is fully in person. */
  distance_hours?: number | null;
}

export interface ProgramCompetenceApi {
  id: string;
  statement: string;
  code?: string | null;
}

export interface ProgramSummaryApi {
  goal: string;
  tasks: readonly string[];
  target_audience: string;
  admission_requirements?: string | null;
  annotation?: string | null;
  relevance?: string | null;
  form?: string | null;
  issued_document?: string | null;
  focus?: string | null;
  level?: string | null;
}

export type ProgramSourceKind =
  'normative_act' | 'professional_standard' | 'literature' | 'internet_resource' | 'reference';

export interface ProgramSourceApi {
  id: string;
  kind: ProgramSourceKind;
  title: string;
  identifier?: string | null;
  url?: string | null;
  primary?: boolean | null;
  confirmed: boolean;
  trust?: string | null;
  origin?: string | null;
}

export interface ProgramCheckApi {
  name: string;
  ok: boolean;
  problems: readonly string[];
  details: Record<string, unknown>;
}

export interface ProgramReportApi {
  ok: boolean;
  checks: readonly ProgramCheckApi[];
  notes?: readonly string[];
}

export interface ProgramExportsApi {
  document_attachment_id: number | null;
  slides_attachment_id: number | null;
  json_attachment_id?: number | null;
  lms_attachment_id?: number | null;
}

export interface ProgramBlocksApi {
  kind: ProgramKind | null;
  mode?: ProgramMode | null;
  title: string | null;
  competencies: readonly ProgramCompetenceApi[];
  curriculum: ProgramCurriculumApi | null;
  sources: readonly ProgramSourceApi[];
  summary: ProgramSummaryApi | null;
  assessment?: Record<string, unknown> | null;
  materials?: Record<string, unknown> | null;
}

export type ProgramStage =
  'competences' | 'hours' | 'assessment' | 'materials' | 'sources' | 'summary';

export interface ProgramQuestionApi {
  id: string;
  text: string;
}

export interface ProgramAnswerApi {
  question_id: string;
  text: string;
}

/** A change the methodist asked for in one part of the programme. */
export interface ProgramRevisionApi {
  stage: ProgramStage;
  notes: string;
  requested_by: string | null;
  requested_at: string | null;
}

/** The body of `POST /chats/{session_id}/program/revision`. */
export interface ProgramRevisionRequest {
  stage: ProgramStage;
  notes: string;
}

export interface ProgramStateApi {
  status: ProgramViewStatus;
  mode?: ProgramMode;
  current_stage: string | null;
  stages: Record<string, string>;
  running: boolean;
  busy: boolean;
  error: string | null;
  expectations?: string | null;
  sample_issues?: readonly string[];
  updated_at: string | null;
  version: number;
  questions: readonly ProgramQuestionApi[];
  draft: ProgramBlocksApi;
  report: ProgramReportApi | null;
  exports: ProgramExportsApi | null;
  /** The change being carried out right now, while one is pending. */
  revision: ProgramRevisionApi | null;
  /** The last change that was carried out, so the methodist can see who asked. */
  last_revision: ProgramRevisionApi | null;
  approved_by: string | null;
  approved_at: string | null;
}

export const PROGRAM_STATUS_LABELS: Record<ProgramStatus, string> = {
  none: 'Нет программы',
  draft: 'Черновик',
  generating: 'Генерируется',
  awaiting_answers: 'Нужны ответы',
  pending_approval: 'На согласовании',
  approved: 'Согласована',
  verified: 'Проверена',
  failed: 'Ошибка'
};
