import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { By } from '@angular/platform-browser';
import { vi } from 'vitest';

import { CheckControlComponent } from './check-control.component';

@Component({
  selector: 'app-check-control-test-host',
  imports: [CheckControlComponent, ReactiveFormsModule],
  template: `<app-check-control [formControl]="control" [label]="label" [caption]="caption" />`
})
class CheckboxTestHost {
  readonly label = 'Суперпользователь';
  readonly caption = 'Доступ ко всем чатам';
  readonly control = new FormControl<boolean | null>(null, { nonNullable: false });
}

@Component({
  selector: 'app-switch-test-host',
  imports: [CheckControlComponent, ReactiveFormsModule],
  template: `<app-check-control
    variant="switch"
    [formControl]="control"
    [label]="label"
    [caption]="caption"
  />`
})
class SwitchTestHost {
  readonly label = 'Показывать превью';
  readonly caption = 'Открывать файл после загрузки';
  readonly control = new FormControl<boolean | null>(null, { nonNullable: false });
}

@Component({
  selector: 'app-required-switch-test-host',
  imports: [CheckControlComponent, ReactiveFormsModule],
  template: `<app-check-control variant="switch" [formControl]="control" label="Согласен" />`
})
class RequiredSwitchTestHost {
  readonly control = new FormControl<boolean | null>(null, Validators.requiredTrue);
}

@Component({
  selector: 'app-required-test-host',
  imports: [CheckControlComponent, ReactiveFormsModule],
  template: `<app-check-control [formControl]="control" label="Согласен" />`
})
class RequiredTestHost {
  readonly control = new FormControl<boolean | null>(null, Validators.requiredTrue);
}

describe('CheckControlComponent', () => {
  const getInput = (fx: ComponentFixture<unknown>) =>
    fx.debugElement.query(By.css('input.check-control__input')).nativeElement as HTMLInputElement;

  async function create<T>(hostType: new () => T) {
    const fixture = TestBed.createComponent(hostType);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.debugElement.query(By.directive(CheckControlComponent))
      .componentInstance as CheckControlComponent;
    return { fixture, component, host: fixture.componentInstance };
  }

  it('renders the label and the caption', async () => {
    const { fixture } = await create(CheckboxTestHost);
    const text = fixture.debugElement.nativeElement.textContent as string;

    expect(text).toContain('Суперпользователь');
    expect(text).toContain('Доступ ко всем чатам');
  });

  it('renders the checkbox box by default', async () => {
    const { fixture } = await create(CheckboxTestHost);

    expect(fixture.debugElement.query(By.css('.check-control__box'))).not.toBeNull();
    expect(fixture.debugElement.query(By.css('.check-control__track'))).toBeNull();
  });

  it('renders the switch track and aria-label for the switch variant', async () => {
    const { fixture } = await create(SwitchTestHost);
    const text = fixture.debugElement.nativeElement.textContent as string;

    expect(text).toContain('Показывать превью');
    expect(text).toContain('Открывать файл после загрузки');
    expect(getInput(fixture).getAttribute('aria-label')).toBe('Показывать превью');
    expect(fixture.debugElement.query(By.css('.check-control__track'))).not.toBeNull();
    expect(fixture.debugElement.query(By.css('.check-control__box'))).toBeNull();
  });

  it('propagates a user toggle to the form control', async () => {
    const { fixture, host } = await create(CheckboxTestHost);
    const input = getInput(fixture);
    input.checked = true;
    input.dispatchEvent(new Event('change'));

    expect(host.control.value).toBe(true);
  });

  it('propagates a switch toggle to the form control', async () => {
    const { fixture, host } = await create(SwitchTestHost);
    const input = getInput(fixture);
    input.checked = true;
    input.dispatchEvent(new Event('change'));

    expect(host.control.value).toBe(true);
  });

  it('writes the form control value into the input', async () => {
    const { fixture, host } = await create(CheckboxTestHost);
    host.control.setValue(true);
    fixture.detectChanges();

    expect(getInput(fixture).checked).toBe(true);
  });

  it('checks the input for a null control value', async () => {
    const { fixture, host } = await create(CheckboxTestHost);
    host.control.setValue(null);
    fixture.detectChanges();

    expect(getInput(fixture).checked).toBe(false);
  });

  it('marks the control as touched on blur', async () => {
    const { fixture, host } = await create(CheckboxTestHost);
    getInput(fixture).dispatchEvent(new Event('blur'));

    expect(host.control.touched).toBe(true);
  });

  it('disables the input through the form control', async () => {
    const { fixture, component, host } = await create(CheckboxTestHost);
    host.control.disable();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.isDisabled()).toBe(true);
    expect(getInput(fixture).disabled).toBe(true);
    expect(
      (fixture.debugElement.nativeElement as HTMLElement).querySelector('.check-control--disabled')
    ).not.toBeNull();
  });

  it('enables the input again when the control is re-enabled', async () => {
    const { fixture, component, host } = await create(CheckboxTestHost);
    host.control.disable();
    fixture.detectChanges();
    host.control.enable();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.isDisabled()).toBe(false);
    expect(getInput(fixture).disabled).toBe(false);
  });

  it('supports direct ControlValueAccessor calls', async () => {
    const { fixture, component } = await create(CheckboxTestHost);
    const onChange = vi.fn();
    const onTouched = vi.fn();
    component.registerOnChange(onChange);
    component.registerOnTouched(onTouched);

    const input = getInput(fixture);
    input.checked = true;
    component.onInput({ target: input } as unknown as Event);
    component.onBlur();

    expect(onChange).toHaveBeenCalledWith(true);
    expect(onTouched).toHaveBeenCalledTimes(1);

    component.writeValue(null);
    expect(component.input.nativeElement.checked).toBe(false);

    component.writeValue(true);
    expect(component.input.nativeElement.checked).toBe(true);

    component.setDisabledState(true);
    expect(component.isDisabled()).toBe(true);
    expect(component.input.nativeElement.disabled).toBe(true);
  });

  it('falls back to no-op callbacks when used without a form control', async () => {
    const fixture = TestBed.createComponent(CheckControlComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const input = getInput(fixture);
    input.checked = true;

    expect(() => component.onInput({ target: input } as unknown as Event)).not.toThrow();
    expect(() => component.onBlur()).not.toThrow();
    expect(component.showError()).toBe(false);
  });

  it('marks a checkbox invalid once a required control is touched and empty', async () => {
    const { fixture, component } = await create(RequiredTestHost);

    expect(component.showError()).toBe(false);

    getInput(fixture).dispatchEvent(new Event('blur'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.showError()).toBe(true);
    expect(
      (fixture.debugElement.nativeElement as HTMLElement).querySelector('.check-control--error')
    ).not.toBeNull();
  });

  it('marks a switch invalid once a required control is touched and empty', async () => {
    const { fixture, component } = await create(RequiredSwitchTestHost);

    expect(component.showError()).toBe(false);

    getInput(fixture).dispatchEvent(new Event('blur'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.showError()).toBe(true);
    expect(
      (fixture.debugElement.nativeElement as HTMLElement).querySelector('.check-control--error')
    ).not.toBeNull();
  });
});
