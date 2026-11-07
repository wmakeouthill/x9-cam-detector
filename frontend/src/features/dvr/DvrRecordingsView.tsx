import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Film,
  RefreshCw,
  Calendar,
  Clock,
  CloudUpload,
  Trash2,
  Play,
  Pause,
  Rewind,
  FastForward,
  SkipBack,
  SkipForward,
  Download,
  HardDrive,
  Cloud,
  Radio,
  ShieldCheck,
  AlertTriangle,
  FolderArchive,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

export interface DvrSegment {
  id: number;
  filename: string;
  start_time: string;
  end_time?: string;
  duration_seconds?: number;
  size_bytes?: number;
  drive_file_id?: string | null;
  gdrive_file_id?: string | null;
  status?: 'recording' | 'local' | 'synced' | string;
}

interface DvrStatus {
  enabled: boolean;
  recording: boolean;
  retention_days: number;
  segment_seconds?: number;
  gdrive_folder_name: string;
  total_segments: number;
  current_segment?: string | null;
}

function formatBRDateTime(raw?: string): string {
  if (!raw) return 'Em andamento...';
  try {
    const parts = raw.split(/[- :T.]/);
    if (parts.length >= 6) {
      const [year, month, day, hour, min, sec] = parts;
      return `${day}/${month}/${year} ${hour}:${min}:${sec}`;
    }
    const d = new Date(raw);
    if (!isNaN(d.getTime())) {
      return d.toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });
    }
  } catch {
    // fallback
  }
  return raw;
}

function formatBRDateOnly(rawDate: string): string {
  const parts = rawDate.split('-');
  if (parts.length === 3) {
    return `${parts[2]}/${parts[1]}/${parts[0]}`;
  }
  return rawDate;
}

function formatDurationLabel(seconds?: number, status?: string): string {
  if (status === 'recording') return 'Gravando agora';
  if (!seconds || seconds <= 0) return '1 min';
  if (seconds >= 60) {
    const mins = Math.round(seconds / 60);
    return `${mins} min`;
  }
  return `${Math.round(seconds)}s`;
}

export function DvrRecordingsView() {
  const { token } = useAuthStore();

  const [dvrStatus, setDvrStatus] = useState<DvrStatus>({
    enabled: true,
    recording: false,
    retention_days: 15,
    gdrive_folder_name: 'Gravacoes_24h',
    total_segments: 0,
    current_segment: null,
  });

  const [segments, setSegments] = useState<DvrSegment[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>('');
  const [selectedHour, setSelectedHour] = useState<string>('');
  const [offset, setOffset] = useState<number>(0);
  const LIMIT = 60;

  const [activeSegment, setActiveSegment] = useState<DvrSegment | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [toggling, setToggling] = useState<boolean>(false);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1);
  const [jumpTime, setJumpTime] = useState<string>('');

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const safeParseJson = async (res: Response) => {
    const text = await res.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch {
      return {};
    }
  };

  const handleSetSegmentDuration = async (seconds: number) => {
    if (!token) return;
    try {
      const res = await fetch('/api/camera/dvr/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ segment_seconds: seconds }),
      });
      if (res.ok) {
        const data = await safeParseJson(res);
        setDvrStatus((prev) => ({ ...prev, segment_seconds: data.segment_duration || seconds }));
        setFeedback({ text: `Tamanho dos blocos do DVR alterado para ${Math.round(seconds / 60)} min.`, type: 'success' });
      }
    } catch {
      setFeedback({ text: 'Erro ao alterar tamanho de bloco.', type: 'error' });
    }
  };

  const handleJumpToTime = async () => {
    if (!jumpTime || !token) return;
    const targetDate = selectedDate || new Date().toISOString().split('T')[0];
    const targetTimestamp = `${targetDate} ${jumpTime}:00`;
    try {
      const res = await fetch(`/api/camera/dvr/seek?target_time=${encodeURIComponent(targetTimestamp)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await safeParseJson(res);
      if (res.ok && data.found && data.segment) {
        setActiveSegment(data.segment);
        const offset = data.seek_offset || 0;
        setTimeout(() => {
          if (videoRef.current) {
            videoRef.current.currentTime = offset;
            videoRef.current.play().catch(() => {});
          }
        }, 200);
        setFeedback({ text: `Carregado: bloco de ${data.segment.start_time} no horário ${jumpTime}.`, type: 'success' });
      } else {
        setFeedback({ text: data.message || `Nenhum bloco encontrado próximo das ${jumpTime}.`, type: 'error' });
      }
    } catch {
      setFeedback({ text: 'Erro ao buscar gravação pelo horário informado.', type: 'error' });
    }
  };

  const fetchDvrStatus = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch('/api/camera/dvr/status', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await safeParseJson(res);
        setDvrStatus({
          enabled: data.enabled ?? true,
          recording: Boolean(data.recording),
          retention_days: data.retention_days ?? 15,
          segment_seconds: data.segment_duration ?? data.segment_seconds ?? 60,
          gdrive_folder_name: data.gdrive_folder_name || 'Gravacoes_24h',
          total_segments: data.total_segments ?? 0,
          current_segment: data.current_segment || null,
        });
      }
    } catch {
      // ignora falha silenciosa de polling
    }
  }, [token]);

  const fetchSegments = useCallback(
    async (customOffset: number = offset, silent: boolean = false) => {
      if (!token) return;
      if (!silent) setLoading(true);
      try {
        const params = new URLSearchParams();
        if (selectedDate) params.set('date', selectedDate);
        if (selectedHour !== '') params.set('hour', selectedHour);
        params.set('limit', String(LIMIT));
        params.set('offset', String(customOffset));

        const res = await fetch(`/api/camera/dvr/segments?${params.toString()}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await safeParseJson(res);
          const items: DvrSegment[] = Array.isArray(data) ? data : data.items || [];
          const totalCount: number = typeof data.total === 'number' ? data.total : items.length;
          setSegments(items);
          setTotal(totalCount);
          if (Array.isArray(data.available_dates)) {
            setAvailableDates(data.available_dates);
          }

          // Seleciona automaticamente o bloco mais recente concluído (ou o primeiro disponível) se nenhum estiver ativo
          setActiveSegment((prev) => {
            if (prev && items.some((s) => s.id === prev.id)) {
              return items.find((s) => s.id === prev.id) || prev;
            }
            const firstReady = items.find((s) => s.status !== 'recording') || items[0] || null;
            return firstReady;
          });
        }
      } catch {
        if (!silent) {
          setFeedback({ text: 'Falha ao carregar blocos de gravação 24h.', type: 'error' });
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [token, selectedDate, selectedHour, offset]
  );

  useEffect(() => {
    fetchDvrStatus();
    fetchSegments(offset);
    const interval = setInterval(() => {
      fetchDvrStatus();
      fetchSegments(offset, true);
    }, 10000);
    return () => clearInterval(interval);
  }, [fetchDvrStatus, fetchSegments, offset]);

  const handleToggleDvr = async () => {
    if (!token || toggling) return;
    setToggling(true);
    setFeedback(null);
    try {
      const nextEnabled = !dvrStatus.enabled;
      const res = await fetch('/api/camera/dvr/toggle', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ enabled: nextEnabled }),
      });
      const data = await safeParseJson(res);
      if (res.ok) {
        setDvrStatus((prev) => ({
          ...prev,
          enabled: data.enabled ?? nextEnabled,
          recording: Boolean(data.recording ?? nextEnabled),
        }));
        setFeedback({
          text:
            (data.enabled ?? nextEnabled)
              ? 'Gravador 24h ativado! Blocos de 1 min serão enviados para Gravacoes_24h no Google Drive.'
              : 'Gravador 24h pausado.',
          type: 'success',
        });
        fetchDvrStatus();
        fetchSegments(offset);
      } else {
        throw new Error(data.detail || 'Falha ao alternar gravador 24h.');
      }
    } catch (err: any) {
      setFeedback({ text: err.message || 'Erro ao alternar gravação 24h.', type: 'error' });
    } finally {
      setToggling(false);
    }
  };

  const handleManualSync = async () => {
    if (!token || syncing) return;
    setSyncing(true);
    setFeedback(null);
    try {
      const res = await fetch('/api/camera/gdrive/sync', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await safeParseJson(res);
      if (res.ok && data.ok) {
        setFeedback({
          text: data.message || 'Sincronização com Google Drive (pasta Gravacoes_24h) iniciada!',
          type: 'success',
        });
        fetchSegments(offset);
      } else {
        setFeedback({
          text: data.message || 'Falha ao iniciar sincronização com Google Drive.',
          type: 'error',
        });
      }
    } catch {
      setFeedback({ text: 'Erro de comunicação ao sincronizar com Google Drive.', type: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  const handleDeleteSegment = async (seg: DvrSegment) => {
    if (!token || deletingId !== null) return;
    setDeletingId(seg.id);
    setFeedback(null);
    try {
      const res = await fetch(`/api/camera/dvr/segments/${seg.id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        setFeedback({
          text: `Bloco ${seg.filename} removido da nuvem.`,
          type: 'success',
        });
        if (activeSegment?.id === seg.id) {
          setActiveSegment(null);
        }
        fetchSegments(offset);
        fetchDvrStatus();
      } else {
        const data = await safeParseJson(res);
        throw new Error(data.detail || 'Não foi possível excluir este bloco.');
      }
    } catch (err: any) {
      setFeedback({ text: err.message || 'Erro ao excluir bloco de gravação.', type: 'error' });
    } finally {
      setDeletingId(null);
    }
  };

  // Controle de avanço/retrocesso em segundos no player principal
  const handleSeekPlayer = (deltaSeconds: number) => {
    if (!videoRef.current) return;
    const video = videoRef.current;
    const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : Infinity;
    const nextTime = Math.max(0, Math.min(duration, video.currentTime + deltaSeconds));
    video.currentTime = nextTime;
  };

  // Ordenação cronológica para achar Bloco Anterior e Próximo Bloco
  const chronologicalSegments = [...segments].sort((a, b) =>
    (a.start_time || '').localeCompare(b.start_time || '')
  );

  const activeChronoIndex = activeSegment
    ? chronologicalSegments.findIndex((s) => s.id === activeSegment.id)
    : -1;

  const previousChronoSegment =
    activeChronoIndex > 0 ? chronologicalSegments[activeChronoIndex - 1] : null;
  const nextChronoSegment =
    activeChronoIndex >= 0 && activeChronoIndex < chronologicalSegments.length - 1
      ? chronologicalSegments[activeChronoIndex + 1]
      : null;

  const handlePlayPreviousSegment = () => {
    if (previousChronoSegment) {
      setActiveSegment(previousChronoSegment);
    }
  };

  const handlePlayNextSegment = () => {
    if (nextChronoSegment) {
      setActiveSegment(nextChronoSegment);
    }
  };

  const hoursList = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));

  return (
    <div className="flex-1 p-3.5 sm:p-6 md:p-8 overflow-y-auto space-y-4 sm:space-y-6">
      {/* Cabeçalho NOC Gravações 24h */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-zinc-800/80 pb-4 sm:pb-5">
        <div className="flex items-start sm:items-center gap-3">
          <div className="w-10 h-10 sm:w-11 sm:h-11 rounded-xl bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.18)] shrink-0">
            <Film className="w-5 h-5 sm:w-6 sm:h-6" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg sm:text-xl font-bold font-['Chakra_Petch'] text-slate-100 uppercase tracking-wide">
                Gravações 24 Horas (DVR Nuvem)
              </h2>

              {dvrStatus.recording ? (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-red-950/80 text-red-300 border border-red-500/50 font-bold flex items-center gap-1.5 shadow-[0_0_12px_rgba(239,68,68,0.25)]">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-ping" />
                  GRAVANDO 24H
                </span>
              ) : (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700 font-bold">
                  PAUSADO
                </span>
              )}

              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-950/60 text-cyan-300 border border-cyan-500/30 font-semibold flex items-center gap-1">
                <FolderArchive className="w-3 h-3 text-cyan-400" />
                Drive: {dvrStatus.gdrive_folder_name} ({dvrStatus.retention_days} dias)
              </span>

              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/60 text-emerald-300 border border-emerald-500/30 font-semibold flex items-center gap-1">
                <HardDrive className="w-3 h-3 text-emerald-400" />
                {dvrStatus.total_segments || total} blocos
              </span>
            </div>
            <p className="text-[11px] sm:text-xs text-slate-400 font-mono mt-0.5">
              Gravação contínua 24h em blocos de {Math.round((dvrStatus.segment_seconds || 900) / 60)} min com áudio limpo e marcações IA • Retenção automática de{' '}
              {dvrStatus.retention_days} dias na pasta <b className="text-cyan-300">{dvrStatus.gdrive_folder_name}</b>
            </p>
          </div>
        </div>

        {/* Seletor de Tamanho de Bloco do DVR 24h */}
        <div className="flex items-center gap-2 pt-2 border-t border-slate-800/80 flex-wrap">
          <span className="text-[11px] font-mono text-slate-400 flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-cyan-400" /> Tamanho do Bloco 24h:
          </span>
          {[
            { label: '15 min (Padrão)', val: 900 },
            { label: '10 min', val: 600 },
            { label: '5 min', val: 300 },
            { label: '1 min', val: 60 },
          ].map((b) => (
            <button
              key={b.val}
              onClick={() => handleSetSegmentDuration(b.val)}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold border transition-all cursor-pointer ${
                (dvrStatus.segment_seconds || 900) === b.val
                  ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/60 shadow-[0_0_10px_rgba(6,182,212,0.2)]'
                  : 'bg-[#141A26] border-slate-800 text-slate-400 hover:border-slate-700 hover:text-slate-200'
              }`}
            >
              {b.label}
            </button>
          ))}
        </div>

        {/* Botões de Controle do Gravador 24h */}
        <div className="flex items-center gap-2 sm:gap-2.5 flex-wrap">
          <button
            onClick={handleToggleDvr}
            disabled={toggling}
            className={`flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-lg border text-xs font-mono font-bold uppercase tracking-wider transition-all cursor-pointer disabled:opacity-50 ${
              dvrStatus.enabled
                ? 'bg-amber-950/50 border-amber-500/50 hover:border-amber-400 text-amber-300'
                : 'bg-emerald-950/60 border-emerald-500/50 hover:border-emerald-400 text-emerald-300'
            }`}
          >
            {dvrStatus.enabled ? (
              <>
                <Pause className="w-3.5 h-3.5 text-amber-400" />
                <span>{toggling ? 'Alterando...' : 'Pausar 24h'}</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 text-emerald-400" />
                <span>{toggling ? 'Ativando...' : 'Ativar Gravação 24h'}</span>
              </>
            )}
          </button>

          <button
            onClick={() => {
              fetchDvrStatus();
              fetchSegments(offset);
            }}
            disabled={loading}
            className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 text-xs font-mono text-slate-300 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Atualizar</span>
          </button>

          <button
            onClick={handleManualSync}
            disabled={syncing || loading}
            className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-cyan-950/40 border border-cyan-500/40 hover:border-cyan-400 text-xs font-mono text-cyan-300 hover:text-white transition-all cursor-pointer disabled:opacity-50"
            title="Força sincronização imediata dos blocos 24h com o Google Drive"
          >
            <CloudUpload className={`w-3.5 h-3.5 text-cyan-400 ${syncing ? 'animate-bounce' : ''}`} />
            <span>{syncing ? 'Sincronizando...' : 'Sincronizar Drive'}</span>
          </button>
        </div>
      </div>

      {/* Feedback de Ação */}
      {feedback && (
        <div
          className={`p-3 rounded-xl border text-xs flex items-center justify-between font-mono ${
            feedback.type === 'success'
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
              : 'bg-red-500/10 border-red-500/30 text-red-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === 'success' ? (
              <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
            ) : (
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            )}
            <span>{feedback.text}</span>
          </div>
          <button onClick={() => setFeedback(null)} className="cursor-pointer text-slate-400 hover:text-white">
            ✕
          </button>
        </div>
      )}

      {/* Player Integrado com Controles de Avanço/Retrocesso e Auto-Play Contínuo */}
      <div className="bg-[#0D111A] border border-cyan-500/30 rounded-2xl p-3.5 sm:p-5 shadow-[0_0_35px_rgba(0,0,0,0.6)] space-y-3.5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-zinc-800/80 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-cyan-500/15 border border-cyan-500/30 flex items-center justify-center text-cyan-400">
              <Play className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold font-['Chakra_Petch'] text-slate-100 uppercase tracking-wide flex items-center gap-2">
                <span>Reprodutor Contínuo DVR 24h</span>
                {activeSegment && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/70 text-emerald-300 border border-emerald-500/30">
                    AUTO-PLAY CONTÍNUO ATIVO
                  </span>
                )}
              </h3>
              <p className="text-[11px] text-slate-400 font-mono">
                {activeSegment
                  ? `Reproduzindo: ${formatBRDateTime(activeSegment.start_time)} → ${formatBRDateTime(
                      activeSegment.end_time
                    )} (${activeSegment.filename})`
                  : 'Selecione qualquer bloco abaixo para iniciar a reprodução com áudio'}
              </p>
            </div>
          </div>

          {activeSegment && (
            <a
              href={`/api/camera/dvr/video/${activeSegment.filename}?token=${token}`}
              target="_blank"
              rel="noreferrer"
              download
              className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-950/50 border border-emerald-500/40 hover:border-emerald-400 text-emerald-300 text-xs font-mono font-semibold transition-all"
            >
              <Download className="w-3.5 h-3.5 text-emerald-400" />
              <span>Baixar MP4</span>
            </a>
          )}
        </div>

        {activeSegment ? (
          <>
            <div className="w-full aspect-video max-h-[58vh] bg-black rounded-xl overflow-hidden border border-zinc-800 flex items-center justify-center">
              <video
                ref={videoRef}
                key={activeSegment.filename}
                src={`/api/camera/dvr/video/${activeSegment.filename}?token=${token}`}
                controls
                autoPlay
                onEnded={handlePlayNextSegment}
                className="w-full h-full object-contain"
              />
            </div>

            {/* Barra de Controles Rápidos do Player DVR */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 pt-2 border-t border-zinc-800/80">
              <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                <button
                  onClick={handlePlayPreviousSegment}
                  disabled={!previousChronoSegment}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#141A26] hover:bg-slate-800 border border-slate-700 hover:border-cyan-500/40 text-slate-200 text-xs font-mono font-semibold transition-all cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed"
                  title="Ir para o bloco anterior"
                >
                  <SkipBack className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Anterior</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(-300)}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#141A26] hover:bg-cyan-950/50 border border-cyan-500/30 hover:border-cyan-400 text-cyan-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Voltar 5 minutos"
                >
                  <Rewind className="w-3.5 h-3.5 text-cyan-400" />
                  <span>-5m</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(-60)}
                  className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-[#141A26] hover:bg-cyan-950/50 border border-cyan-500/30 hover:border-cyan-400 text-cyan-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Voltar 1 minuto"
                >
                  <span>-1m</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(-15)}
                  className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-[#141A26] hover:bg-cyan-950/50 border border-cyan-500/30 hover:border-cyan-400 text-cyan-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Voltar 15 segundos"
                >
                  <span>-15s</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(15)}
                  className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-[#141A26] hover:bg-emerald-950/50 border border-emerald-500/30 hover:border-emerald-400 text-emerald-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Avançar 15 segundos"
                >
                  <span>+15s</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(60)}
                  className="flex items-center gap-1 px-2 py-1.5 rounded-lg bg-[#141A26] hover:bg-emerald-950/50 border border-emerald-500/30 hover:border-emerald-400 text-emerald-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Avançar 1 minuto"
                >
                  <span>+1m</span>
                </button>

                <button
                  onClick={() => handleSeekPlayer(300)}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#141A26] hover:bg-emerald-950/50 border border-emerald-500/30 hover:border-emerald-400 text-emerald-300 text-xs font-mono font-bold transition-all cursor-pointer"
                  title="Avançar 5 minutos"
                >
                  <FastForward className="w-3.5 h-3.5 text-emerald-400" />
                  <span>+5m</span>
                </button>

                <button
                  onClick={handlePlayNextSegment}
                  disabled={!nextChronoSegment}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#141A26] hover:bg-slate-800 border border-slate-700 hover:border-emerald-500/40 text-slate-200 text-xs font-mono font-semibold transition-all cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed"
                  title="Avançar para o próximo bloco cronológico"
                >
                  <span>Próximo</span>
                  <SkipForward className="w-3.5 h-3.5 text-emerald-400" />
                </button>
              </div>

              {/* Seletor de Velocidade & Pulo Rápido por Horário */}
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono text-zinc-400">Velocidade:</span>
                {[1, 1.5, 2, 4].map((spd) => (
                  <button
                    key={spd}
                    onClick={() => {
                      setPlaybackSpeed(spd);
                      if (videoRef.current) videoRef.current.playbackRate = spd;
                    }}
                    className={`px-2 py-0.5 rounded text-xs font-mono font-bold border transition-all cursor-pointer ${
                      playbackSpeed === spd
                        ? 'bg-amber-500/20 text-amber-300 border-amber-500/60 shadow-[0_0_8px_rgba(245,158,11,0.2)]'
                        : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:text-zinc-200'
                    }`}
                  >
                    {spd}x
                  </button>
                ))}

                <div className="flex items-center gap-1.5 pl-2 border-l border-zinc-700">
                  <span className="text-[10px] font-mono text-zinc-400">Ir para:</span>
                  <input
                    type="time"
                    value={jumpTime}
                    onChange={(e) => setJumpTime(e.target.value)}
                    className="bg-[#0E131C] border border-zinc-700 rounded px-1.5 py-0.5 text-xs text-zinc-100 font-mono focus:border-cyan-500 focus:outline-none"
                  />
                  <button
                    onClick={handleJumpToTime}
                    disabled={!jumpTime}
                    className="px-2.5 py-0.5 rounded text-xs font-mono bg-cyan-500 hover:bg-cyan-400 text-black font-bold disabled:opacity-40 cursor-pointer"
                  >
                    Buscar
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl bg-[#090C12]">
            <Film className="w-10 h-10 text-slate-600 mx-auto mb-2" />
            <p className="text-xs font-mono text-slate-400">
              Nenhum bloco selecionado ou o primeiro segmento de 5 minutos ainda está sendo gravado.
            </p>
          </div>
        )}
      </div>

      {/* Filtros de Data (Últimos 15 dias) e Hora (00h–23h) */}
      <div className="bg-[#0D111A] border border-slate-800/90 rounded-2xl p-4 space-y-4">
        {/* Filtro de Datas */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs font-mono text-slate-300 font-semibold">
            <Calendar className="w-4 h-4 text-cyan-400" />
            <span>FILTRAR POR DATA (JANELA DE {dvrStatus.retention_days} DIAS):</span>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              onClick={() => {
                setSelectedDate('');
                setOffset(0);
              }}
              className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer border ${
                selectedDate === ''
                  ? 'bg-cyan-500/20 border-cyan-400 text-cyan-300 shadow-[0_0_10px_rgba(6,182,212,0.2)]'
                  : 'bg-[#141A26] border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              Todas as Datas
            </button>

            {availableDates.map((d) => (
              <button
                key={d}
                onClick={() => {
                  setSelectedDate(d);
                  setOffset(0);
                }}
                className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer border ${
                  selectedDate === d
                    ? 'bg-cyan-500/20 border-cyan-400 text-cyan-300 shadow-[0_0_10px_rgba(6,182,212,0.2)]'
                    : 'bg-[#141A26] border-slate-700 text-slate-400 hover:text-slate-200'
                }`}
              >
                {formatBRDateOnly(d)}
              </button>
            ))}
          </div>
        </div>

        {/* Filtro de Horas (00h a 23h) */}
        <div className="space-y-2 pt-2 border-t border-zinc-800/70">
          <div className="flex items-center gap-2 text-xs font-mono text-slate-300 font-semibold">
            <Clock className="w-4 h-4 text-emerald-400" />
            <span>FILTRAR POR HORA DO DIA (00h – 23h):</span>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              onClick={() => {
                setSelectedHour('');
                setOffset(0);
              }}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-mono font-bold transition-all cursor-pointer border ${
                selectedHour === ''
                  ? 'bg-emerald-500/20 border-emerald-400 text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                  : 'bg-[#141A26] border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >
              Todas as horas
            </button>

            {hoursList.map((hr) => (
              <button
                key={hr}
                onClick={() => {
                  setSelectedHour(hr);
                  setOffset(0);
                }}
                className={`px-2 py-1 rounded-lg text-[11px] font-mono font-bold transition-all cursor-pointer border ${
                  selectedHour === hr
                    ? 'bg-emerald-500/20 border-emerald-400 text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                    : 'bg-[#141A26] border-slate-700 text-slate-400 hover:text-slate-200'
                }`}
              >
                {hr}h
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Grid de Blocos de Gravação 24h */}
      {segments.length === 0 ? (
        <div className="p-10 text-center border border-dashed border-slate-800 rounded-2xl bg-[#0B0E15]">
          <Film className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <p className="text-sm font-semibold text-slate-300 font-mono">
            Nenhum bloco de gravação 24h encontrado para este filtro
          </p>
          <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto font-mono">
            O gravador contínuo salva segmentos de 5 minutos com vídeo 1080p, áudio e detecções YOLO, mantendo os últimos{' '}
            {dvrStatus.retention_days} dias sincronizados na pasta {dvrStatus.gdrive_folder_name}.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
          {segments.map((seg) => {
            const isSelected = activeSegment?.id === seg.id;
            const sizeMb = seg.size_bytes ? (seg.size_bytes / (1024 * 1024)).toFixed(1) : '0.0';
            const isRecordingNow = seg.status === 'recording';
            const isSynced = seg.status === 'synced' || Boolean(seg.drive_file_id || seg.gdrive_file_id);

            return (
              <div
                key={seg.id}
                onClick={() => setActiveSegment(seg)}
                className={`bg-[#0D111A] border rounded-xl p-3.5 transition-all cursor-pointer flex flex-col justify-between gap-3 relative ${
                  isSelected
                    ? 'border-emerald-500/90 ring-1 ring-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.2)] bg-[#101824]'
                    : 'border-slate-800/90 hover:border-cyan-500/50 hover:bg-[#111724]'
                }`}
              >
                {/* Topo do Card: Status Badge + Botão Excluir */}
                <div className="flex items-center justify-between gap-2">
                  {isRecordingNow ? (
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-red-500/20 border border-red-500/50 text-red-300 text-[10px] font-mono font-bold animate-pulse">
                      <Radio className="w-3 h-3 text-red-400" />
                      GRAVANDO AGORA
                    </span>
                  ) : isSynced ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/40 text-emerald-300 text-[10px] font-mono font-bold">
                      <Cloud className="w-3 h-3 text-emerald-400" />
                      NUVEM DRIVE (Gravacoes_24h)
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300 text-[10px] font-mono font-bold">
                      <HardDrive className="w-3 h-3 text-cyan-400" />
                      DISCO LOCAL
                    </span>
                  )}

                  {!isRecordingNow && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteSegment(seg);
                      }}
                      disabled={deletingId === seg.id}
                      title="Excluir este bloco de gravação"
                      className="p-1.5 rounded-lg bg-black/60 hover:bg-red-500/20 text-zinc-400 hover:text-red-400 border border-zinc-800 hover:border-red-500/40 transition-colors cursor-pointer"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                {/* Horários de Início e Fim */}
                <div className="space-y-1 font-mono">
                  <div className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
                    <Play className={`w-3.5 h-3.5 shrink-0 ${isSelected ? 'text-emerald-400' : 'text-cyan-400'}`} />
                    <span>{formatBRDateTime(seg.start_time)}</span>
                  </div>
                  <div className="text-[11px] text-slate-400 pl-5">
                    Até: {seg.end_time ? formatBRDateTime(seg.end_time) : 'Gravando em tempo real...'}
                  </div>
                </div>

                {/* Rodapé com Duração, Tamanho em MB e Ação */}
                <div className="pt-2.5 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono text-slate-400">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded bg-[#141A26] border border-slate-800 text-cyan-300">
                      {formatDurationLabel(seg.duration_seconds, seg.status)}
                    </span>
                    <span>{sizeMb} MB</span>
                  </div>

                  <span className={`font-bold ${isSelected ? 'text-emerald-400' : 'text-cyan-400'}`}>
                    {isSelected ? '▶ Tocando' : 'Assistir'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
