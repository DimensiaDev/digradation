import { Injector } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { FormControl, NgControl, Validators } from '@angular/forms';
import { controlErrorSignal } from './control-error-signal';

describe('controlErrorSignal', () => {
  let injector: Injector;

  beforeEach(() => {
    injector = TestBed.inject(Injector);
  });

  function makeNgControl(control: FormControl): NgControl {
    return { control } as unknown as NgControl;
  }

  it('returns false when ngControl is null', () => {
    expect(controlErrorSignal(null, injector)()).toBe(false);
  });

  it('returns false when control is null', () => {
    expect(controlErrorSignal({ control: null } as unknown as NgControl, injector)()).toBe(false);
  });

  it('returns false for a valid, untouched control', () => {
    const control = new FormControl<string | null>('value');

    expect(controlErrorSignal(makeNgControl(control), injector)()).toBe(false);
  });

  it('returns true when the control becomes invalid and touched', async () => {
    const control = new FormControl<string | null>(null, Validators.required);
    const showError = controlErrorSignal(makeNgControl(control), injector);

    control.markAsTouched();
    control.updateValueAndValidity();
    await new Promise((resolve) => setTimeout(resolve));

    expect(showError()).toBe(true);
  });

  it('returns false again when the control becomes valid', async () => {
    const control = new FormControl<string | null>(null, Validators.required);
    const showError = controlErrorSignal(makeNgControl(control), injector);

    control.markAsTouched();
    control.updateValueAndValidity();
    await new Promise((resolve) => setTimeout(resolve));
    expect(showError()).toBe(true);

    control.setValue('value');
    await new Promise((resolve) => setTimeout(resolve));

    expect(showError()).toBe(false);
  });

  it('stays false for an invalid but untouched control', () => {
    const control = new FormControl<string | null>(null, Validators.required);

    expect(controlErrorSignal(makeNgControl(control), injector)()).toBe(false);
  });
});
