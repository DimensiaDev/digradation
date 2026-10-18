import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  ViewChild,
  computed,
  inject,
  Injector,
  input,
  OnInit,
  signal,
  Signal
} from '@angular/core';
import { ControlValueAccessor, NgControl } from '@angular/forms';
import { LucideCheck } from '@lucide/angular';
import { noop } from 'rxjs';
import { controlErrorSignal } from '../../utils/control-error-signal';

@Component({
  selector: 'app-check-control',
  standalone: true,
  imports: [LucideCheck],
  templateUrl: './check-control.component.html',
  styleUrl: './check-control.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.check-control--error]': 'showError()'
  }
})
export class CheckControlComponent implements ControlValueAccessor, OnInit {
  readonly variant = input<'checkbox' | 'switch'>('checkbox');
  readonly label = input('');
  readonly caption = input('');

  private readonly formDisabled = signal(false);

  readonly isDisabled = computed(() => this.formDisabled());

  @ViewChild('input', { static: true, read: ElementRef<HTMLInputElement> })
  input!: ElementRef<HTMLInputElement>;

  private readonly ngControl = inject(NgControl, { optional: true, self: true });
  private readonly injector = inject(Injector);

  showError!: Signal<boolean>;

  private onChange: (checked: boolean) => void = noop;
  private onTouched: () => void = noop;

  ngOnInit(): void {
    this.showError = controlErrorSignal(this.ngControl, this.injector);
  }

  constructor() {
    if (this.ngControl) {
      this.ngControl.valueAccessor = this;
    }
  }

  onInput(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.onChange(target.checked);
  }

  onBlur(): void {
    this.onTouched();
  }

  writeValue(checked: boolean | null): void {
    this.input.nativeElement.checked = checked ?? false;
  }

  registerOnChange(fn: (checked: boolean) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(isDisabled: boolean): void {
    this.formDisabled.set(isDisabled);
    this.input.nativeElement.disabled = isDisabled;
  }
}
