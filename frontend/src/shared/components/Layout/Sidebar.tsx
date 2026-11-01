import { Video, Clock, Settings, Image as ImageIcon, Shield } from 'lucide-react';

export type NavTab = 'live' | 'rules' | 'config' | 'snapshots';

interface SidebarProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
}

export function Sidebar({ activeTab, onTabChange }: SidebarProps) {
  const navItems = [
    {
      id: 'live' as NavTab,
      label: 'Ao Vivo',
      shortLabel: 'Ao Vivo',
      icon: Video,
      badge: 'LIVE',
    },
    {
      id: 'rules' as NavTab,
      label: 'Vigia Noturno',
      shortLabel: 'Vigia',
      icon: Clock,
      badge: 'YOLO',
    },
    {
      id: 'snapshots' as NavTab,
      label: 'Fotos & Disparos',
      shortLabel: 'Fotos',
      icon: ImageIcon,
    },
    {
      id: 'config' as NavTab,
      label: 'Câmera & Rede',
      shortLabel: 'Config',
      icon: Settings,
    },
  ];

  return (
    <>
      {/* Desktop Sidebar (>= md) - Inalterado no Desktop */}
      <aside className="hidden md:flex md:w-64 md:h-full bg-[#080B10] border-r border-zinc-800/80 flex-col justify-between shrink-0 select-none overflow-hidden md:order-1">
        <div className="p-4 space-y-6">
          <div className="px-3 pt-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 font-mono">
              // Modulos do Sistema
            </span>
          </div>

          <nav className="space-y-1.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;

              return (
                <button
                  key={item.id}
                  onClick={() => onTabChange(item.id)}
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg text-xs font-semibold tracking-wide transition-all cursor-pointer ${
                    isActive
                      ? 'bg-gradient-to-r from-emerald-950/70 to-[#0e161c] border border-emerald-500/40 text-emerald-300 shadow-[0_0_12px_rgba(16,185,129,0.12)]'
                      : 'text-zinc-400 hover:text-zinc-200 hover:bg-[#0E131C] border border-transparent'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon className={`w-4 h-4 ${isActive ? 'text-emerald-400' : 'text-zinc-500'}`} />
                    <span className="font-sans font-medium">{item.label}</span>
                  </div>
                  {item.badge && (
                    <span
                      className={`text-[9px] font-mono px-1.5 py-0.5 rounded font-bold ${
                        isActive
                          ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                          : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Rodapé da Sidebar Desktop */}
        <div className="p-4 border-t border-zinc-800/80">
          <div className="p-3 rounded-lg bg-[#0C1017] border border-zinc-800/80 flex items-center gap-3">
            <Shield className="w-5 h-5 text-emerald-400 shrink-0" />
            <div className="text-[11px] leading-tight text-zinc-400">
              <p className="font-semibold text-zinc-200 font-mono text-xs">X9 Security Daemon</p>
              <p className="text-[10px] text-zinc-500 font-mono">v1.0 • YOLOv8 Standalone</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Mobile Bottom Navigation (< md) */}
      <nav className="md:hidden w-full bg-[#080B10]/95 backdrop-blur-lg border-t border-zinc-800/90 px-2 py-1.5 flex items-center justify-around shrink-0 select-none z-30 pb-[max(0.5rem,env(safe-area-inset-bottom))] order-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;

          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id)}
              className={`flex-1 flex flex-col items-center justify-center py-1.5 px-1 rounded-lg transition-all relative cursor-pointer min-h-[48px] ${
                isActive
                  ? 'text-emerald-400 font-semibold'
                  : 'text-zinc-400 hover:text-zinc-200 active:bg-zinc-900/40'
              }`}
            >
              <div className="relative flex items-center justify-center">
                <Icon
                  className={`w-5 h-5 transition-transform ${
                    isActive ? 'scale-110 text-emerald-400' : 'text-zinc-400'
                  }`}
                />
                {item.badge && (
                  <span className="absolute -top-1.5 -right-3 text-[8px] font-mono font-bold px-1 py-0.2 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 leading-none">
                    {item.badge}
                  </span>
                )}
              </div>
              <span
                className={`text-[10px] tracking-tight mt-1 transition-colors ${
                  isActive ? 'text-emerald-300 font-bold' : 'text-zinc-400'
                }`}
              >
                {item.shortLabel}
              </span>
              {isActive && (
                <span className="absolute bottom-0 w-8 h-0.5 bg-emerald-400 rounded-full shadow-[0_0_8px_#10b981]" />
              )}
            </button>
          );
        })}
      </nav>
    </>
  );
}
