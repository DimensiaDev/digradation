import { ComponentFixture, TestBed } from '@angular/core/testing';

import { HomePage } from './home-page.component';

describe('HomePage', () => {
  let fixture: ComponentFixture<HomePage>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HomePage] }).compileComponents();

    fixture = TestBed.createComponent(HomePage);
    fixture.detectChanges();
  });

  it('renders the public home placeholder', () => {
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('h1')?.textContent).toContain('Главная');
  });
});
