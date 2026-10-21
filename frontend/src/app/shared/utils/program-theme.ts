import {
  LucideBriefcase,
  LucideChartColumn,
  LucideCode2,
  LucideRefreshCw,
  LucideShieldCheck,
  LucideUsers,
  type LucideIcon
} from '@lucide/angular';
import type { ProgramTheme } from '../../core/models/models';

export const PROGRAM_THEME_ICONS: Record<ProgramTheme, LucideIcon> = {
  it: LucideCode2,
  analytics: LucideChartColumn,
  'soft-skills': LucideUsers,
  agile: LucideRefreshCw,
  management: LucideBriefcase,
  security: LucideShieldCheck
};
