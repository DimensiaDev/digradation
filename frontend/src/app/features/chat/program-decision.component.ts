import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  output,
  signal
} from '@angular/core';
import {
  LucideCheck,
  LucideCircleCheck,
  LucideClipboardCheck,
  LucidePencilLine,
  LucideSend
} from '@lucide/angular';
import {
  MAX_REVISION_NOTES_LENGTH,
  ProgramRevisionRequest,
  ProgramStage
} from '../../core/models/models';
import { InputComponent } from '../../shared/components/input/input.component';
import { ProgramDecisionBlock, ProgramRevisionView } from './program-view';

/** What the methodist chose to do with the rendered draft. */
export type ProgramDecisionAction = 'approve' | 'revise';

@Component({
  selector: 'app-program-decision',
  imports: [
    InputComponent,
    LucideCheck,
    LucideCircleCheck,
    LucideClipboardCheck,
    LucidePencilLine,
    LucideSend
  ],
  templateUrl: './program-decision.component.html',
  styleUrl: './program-decision.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramDecisionComponent {
  /** The blocks that already exist and may be asked to be redone. */
  readonly blocks = input.required<readonly ProgramDecisionBlock[]>();
  readonly lastRevision = input<ProgramRevisionView | null>(null);
  readonly submitting = input(false);
  readonly approved = output<void>();
  readonly revised = output<ProgramRevisionRequest>();

  readonly maxNotes = MAX_REVISION_NOTES_LENGTH;
  readonly mode = signal<ProgramDecisionAction>('approve');
  readonly selectedStage = signal<ProgramStage | null>(null);
  readonly notes = signal('');

  readonly selectedLabel = computed(() => {
    const stage = this.selectedStage();
    return this.blocks().find((block) => block.id === stage)?.label ?? null;
  });
  readonly canSubmitRevision = computed(
    () => this.selectedStage() !== null && this.notes().trim().length > 0
  );

  private lastBlocksKey = '';

  constructor() {
    effect(() => {
      const key = this.blocks()
        .map((block) => block.id)
        .join('|');

      if (key === this.lastBlocksKey) {
        return;
      }
      this.lastBlocksKey = key;
      this.mode.set('approve');
      this.selectedStage.set(this.blocks()[0]?.id ?? null);
      this.notes.set('');
    });
  }

  startRevision(): void {
    if (this.blocks().length === 0 || this.submitting()) {
      return;
    }
    if (this.selectedStage() === null) {
      this.selectedStage.set(this.blocks()[0].id);
    }
    this.mode.set('revise');
  }

  cancelRevision(): void {
    if (this.submitting()) {
      return;
    }
    this.mode.set('approve');
    this.notes.set('');
  }

  submitRevision(): void {
    const stage = this.selectedStage();
    if (!stage || !this.canSubmitRevision() || this.submitting()) {
      return;
    }
    this.revised.emit({ stage, notes: this.notes().trim() });
  }

  onNotesKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) {
      return;
    }
    if (!this.canSubmitRevision() || this.submitting()) {
      return;
    }
    event.preventDefault();
    this.submitRevision();
  }
}
