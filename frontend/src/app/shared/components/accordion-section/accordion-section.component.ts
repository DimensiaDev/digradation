import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { LucideChevronDown } from '@lucide/angular';

@Component({
  selector: 'app-accordion-section',
  imports: [LucideChevronDown],
  templateUrl: './accordion-section.component.html',
  styleUrl: './accordion-section.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AccordionSectionComponent {
  readonly sectionId = input.required<string>();
  readonly expanded = input(false);
  readonly toggled = output<void>();

  toggleId(): string {
    return `${this.sectionId()}-toggle`;
  }

  panelId(): string {
    return `${this.sectionId()}-panel`;
  }
}
