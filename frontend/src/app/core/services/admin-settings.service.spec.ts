import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { AdminSettingsService } from './admin-settings.service';
import { environment } from '../../../environments/environment';

const BASE_URL = `${environment.apiUrl}/admin/settings`;

const SETTINGS = {
  prompt_extension: 'Не использовать Ст3.',
  parameters: {
    laser_speed_m_per_hour: 10,
    welding_speed_m_per_hour: 2,
    bending_rate_per_hour: 84,
    painting_rate_m2_per_hour: 5.53
  },
  last_update_by: 1,
  last_update_at: '2026-10-09T12:00:00Z'
};

describe('AdminSettingsService', () => {
  let service: AdminSettingsService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()]
    });
    service = TestBed.inject(AdminSettingsService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
  });

  it('gets the instance settings', () => {
    let result: unknown;

    service.getSettings().subscribe((settings) => (result = settings));

    const req = http.expectOne((r) => r.url === BASE_URL && r.method === 'GET');
    req.flush(SETTINGS);

    expect(result).toEqual(SETTINGS);
  });

  it('updates the prompt extension with a partial body', () => {
    let result: unknown;

    service.updateSettings({ prompt_extension: 'Новый промпт' }).subscribe((s) => (result = s));

    const req = http.expectOne((r) => r.url === BASE_URL && r.method === 'PATCH');
    expect(req.request.body).toEqual({ prompt_extension: 'Новый промпт' });
    req.flush({ ...SETTINGS, prompt_extension: 'Новый промпт' });

    expect(result).toEqual({ ...SETTINGS, prompt_extension: 'Новый промпт' });
  });

  it('sends an empty body as-is, leaving every setting untouched', () => {
    service.updateSettings({}).subscribe();

    const req = http.expectOne((r) => r.url === BASE_URL && r.method === 'PATCH');
    expect(req.request.body).toEqual({});
    req.flush(SETTINGS);
  });
});
