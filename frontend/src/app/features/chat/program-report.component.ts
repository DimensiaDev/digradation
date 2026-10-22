import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { LucideCheck, LucideTriangleAlert } from '@lucide/angular';
import { ProgramCheckView, ProgramReportView } from './program-view';

@Component({
  selector: 'app-program-report',
  imports: [LucideCheck, LucideTriangleAlert],
  templateUrl: './program-report.component.html',
  styleUrl: './program-report.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramReportComponent {
  readonly report = input<ProgramReportView | null>(null);

  readonly progress = computed(() => {
    const report = this.report();
    return report && report.checks.length
      ? Math.round((report.passed / report.checks.length) * 100)
      : 0;
  });

  readonly groups = computed(() => {
    const report = this.report();
    if (!report) {
      return [];
    }

    const groups: readonly {
      readonly id: 'problems' | 'passed';
      readonly label: string;
      readonly checks: readonly ProgramCheckView[];
    }[] = [
      {
        id: 'problems',
        label: 'Требует внимания',
        checks: report.checks.filter((check) => !check.ok)
      },
      {
        id: 'passed',
        label: 'Пройдено',
        checks: report.checks.filter((check) => check.ok)
      }
    ];

    return groups.filter((group) => group.checks.length > 0);
  });
}
