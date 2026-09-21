/**
 * 统一图标层
 *
 * v1.5.0 之前：导航项、空状态、状态标签都用 emoji 字面量（📁📝🔄📭…），
 * 在不同操作系统上渲染出的形状与颜色都不一样，是"廉价感"的主要来源（需求 AC-5）。
 *
 * 现在：全部走 lucide-react 线性图标，统一尺寸与线宽，并提供**语义图标名**，
 * 避免各页面各写各的图标导致同类功能图标不一致。
 */
import type { LucideIcon, LucideProps } from 'lucide-react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpDown,
  BadgeCheck,
  Bell,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Clock,
  Cloud,
  Columns2,
  Copy,
  Cpu,
  Database,
  Download,
  Eye,
  File,
  FileArchive,
  FileCode2,
  FileImage,
  FileText,
  FileVideo,
  Filter,
  Folder,
  FolderOpen,
  Grid2x2,
  HardDrive,
  Info,
  Key,
  LayoutGrid,
  Link2,
  List,
  Loader2,
  Lock,
  LogOut,
  Maximize2,
  Minus,
  Monitor,
  Moon,
  MoreHorizontal,
  Music,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Server,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Square,
  Sun,
  Trash2,
  TriangleAlert,
  Upload,
  User,
  UserPlus,
  Users,
  Wrench,
  X,
  Zap,
  ZoomIn,
  ZoomOut,
  Network,
  Bold,
  Italic,
  Strikethrough,
  Heading1,
  Heading2,
  Heading3,
  ListOrdered,
  Quote,
  Image as ImageIcon,
  Tags,
} from 'lucide-react';

/** 语义图标名 → lucide 组件 */
const ICONS = {
  // ---------- 导航 ----------
  files: Folder,
  filesOpen: FolderOpen,
  notes: FileText,
  sync: RefreshCw,
  transfer: ArrowUpDown,
  repair: Wrench,
  security: ShieldCheck,
  shieldCheck: ShieldCheck,
  shieldOutline: Shield,
  settings: Settings,
  users: Users,
  disks: HardDrive,
  permissions: Key,
  system: Cpu,

  // ---------- 文件类型 ----------
  folder: Folder,
  file: File,
  doc: FileText,
  image: FileImage,
  video: FileVideo,
  audio: Music,
  archive: FileArchive,
  code: FileCode2,

  // ---------- 状态 ----------
  success: CircleCheck,
  checked: Check,
  checkAll: CheckCheck,
  error: CircleAlert,
  warning: TriangleAlert,
  alert: AlertTriangle,
  info: Info,
  help: CircleHelp,
  clock: Clock,
  locked: Lock,
  verified: BadgeCheck,
  shield: Shield,
  sparkles: Sparkles,
  zap: Zap,
  cloud: Cloud,

  // ---------- 操作 ----------
  search: Search,
  plus: Plus,
  minus: Minus,
  close: X,
  back: ArrowLeft,
  prev: ChevronLeft,
  next: ChevronRight,
  expand: ChevronDown,
  expandUpDown: ChevronsUpDown,
  more: MoreHorizontal,
  edit: Pencil,
  save: Save,
  delete: Trash2,
  upload: Upload,
  download: Download,
  refresh: RefreshCw,
  filter: Filter,
  copy: Copy,
  view: Eye,
  fullscreen: Maximize2,
  play: Play,
  stop: Square,
  logout: LogOut,
  user: User,
  userAdd: UserPlus,
  link: Link2,
  server: Server,
  database: Database,
  book: BookOpen,
  grid: Grid2x2,
  layoutGrid: LayoutGrid,
  list: List,
  columns: Columns2,
  theme: Moon,
  themeLight: Sun,
  themeSystem: Monitor,
  sparkle: Sparkles,
  network: Network,
  tags: Tags,

  // ---------- 图表（Mermaid）----------
  diagram: Network,
  zoomIn: ZoomIn,
  zoomOut: ZoomOut,
  bell: Bell,

  // ---------- 编辑器工具栏 ----------
  bold: Bold,
  italic: Italic,
  strike: Strikethrough,
  h1: Heading1,
  h2: Heading2,
  h3: Heading3,
  listUl: List,
  listOl: ListOrdered,
  quote: Quote,
  inlineCode: FileCode2,
  imageInsert: ImageIcon,

  // ---------- 其他 ----------
  loading: Loader2,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** 图标尺寸档位（与设计令牌的间距阶梯对齐） */
const SIZES = { sm: 15, md: 18, lg: 22, xl: 28 } as const;
export type IconSize = keyof typeof SIZES;

export interface IconProps extends Omit<LucideProps, 'size' | 'strokeWidth'> {
  /** 语义图标名 */
  name: IconName;
  /** 尺寸档位，默认 md（18） */
  size?: IconSize;
  /** 线宽，默认 1.75（全站统一，保证视觉重量一致） */
  strokeWidth?: number;
}

/**
 * 语义图标组件。
 *
 * @example
 * <Icon name="files" />            // 18px 线性图标
 * <Icon name="sync" size="sm" />   // 15px
 * <Icon name="loading" className="animate-spin" />
 */
export function Icon({ name, size = 'md', strokeWidth = 1.75, className, ...rest }: IconProps) {
  const Cmp = ICONS[name];
  return (
    <Cmp
      size={SIZES[size]}
      strokeWidth={strokeWidth}
      className={className}
      aria-hidden="true"
      {...rest}
    />
  );
}

/** 供需要直接拿组件（例如放进 `icon` prop）的场景使用 */
export function getIcon(name: IconName): LucideIcon {
  return ICONS[name];
}
