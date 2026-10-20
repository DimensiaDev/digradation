import { TestBed } from '@angular/core/testing';

import { SpeechRecognitionService } from './speech-recognition.service';

interface FakeResult {
  readonly transcript: string;
  readonly isFinal: boolean;
}

class FakeSpeechRecognition {
  static last: FakeSpeechRecognition | null = null;

  lang = '';
  continuous = false;
  interimResults = false;
  startCount = 0;
  stopCount = 0;
  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onend: (() => void) | null = null;

  constructor() {
    FakeSpeechRecognition.last = this;
  }

  start(): void {
    this.startCount++;
  }

  stop(): void {
    this.stopCount++;
  }

  emitResult(results: readonly FakeResult[], resultIndex = 0): void {
    this.onresult?.({
      resultIndex,
      results: results.map(({ transcript, isFinal }) => ({
        isFinal,
        length: 1,
        0: { transcript }
      }))
    });
  }

  fail(error: string): void {
    this.onerror?.({ error });
  }
}

interface SpeechWindow {
  SpeechRecognition?: unknown;
  webkitSpeechRecognition?: unknown;
}

const speechWindow = window as unknown as SpeechWindow;

describe('SpeechRecognitionService', () => {
  const createService = (): SpeechRecognitionService => TestBed.inject(SpeechRecognitionService);

  beforeEach(() => {
    FakeSpeechRecognition.last = null;
    speechWindow.SpeechRecognition = FakeSpeechRecognition;
    speechWindow.webkitSpeechRecognition = undefined;
  });

  afterEach(() => {
    delete speechWindow.SpeechRecognition;
    delete speechWindow.webkitSpeechRecognition;
  });

  it('reports when the browser has no speech recognition', () => {
    delete speechWindow.SpeechRecognition;
    const service = createService();

    expect(service.isSupported).toBe(false);
    service.start();

    expect(service.isListening()).toBe(false);
  });

  it('configures continuous Russian recognition with interim results', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    expect(service.isSupported).toBe(true);
    expect(recognition.lang).toBe('ru-RU');
    expect(recognition.continuous).toBe(true);
    expect(recognition.interimResults).toBe(true);
  });

  it('starts listening and publishes interim and final transcripts', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    service.start();

    expect(recognition.startCount).toBe(1);
    expect(service.isListening()).toBe(true);

    recognition.emitResult([{ transcript: 'Нужна ', isFinal: false }]);
    expect(service.transcript()).toEqual({ final: '', interim: 'Нужна' });

    recognition.emitResult([{ transcript: 'Нужна ', isFinal: true }]);
    expect(service.transcript()).toEqual({ final: 'Нужна', interim: '' });

    recognition.emitResult(
      [
        { transcript: 'Нужна ', isFinal: true },
        { transcript: 'программа', isFinal: false }
      ],
      1
    );
    expect(service.transcript()).toEqual({ final: 'Нужна', interim: 'программа' });

    recognition.emitResult(
      [
        { transcript: 'Нужна ', isFinal: true },
        { transcript: 'программа', isFinal: true }
      ],
      1
    );
    expect(service.transcript()).toEqual({ final: 'Нужна программа', interim: '' });
  });

  it('ignores a second start while the session is running', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    service.start();
    service.start();

    expect(recognition.startCount).toBe(1);
  });

  it('stops listening on request', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    service.start();
    service.stop();

    expect(recognition.stopCount).toBe(1);
    expect(service.isListening()).toBe(false);
  });

  it('tolerates stopping an engine that already ended', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    service.start();
    recognition.stop = () => {
      throw new Error('already ended');
    };

    expect(() => service.stop()).not.toThrow();
    expect(service.isListening()).toBe(false);
  });

  it('reports a recognition error and stops listening', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;
    const onError = vi.fn();

    service.start({ onError });
    recognition.fail('not-allowed');

    expect(service.isListening()).toBe(false);
    expect(onError).toHaveBeenCalledWith('not-allowed');
  });

  it('does not report an aborted session as an error', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;
    const onError = vi.fn();

    service.start({ onError });
    recognition.fail('aborted');

    expect(service.isListening()).toBe(false);
    expect(onError).not.toHaveBeenCalled();
  });

  it('stops listening when the recognition session ends', () => {
    const service = createService();
    const recognition = FakeSpeechRecognition.last as FakeSpeechRecognition;

    service.start();
    recognition.onend?.();

    expect(service.isListening()).toBe(false);
  });
});
