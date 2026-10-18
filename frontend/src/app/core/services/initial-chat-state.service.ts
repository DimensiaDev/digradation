import { Injectable, signal } from '@angular/core';

export interface InitialChatMessage {
  text: string;
  files: File[];
}

interface PendingInitialChat {
  sessionId: string;
  message: InitialChatMessage;
}

@Injectable({
  providedIn: 'root'
})
export class InitialChatStateService {
  private readonly pending = signal<PendingInitialChat | null>(null);

  set(sessionId: string, message: InitialChatMessage): void {
    this.pending.set({ sessionId, message });
  }

  consume(sessionId: string): InitialChatMessage | null {
    const pending = this.pending();
    if (!pending) {
      return null;
    }

    this.pending.set(null);

    return pending.sessionId === sessionId ? pending.message : null;
  }
}
