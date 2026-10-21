import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { ActivatedRoute, RouterOutlet } from '@angular/router';
import { ProfileService } from '../../../core/services/profile.service';
import { LoginPrompt } from '../login-prompt/login-prompt.component';

@Component({
  selector: 'app-auth-gate',
  imports: [RouterOutlet, LoginPrompt],
  templateUrl: './auth-gate.component.html',
  styleUrl: './auth-gate.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AuthGate {
  private readonly profile = inject(ProfileService);
  private readonly route = inject(ActivatedRoute);

  readonly user = this.profile.user;
  readonly guestPreview = this.route.snapshot.data['authGateMode'] === 'preview';
}
