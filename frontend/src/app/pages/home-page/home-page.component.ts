import { ChangeDetectionStrategy, Component } from '@angular/core';
import { SectionPlaceholder } from '../../shared/components/section-placeholder/section-placeholder.component';

@Component({
  selector: 'app-home-page',
  imports: [SectionPlaceholder],
  templateUrl: './home-page.component.html',
  styleUrl: './home-page.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class HomePage {}
