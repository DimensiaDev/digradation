import { Injectable, signal } from '@angular/core';

export interface PendingChatDraft {
  readonly text: string;
  readonly files: readonly File[];
}

@Injectable({
  providedIn: 'root'
})
export class PendingChatDraftService {
  private readonly draft = signal<PendingChatDraft | null>(null);

  set(draft: PendingChatDraft): void {
    this.draft.set(draft);
  }

  peek(): PendingChatDraft | null {
    return this.draft();
  }

  consume(): PendingChatDraft | null {
    const draft = this.draft();
    if (!draft) {
      return null;
    }

    this.draft.set(null);

    return draft;
  }
}
