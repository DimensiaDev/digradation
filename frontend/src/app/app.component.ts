import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { AuthDialogComponent } from './shared/components/auth-dialog/auth-dialog.component';
import { NotificationsComponent } from './shared/components/notifications/notifications.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, AuthDialogComponent, NotificationsComponent],
  templateUrl: './app.component.html'
})
export class App {}
