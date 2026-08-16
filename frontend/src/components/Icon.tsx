import React from "react";

type IconSize = 16 | 18 | 20 | 24 | number;

interface IconProps extends React.SVGProps<SVGSVGElement> {
  size?: IconSize;
  strokeWidth?: number;
}

const defaultSize = 16;
const defaultStroke = 1.5;

function makeIcon(
  name: string,
  render: (p: IconProps) => React.ReactNode
): React.FC<IconProps> {
  const Comp: React.FC<IconProps> = ({
    size = defaultSize,
    strokeWidth = defaultStroke,
    className,
    ...rest
  }) => (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...rest}
    >
      {render({ size, strokeWidth, className, ...rest })}
    </svg>
  );
  Comp.displayName = `${name}Icon`;
  return Comp;
}

export const TargetIcon = makeIcon("Target", () => (
  <>
    <circle cx="12" cy="12" r="10" />
    <circle cx="12" cy="12" r="6" />
    <circle cx="12" cy="12" r="2" />
  </>
));

export const BarChart3Icon = makeIcon("BarChart3", () => (
  <>
    <path d="M3 3v18h18" />
    <path d="M18 17V9" />
    <path d="M13 17V5" />
    <path d="M8 17v-3" />
  </>
));

export const NewspaperIcon = makeIcon("Newspaper", () => (
  <>
    <path d="M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-4 0v-9a2 2 0 012-2h2" />
    <path d="M8 6h8" />
    <path d="M8 10h8" />
    <path d="M8 14h5" />
  </>
));

export const TrendingUpIcon = makeIcon("TrendingUp", () => (
  <>
    <path d="M16 7h6v6" />
    <path d="M22 7l-8.5 8.5-5-5L2 17" />
  </>
));

export const ShieldCheckIcon = makeIcon("ShieldCheck", () => (
  <>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <path d="M9 12l2 2 4-4" />
  </>
));

export const FileTextIcon = makeIcon("FileText", () => (
  <>
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
    <path d="M14 2v6h6" />
    <path d="M16 13H8" />
    <path d="M16 17H8" />
    <path d="M10 9H8" />
  </>
));

export const LayersIcon = makeIcon("Layers", () => (
  <>
    <path d="M12 2L2 7l10 5 10-5-10-5z" />
    <path d="M2 17l10 5 10-5" />
    <path d="M2 12l10 5 10-5" />
  </>
));

export const PlusIcon = makeIcon("Plus", () => (
  <>
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </>
));

export const CheckIcon = makeIcon("Check", () => (
  <polyline points="5 12 10 17 19 8" />
));

export const XIcon = makeIcon("X", () => (
  <>
    <path d="M18 6L6 18" />
    <path d="M6 6l12 12" />
  </>
));

export const SearchIcon = makeIcon("Search", () => (
  <>
    <circle cx="11" cy="11" r="8" />
    <path d="M21 21l-4.35-4.35" />
  </>
));

export const LayoutDashboardIcon = makeIcon("LayoutDashboard", () => (
  <>
    <rect x="3" y="3" width="7" height="9" rx="1" />
    <rect x="14" y="3" width="7" height="5" rx="1" />
    <rect x="14" y="12" width="7" height="9" rx="1" />
    <rect x="3" y="16" width="7" height="5" rx="1" />
  </>
));

export const ListIcon = makeIcon("List", () => (
  <>
    <path d="M8 6h13" />
    <path d="M8 12h13" />
    <path d="M8 18h13" />
    <path d="M3 6h.01" />
    <path d="M3 12h.01" />
    <path d="M3 18h.01" />
  </>
));

export const WalletIcon = makeIcon("Wallet", () => (
  <>
    <path d="M21 12V7H5a2 2 0 01-2-2v0a2 2 0 012-2h14v14a2 2 0 01-2 2H5a2 2 0 01-2-2V8" />
    <path d="M16 12h.01" />
  </>
));

export const ArrowUpIcon = makeIcon("ArrowUp", () => (
  <>
    <path d="M12 19V5" />
    <path d="M5 12l7-7 7 7" />
  </>
));

export const ArrowDownIcon = makeIcon("ArrowDown", () => (
  <>
    <path d="M12 5v14" />
    <path d="M19 12l-7 7-7-7" />
  </>
));

export const MenuIcon = makeIcon("Menu", () => (
  <>
    <path d="M4 6h16" />
    <path d="M4 12h16" />
    <path d="M4 18h16" />
  </>
));

export const ExternalLinkIcon = makeIcon("ExternalLink", () => (
  <>
    <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
    <path d="M15 3h6v6" />
    <path d="M10 14L21 3" />
  </>
));

export const DownloadIcon = makeIcon("Download", () => (
  <>
    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
    <path d="M7 10l5 5 5-5" />
    <path d="M12 15V3" />
  </>
));

export const SendIcon = makeIcon("Send", () => (
  <>
    <path d="M22 2L11 13" />
    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
  </>
));

export const ChevronDownIcon = makeIcon("ChevronDown", () => (
  <path d="M6 9l6 6 6-6" />
));

export const ChevronUpIcon = makeIcon("ChevronUp", () => (
  <path d="M18 15l-6-6-6 6" />
));

export const RefreshCwIcon = makeIcon("RefreshCw", () => (
  <>
    <path d="M23 4v6h-6" />
    <path d="M1 20v-6h6" />
    <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
  </>
));

export const ClockIcon = makeIcon("Clock", () => (
  <>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 6v6l4 2" />
  </>
));

export const AlertCircleIcon = makeIcon("AlertCircle", () => (
  <>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 8v4" />
    <path d="M12 16h.01" />
  </>
));

export const CheckCircleIcon = makeIcon("CheckCircle", () => (
  <>
    <circle cx="12" cy="12" r="10" />
    <path d="M9 12l2 2 4-4" />
  </>
));

export const InfoIcon = makeIcon("Info", () => (
  <>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4" />
    <path d="M12 8h.01" />
  </>
));

export const BotIcon = makeIcon("Bot", () => (
  <>
    <rect x="3" y="11" width="18" height="10" rx="2" />
    <circle cx="12" cy="5" r="2" />
    <path d="M12 7v4" />
    <path d="M8 15h.01" />
    <path d="M16 15h.01" />
  </>
));

export const TerminalIcon = makeIcon("Terminal", () => (
  <>
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" y1="19" x2="20" y2="19" />
  </>
));

export const UserRoundIcon = makeIcon("UserRound", () => (
  <>
    <circle cx="12" cy="7" r="3.5" fill="currentColor" opacity="0.15" />
    <circle cx="12" cy="7" r="3.5" fill="none" strokeWidth="1.5" />
    <path d="M5 21v-1c0-2.76 2.69-4.5 7-4.5s7 1.74 7 4.5v1" fill="none" strokeWidth="1.5" strokeLinecap="round" />
  </>
));

export const PanelRightIcon = makeIcon("PanelRight", () => (
  <>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <path d="M15 3v18" />
  </>
));

export const BriefcaseIcon = makeIcon("Briefcase", () => (
  <>
    <rect x="2" y="7" width="20" height="14" rx="2" />
    <path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2" />
  </>
));

export const MessageSquareIcon = makeIcon("MessageSquare", () => (
  <>
    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
  </>
));

export const EyeIcon = makeIcon("Eye", () => (
  <>
    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
    <circle cx="12" cy="12" r="3" />
  </>
));

export const KeyIcon = makeIcon("Key", () => (
  <>
    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
  </>
));

export const UploadIcon = makeIcon("Upload", () => (
  <>
    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </>
));

export const EyeOffIcon = makeIcon("EyeOff", () => (
  <>
    <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </>
));

export const SquareIcon = makeIcon("Square", () => (
  <rect x="6" y="6" width="12" height="12" rx="2" />
));

export const MaximizeIcon = makeIcon("Maximize", () => (
  <>
    <path d="M8 3H5a2 2 0 00-2 2v3" />
    <path d="M16 3h3a2 2 0 012 2v3" />
    <path d="M8 21H5a2 2 0 01-2-2v-3" />
    <path d="M16 21h3a2 2 0 002-2v-3" />
  </>
));

export const MinimizeIcon = makeIcon("Minimize", () => (
  <>
    <path d="M3 8h2a3 3 0 003-3V3" />
    <path d="M21 8h-2a3 3 0 01-3-3V3" />
    <path d="M3 16h2a3 3 0 013 3v2" />
    <path d="M21 16h-2a3 3 0 00-3 3v2" />
  </>
));

export const CopyIcon = makeIcon("Copy", () => (
  <>
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
  </>
));

export const ThumbsUpIcon = makeIcon("ThumbsUp", () => (
  <>
    <path d="M7 10v12" />
    <path d="M15 5.88L14 10h5.83a2 2 0 011.92 2.56l-2.33 8A2 2 0 0117.5 22H4a2 2 0 01-2-2v-8a2 2 0 012-2h2.76a2 2 0 001.79-1.11L12 2h0a3.13 3.13 0 013 3.88z" />
  </>
));

export const ThumbsDownIcon = makeIcon("ThumbsDown", () => (
  <>
    <path d="M17 14V2" />
    <path d="M9 18.12L10 14H4.17a2 2 0 01-1.92-2.56l2.33-8A2 2 0 016.5 2H20a2 2 0 012 2v8a2 2 0 01-2 2h-2.76a2 2 0 00-1.79 1.11L12 22a3.13 3.13 0 01-3-3.88z" />
  </>
));

export const ThumbsUpFilledIcon: React.FC<IconProps> = ({ size = 16, className, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true" {...rest}>
    <path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z" />
  </svg>
);

export const ThumbsDownFilledIcon: React.FC<IconProps> = ({ size = 16, className, ...rest }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true" {...rest}>
    <path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2z" />
  </svg>
);

export interface AgentIconMeta {
  icon: React.FC<IconProps>;
  color: string;
  name_cn: string;
}

export const AGENT_PROFILES: Record<string, AgentIconMeta> = {
  chief_strategist: { icon: TargetIcon, color: "#60a5fa", name_cn: "首席策略" },
  quant_researcher: { icon: BarChart3Icon, color: "#34d399", name_cn: "量化分析" },
  market_intelligence: { icon: NewspaperIcon, color: "#fbbf24", name_cn: "市场情报" },
  trade_executor: { icon: TrendingUpIcon, color: "#f87171", name_cn: "交易执行" },
  portfolio_monitor: { icon: ShieldCheckIcon, color: "#a78bfa", name_cn: "持仓风控" },
  response_generator: { icon: FileTextIcon, color: "#22d3ee", name_cn: "综合分析" },
};

export function getAgentProfile(agent?: string): AgentIconMeta {
  if (agent && AGENT_PROFILES[agent]) return AGENT_PROFILES[agent];
  return { icon: BotIcon, color: "#94a3b8", name_cn: agent || "助手" };
}
