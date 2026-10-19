import { ChangeDetectionStrategy, Component } from '@angular/core';
import { SectionPlaceholder } from '../../shared/components/section-placeholder/section-placeholder.component';

@Component({
  selector: 'app-help-page',
  imports: [SectionPlaceholder],
  templateUrl: './help-page.component.html',
  styleUrl: './help-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HelpPage {}
