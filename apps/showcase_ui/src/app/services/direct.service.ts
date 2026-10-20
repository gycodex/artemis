/** Model-free Web Direct API client. */

import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface DirectResponse {
  result: Record<string, any>;
  texts: string[];
  image_data_url: string | null;
  device_serial?: string | null;
}

@Injectable({ providedIn: 'root' })
export class DirectService {
  private http = inject(HttpClient);

  status(): Observable<DirectResponse> {
    return this.http.get<DirectResponse>('/api/direct/status');
  }

  start(deviceSerial: string, name: string, packageName: string): Observable<DirectResponse> {
    return this.http.post<DirectResponse>('/api/direct/session/start', {
      device_serial: deviceSerial,
      name,
      package_name: packageName || null,
      auto_observe: true
    });
  }

  observe(): Observable<DirectResponse> {
    return this.http.post<DirectResponse>('/api/direct/observe', { settle_ms: 200 });
  }

  action(tool: string, arguments_: Record<string, any>): Observable<DirectResponse> {
    return this.http.post<DirectResponse>('/api/direct/action', {
      tool,
      arguments: arguments_
    });
  }

  checkpoint(
    name: string,
    passed: boolean | 'blocked' | 'skipped' | 'not_run',
    details: string,
    captureScreen = true,
    category: 'functional' | 'visual' | 'performance' = 'functional'
  ): Observable<DirectResponse> {
    return this.http.post<DirectResponse>('/api/direct/checkpoint', {
      name,
      ...(typeof passed === 'boolean' ? { passed } : { outcome: passed }),
      details,
      capture_screen: captureScreen,
      category
    });
  }

  finish(status: 'passed' | 'failed' | 'aborted', summary: string): Observable<DirectResponse> {
    return this.http.post<DirectResponse>('/api/direct/session/finish', {
      status,
      summary,
      include_logcat: true,
      logcat_lines: 1000
    });
  }
}
