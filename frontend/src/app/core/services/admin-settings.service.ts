import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { InstanceSettings, InstanceSettingsUpdate } from '../models/models';

@Injectable({
  providedIn: 'root'
})
export class AdminSettingsService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/admin/settings`;

  getSettings(): Observable<InstanceSettings> {
    return this.http.get<InstanceSettings>(this.baseUrl);
  }

  updateSettings(patch: InstanceSettingsUpdate): Observable<InstanceSettings> {
    // PATCH, not PUT: the payload is partial, so a setting added on the server
    // later does not become something this client has to know about.
    return this.http.patch<InstanceSettings>(this.baseUrl, patch);
  }
}
