import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DirectResponse, DirectService } from '../../services/direct.service';
import { SystemService } from '../../services/system.service';

interface DirectEvent {
  time: string;
  title: string;
  detail: string;
  ok: boolean;
}

@Component({
  selector: 'app-direct-control',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './direct-control.component.html',
  styleUrl: './direct-control.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class DirectControlComponent implements OnInit {
  public system = inject(SystemService);
  private direct = inject(DirectService);

  public active = signal(false);
  public busy = signal(false);
  public screenshot = signal<string | null>(null);
  public error = signal<string | null>(null);
  public events = signal<DirectEvent[]>([]);
  public lastClick = signal<[number, number] | null>(null);
  public reportPath = signal<string | null>(null);
  public selectedDevice = signal('');
  public sessionName = signal('Web Direct mobile test');
  public packageName = signal('');
  public textInput = signal('');
  public checkpointName = signal('Screen is correct');
  public checkpointDetails = signal('');
  public summary = signal('Manual Direct verification completed.');
  public actionCount = signal(0);
  public checkpointCount = signal(0);

  public canStart = computed(() => !!this.selectedDevice() && !this.busy() && !this.active());

  ngOnInit(): void {
    this.system.fetchReadiness(true, true).subscribe({
      next: () => {
        const preferred = this.system.activeDevice()?.serial || this.system.connectedDevices()[0]?.serial;
        if (preferred && !this.selectedDevice()) this.selectedDevice.set(preferred);
      },
      error: () => {}
    });
    this.direct.status().subscribe({
      next: response => {
        this.active.set(!!response.result['active']);
        if (response.device_serial) this.selectedDevice.set(response.device_serial);
        this.actionCount.set(response.result['action_count'] || 0);
        this.checkpointCount.set(response.result['checkpoint_count'] || 0);
        if (this.active()) this.observe();
      },
      error: () => {}
    });
  }

  public start(): void {
    if (!this.canStart()) return;
    this.runRequest('Session started', this.direct.start(
      this.selectedDevice(),
      this.sessionName().trim() || 'Web Direct mobile test',
      this.packageName().trim()
    ), response => {
      this.active.set(!!response.result['ok']);
      this.observe();
    });
  }

  public observe(): void {
    if (!this.active() || this.busy()) return;
    this.runRequest('Screen refreshed', this.direct.observe());
  }

  public launchApp(): void {
    const pkg = this.packageName().trim();
    if (!pkg) {
      this.error.set('Enter an Android package name first.');
      return;
    }
    this.action('manage_app', { action: 'launch', app_name: pkg }, `Launch ${pkg}`);
  }

  public pressKey(key: string): void {
    this.action('press_key', { key }, `Press ${key}`);
  }

  public swipe(direction: string): void {
    this.action('swipe', { direction }, `Swipe ${direction}`);
  }

  public inputText(): void {
    const target = this.lastClick();
    const text = this.textInput();
    if (!target || !text) {
      this.error.set('Click an input field on the screen, then enter text.');
      return;
    }
    this.action('input_text', { text, target, clear_exist: true }, 'Input text');
  }

  public onScreenClick(event: MouseEvent): void {
    if (!this.active() || this.busy()) return;
    const image = event.currentTarget as HTMLImageElement;
    const rect = image.getBoundingClientRect();
    const x = Math.max(0, Math.min(1000, Math.round(((event.clientX - rect.left) / rect.width) * 1000)));
    const y = Math.max(0, Math.min(1000, Math.round(((event.clientY - rect.top) / rect.height) * 1000)));
    this.lastClick.set([x, y]);
    this.action('click', { target: [x, y], target_description: 'Web console pointer' }, `Tap [${x}, ${y}]`);
  }

  public addCheckpoint(passed: boolean): void {
    const name = this.checkpointName().trim();
    if (!name) return;
    this.runRequest(
      `${passed ? 'PASS' : 'FAIL'} checkpoint: ${name}`,
      this.direct.checkpoint(name, passed, this.checkpointDetails()),
      response => {
        this.checkpointCount.set(response.result['checkpoint']?.index || this.checkpointCount() + 1);
      }
    );
  }

  public finish(status: 'passed' | 'failed' | 'aborted'): void {
    this.runRequest('Session finished', this.direct.finish(status, this.summary()), response => {
      this.active.set(false);
      this.reportPath.set(response.result['report_path'] || null);
    });
  }

  private action(tool: string, args: Record<string, any>, title: string): void {
    if (!this.active()) return;
    this.runRequest(title, this.direct.action(tool, args), () => {
      this.actionCount.update(value => value + 1);
    });
  }

  private runRequest(
    title: string,
    request: { subscribe: Function },
    onSuccess?: (response: DirectResponse) => void
  ): void {
    this.busy.set(true);
    this.error.set(null);
    request.subscribe({
      next: (response: DirectResponse) => {
        if (response.image_data_url) this.screenshot.set(response.image_data_url);
        const ok = response.result['ok'] !== false;
        this.events.update(items => [{
          time: new Date().toLocaleTimeString(),
          title,
          detail: response.texts.join(' · ') || (ok ? 'Done' : 'Failed'),
          ok
        }, ...items].slice(0, 30));
        if (!ok) this.error.set(response.texts.join(' ') || 'Direct operation failed.');
        this.busy.set(false);
        onSuccess?.(response);
      },
      error: (err: any) => {
        const message = err?.error?.detail || err?.message || 'Direct operation failed.';
        this.error.set(message);
        this.events.update(items => [{
          time: new Date().toLocaleTimeString(), title, detail: message, ok: false
        }, ...items].slice(0, 30));
        this.busy.set(false);
      }
    });
  }
}
