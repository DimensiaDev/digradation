import { ComponentFixture, TestBed } from '@angular/core/testing';

import { HelpPage } from './help-page.component';

describe('HelpPage', () => {
  let fixture: ComponentFixture<HelpPage>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HelpPage] }).compileComponents();

    fixture = TestBed.createComponent(HelpPage);
    fixture.detectChanges();
  });

  it('renders the public help placeholder', () => {
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('h1')?.textContent).toContain('Помощь');
  });
});
