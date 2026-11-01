import { useState, useEffect, useCallback } from 'react';
import {
  Image as ImageIcon,
  RefreshCw,
  Zap,
  Calendar,
  ExternalLink,
  Trash2,
  Check,
  CheckSquare,
  Square,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  HardDrive,
  X,
  ShieldCheck,
  Film,
  CloudUpload,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';

interface DetectionEvent {
  id: number;
  timestamp: string;
  event_type: string;
  confidence: number;
  flash_triggered: number;
  snapshot_path: string;
  video_path?: string;
}

const PAGE_SIZE = 12; // 3 linhas completas no grid desktop de 4 colunas (4 x 3 = 12 fotos)

function formatBRDateTime(raw?: string): string {
  if (!raw) return '—';
  try {
    // Converte timestamp SQLite 'YYYY-MM-DD HH:MM:SS' para formato brasileiro
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
    // fallback silencioso
  }
  return raw;
}

interface DeleteModalState {
  isOpen: boolean;
  type: 'single' | 'batch';
  targetId?: number;
  count?: number;
  filename?: string;
}

export function SnapshotsView() {
  const { token } = useAuthStore();
  const [events, setEvents] = useState<DetectionEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const [deleteModal, setDeleteModal] = useState<DeleteModalState>({
    isOpen: false,
    type: 'single',
  });

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const fetchEvents = useCallback(
    async (pageToFetch: number = page) => {
      if (!token) return;
      setLoading(true);
      try {
        const offset = (pageToFetch - 1) * PAGE_SIZE;
        const res = await fetch(`/api/camera/events?limit=${PAGE_SIZE}&offset=${offset}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          const items: DetectionEvent[] = Array.isArray(data) ? data : data.items || [];
          const totalCount: number = typeof data.total === 'number' ? data.total : items.length;
          setEvents(items);
          setTotal(totalCount);
        }
      } catch {
        setFeedback({ text: 'Falha ao carregar galeria de fotos.', type: 'error' });
      } finally {
        setLoading(false);
      }
    },
    [token, page]
  );

  useEffect(() => {
    fetchEvents(page);
  }, [fetchEvents, page]);

  // Alterna seleção individual
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Verifica se todos os itens da página atual estão selecionados
  const isAllCurrentPageSelected =
    events.length > 0 && events.every((item) => selectedIds.has(item.id));

  // Alterna seleção de todas as fotos da página atual
  const toggleSelectAllCurrentPage = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (isAllCurrentPageSelected) {
        events.forEach((item) => next.delete(item.id));
      } else {
        events.forEach((item) => next.add(item.id));
      }
      return next;
    });
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
  };

  // Força sincronização manual de todas as fotos e vídeos locais com o Google Drive
  const handleManualSync = async () => {
    if (!token || syncing) return;
    setSyncing(true);
    setFeedback(null);
    try {
      const res = await fetch('/api/camera/gdrive/sync', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setFeedback({
          text: data.message || 'Sincronização manual com o Google Drive iniciada!',
          type: 'success',
        });
        fetchEvents(page);
      } else {
        setFeedback({
          text: data.message || 'Falha ao acionar sincronização com o Google Drive.',
          type: 'error',
        });
      }
    } catch {
      setFeedback({ text: 'Erro de comunicação ao sincronizar com o Google Drive.', type: 'error' });
    } finally {
      setSyncing(false);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  // Executa exclusão confirmada (individual ou em lote)
  const handleConfirmDelete = async () => {
    if (!token) return;
    setDeleting(true);
    setFeedback(null);

    try {
      if (deleteModal.type === 'single' && deleteModal.targetId) {
        const res = await fetch(`/api/camera/events/${deleteModal.targetId}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error('Falha ao excluir foto.');

        setSelectedIds((prev) => {
          const next = new Set(prev);
          next.delete(deleteModal.targetId!);
          return next;
        });

        setFeedback({ text: 'Foto excluída com sucesso do disco e banco.', type: 'success' });
      } else if (deleteModal.type === 'batch') {
        const idsToDelete = Array.from(selectedIds);
        if (idsToDelete.length === 0) return;

        const res = await fetch('/api/camera/events/delete-batch', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ ids: idsToDelete }),
        });
        if (!res.ok) throw new Error('Falha ao excluir fotos em lote.');

        const data = await res.json();
        setSelectedIds(new Set());
        setFeedback({
          text: `${data.deleted_count || idsToDelete.length} foto(s) excluída(s) com sucesso!`,
          type: 'success',
        });
      }

      setDeleteModal({ isOpen: false, type: 'single' });

      // Se apagou itens e a página atual ficou vazia, volta 1 página se possível
      const remainingOnPage =
        deleteModal.type === 'single'
          ? events.length - 1
          : events.filter((e) => !selectedIds.has(e.id)).length;

      if (remainingOnPage <= 0 && page > 1) {
        setPage((p) => p - 1);
      } else {
        fetchEvents(page);
      }
    } catch (err: any) {
      setFeedback({ text: err.message || 'Erro ao realizar exclusão.', type: 'error' });
    } finally {
      setDeleting(false);
      setTimeout(() => setFeedback(null), 4000);
    }
  };

  // Gerador de páginas para a paginação
  const getPageNumbers = () => {
    const pages: (number | string)[] = [];
    if (totalPages <= 5) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (page > 3) pages.push('...');
      const start = Math.max(2, page - 1);
      const end = Math.min(totalPages - 1, page + 1);
      for (let i = start; i <= end; i++) pages.push(i);
      if (page < totalPages - 2) pages.push('...');
      pages.push(totalPages);
    }
    return pages;
  };

  return (
    <div className="flex-1 p-3.5 sm:p-6 md:p-8 overflow-y-auto space-y-4 sm:space-y-6">
      {/* Cabeçalho Principal da Galeria */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-zinc-800/80 pb-4 sm:pb-5">
        <div className="flex items-center gap-2.5 sm:gap-3">
          <div className="w-10 h-10 sm:w-11 sm:h-11 rounded-xl bg-cyan-600/20 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shadow-[0_0_20px_rgba(6,182,212,0.15)] shrink-0">
            <ImageIcon className="w-5 h-5 sm:w-6 sm:h-6" />
          </div>
          <div>
            <h2 className="text-lg sm:text-xl font-bold font-['Chakra_Petch'] text-slate-100 uppercase tracking-wide flex flex-wrap items-center gap-2">
              <span>Galeria de Disparos & Fotos</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/60 text-emerald-400 border border-emerald-500/30 font-semibold flex items-center gap-1">
                <HardDrive className="w-3 h-3" /> {total} SALVAS NO DISCO
              </span>
            </h2>
            <p className="text-[11px] sm:text-xs text-slate-400 font-mono">
              Capturas noturnas 1080p nativas • Paginado de 3 em 3 linhas ({PAGE_SIZE} fotos por página)
            </p>
          </div>
        </div>

        {/* Botões do Topo */}
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          {events.length > 0 && (
            <button
              onClick={toggleSelectAllCurrentPage}
              className={`flex items-center justify-center gap-1.5 px-3 py-2 sm:py-1.5 rounded-lg border text-xs font-mono transition-all cursor-pointer min-h-[40px] sm:min-h-0 ${
                isAllCurrentPageSelected
                  ? 'bg-emerald-950/60 border-emerald-500/50 text-emerald-300'
                  : 'bg-[#141A26] border-slate-700 hover:border-slate-500 text-slate-300'
              }`}
            >
              {isAllCurrentPageSelected ? (
                <CheckSquare className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Square className="w-3.5 h-3.5 text-slate-400" />
              )}
              <span>{isAllCurrentPageSelected ? 'Desmarcar Página' : 'Selecionar Todas'}</span>
            </button>
          )}

          <button
            onClick={() => fetchEvents(page)}
            disabled={loading}
            className="flex items-center justify-center gap-1.5 px-3 py-2 sm:py-1.5 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 text-xs text-slate-300 transition-colors cursor-pointer min-h-[40px] sm:min-h-0 disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Atualizar</span>
          </button>

          <button
            onClick={handleManualSync}
            disabled={syncing || loading}
            title="Varre todas as fotos e vídeos locais órfãos e sincroniza com o Google Drive"
            className="flex items-center justify-center gap-1.5 px-3 py-2 sm:py-1.5 rounded-lg bg-cyan-950/40 border border-cyan-500/40 hover:border-cyan-400 text-xs text-cyan-300 hover:text-white transition-all cursor-pointer min-h-[40px] sm:min-h-0 disabled:opacity-50"
          >
            <CloudUpload className={`w-3.5 h-3.5 ${syncing ? 'animate-bounce text-cyan-400' : 'text-cyan-400'}`} />
            <span>{syncing ? 'Sincronizando...' : 'Sincronizar Drive'}</span>
          </button>
        </div>
      </div>

      {/* Alerta de Feedback (Sucesso / Erro) */}
      {feedback && (
        <div
          className={`p-3 rounded-xl border text-xs flex items-center justify-between font-mono animate-fade-in ${
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

      {/* Barra de Ações em Lote Flutuante / Fixa quando há itens selecionados */}
      {selectedIds.size > 0 && (
        <div className="sticky top-2 z-20 p-3 sm:p-3.5 rounded-xl bg-[#0E131E]/95 backdrop-blur-md border border-emerald-500/50 shadow-[0_0_25px_rgba(16,185,129,0.2)] flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 font-mono text-xs font-bold">
              {selectedIds.size}
            </div>
            <span className="text-xs font-semibold text-slate-200 font-mono">
              {selectedIds.size} {selectedIds.size === 1 ? 'foto selecionada' : 'fotos selecionadas'}
            </span>
            <button
              onClick={clearSelection}
              className="text-[11px] text-zinc-400 hover:text-zinc-200 underline font-mono ml-2 cursor-pointer"
            >
              Desmarcar todas
            </button>
          </div>

          <button
            onClick={() =>
              setDeleteModal({
                isOpen: true,
                type: 'batch',
                count: selectedIds.size,
              })
            }
            className="w-full sm:w-auto flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white font-bold font-mono text-xs uppercase tracking-wider transition-all cursor-pointer shadow-lg shadow-red-600/30 min-h-[42px] sm:min-h-0"
          >
            <Trash2 className="w-4 h-4" />
            <span>Excluir Selecionadas ({selectedIds.size})</span>
          </button>
        </div>
      )}

      {/* Grid de Fotos ou Estado Vazio */}
      {events.length === 0 ? (
        <div className="p-8 sm:p-14 text-center border border-dashed border-slate-800 rounded-2xl bg-[#0B0E15]">
          <ImageIcon className="w-12 h-12 text-slate-600 mx-auto mb-3" />
          <p className="text-sm font-semibold text-slate-300 font-mono">Nenhum disparo noturno registrado ainda</p>
          <p className="text-[11px] sm:text-xs text-slate-500 mt-1 max-w-sm mx-auto">
            Assim que uma pessoa for detectada na janela da madrugada configurada, a foto tirada com flash aparecerá aqui em tempo real.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 sm:gap-4">
          {events.map((evt) => {
            const hasSnapshot = Boolean(evt.snapshot_path);
            const imageUrl = hasSnapshot ? `/api/camera/snapshots/${evt.snapshot_path}?token=${token}` : null;
            const isSelected = selectedIds.has(evt.id);

            return (
              <div
                key={evt.id}
                className={`bg-[#0D111A] border rounded-xl overflow-hidden shadow-lg transition-all flex flex-col group relative ${
                  isSelected
                    ? 'border-emerald-500/80 shadow-[0_0_15px_rgba(16,185,129,0.25)] ring-1 ring-emerald-500/50'
                    : 'border-slate-800/90 hover:border-cyan-500/50'
                }`}
              >
                {/* Checkbox de Seleção no Canto Superior Esquerdo */}
                <div
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleSelect(evt.id);
                  }}
                  title={isSelected ? 'Desmarcar' : 'Selecionar para apagar'}
                  className={`absolute top-2 left-2 z-10 w-7 h-7 rounded-lg flex items-center justify-center cursor-pointer transition-all backdrop-blur-md ${
                    isSelected
                      ? 'bg-emerald-500 text-black shadow-[0_0_10px_#10b981]'
                      : 'bg-black/75 border border-zinc-600 text-transparent hover:border-emerald-400 hover:text-emerald-400/50'
                  }`}
                >
                  <Check className="w-4 h-4 stroke-[3]" />
                </div>

                {/* Badges de Flash / Vídeo e Botão Lixeira no Canto Superior Direito */}
                <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5">
                  {evt.flash_triggered ? (
                    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-[10px] font-mono text-amber-300 backdrop-blur-md">
                      <Zap className="w-3 h-3 text-amber-400" />
                      <span className="hidden xs:inline">FLASH</span>
                    </div>
                  ) : null}

                  {evt.video_path ? (
                    <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-[10px] font-mono text-emerald-300 backdrop-blur-md">
                      <Film className="w-3 h-3 text-emerald-400" />
                      <span className="hidden xs:inline">VÍDEO</span>
                    </div>
                  ) : null}

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeleteModal({
                        isOpen: true,
                        type: 'single',
                        targetId: evt.id,
                        filename: evt.snapshot_path || evt.video_path,
                      });
                    }}
                    title="Excluir este registro"
                    className="w-7 h-7 rounded-lg bg-black/80 hover:bg-red-500/20 text-zinc-400 hover:text-red-400 border border-zinc-700/80 hover:border-red-500/50 flex items-center justify-center transition-all cursor-pointer backdrop-blur-md"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Área de Mídia (Foto ou Vídeo Playback) */}
                {hasSnapshot && imageUrl ? (
                  <div
                    className="aspect-video relative overflow-hidden bg-black/60 cursor-pointer"
                    onClick={() => setSelectedImage(imageUrl)}
                  >
                    <img
                      src={imageUrl}
                      alt={`Detecção ${evt.id}`}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      loading="lazy"
                    />

                    {/* Badge de Data/Hora no Rodapé da Imagem */}
                    <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded bg-black/75 backdrop-blur-md text-[10px] font-mono border border-slate-700/80 text-zinc-300">
                      <Calendar className="w-3 h-3 text-cyan-400" />
                      <span>{formatBRDateTime(evt.timestamp)}</span>
                    </div>
                  </div>
                ) : evt.video_path ? (
                  <div
                    className="aspect-video relative overflow-hidden bg-black/80 flex flex-col items-center justify-center cursor-pointer group hover:bg-black/60 transition-colors"
                    onClick={() => setSelectedVideo(evt.video_path!)}
                  >
                    <div className="w-12 h-12 rounded-full bg-emerald-500/20 border border-emerald-500/50 flex items-center justify-center text-emerald-400 group-hover:scale-110 group-hover:bg-emerald-500/30 transition-all shadow-[0_0_20px_rgba(16,185,129,0.3)]">
                      <Film className="w-6 h-6 ml-0.5 text-emerald-400" />
                    </div>
                    <span className="text-[11px] font-mono text-emerald-400 mt-2 font-semibold">
                      Assistir Clipe MP4
                    </span>

                    {/* Badge de Data/Hora */}
                    <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded bg-black/75 backdrop-blur-md text-[10px] font-mono border border-slate-700/80 text-zinc-300">
                      <Calendar className="w-3 h-3 text-cyan-400" />
                      <span>{formatBRDateTime(evt.timestamp)}</span>
                    </div>
                  </div>
                ) : (
                  <div className="aspect-video bg-black/60 flex items-center justify-center text-slate-500 font-mono text-xs">
                    Sem mídia
                  </div>
                )}

                {/* Rodapé do Card com Confiança, Botão de Vídeo e Link de Foto */}
                <div className="p-2.5 sm:p-3 flex items-center justify-between text-xs border-t border-slate-800/80 bg-[#101522]">
                  <span className="text-slate-400 font-mono text-[11px]">
                    IA: <b className="text-cyan-400">{Math.round(evt.confidence * 100)}%</b>
                  </span>
                  <div className="flex items-center gap-1.5">
                    {evt.video_path && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedVideo(evt.video_path!);
                        }}
                        className="text-emerald-400 hover:text-emerald-300 flex items-center gap-1 text-[11px] font-mono px-2 py-1 rounded bg-emerald-950/40 border border-emerald-500/30 hover:bg-emerald-950/70 transition-colors cursor-pointer"
                        title="Assistir clipe de vídeo MP4 com áudio sincronizado no player"
                      >
                        <Film className="w-3.5 h-3.5 text-emerald-400" />
                        <span className="font-semibold">Assistir Vídeo</span>
                      </button>
                    )}
                    {hasSnapshot && imageUrl && (
                      <a
                        href={imageUrl}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-cyan-400 hover:text-cyan-300 flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded hover:bg-cyan-950/40 transition-colors"
                        title="Abrir foto original em alta resolução"
                      >
                        <span>Foto</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Barra de Paginação (3 Linhas por Página = 12 Fotos) */}
      {total > 0 && (
        <div className="pt-4 border-t border-zinc-800 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs font-mono">
          <div className="text-zinc-400 text-center sm:text-left">
            Mostrando <span className="text-zinc-200 font-semibold">{Math.min((page - 1) * PAGE_SIZE + 1, total)}</span>–
            <span className="text-zinc-200 font-semibold">{Math.min(page * PAGE_SIZE, total)}</span> de{' '}
            <span className="text-emerald-400 font-semibold">{total}</span> capturas (Pág {page}/{totalPages})
          </div>

          <div className="flex items-center gap-1 sm:gap-1.5 flex-wrap justify-center">
            {/* Primeira Página */}
            <button
              onClick={() => setPage(1)}
              disabled={page === 1}
              title="Primeira página"
              className="p-2 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed text-slate-300 cursor-pointer"
            >
              <ChevronsLeft className="w-4 h-4" />
            </button>

            {/* Página Anterior */}
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              title="Página anterior"
              className="px-2.5 py-2 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed text-slate-300 flex items-center gap-1 cursor-pointer"
            >
              <ChevronLeft className="w-4 h-4" />
              <span className="hidden sm:inline">Anterior</span>
            </button>

            {/* Botões Numéricos de Página */}
            <div className="flex items-center gap-1">
              {getPageNumbers().map((item, idx) => {
                if (item === '...') {
                  return (
                    <span key={`dots-${idx}`} className="px-2 text-zinc-500">
                      ...
                    </span>
                  );
                }
                const isCurrent = item === page;
                return (
                  <button
                    key={item}
                    onClick={() => setPage(Number(item))}
                    className={`min-w-[34px] h-[34px] px-2 rounded-lg font-mono text-xs font-bold transition-all cursor-pointer ${
                      isCurrent
                        ? 'bg-emerald-500 text-black shadow-[0_0_10px_rgba(16,185,129,0.3)]'
                        : 'bg-[#141A26] border border-slate-700 text-slate-300 hover:border-slate-500'
                    }`}
                  >
                    {item}
                  </button>
                );
              })}
            </div>

            {/* Próxima Página */}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              title="Próxima página"
              className="px-2.5 py-2 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed text-slate-300 flex items-center gap-1 cursor-pointer"
            >
              <span className="hidden sm:inline">Próxima</span>
              <ChevronRight className="w-4 h-4" />
            </button>

            {/* Última Página */}
            <button
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages}
              title="Última página"
              className="p-2 rounded-lg bg-[#141A26] border border-slate-700 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed text-slate-300 cursor-pointer"
            >
              <ChevronsRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Modal de Confirmação de Exclusão (Estilo NOC) */}
      {deleteModal.isOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => !deleting && setDeleteModal({ isOpen: false, type: 'single' })}
        >
          <div
            className="w-full max-w-md bg-[#0D111A] border border-red-500/40 rounded-2xl p-6 shadow-[0_0_50px_rgba(239,68,68,0.2)] space-y-5 animate-scale-up"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-xl bg-red-500/10 border border-red-500/30 flex items-center justify-center text-red-400 shrink-0 shadow-[0_0_15px_rgba(239,68,68,0.2)]">
                <AlertTriangle className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-bold font-['Chakra_Petch'] text-zinc-100 uppercase tracking-wide">
                  Confirmar Exclusão
                </h3>
                <p className="text-xs text-red-400 font-mono">Ação destrutiva e irreversível</p>
              </div>
            </div>

            <p className="text-xs text-zinc-300 font-mono leading-relaxed">
              {deleteModal.type === 'single' ? (
                <>
                  Deseja excluir permanentemente a foto selecionada? O arquivo de imagem será apagado do disco da VPS e o registro removido do SQLite.
                </>
              ) : (
                <>
                  Deseja realmente apagar permanentemente as{' '}
                  <span className="text-red-400 font-bold">{deleteModal.count} fotos selecionadas</span> do disco e do banco de dados?
                </>
              )}
            </p>

            <div className="flex items-center justify-end gap-3 pt-3 border-t border-zinc-800">
              <button
                type="button"
                disabled={deleting}
                onClick={() => setDeleteModal({ isOpen: false, type: 'single' })}
                className="px-4 py-2.5 rounded-lg bg-zinc-800/80 hover:bg-zinc-700 text-xs font-mono text-zinc-300 transition-colors cursor-pointer disabled:opacity-50"
              >
                Cancelar
              </button>

              <button
                type="button"
                disabled={deleting}
                onClick={handleConfirmDelete}
                className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white font-bold font-mono text-xs uppercase tracking-wider transition-all cursor-pointer shadow-lg shadow-red-600/30 disabled:opacity-50"
              >
                {deleting ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Excluindo...</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Confirmar Exclusão</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de Zoom da Foto Original em Alta Resolução */}
      {selectedImage && (
        <div
          className="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm flex items-center justify-center p-3 sm:p-4 cursor-pointer"
          onClick={() => setSelectedImage(null)}
        >
          <div className="max-w-5xl max-h-[90vh] relative w-full flex flex-col items-center">
            <button
              onClick={() => setSelectedImage(null)}
              className="self-end mb-2 p-2 rounded-lg bg-zinc-900/90 border border-zinc-700 text-zinc-300 hover:text-white cursor-pointer"
              title="Fechar"
            >
              <X className="w-5 h-5" />
            </button>
            <img
              src={selectedImage}
              alt="Foto ampliada em 1080p"
              className="max-w-full max-h-[80vh] rounded-xl border border-cyan-500/40 object-contain shadow-2xl"
            />
            <p className="text-center text-[11px] sm:text-xs text-slate-400 mt-2 font-mono">
              Clique em qualquer lugar para fechar
            </p>
          </div>
        </div>
      )}

      {/* Modal do Player de Vídeo MP4 (Com Áudio Sincronizado) */}
      {selectedVideo && (
        <div
          className="fixed inset-0 z-50 bg-black/90 backdrop-blur-md flex items-center justify-center p-3 sm:p-4"
          onClick={() => setSelectedVideo(null)}
        >
          <div
            className="w-full max-w-4xl bg-[#0D111A] border border-emerald-500/40 rounded-2xl p-4 sm:p-5 shadow-[0_0_50px_rgba(16,185,129,0.2)] flex flex-col space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-3 border-b border-zinc-800">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400">
                  <Film className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-sm font-bold font-['Chakra_Petch'] text-zinc-100 uppercase tracking-wide">
                    Gravação de Vídeo MP4
                  </h3>
                  <p className="text-[10px] text-emerald-400 font-mono">Alta definição 1080p • Áudio ambiente sincronizado</p>
                </div>
              </div>

              <button
                onClick={() => setSelectedVideo(null)}
                className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white cursor-pointer"
                title="Fechar reprodutor"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="w-full aspect-video bg-black rounded-xl overflow-hidden flex items-center justify-center border border-zinc-800">
              <video
                src={`/api/camera/recordings/${selectedVideo}?token=${token}`}
                controls
                autoPlay
                className="w-full h-full object-contain"
              />
            </div>

            <div className="flex items-center justify-between pt-2 border-t border-zinc-800/80 text-[11px] font-mono text-zinc-400">
              <span className="truncate">{selectedVideo}</span>
              <a
                href={`/api/camera/recordings/${selectedVideo}?token=${token}`}
                target="_blank"
                rel="noreferrer"
                download
                className="text-emerald-400 hover:text-emerald-300 flex items-center gap-1 hover:underline ml-2 shrink-0"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                <span>Baixar Clipe MP4</span>
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
