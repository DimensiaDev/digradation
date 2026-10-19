import { booleanAttribute, ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * Animated brand mark for the landing hero.
 *
 * The static sidebar mark (`/assets/svgs/logo.svg`) is a raster inside an SVG, so
 * it cannot be recoloured or morphed without shipping a different drawing. This
 * component keeps that exact asset as the base image and animates around it: a
 * breathing transform, a soft aura and a light wave clipped to the mark's own
 * silhouette with a CSS mask. Only `transform` / `opacity` / `background-position`
 * animate, so the compositor stays in charge.
 */
@Component({
  selector: 'app-brand-logo-animated',
  templateUrl: './brand-logo-animated.component.html',
  styleUrl: './brand-logo-animated.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'img',
    '[attr.aria-label]': 'label()',
    '[class.brand-logo--active]': 'active()',
    '[style.width.px]': 'size()',
    '[style.height.px]': 'size()'
  }
})
export class BrandLogoAnimatedComponent {
  readonly size = input(180);
  readonly active = input(false, { transform: booleanAttribute });
  readonly label = input('Логотип di:gradation');
}
