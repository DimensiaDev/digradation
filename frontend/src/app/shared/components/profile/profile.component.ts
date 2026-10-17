import { ChangeDetectionStrategy, Component, OnInit, computed, inject } from '@angular/core';
import { ProfileService } from '../../../core/services/profile.service';
import { ROLE_LABELS } from '../../../core/models/models';

@Component({
  selector: 'app-profile',
  imports: [],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ProfileComponent implements OnInit {
  private static readonly EMPTY_AVATAR = '/assets/imgs/empty-avatar.png';

  private readonly profile = inject(ProfileService);

  readonly avatarLoading = this.profile.avatarLoading;
  readonly avatarSrc = computed(() => this.profile.avatarUrl() ?? ProfileComponent.EMPTY_AVATAR);
  readonly displayName = computed(() => {
    const user = this.profile.user();
    if (!user) {
      return '';
    }
    const name = `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();
    return name || user.username;
  });
  readonly roleLabel = computed(() => {
    const user = this.profile.user();
    return user ? ROLE_LABELS[user.role] : '';
  });

  ngOnInit(): void {
    this.profile.loadAvatar();
  }

  onAvatarError(): void {
    this.profile.resetAvatar();
  }
}
