import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { HeaderComponent } from '../header/header.component';
import { Sidebar } from '../sidebar/sidebar.component';
import type { NavItem, Role } from '../../navigation/navigation.config';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, HeaderComponent, Sidebar],
  templateUrl: './layout.component.html',
  styleUrl: './layout.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class Layout {
  // See Sidebar: componentInputBinding turns a key the route data omits into
  // undefined, so both lists must survive that instead of relying on defaults.
  readonly navItems = input<NavItem[], NavItem[] | undefined>([], {
    transform: (items) => items ?? []
  });
  readonly bottomNavItems = input<NavItem[], NavItem[] | undefined>([], {
    transform: (items) => items ?? []
  });
  readonly role = input<Role>();
}
