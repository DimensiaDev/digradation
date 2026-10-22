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

  stepState(step: ProgramWizardStep) {
    return programStepState(this.state(), step);
  }
}
