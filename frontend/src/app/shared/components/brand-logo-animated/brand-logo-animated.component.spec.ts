import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { BrandLogoAnimatedComponent } from './brand-logo-animated.component';

describe('BrandLogoAnimatedComponent', () => {
  let fixture: ComponentFixture<BrandLogoAnimatedComponent>;

  const create = (): void => {
    fixture = TestBed.createComponent(BrandLogoAnimatedComponent);
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BrandLogoAnimatedComponent]
    }).compileComponents();
  });

  it('renders the brand mark with an accessible label', () => {
    create();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const mark = fixture.debugElement.query(By.css('.brand-logo__mark'))
      .nativeElement as HTMLImageElement;

    expect(host.getAttribute('role')).toBe('img');
    expect(host.getAttribute('aria-label')).toBe('Логотип di:gradation');
    expect(mark.getAttribute('src')).toBe('/assets/svgs/logo.svg');
  });

  it('exposes the size on the host box', () => {
    create();
    fixture.componentRef.setInput('size', 220);
    fixture.detectChanges();

    expect(fixture.nativeElement.style.width).toBe('220px');
    expect(fixture.nativeElement.style.height).toBe('220px');
  });

  it('enters the active state when active is set', () => {
    create();
    fixture.componentRef.setInput('active', true);
    fixture.detectChanges();

    expect(fixture.nativeElement.classList.contains('brand-logo--active')).toBe(true);
  });

  it('stays idle by default', () => {
    create();
    fixture.detectChanges();

    expect(fixture.nativeElement.classList.contains('brand-logo--active')).toBe(false);
  });
});
