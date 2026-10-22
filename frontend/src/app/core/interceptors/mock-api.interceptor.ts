import {
  HttpErrorResponse,
  HttpEvent,
  HttpInterceptorFn,
  HttpResponse
} from '@angular/common/http';
import { Observable, of, throwError } from 'rxjs';
import { delay, mergeMap } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import {
  AdminUser,
  ChatMessageApi,
  InstanceSettings,
  ProgramApiItem,
  ProgramQuestionApi,
  ProgramStateApi,
  User,
  UserChat,
  UserRole
} from '../models/models';

const MOCK_USERNAME = 'admin';
const MOCK_PASSWORD = 'admin123';

const CURRENT_USER: User = {
  id: 1,
  username: MOCK_USERNAME,
  first_name: 'Админ',
  last_name: 'Демо',
  role: 'superuser'
};

const SESSION_KEY = 'mock-authenticated';

const isLoggedIn = (): boolean => localStorage.getItem(SESSION_KEY) === '1';
const setLoggedIn = (value: boolean): void => localStorage.setItem(SESSION_KEY, value ? '1' : '0');
let nextUserId = 4;

const users: AdminUser[] = [
  {
    id: 1,
    username: 'admin',
    first_name: 'Админ',
    last_name: 'Демо',
    role: 'superuser',
    expires_at: null
  },
  {
    id: 2,
    username: 'ivanov',
    first_name: 'Иван',
    last_name: 'Иванов',
    role: 'user',
    expires_at: '2100-01-01T00:00:00Z'
  },
  {
    id: 3,
    username: 'petrov',
    first_name: 'Пётр',
    last_name: 'Петров',
    role: 'user',
    expires_at: '2000-01-01T00:00:00Z'
  }
];

const settings: InstanceSettings = {
  prompt_extension: '',
  last_update_by: null,
  last_update_at: null
};

const PROGRAMMES: ProgramApiItem[] = [
  {
    session_id: 'mock-programme-1',
    title: 'Python для анализа данных',
    status: 'draft',
    updated_at: '2025-10-12T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 72,
    tags: ['IT', 'Python', 'Аналитика'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'it',
    description: 'Практический курс для специалистов, которые хотят освоить Python.',
    goals: ['Уверенно работать с синтаксисом Python.']
  },
  {
    session_id: 'mock-programme-2',
    title: 'Кибербезопасность для сотрудников',
    status: 'generating',
    updated_at: '2025-10-08T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Информационная безопасность',
    hours: 40,
    tags: ['IT', 'Безопасность'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'security'
  },
  {
    session_id: 'mock-programme-3',
    title: 'Основы машинного обучения',
    status: 'pending_approval',
    updated_at: '2025-10-05T09:00:00Z',
    type: 'Профессиональная переподготовка',
    direction: 'Искусственный интеллект',
    hours: 256,
    tags: ['ML', 'Python', 'Аналитика'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'analytics'
  },
  {
    session_id: 'mock-programme-4',
    title: 'Agile-трансформация команд',
    status: 'approved',
    updated_at: '2025-09-28T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Управление и процессы',
    hours: 16,
    tags: ['Agile', 'Soft skills'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'agile'
  },
  {
    session_id: 'mock-programme-5',
    title: 'Управление проектами',
    status: 'draft',
    updated_at: '2025-09-24T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'Управление и процессы',
    hours: 36,
    tags: ['Менеджмент', 'Agile'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'management'
  },
  {
    session_id: 'mock-programme-6',
    title: 'Soft skills для руководителей',
    status: 'awaiting_answers',
    updated_at: '2025-09-19T09:00:00Z',
    type: 'Корпоративное обучение',
    direction: 'Развитие руководителей',
    hours: 24,
    tags: ['Soft skills', 'Коммуникация'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'soft-skills'
  },
  {
    session_id: 'mock-programme-7',
    title: 'Веб-разработка: полный цикл',
    status: 'approved',
    updated_at: '2025-08-30T09:00:00Z',
    type: 'Профессиональная переподготовка',
    direction: 'IT и аналитика',
    hours: 256,
    tags: ['IT', 'JavaScript'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'it'
  },
  {
    session_id: 'mock-programme-8',
    title: 'Продуктовая аналитика',
    status: 'verified',
    updated_at: '2025-08-22T09:00:00Z',
    type: 'Повышение квалификации',
    direction: 'IT и аналитика',
    hours: 48,
    tags: ['Аналитика', 'Data'],
    author: { name: 'Админ Демо', avatarUrl: null },
    theme: 'analytics'
  }
];

interface ProgrammeRuntime {
  status: ProgramApiItem['status'];
  questions: ProgramQuestionApi[];
}

const programmeRuntime = new Map<string, ProgrammeRuntime>();
programmeRuntime.set('mock-programme-6', {
  status: 'awaiting_answers',
  questions: [
    { id: 'mock-question-1', text: 'Для какой аудитории предназначена программа?' },
    { id: 'mock-question-2', text: 'Сколько часов нужно отвести на практику?' }
  ]
});

const programmeSummary = (programme: ProgramApiItem) => ({
  goal: programme.description ?? `Сформировать практические навыки по теме «${programme.title}».`,
  tasks: programme.goals ?? [],
  target_audience: 'Специалисты профильных направлений',
  form: 'Очно-заочная, с применением дистанционных технологий'
});

const programmeState = (programme: ProgramApiItem): ProgramStateApi => {
  const runtime = programmeRuntime.get(programme.session_id);
  return {
    status: runtime?.status ?? programme.status,
    current_stage: null,
    stages: {},
    running: false,
    busy: false,
    error: null,
    updated_at: programme.updated_at,
    version: 1,
    questions: runtime?.questions ?? [],
    draft: {
      kind: null,
      title: programme.title,
      competencies: [],
      curriculum: null,
      sources: [],
      summary: programmeSummary(programme)
    }
  };
};

const emptyProgramState = (): ProgramStateApi => ({
  status: 'none',
  current_stage: null,
  stages: {},
  running: false,
  busy: false,
  error: null,
  updated_at: null,
  version: 0,
  questions: [],
  draft: {
    kind: null,
    title: null,
    competencies: [],
    curriculum: null,
    sources: [],
    summary: null
  }
});

const CHAT_NAMES = [
  'Заметки по проекту',
  'Разбор документа',
  'Вопросы по ТЗ',
  'Сводка по разделу',
  'План работы',
  'Черновик письма',
  'Конспект встречи'
];

const CHAT_REQUESTS = [
  'Собери основные тезисы из приложенного документа',
  'Составь краткое резюме по второй главе',
  'Что здесь нужно уточнить перед началом работы?',
  'Сделай список задач со сроками',
  'Напиши черновик письма по этому поводу',
  'Разбери документ и выдели спорные места',
  'Сделай конспект встречи по этому материалу'
];

const REPLY = (index: number): string =>
  `Готов ответ по теме «${CHAT_NAMES[index].toLowerCase()}»: ` +
  `основных пунктов — 3, объём — ${(index + 1) * 4} страницы.`;

const chats: UserChat[] = CHAT_NAMES.map((name, index) => {
  const timestamp = new Date(Date.now() - (index + 1) * 36 * 60 * 60 * 1000).toISOString();
  const lastMessage: ChatMessageApi = {
    id: (index + 1) * 10,
    role: 'assistant',
    content: REPLY(index),
    attachments: [],
    timestamp
  };
  return {
    session_id: `mock${index}-dead-beef-cafe-00000000000${index}`,
    name,
    last_message: lastMessage
  };
});

const sessionMessages = (session: UserChat): ChatMessageApi[] => [
  {
    id: session.last_message.id - 1,
    role: 'user',
    content: CHAT_REQUESTS[Number(session.session_id.slice(-1))],
    attachments: [],
    timestamp: new Date(new Date(session.last_message.timestamp).getTime() - 60_000).toISOString()
  },
  session.last_message
];

function programmeMessages(programme: ProgramApiItem, index: number): ChatMessageApi[] {
  const updatedAt = programme.updated_at ? new Date(programme.updated_at) : new Date();
  return [
    {
      id: 1000 + index * 2,
      role: 'user',
      content: 'Собери образовательную программу по этому направлению',
      attachments: [],
      timestamp: new Date(updatedAt.getTime() - 60_000).toISOString()
    },
    {
      id: 1001 + index * 2,
      role: 'assistant',
      content: `Черновик программы «${programme.title}» готов. Проверьте карточку и подтвердите программу в чате.`,
      attachments: [],
      timestamp: updatedAt.toISOString()
    }
  ];
}

const chatMessages = new Map<string, ChatMessageApi[]>(
  chats.map((chat) => [chat.session_id, sessionMessages(chat)])
);
const programmeChatMessages = new Map<string, ChatMessageApi[]>(
  PROGRAMMES.map((programme, index) => [programme.session_id, programmeMessages(programme, index)])
);

const messagesFor = (sessionId: string): ChatMessageApi[] | undefined =>
  chatMessages.get(sessionId) ?? programmeChatMessages.get(sessionId);

const nameFor = (text: string): string => {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (!clean) {
    return 'Новый чат';
  }
  return clean.length > 48 ? `${clean.slice(0, 48)}…` : clean;
};

let nextChatNumber = 1;
let nextMessageId = 2000;

type MockResponse = Observable<HttpEvent<unknown>>;

const fail = (status: number, message: string): MockResponse =>
  throwError(
    () =>
      new HttpErrorResponse({
        status,
        statusText: message,
        error: { detail: message }
      })
  );

const ok = <T>(body: T): MockResponse =>
  of(new HttpResponse({ status: 200, body })) as MockResponse;

export const mockApiInterceptor: HttpInterceptorFn = (req, next) => {
  if (!environment.useMocks) {
    return next(req);
  }

  const respond = (): MockResponse => {
    const url = req.url.replace(environment.apiUrl, '');
    const [rawPath, query = ''] = url.split('?');
    const path = rawPath.length > 1 && rawPath.endsWith('/') ? rawPath.slice(0, -1) : rawPath;
    const params = new URLSearchParams(query);
    const body = req.body as Record<string, unknown> | null;

    if (path === '/auth/login' && req.method === 'POST') {
      if (body?.['username'] === MOCK_USERNAME && body?.['password'] === MOCK_PASSWORD) {
        setLoggedIn(true);
        return ok({ message: 'ok' });
      }
      return fail(401, 'Неверное имя пользователя или пароль');
    }

    if (path === '/auth/logout' && req.method === 'POST') {
      setLoggedIn(false);
      return ok({ message: 'ok' });
    }

    if (!isLoggedIn()) {
      return fail(401, 'Не авторизован');
    }

    if (path === '/users/me') {
      return ok(CURRENT_USER);
    }

    if (path === '/users/me/avatar') {
      return fail(404, 'No avatar');
    }

    if (path === '/chats/programmes' && req.method === 'GET') {
      const page = Number(params.get('page') ?? 0);
      const itemsPerPage = Number(params.get('items_per_page') ?? 20);
      const items = PROGRAMMES.slice(page * itemsPerPage, (page + 1) * itemsPerPage).map(
        (programme) => ({ ...programme })
      );
      return ok({ items, total: PROGRAMMES.length, page, items_per_page: itemsPerPage });
    }

    if (path === '/chats' && req.method === 'GET') {
      const page = Number(params.get('page') ?? 0);
      const itemsPerPage = Number(params.get('items_per_page') ?? 10);
      const sort = params.get('sort') ?? 'date';
      const order = params.get('order') ?? 'desc';
      const sorted = [...chats].sort((a, b) => {
        const factor = order === 'asc' ? 1 : -1;
        if (sort === 'number') {
          return factor * (chats.indexOf(a) - chats.indexOf(b));
        }
        return (
          factor *
          (new Date(a.last_message.timestamp).getTime() -
            new Date(b.last_message.timestamp).getTime())
        );
      });
      const items = sorted.slice(page * itemsPerPage, (page + 1) * itemsPerPage).map((chat) => ({
        ...chat,
        last_message: { ...chat.last_message }
      }));
      return ok({ items, total: chats.length, page, items_per_page: itemsPerPage });
    }

    if (path === '/chats' && req.method === 'POST') {
      const sessionId = `mock-chat-${nextChatNumber++}`;
      chatMessages.set(sessionId, []);
      return ok({ session_id: sessionId });
    }

    const programMatch = path.match(/^\/chats\/([^/]+)\/program$/);
    if (programMatch) {
      const programme = PROGRAMMES.find((candidate) => candidate.session_id === programMatch[1]);
      if (programme) {
        return ok(programmeState(programme));
      }
      if (chatMessages.has(programMatch[1])) {
        return ok(emptyProgramState());
      }
      return fail(403, 'Программа не найдена');
    }

    const resultMatch = path.match(/^\/chats\/([^/]+)\/result$/);
    if (resultMatch && req.method === 'GET') {
      const messages = messagesFor(resultMatch[1]);
      if (!messages) {
        return fail(403, 'Чат не найден');
      }
      const lastMessage = messages[messages.length - 1];
      return ok({
        running: false,
        result: lastMessage
          ? {
              type: 'success',
              content: lastMessage.content,
              timestamp: lastMessage.timestamp,
              attachment_id: null,
              update_name: null
            }
          : null
      });
    }

    const sendMatch = path.match(/^\/chats\/([^/]+)$/);
    if (sendMatch && req.method === 'POST') {
      const sessionId = sendMatch[1];
      const messages = messagesFor(sessionId);
      if (!messages) {
        return fail(403, 'Чат не найден');
      }

      const rawAnswers = body?.['answers'];
      const answers = Array.isArray(rawAnswers) ? rawAnswers : [];
      const content = String(body?.['content'] ?? '').trim();
      const text = content || (answers.length > 0 ? 'Ответы на уточняющие вопросы' : '');
      if (!text) {
        return fail(422, 'Сообщение не может быть пустым');
      }

      messages.push({
        id: nextMessageId++,
        role: 'user',
        content: text,
        attachments: [],
        timestamp: new Date().toISOString()
      });

      const runtime = programmeRuntime.get(sessionId);
      const programme = PROGRAMMES.find((candidate) => candidate.session_id === sessionId);
      if (runtime) {
        runtime.questions = [];
        runtime.status = 'pending_approval';
        if (programme) {
          programme.status = 'pending_approval';
        }
      }

      const reply: ChatMessageApi = {
        id: nextMessageId++,
        role: 'assistant',
        content: runtime
          ? `Ответы получены. Программа «${programme?.title ?? ''}» переходит на проверку.`
          : `Принято: «${text.length > 80 ? `${text.slice(0, 80)}…` : text}». Черновик ответа готов.`,
        attachments: [],
        timestamp: new Date(Date.now() + 500).toISOString()
      };
      messages.push(reply);

      const existing = chats.find((candidate) => candidate.session_id === sessionId);
      if (existing) {
        existing.last_message = reply;
      } else {
        chats.push({
          session_id: sessionId,
          name: programme?.title ?? nameFor(text),
          last_message: reply
        });
      }

      return ok({ message: 'ok' });
    }

    const historyMatch = path.match(/^\/chats\/([^/]+)$/);
    if (historyMatch && req.method === 'GET') {
      const messages = messagesFor(historyMatch[1]);
      if (!messages) {
        return fail(403, 'Чат не найден');
      }
      return ok(messages.map((message) => ({ ...message, attachments: [...message.attachments] })));
    }

    if (path === '/admin/users' && req.method === 'GET') {
      return ok(users.map((user) => ({ ...user })));
    }

    if (path === '/admin/users' && req.method === 'POST') {
      const created: AdminUser = {
        id: nextUserId++,
        username: String(body?.['username'] ?? ''),
        first_name: (body?.['first_name'] as string | null) ?? null,
        last_name: (body?.['last_name'] as string | null) ?? null,
        role: (body?.['role'] as UserRole | undefined) ?? 'user',
        expires_at: (body?.['expires_at'] as string | null) ?? null
      };
      users.push(created);
      return ok({ ...created });
    }

    const userMatch = path.match(/^\/admin\/users\/(\d+)(\/set-password)?$/);
    if (userMatch) {
      const user = users.find((candidate) => candidate.id === Number(userMatch[1]));
      if (!user) {
        return fail(404, 'Пользователь не найден');
      }
      if (userMatch[2]) {
        return ok({ message: 'ok' });
      }
      if (req.method === 'PATCH') {
        Object.assign(user, body);
        return ok({ ...user });
      }
      if (req.method === 'DELETE') {
        users.splice(users.indexOf(user), 1);
        return ok({ ...user });
      }
    }

    if (path === '/admin/settings') {
      if (req.method === 'PATCH') {
        const patch = body as Partial<InstanceSettings> | null;
        if (patch?.['prompt_extension'] !== undefined) {
          settings.prompt_extension = patch['prompt_extension'];
        }
        settings.last_update_by = CURRENT_USER.id;
        settings.last_update_at = new Date().toISOString();
      }
      return ok({ ...settings });
    }

    return fail(404, `Мок не знает адрес ${req.method} ${path}`);
  };

  return of(null).pipe(mergeMap(respond), delay(150));
};
