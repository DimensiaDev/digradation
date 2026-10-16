import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { AdminSettingsPage } from './settings-page.component';
import { environment } from '../../../../environments/environment';

const SETTINGS_URL = `${environment.apiUrl}/admin/settings`;

const PROMPT = 'Отвечай кратко и по существу.';

const SETTINGS = {
  prompt_extension: PROMPT,
  last_update_by: null,
  last_update_at: null
};

describe('AdminSettingsPage', () => {
  let fixture: ComponentFixture<AdminSettingsPage>;
  let component: AdminSettingsPage;
  let http: HttpTestingController;

  const createPage = (prompt_extension = PROMPT): void => {
    fixture = TestBed.createComponent(AdminSettingsPage);
    component = fixture.componentInstance;
    fixture.detectChanges();
    http
      .expectOne((req) => req.url === SETTINGS_URL && req.method === 'GET')
      .flush({ ...SETTINGS, prompt_extension });
    fixture.detectChanges();
  };

  const textarea = (): HTMLTextAreaElement =>
    fixture.debugElement.query(By.css('textarea')).nativeElement;

  const typePrompt = (value: string): void => {
    textarea().value = value;
    textarea().dispatchEvent(new Event('input'));
    fixture.detectChanges();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AdminSettingsPage],
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('loads the settings with a single request and enables the form', () => {
    createPage();

    expect(component.promptForm.getRawValue().prompt).toBe(PROMPT);
    expect(component.promptForm.controls.prompt.enabled).toBe(true);
    expect(textarea().value).toBe(PROMPT);
  });

  it('keeps save disabled while untouched, blank or whitespace-only', () => {
    createPage();
    expect(component.canSave()).toBe(false);

    typePrompt('Новый промпт');
    expect(component.canSave()).toBe(true);

    typePrompt('');
    expect(component.canSave()).toBe(false);

    typePrompt('   ');
    expect(component.canSave()).toBe(false);
  });

  it('rejects a prompt extension over the server limit', () => {
    createPage();

    component.promptForm.controls.prompt.setValue('x'.repeat(8001));
    fixture.detectChanges();

    expect(component.canSave()).toBe(false);
  });

  it('saves an edited prompt as a PATCH carrying only that field', () => {
    createPage();
    typePrompt('Новый промпт');

    component.save();

    const req = http.expectOne(SETTINGS_URL);
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ prompt_extension: 'Новый промпт' });
    req.flush({ ...SETTINGS, prompt_extension: 'Новый промпт' });
    fixture.detectChanges();

    expect(component.promptForm.controls.prompt.pristine).toBe(true);
    expect(component.canSave()).toBe(false);
  });

  it('reports a failed save and keeps the edit on screen', () => {
    createPage();
    typePrompt('Новый промпт');

    component.save();

    http
      .expectOne(SETTINGS_URL)
      .flush({ detail: 'Нет доступа' }, { status: 403, statusText: 'Forbidden' });
    fixture.detectChanges();

    expect(component.promptForm.controls.prompt.pristine).toBe(false);
    expect(textarea().value).toBe('Новый промпт');
  });
});
