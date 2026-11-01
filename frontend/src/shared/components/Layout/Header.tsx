import { useState, useEffect } from 'react';
import { ShieldAlert, Wifi, WifiOff, Clock, LogOut, Terminal } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface HeaderProps {
  status: string;
  fps: number;
  resolution: string;
  cameraIp: string;
}

export function Header({ status, fps, resolution, cameraIp }: HeaderProps) {
  const { user, logout } = useAuthStore();
  const [time, setTime] = useState(
    new Date().toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo' })
  );

  useEffect(() => {
    const timer = setInterval(() => {
      setTime(new Date().toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo' }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const isOnline = status === 'ONLINE';

  return (
    <header className="h-14 sm:h-16 bg-[#080B10] border-b border-zinc-800/80 px-3 sm:px-6 flex items-center justify-between shrink-0 select-none">
      {/* Esquerda: Identificação do Sistema & Logo */}
      <div className="flex items-center gap-2.5 sm:gap-3">
        <div className="w-8 h-8 sm:w-10 sm:h-10 rounded-lg bg-black border border-emerald-500/40 overflow-hidden flex items-center justify-center shadow-[0_0_15px_rgba(16,185,129,0.25)] relative group shrink-0">
          <img src="/logo.png" alt="X9 Logo" className="w-full h-full object-cover" />
          <div className="absolute inset-0 bg-emerald-500/10 pointer-events-none" />
        </div>
        <div>
          <div className="flex items-center gap-1.5 sm:gap-2">
            <span className="font-['Chakra_Petch'] text-sm sm:text-base font-bold tracking-wider text-zinc-100 uppercase flex items-center gap-1.5">
              X9 Monitor
            </span>
            <span className="text-[9px] sm:text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-500/30 text-emerald-400 font-semibold">
              VIP 1230 FC+
            </span>
            {/* Status Compacto Mobile */}
            <span className="md:hidden flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded bg-zinc-900/90 border border-zinc-800">
              <span className={`w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-emerald-500 shadow-[0_0_6px_#10b981] animate-pulse' : 'bg-amber-500'}`} />
              <span className={isOnline ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>
                {isOnline ? `${fps} FPS` : status}
              </span>
            </span>
            <span className="hidden sm:inline-block text-[9px] font-mono px-1 py-0.2 rounded bg-zinc-800/80 text-zinc-400 border border-zinc-700/50">
              SYS::NOC
            </span>
          </div>
          <p className="text-[10px] sm:text-[11px] text-zinc-400 font-mono truncate max-w-[200px] sm:max-w-none">
            IP: {cameraIp} • {resolution}
          </p>
        </div>
      </div>

      {/* Centro: Indicadores de Status em Tempo Real */}
      <div className="hidden md:flex items-center gap-3">
        {/* Status da Câmera */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#0E131C] border border-zinc-800/80 font-mono">
          {isOnline ? (
            <>
              <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_#10b981] animate-pulse" />
              <span className="text-xs font-semibold text-emerald-400 tracking-wider flex items-center gap-1.5">
                <Wifi className="w-3.5 h-3.5" /> ONLINE // {fps} FPS
              </span>
            </>
          ) : (
            <>
              <span className="w-2 h-2 rounded-full bg-amber-500 shadow-[0_0_8px_#f59e0b]" />
              <span className="text-xs font-semibold text-amber-400 tracking-wider flex items-center gap-1.5">
                <WifiOff className="w-3.5 h-3.5" /> {status}
              </span>
            </>
          )}
        </div>

        {/* Status do Vigia Noturno */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#0E131C] border border-zinc-800/80 font-mono">
          <ShieldAlert className="w-4 h-4 text-emerald-400" />
          <span className="text-xs font-semibold text-zinc-300">
            SENTINEL: <span className="text-emerald-400 font-mono">00:00 - 06:00 [FLASH ON]</span>
          </span>
        </div>

        {/* Relógio Militar */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#0E131C] border border-zinc-800/80 font-mono text-xs text-zinc-300">
          <Clock className="w-3.5 h-3.5 text-zinc-500" />
          <span>{time}</span>
        </div>
      </div>

      {/* Direita: Usuário & Logout */}
      <div className="flex items-center gap-2 sm:gap-3">
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#0E131C] border border-zinc-800/80 font-mono">
          <div className="w-5 h-5 rounded bg-emerald-950/60 border border-emerald-500/40 flex items-center justify-center text-emerald-400">
            <Terminal className="w-3 h-3" />
          </div>
          <span className="text-xs font-medium text-zinc-200">{user?.username || 'admin'}</span>
        </div>

        <button
          onClick={logout}
          title="Desconectar da Sessão"
          className="p-2 sm:p-2 rounded-lg bg-[#0E131C] border border-zinc-800 hover:border-red-500/50 hover:bg-red-500/10 text-zinc-400 hover:text-red-400 transition-colors cursor-pointer"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
