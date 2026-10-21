import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { LucideFolderOpen, LucidePlus } from '@lucide/angular';
import { Program, ProgramFilter } from '../../core/models/models';
import { NotificationService } from '../../core/services/notification.service';
import { ProgramsService } from '../../core/services/programs.service';
import {
  ProgramAction,
  ProgramCardComponent
} from '../../shared/components/program-card/program-card.component';

interface ProgramFilterOption {
  value: ProgramFilter;
  label: string;
}

@Component({
  selector: 'app-programs-page',
  imports: [ProgramCardComponent, RouterLink, LucideFolderOpen, LucidePlus],
  templateUrl: './programs-page.component.html',
  styleUrl: './programs-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramsPage {
  private readonly programsService = inject(ProgramsService);
  private readonly notifications = inject(NotificationService);
  private readonly router = inject(Router);

  readonly filters: readonly ProgramFilterOption[] = [
    { value: 'all', label: 'Все' },
    { value: 'in_progress', label: 'В работе' },
    { value: 'review', label: 'На проверке' },
    { value: 'published', label: 'Опубликовано' },
    { value: 'archived', label: 'Архив' }
  ];

  readonly filter = signal<ProgramFilter>('all');

  readonly programs = computed(() => {
    const status = this.filter();
    const programs = this.programsService.programs();
    return status === 'all' ? programs : programs.filter((program) => program.status === status);
  });

  selectFilter(filter: ProgramFilter): void {
    this.filter.set(filter);
  }

  onAction(program: Program, action: ProgramAction): void {
    switch (action) {
      case 'edit':
        this.router.navigate(['/programs/new']);
        return;
      case 'duplicate':
        this.programsService.duplicate(program.id);
        this.filter.set('all');
        this.notifications.success('Программа продублирована', {
          description: 'Копия сохранена как черновик.'
        });
        return;
      case 'archive':
        this.programsService.archive(program.id);
        this.notifications.info('Программа архивирована');
        return;
      case 'delete':
        this.programsService.remove(program.id);
        this.notifications.info('Программа удалена');
        return;
    }
  }
}
