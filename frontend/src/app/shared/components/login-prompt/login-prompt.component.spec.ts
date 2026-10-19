import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';

import { LoginPrompt } from './login-prompt.component';
import { AuthModalService } from '../../../core/services/auth-modal.service';

describe('LoginPrompt', () => {
  let fixture: ComponentFixture<LoginPrompt>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LoginPrompt],
      providers: [provideRouter([])]
    }).compileComponents();

    fixture = TestBed.createComponent(LoginPrompt);
    fixture.detectChanges();
  });

  it('renders the lock, the title and the sign-in button', () => {
    const element = fixture.nativeElement as HTMLElement;

    expect(element.textContent).toContain('Требуется вход');
    expect(element.querySelector('svg')).not.toBeNull();
    expect(element.querySelector('button')?.textContent?.trim()).toBe('Войти');
  });

  it('opens the sign-in dialog on the current url', () => {
    const modal = TestBed.inject(AuthModalService);

    fixture.debugElement.query(By.css('button')).nativeElement.click();

    expect(modal.isOpen()).toBe(true);
    expect(modal.returnUrl()).toBe('/');
  });
});
