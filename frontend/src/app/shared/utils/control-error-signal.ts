import { Injector, Signal, computed, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { AbstractControl, NgControl } from '@angular/forms';

export function controlErrorSignal(
  ngControl: NgControl | null | undefined,
  injector: Injector
): Signal<boolean> {
  const control: AbstractControl | null = ngControl?.control ?? null;
  if (!control) {
    return signal(false);
  }

  const controlEvents = toSignal(control.events, { initialValue: null, injector });

  return computed(() => {
    controlEvents();
    return control.invalid && control.touched;
  });
}
