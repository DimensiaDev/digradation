import { Injectable, signal } from '@angular/core';

export interface SpeechTranscript {
  readonly final: string;
  readonly interim: string;
}

export interface SpeechStartOptions {
  readonly lang?: string;
  readonly onError?: (code: string) => void;
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEventLike {
  readonly error: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function createRecognition(): SpeechRecognitionLike | null {
  if (typeof window === 'undefined') {
    return null;
  }

  const speechWindow = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  const Constructor = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

  return Constructor ? new Constructor() : null;
}

function appendText(base: string, addition: string): string {
  const next = addition.trim();
  if (!next) {
    return base;
  }
  return base ? `${base.trimEnd()} ${next}` : next;
}

@Injectable({
  providedIn: 'root'
})
export class SpeechRecognitionService {
  private readonly recognition = createRecognition();
  private readonly listeningState = signal(false);
  private readonly transcriptState = signal<SpeechTranscript>({ final: '', interim: '' });

  readonly isSupported = this.recognition !== null;
  readonly isListening = this.listeningState.asReadonly();
  readonly transcript = this.transcriptState.asReadonly();

  private finalTranscript = '';
  private errorHandler: ((code: string) => void) | null = null;

  constructor() {
    const recognition = this.recognition;
    if (!recognition) {
      return;
    }

    recognition.lang = 'ru-RU';
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => this.handleResult(event);
    recognition.onerror = (event) => {
      this.listeningState.set(false);
      if (event.error !== 'aborted') {
        this.errorHandler?.(event.error);
      }
    };
    recognition.onend = () => this.listeningState.set(false);
  }

  start(options: SpeechStartOptions = {}): void {
    const recognition = this.recognition;
    if (!recognition || this.listeningState()) {
      return;
    }

    this.finalTranscript = '';
    this.transcriptState.set({ final: '', interim: '' });
    this.errorHandler = options.onError ?? null;
    recognition.lang = options.lang ?? 'ru-RU';
    this.listeningState.set(true);

    try {
      recognition.start();
    } catch {
      this.listeningState.set(false);
    }
  }

  stop(): void {
    if (!this.recognition) {
      return;
    }

    this.listeningState.set(false);

    try {
      this.recognition.stop();
    } catch {
      // The engine may already have stopped between the click and this call.
    }
  }

  private handleResult(event: SpeechRecognitionEventLike): void {
    let interim = '';

    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index];
      const transcript = result[0]?.transcript ?? '';
      if (result.isFinal) {
        this.finalTranscript = appendText(this.finalTranscript, transcript);
      } else {
        interim = appendText(interim, transcript);
      }
    }

    this.transcriptState.set({ final: this.finalTranscript, interim });
  }
}
