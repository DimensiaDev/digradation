import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { LucideCheck, LucideTriangleAlert } from '@lucide/angular';
import { ProgramStateApi } from '../../core/models/models';
import {
  PROGRAM_WIZARD_STEPS,
  ProgramWizardStep,
  programStep,
  programStepState
} from './program-view';

@Component({
  selector: 'app-program-stepper',
  imports: [LucideCheck, LucideTriangleAlert],
  templateUrl: './program-stepper.component.html',
  styleUrl: './program-stepper.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProgramStepperComponent {
  readonly state = input<ProgramStateApi | null>(null);

  readonly steps = PROGRAM_WIZARD_STEPS;
  readonly activeStep = computed(() => programStep(this.state()));
  readonly busy = computed(() => this.state()?.busy === true);

  stepState(step: ProgramWizardStep) {
    return programStepState(this.state(), step);
  }

  /**
   * `busy` covers the finalisation too, which holds no stage lock, so the
   * spinner follows the run rather than the worker's live hint.
   */
  isWorking(step: ProgramWizardStep): boolean {
    return this.busy() && this.stepState(step) === 'active';
  }
}
