import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { ProfileService } from '../../../core/services/profile.service';
import { ROLE_LABELS } from '../../../core/models/models';

@Component({
  selector: 'app-profile',
  imports: [],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProfileComponent {
  private static readonly EMPTY_AVATAR = '/assets/imgs/empty-avatar.png';
  private static readonly GUEST_NAME = 'Гость';

  private readonly profile = inject(ProfileService);

  readonly avatarLoading = this.profile.avatarLoading;
  readonly avatarSrc = computed(() => this.profile.avatarUrl() ?? ProfileComponent.EMPTY_AVATAR);
  readonly displayName = computed(() => {
    const user = this.profile.user();
    if (!user) {
      return ProfileComponent.GUEST_NAME;
    }
    const name = `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();
    return name || user.username;
  });
  readonly roleLabel = computed(() => {
    const user = this.profile.user();
    return user ? ROLE_LABELS[user.role] : '';
  });

  constructor() {
    effect(() => this.profile.loadAvatar());
  }

  onAvatarError(): void {
    this.profile.resetAvatar();
  }
}
