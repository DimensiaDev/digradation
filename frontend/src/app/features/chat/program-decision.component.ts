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

/**
 * Which card the panel is showing.
 *
 * The panel is one decision at a time: `choose` offers both, and picking either
 * action hides the other card so only the chosen path is on screen.
 */
export type ProgramDecisionMode = 'choose' | 'approve' | 'revise';

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
  readonly mode = signal<ProgramDecisionMode>('choose');
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
  private wasSubmitting = false;

  constructor() {
    effect(() => {
      const key = this.blocks()
        .map((block) => block.id)
        .join('|');

      if (key === this.lastBlocksKey) {
        return;
      }
      this.lastBlocksKey = key;
      this.mode.set('choose');
      this.selectedStage.set(this.blocks()[0]?.id ?? null);
      this.notes.set('');
    });

    /**
     * An approval the backend refused leaves the panel on screen, so the
     * revision card has to come back: the methodist still owes the programme a
     * decision, and the only card left was the one that just failed.
     */
    effect(() => {
      const submitting = this.submitting();
      if (this.wasSubmitting && !submitting && this.mode() === 'approve') {
        this.mode.set('choose');
      }
      this.wasSubmitting = submitting;
    });
  }

  startApproval(): void {
    if (this.submitting()) {
      return;
    }
    this.mode.set('approve');
    this.approved.emit();
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
    this.mode.set('choose');
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
