import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AccordionSectionComponent } from './accordion-section.component';

@Component({
  imports: [AccordionSectionComponent],
  template: `
    <app-accordion-section
      sectionId="demo"
      [expanded]="expanded()"
      (toggled)="toggles.set(toggles() + 1)"
    >
      <span accordionTitle>Заголовок</span>
      <span accordionMeta>3</span>
      <p class="body">Содержимое</p>
    </app-accordion-section>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush
})
class AccordionSectionHostComponent {
  readonly expanded = signal(true);
  readonly toggles = signal(0);
}

describe('AccordionSectionComponent', () => {
  let fixture: ComponentFixture<AccordionSectionHostComponent>;
  let host: AccordionSectionHostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccordionSectionHostComponent]
    }).compileComponents();
    fixture = TestBed.createComponent(AccordionSectionHostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('exposes an expanded region wired to its toggle', () => {
    const toggle = fixture.nativeElement.querySelector('#demo-toggle') as HTMLButtonElement;
    const panel = fixture.nativeElement.querySelector('#demo-panel') as HTMLElement;

    expect(toggle.textContent).toContain('Заголовок');
    expect(toggle.textContent).toContain('3');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-controls')).toBe('demo-panel');
    expect(panel.getAttribute('aria-labelledby')).toBe('demo-toggle');
    expect(panel.textContent).toContain('Содержимое');
    expect(panel.classList.contains('accordion__panel--collapsed')).toBe(false);
    expect(panel.hasAttribute('aria-hidden')).toBe(false);
    expect(panel.hasAttribute('inert')).toBe(false);
  });

  it('collapses the panel without removing it, so the transition can run', () => {
    const toggle = fixture.nativeElement.querySelector('#demo-toggle') as HTMLButtonElement;
    const panel = fixture.nativeElement.querySelector('#demo-panel') as HTMLElement;

    toggle.click();
    host.expanded.set(false);
    fixture.detectChanges();

    expect(host.toggles()).toBe(1);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(panel.classList.contains('accordion__panel--collapsed')).toBe(true);
    expect(panel.getAttribute('aria-hidden')).toBe('true');
    expect(panel.hasAttribute('inert')).toBe(true);
  });
});
