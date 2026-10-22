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

export type ChatSortKey = 'date' | 'number';
export type SortOrder = 'asc' | 'desc';

export interface ChatListQuery {
  page?: number;
  itemsPerPage?: number;
  sort?: ChatSortKey;
  order?: SortOrder;
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

export type ProgramStatus = ProgramViewStatus | 'in_progress' | 'review' | 'published' | 'archived';

export type ProgramKind = 'professional_development' | 'general_development';

export type ProgramTheme = 'it' | 'analytics' | 'soft-skills' | 'agile' | 'management' | 'security';

export interface ProgramAuthor {
  name: string;
  avatarUrl: string | null;
}

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
  tags: readonly string[];
  status: ProgramStatus;
  theme: ProgramTheme;
  author: ProgramAuthor;
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
  /** Optional fields the catalogue may expose later. */
  kind?: ProgramKind | null;
  type?: string | null;
  direction?: string | null;
  hours?: number | null;
  tags?: readonly string[] | null;
  author?: ProgramAuthor | null;
  description?: string | null;
  goals?: readonly string[] | null;
  theme?: ProgramTheme | null;
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

export interface ProgramBlocksApi {
  kind: ProgramKind | null;
  title: string | null;
  competencies: readonly ProgramCompetenceApi[];
  curriculum: ProgramCurriculumApi | null;
  sources: readonly unknown[];
  summary: ProgramSummaryApi | null;
}

export interface ProgramQuestionApi {
  id: string;
  text: string;
}

export interface ProgramAnswerApi {
  question_id: string;
  text: string;
}

export interface ProgramStateApi {
  status: ProgramViewStatus;
  current_stage: string | null;
  stages: Record<string, string>;
  running: boolean;
  busy: boolean;
  error: string | null;
  updated_at: string | null;
  version: number;
  questions: readonly ProgramQuestionApi[];
  draft: ProgramBlocksApi;
}

export type ProgramAction = 'duplicate' | 'archive' | 'unarchive' | 'delete';

export const PROGRAM_STATUS_LABELS: Record<ProgramStatus, string> = {
  none: 'Нет программы',
  draft: 'Черновик',
  generating: 'Генерируется',
  awaiting_answers: 'Нужны ответы',
  pending_approval: 'На согласовании',
  approved: 'Согласована',
  verified: 'Проверена',
  failed: 'Ошибка',
  in_progress: 'В работе',
  review: 'На проверке',
  published: 'Опубликовано',
  archived: 'Архив'
};
