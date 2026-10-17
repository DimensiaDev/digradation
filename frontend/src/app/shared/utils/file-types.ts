import type { LucideIconData } from '@lucide/angular';

export type PreviewKind = 'pdf' | 'docx' | 'spreadsheet' | 'image';

export interface FileTypeStyle {
  readonly icon: LucideIconData;
  readonly color: string;
}

export interface FileTypeMeta {
  readonly kind: PreviewKind | null;
  readonly mime: string;
  readonly style: FileTypeStyle;
}

const mdiNode = (name: string, d: string): LucideIconData => ({
  name,
  node: [['path', { d, fill: 'currentColor', stroke: 'none' }]]
});

const FILE_TYPE_PDF = mdiNode(
  'file-pdf-box',
  'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2m-9.5 8.5c0 .8-.7 1.5-1.5 1.5H7v2H5.5V9H8c.8 0 1.5.7 1.5 1.5zm5 2c0 .8-.7 1.5-1.5 1.5h-2.5V9H13c.8 0 1.5.7 1.5 1.5zm4-3H17v1h1.5V13H17v2h-1.5V9h3zm-6.5 0h1v3h-1zm-5 0h1v1H7z'
);

const FILE_TYPE_WORD = mdiNode(
  'file-word-box',
  'M15.5 17H14l-2-7.5l-2 7.5H8.5L6.1 7h1.7l1.54 7.5L11.3 7h1.4l1.97 7.5L16.2 7h1.7M19 3H5c-1.11 0-2 .89-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2'
);

const FILE_TYPE_EXCEL = mdiNode(
  'file-excel-box',
  'M16.2 17h-2L12 13.2L9.8 17h-2l3.2-5l-3.2-5h2l2.2 3.8L14.2 7h2L13 12m6-9H5c-1.11 0-2 .89-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2'
);

const FILE_TYPE_IMAGE = mdiNode(
  'file-image-box',
  'M8.5 13.498l2.5 3.006l3.5-4.506l4.5 6H5m16 1v-14a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2z'
);

// Single source of truth for every known extension. Edit here and the preview
// kind, MIME type, icon/colour and the accepted-upload list all follow.
export const FILE_TYPES: Record<string, FileTypeMeta> = {
  '.pdf': {
    kind: 'pdf',
    mime: 'application/pdf',
    style: { icon: FILE_TYPE_PDF, color: 'var(--color-danger)' }
  },
  '.doc': {
    kind: null,
    mime: 'application/msword',
    style: { icon: FILE_TYPE_WORD, color: 'var(--color-main)' }
  },
  '.docx': {
    kind: 'docx',
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    style: { icon: FILE_TYPE_WORD, color: 'var(--color-main)' }
  },
  '.xls': {
    kind: 'spreadsheet',
    mime: 'application/vnd.ms-excel',
    style: { icon: FILE_TYPE_EXCEL, color: 'var(--color-success)' }
  },
  '.xlsx': {
    kind: 'spreadsheet',
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    style: { icon: FILE_TYPE_EXCEL, color: 'var(--color-success)' }
  },
  '.png': {
    kind: 'image',
    mime: 'image/png',
    style: { icon: FILE_TYPE_IMAGE, color: 'var(--color-warning)' }
  },
  '.jpg': {
    kind: 'image',
    mime: 'image/jpeg',
    style: { icon: FILE_TYPE_IMAGE, color: 'var(--color-warning)' }
  },
  '.jpeg': {
    kind: 'image',
    mime: 'image/jpeg',
    style: { icon: FILE_TYPE_IMAGE, color: 'var(--color-warning)' }
  }
};

// Extensions a user may attach. The list an admin edits first.
export const ACCEPTED_EXTENSIONS: readonly string[] = ['.pdf', '.png', '.jpeg', '.jpg'];

const UNKNOWN_FILE_TYPE_STYLE: FileTypeStyle = {
  icon: FILE_TYPE_IMAGE,
  color: 'var(--color-gray)'
};

export function previewKindFor(extension: string): PreviewKind | null {
  return FILE_TYPES[extension]?.kind ?? null;
}

export function mimeTypeFor(extension: string): string {
  return FILE_TYPES[extension]?.mime ?? 'application/octet-stream';
}

export function fileTypeStyleFor(extension: string): FileTypeStyle {
  return FILE_TYPES[extension]?.style ?? UNKNOWN_FILE_TYPE_STYLE;
}
