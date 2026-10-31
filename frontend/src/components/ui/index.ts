// Shared UI components (DESIGN.md 7). Import from 'components/ui'.

// Layout
export { PageHeader, type PageHeaderProps } from './PageHeader';
export { Panel, type PanelProps } from './Panel';
export { Hero, type HeroProps } from './Hero';

// Images
export { CoverArt, isFailedImage, markFailedImage, type CoverArtProps } from './CoverArt';
export { CoverTile, type CoverTileProps, type CoverStatus } from './CoverTile';

// Status
export {
  statusModel, toneColor, toneSoft, toneFill, STATUS_VOCABULARY,
  type StatusDomain, type Tone, type Glyph, type StatusInfo, type StatusOptions,
} from './status';
export { StatusGlyph, type StatusGlyphProps } from './StatusGlyph';
export { StatusChip, type StatusChipProps } from './StatusChip';
export { StatusDot, type StatusDotProps } from './StatusDot';
export { StatusVocabulary, type StatusVocabularyProps } from './StatusVocabulary';
export { Meter, LyricsMeter, type MeterProps, type MeterSegment, type LyricsMeterProps } from './Meter';

// Feedback
export { EmptyState, type EmptyStateProps } from './EmptyState';
export { Banner, type BannerProps } from './Banner';
export {
  TableSkeleton, CardSkeleton, HeroSkeleton, GridSkeleton, ListSkeleton, Bone,
  type TableSkeletonProps, type SkeletonColumn, type SkeletonColumnKind,
} from './Skeletons';

// Controls
export { IconButton, type IconButtonProps } from './IconButton';
export { ViewTabs, type ViewTab, type ViewTabsProps } from './ViewTabs';
export { FilterBar, type FilterBarProps } from './FilterBar';
export { FilterPill, type FilterPillProps, type FilterPillOption } from './FilterPill';
export { SortMenu, type SortMenuProps, type SortOption } from './SortMenu';

// Tables
export { DataTable, TitleCell, type Column, type DataTableProps, type TitleCellProps } from './DataTable';
export { Pager, type PagerProps } from './Pager';
export { BulkBar, type BulkBarProps } from './BulkBar';

// Data display
export { KeyValueList, type KeyValueItem, type KeyValueListProps } from './KeyValueList';
export {
  CoverageCard, JobProgress,
  type CoverageCardProps, type JobPart, type JobProgressProps,
} from './StatCard';
export { Tag, LangTag, type TagProps, type LangTagProps } from './Tag';

// Forms
export { SettingsRow, SettingsGroup, type SettingsRowProps } from './SettingsRow';
export { SaveBar, type SaveBarProps } from './SaveBar';

// Dialogs
export { ConfirmProvider, useConfirm, type ConfirmOptions } from './ConfirmProvider';
