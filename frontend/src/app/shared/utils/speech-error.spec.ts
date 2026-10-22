import { speechErrorNotice } from './speech-error';

describe('speechErrorNotice', () => {
  it('treats silence as an informational notice', () => {
    expect(speechErrorNotice('no-speech')).toEqual({
      type: 'info',
      message: 'Речь не распознана',
      description: 'Попробуйте ещё раз.'
    });
  });

  it('explains a blocked microphone', () => {
    expect(speechErrorNotice('not-allowed')).toMatchObject({
      type: 'error',
      message: 'Разрешите доступ к микрофону',
      description: 'Разрешите микрофон в настройках браузера и попробуйте снова.'
    });
    expect(speechErrorNotice('service-not-allowed').message).toBe(
      'Доступ к микрофону запрещён браузером'
    );
  });

  it('explains a network failure and other engine errors', () => {
    expect(speechErrorNotice('network')).toMatchObject({
      type: 'error',
      message: 'Сервис распознавания речи недоступен',
      description: 'Проверьте подключение или попробуйте Safari.'
    });
    expect(speechErrorNotice('audio-capture').message).toBe('Микрофон не найден');
    expect(speechErrorNotice('language-not-supported').message).toContain('русский');
    expect(speechErrorNotice('unknown')).toEqual({
      type: 'error',
      message: 'Не удалось распознать речь',
      description: undefined
    });
  });
});
