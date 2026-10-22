import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild
} from '@angular/core';
import {
  LucideBookOpen,
  LucideCheck,
  LucideClipboardCheck,
  LucideClock,
  LucideLaptop,
  LucideLayers,
  LucideLayoutTemplate,
  LucideRefreshCw,
  LucideTags,
  LucideTarget,
  LucideTriangleAlert
} from '@lucide/angular';
import { ProgramStateApi } from '../../core/models/models';
import {
  ProgramPreviewSection,
  programBadge,
  programCompetencies,
  programFormLabel,
  programGoal,
  programHours,
  programHoursLabel,
  programKindLabel,
  programModules,
  programReport,
  programSources,
  programTopicCountLabel
} from './program-view';

const FOCUS_HIGHLIGHT_MS = 1_400;

@Component({
  selector: 'app-program-preview',
  imports: [
    LucideBookOpen,
    LucideCheck,
    LucideClipboardCheck,
    LucideClock,
    LucideLaptop,
    LucideLayers,
    LucideLayoutTemplate,
    LucideRefreshCw,
    LucideTags,
    LucideTarget,
    LucideTriangleAlert
  ],
  templateUrl: './program-preview.component.html',
  styleUrl: './program-preview.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramPreviewComponent {
  readonly state = input<ProgramStateApi | null>(null);
  readonly loading = input(false);
  readonly error = input<string | null>(null);
  readonly reload = output<void>();

  readonly badge = computed(() => programBadge(this.state()));
  readonly title = computed(() => this.state()?.draft.title?.trim() || null);
  readonly kind = computed(() => programKindLabel(this.state()));
  readonly hours = computed(() => programHours(this.state()?.draft.curriculum));
  readonly hoursLabel = computed(() => programHoursLabel(this.hours()));
  readonly formLabel = computed(() => programFormLabel(this.state()));
  readonly goal = computed(() => programGoal(this.state()));
  readonly competencies = computed(() => programCompetencies(this.state()));
  readonly modules = computed(() => programModules(this.state()?.draft.curriculum));
  readonly sources = computed(() => programSources(this.state()));
  readonly report = computed(() => programReport(this.state()));
  readonly hasContent = computed(
    () =>
      Boolean(this.title()) ||
      Boolean(this.goal()) ||
      this.competencies().length > 0 ||
      this.modules().length > 0 ||
      this.sources().length > 0 ||
      Boolean(this.report()) ||
      Boolean(this.state()?.draft.summary)
  );
  readonly justUpdated = signal(false);
  readonly focused = signal<ProgramPreviewSection | null>(null);

  private readonly scroll = viewChild<ElementRef<HTMLElement>>('scroll');
  private lastVersion: number | null = null;
  private updateTimer: ReturnType<typeof setTimeout> | null = null;
  private focusTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      const version = this.state()?.version ?? null;

      if (version === null) {
        this.lastVersion = null;
        return;
      }

      if (this.lastVersion !== null && version !== this.lastVersion) {
        this.markUpdated();
      }
      this.lastVersion = version;
    });

    inject(DestroyRef).onDestroy(() => {
      this.clearUpdateTimer();
      this.clearFocusTimer();
    });
  }

  formatHours(hours: number): string {
    return programHoursLabel(hours);
  }

  topicCountLabel(count: number): string {
    return programTopicCountLabel(count);
  }

  focusSection(section: ProgramPreviewSection): void {
    const target = this.scroll()?.nativeElement.querySelector<HTMLElement>(
      `[data-section="${section}"]`
    );

    this.focused.set(section);
    this.clearFocusTimer();
    this.focusTimer = setTimeout(() => this.focused.set(null), FOCUS_HIGHLIGHT_MS);

    if (typeof target?.scrollIntoView === 'function') {
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    }
  }

  private markUpdated(): void {
    this.justUpdated.set(true);
    this.clearUpdateTimer();
    this.updateTimer = setTimeout(() => this.justUpdated.set(false), FOCUS_HIGHLIGHT_MS);
  }

  private clearUpdateTimer(): void {
    if (this.updateTimer !== null) {
      clearTimeout(this.updateTimer);
      this.updateTimer = null;
    }
  }

  private clearFocusTimer(): void {
    if (this.focusTimer !== null) {
      clearTimeout(this.focusTimer);
      this.focusTimer = null;
    }
  }
}
