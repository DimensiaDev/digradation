import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { LucideCalendar, LucideDynamicIcon } from '@lucide/angular';
import { PROGRAM_STATUS_LABELS, Program } from '../../../core/models/models';
import { PROGRAM_THEME_ICONS } from '../../utils/program-theme';

function formatDate(timestamp: string): string {
  if (!timestamp) {
    return '—';
  }
  return new Date(timestamp).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric'
  });
}

@Component({
  selector: 'app-program-card',
  imports: [LucideCalendar, LucideDynamicIcon],
  templateUrl: './program-card.component.html',
  styleUrl: './program-card.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramCardComponent {
  readonly program = input.required<Program>();
  readonly open = output<HTMLElement | null>();

  readonly icon = computed(() => PROGRAM_THEME_ICONS[this.program().theme]);
  readonly statusLabel = computed(() => PROGRAM_STATUS_LABELS[this.program().status]);
  readonly formattedDate = computed(() => formatDate(this.program().updatedAt));

  openProgram(event: Event): void {
    this.open.emit(event.currentTarget instanceof HTMLElement ? event.currentTarget : null);
  }
}
