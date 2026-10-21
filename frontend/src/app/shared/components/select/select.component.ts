import {
  afterRenderEffect,
  booleanAttribute,
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  DOCUMENT,
  ElementRef,
  HostListener,
  inject,
  Injector,
  input,
  NgZone,
  OnInit,
  signal,
  Signal,
  viewChild
} from '@angular/core';
import { ControlValueAccessor, NgControl } from '@angular/forms';
import { LucideCheck, LucideChevronDown } from '@lucide/angular';
import { noop } from 'rxjs';
import { controlErrorSignal } from '../../utils/control-error-signal';
import { createId } from '../../utils/create-id';

export interface SelectOption {
  value: string;
  label: string;
}

export type SelectValue = string | string[] | null;

@Component({
  selector: 'app-select',
  imports: [LucideChevronDown, LucideCheck],
  templateUrl: './select.component.html',
  styleUrl: './select.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class Select implements ControlValueAccessor, OnInit {
  readonly options = input<SelectOption[]>([]);
  readonly placeholder = input('Выберите тип проекта');
  readonly disabled = input(false, { transform: booleanAttribute });
  readonly multiple = input(false, { transform: booleanAttribute });

  readonly isOpen = signal(false);
  readonly value = signal<SelectValue>(null);
  readonly highlightedIndex = signal(0);
  readonly dropUp = signal(false);
  private readonly formDisabled = signal(false);

  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('trigger');
  private readonly elementRef = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly document = inject(DOCUMENT);
  private readonly zone = inject(NgZone);
  private readonly ngControl = inject(NgControl, { optional: true, self: true });
  private readonly injector = inject(Injector);
  private readonly destroyRef = inject(DestroyRef);

  private readonly listbox = viewChild<ElementRef<HTMLUListElement>>('listbox');

  readonly listboxId = `select-listbox-${createId()}`;

  readonly label = input<string>();
  readonly labelId = input<string>();
  readonly inputId = input<string>();

  private onChange: (value: SelectValue) => void = noop;
  private onTouched: () => void = noop;

  showError!: Signal<boolean>;

  ngOnInit(): void {
    this.showError = controlErrorSignal(this.ngControl, this.injector);
  }

  constructor() {
    if (this.ngControl) {
      this.ngControl.valueAccessor = this;
    }

    afterRenderEffect({
      write: () => {
        if (this.isOpen()) {
          this.document.addEventListener('scroll', this.onWindowScroll, true);
        } else {
          this.document.removeEventListener('scroll', this.onWindowScroll, true);
        }
      },
      mixedReadWrite: () => {
        const index = this.highlightedIndex();
        const option =
          this.listbox()?.nativeElement.querySelectorAll<HTMLElement>('[role="option"]')[index];
        option?.scrollIntoView?.({ block: 'nearest' });
      }
    });

    this.zone.runOutsideAngular(() => {
      this.window.addEventListener('resize', this.onWindowScroll);
      this.destroyRef.onDestroy(() => {
        this.window.removeEventListener('resize', this.onWindowScroll);
        this.document.removeEventListener('scroll', this.onWindowScroll, true);
        if (this.scrollFrame !== null) {
          this.window.cancelAnimationFrame(this.scrollFrame);
        }
      });
    });
  }

  private readonly window = this.document.defaultView!;
  private scrollFrame: number | null = null;

  private readonly onWindowScroll = (): void => {
    if (!this.isOpen() || this.scrollFrame !== null) {
      return;
    }
    this.scrollFrame = this.window.requestAnimationFrame(() => {
      this.scrollFrame = null;
      this.processScroll();
    });
  };

  private processScroll(): void {
    if (!this.isOpen()) {
      return;
    }
    if (!this.isTriggerInViewport()) {
      this.zone.run(() => this.close());
      return;
    }
    const flipUp = this.shouldFlipUp();
    if (this.dropUp() !== flipUp) {
      this.zone.run(() => this.dropUp.set(flipUp));
    }
  }

  flushScrollFrame(): void {
    if (this.scrollFrame === null) {
      return;
    }
    this.window.cancelAnimationFrame(this.scrollFrame);
    this.scrollFrame = null;
    this.processScroll();
  }

  readonly selectedLabels = computed(() => {
    const labels = new Map(this.options().map((option) => [option.value, option.label]));
    return this.currentValues()
      .map((value) => labels.get(value))
      .filter((label): label is string => label !== undefined);
  });

  readonly selectedLabel = computed(() => {
    const labels = this.selectedLabels();
    return labels.length > 0 ? labels.join(', ') : this.placeholder();
  });

  readonly hasValue = computed(() => this.selectedLabels().length > 0);

  readonly isDisabled = computed(() => this.disabled() || this.formDisabled());

  isSelected(option: SelectOption): boolean {
    if (this.multiple() && option.value === 'all') {
      return this.currentValues().length === 0;
    }
    return this.currentValues().includes(option.value);
  }

  toggle(): void {
    if (this.isOpen()) {
      this.close();
    } else {
      this.open();
    }
  }

  open(): void {
    if (this.isDisabled()) {
      return;
    }
    this.dropUp.set(this.shouldFlipUp());
    this.highlightedIndex.set(this.currentValueIndex());
    this.isOpen.set(true);
  }

  close(): void {
    if (!this.isOpen()) {
      return;
    }
    this.isOpen.set(false);
    this.onTouched();
  }

  selectOption(option: SelectOption): void {
    if (this.isDisabled()) {
      return;
    }

    if (this.multiple()) {
      const current = this.currentValues();
      const next =
        option.value === 'all'
          ? []
          : current.includes(option.value)
            ? current.filter((value) => value !== option.value)
            : [...current, option.value];
      this.value.set(next);
      this.onChange(next);
      return;
    }

    this.value.set(option.value);
    this.onChange(option.value);
    this.close();
    this.trigger().nativeElement.focus();
  }

  onOptionHover(index: number): void {
    this.highlightedIndex.set(index);
  }

  onKeydown(event: KeyboardEvent): void {
    switch (event.key) {
      case 'Enter':
      case ' ':
        event.preventDefault();
        if (this.isOpen()) {
          const option = this.options()[this.highlightedIndex()];
          if (option) {
            this.selectOption(option);
          }
        } else {
          this.open();
        }
        break;

      case 'Escape':
        if (this.isOpen()) {
          event.stopPropagation();
          this.close();
        }
        break;

      case 'Tab':
        this.close();
        break;

      case 'ArrowDown':
        event.preventDefault();
        if (this.isOpen()) {
          this.moveHighlight(1);
        } else {
          this.open();
        }
        break;

      case 'ArrowUp':
        event.preventDefault();
        if (this.isOpen()) {
          this.moveHighlight(-1);
        } else {
          this.open();
        }
        break;

      case 'Home':
        if (this.isOpen()) {
          event.preventDefault();
          this.highlightedIndex.set(0);
        }
        break;

      case 'End':
        if (this.isOpen()) {
          event.preventDefault();
          this.highlightedIndex.set(this.options().length - 1);
        }
        break;
    }
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as Node;
    if (!this.elementRef.nativeElement.contains(target)) {
      this.close();
    }
  }

  private isTriggerInViewport(): boolean {
    const rect = this.trigger().nativeElement.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < this.window.innerHeight;
  }

  private shouldFlipUp(): boolean {
    const rect = this.trigger().nativeElement.getBoundingClientRect();
    const spaceBelow = this.window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const listbox = this.listbox()?.nativeElement;
    const requiredSpace = listbox ? Math.min(listbox.offsetHeight + 8, 264) : 264;
    if (spaceBelow >= requiredSpace || spaceAbove < requiredSpace) {
      return false;
    }
    return spaceAbove > spaceBelow;
  }

  writeValue(value: SelectValue): void {
    this.value.set(value);
    this.highlightedIndex.set(this.currentValueIndex());
  }

  registerOnChange(fn: (value: SelectValue) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(isDisabled: boolean): void {
    this.formDisabled.set(isDisabled);
  }

  private currentValues(): string[] {
    const value = this.value();
    if (Array.isArray(value)) {
      return value;
    }
    return value === null ? [] : [value];
  }

  private currentValueIndex(): number {
    const values = this.currentValues();
    const index = this.options().findIndex((option) => values.includes(option.value));
    return index >= 0 ? index : 0;
  }

  private moveHighlight(delta: number): void {
    const count = this.options().length;
    if (count === 0) {
      return;
    }
    this.highlightedIndex.set((this.highlightedIndex() + delta + count) % count);
  }
}
