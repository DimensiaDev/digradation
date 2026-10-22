export interface SpeechErrorNotice {
  readonly type: 'info' | 'error';
  readonly message: string;
  readonly description?: string;
}

const MESSAGES: Readonly<Record<string, string>> = {
  'not-allowed': 'Разрешите доступ к микрофону',
  'service-not-allowed': 'Доступ к микрофону запрещён браузером',
  'audio-capture': 'Микрофон не найден',
  network: 'Сервис распознавания речи недоступен',
  'language-not-supported': 'Сервис распознавания не поддерживает русский язык'
};

export function speechErrorNotice(code: string): SpeechErrorNotice {
  if (code === 'no-speech') {
    return {
      type: 'info',
      message: 'Речь не распознана',
      description: 'Попробуйте ещё раз.'
    };
  }

  const microphoneBlocked = code === 'not-allowed' || code === 'service-not-allowed';
  let description: string | undefined;

  if (microphoneBlocked) {
    description = 'Разрешите микрофон в настройках браузера и попробуйте снова.';
  } else if (code === 'network') {
    description = 'Проверьте подключение или попробуйте Safari.';
  }

  return {
    type: 'error',
    message: MESSAGES[code] ?? 'Не удалось распознать речь',
    description
  };
}
