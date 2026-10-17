import { Component, input } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { HeaderComponent } from '../header/header.component';
import { Sidebar } from '../sidebar/sidebar.component';
import type { NavItem, Role } from '../sidebar/sidebar.config';

@Component({
  selector: 'app-layout',
  imports: [RouterOutlet, HeaderComponent, Sidebar],
  templateUrl: './layout.component.html',
  styleUrl: './layout.component.scss'
})
export class Layout {
  readonly navItems = input<NavItem[]>([]);
  readonly bottomNavItems = input<NavItem[]>([]);
  readonly role = input<Role>();
}
