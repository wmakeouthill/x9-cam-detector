import { useState, useEffect, useRef, useCallback } from 'react';

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

export interface TimelineSeg {
  filename: string;
  start_time: string;
  end_time?: string | null;
  duration_seconds?: number;
  status?: string;
}

// Constantes configuráveis da Janela Deslizante de Gravações DVR
export const DVR_WINDOW_CONFIG = {
  W_BEHIND: 2,
  W_AHEAD_DESKTOP: 10,
  W_AHEAD_MOBILE: 5,
  MAX_PARALLEL_PRELOAD: 3,
};

function getWAhead(): number {
  if (typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(max-width: 768px)').matches) {
    return DVR_WINDOW_CONFIG.W_AHEAD_MOBILE;
  }
  return DVR_WINDOW_CONFIG.W_AHEAD_DESKTOP;
}

function addDays(dateStr: string, days: number): string {
  try {
    const parts = dateStr.split('-').map(Number);
    if (parts.length === 3) {
      const dt = new Date(parts[0], parts[1] - 1, parts[2]);
      dt.setDate(dt.getDate() + days);
      const p = (n: number) => String(n).padStart(2, '0');
      return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
    }
  } catch {}
  return dateStr;
}

function parseTs(raw?: string | null): number {
  if (!raw) return NaN;
  return new Date(raw.replace(' ', 'T')).getTime();
}

interface UseDvrWindowProps {
  token: string | null;
  activeSegment: DvrSegment | null;
  setActiveSegment: (seg: DvrSegment | null) => void;
  pendingOffsetRef: React.MutableRefObject<number | null>;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onEndReached?: () => void;
  onFeedback?: (msg: { text: string; type: 'success' | 'error' }) => void;
}

export function useDvrWindow({
  token,
  activeSegment,
  setActiveSegment,
  pendingOffsetRef,
  videoRef,
  onEndReached,
  onFeedback,
}: UseDvrWindowProps) {
  // Playlist unificada contínua (pode cruzar dias)
  const [playlist, setPlaylist] = useState<TimelineSeg[]>([]);
  // Timeline do dia atual (para a régua de minutos e checagens)
  const [dayTimeline, setDayTimeline] = useState<TimelineSeg[]>([]);

  // Cache de timelines por data YYYY-MM-DD
  const timelineCacheRef = useRef<Map<string, TimelineSeg[]>>(new Map());

  // Container para elementos <video> ocultos de pré-carregamento
  const preloadContainerRef = useRef<HTMLDivElement | null>(null);

  // Mapa de elementos <video> ativos indexados por filename
  const preloadVideoMapRef = useRef<Map<string, HTMLVideoElement>>(new Map());

  // Fila de preloading e controle de concorrência (máx 3 paralelos)
  const loadingFilesRef = useRef<Set<string>>(new Set());
  const loadedFilesRef = useRef<Set<string>>(new Set());
  const priorityQueueRef = useRef<string[]>([]);

  // Último activeSegment.filename processado para evitar loops de refetch
  const lastActiveFilenameRef = useRef<string | null>(null);

  // Helper para buscar timeline de uma data específica
  const fetchTimelineForDate = useCallback(
    async (dateStr: string): Promise<TimelineSeg[]> => {
      if (!token || !dateStr) return [];
      if (timelineCacheRef.current.has(dateStr)) {
        return timelineCacheRef.current.get(dateStr)!;
      }
      try {
        const res = await fetch(`/api/camera/dvr/timeline?date=${encodeURIComponent(dateStr)}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const text = await res.text();
          const data = text ? JSON.parse(text) : {};
          const items: TimelineSeg[] = data.segments || [];
          timelineCacheRef.current.set(dateStr, items);
          return items;
        }
      } catch {}
      return [];
    },
    [token]
  );

  // Invalida cache de uma data (por ex. no polling periódico de gravações)
  const refreshDateTimeline = useCallback(
    async (dateStr: string) => {
      if (!token || !dateStr) return;
      try {
        const res = await fetch(`/api/camera/dvr/timeline?date=${encodeURIComponent(dateStr)}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const text = await res.text();
          const data = text ? JSON.parse(text) : {};
          const items: TimelineSeg[] = data.segments || [];
          timelineCacheRef.current.set(dateStr, items);
          setDayTimeline(items);
        }
      } catch {}
    },
    [token]
  );

  // Descarrega um elemento de vídeo para limitar memória (removeAttribute('src') + load())
  const discardPreloadVideo = useCallback((filename: string) => {
    const el = preloadVideoMapRef.current.get(filename);
    if (el) {
      try {
        el.oncanplaythrough = null;
        el.onloadeddata = null;
        el.onerror = null;
        el.removeAttribute('src');
        el.load();
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      } catch {}
      preloadVideoMapRef.current.delete(filename);
    }
    loadingFilesRef.current.delete(filename);
    loadedFilesRef.current.delete(filename);
  }, []);

  // Processa a fila de preloading respeitando MAX_PARALLEL_PRELOAD (3) e ordem de proximidade
  const pumpPreloadQueue = useCallback(() => {
    if (!token || !preloadContainerRef.current) return;
    const maxParallel = DVR_WINDOW_CONFIG.MAX_PARALLEL_PRELOAD;

    while (loadingFilesRef.current.size < maxParallel && priorityQueueRef.current.length > 0) {
      const nextFilename = priorityQueueRef.current.shift();
      if (!nextFilename) break;

      if (loadedFilesRef.current.has(nextFilename) || loadingFilesRef.current.has(nextFilename)) {
        continue;
      }

      loadingFilesRef.current.add(nextFilename);

      let videoEl = preloadVideoMapRef.current.get(nextFilename);
      if (!videoEl) {
        videoEl = document.createElement('video');
        videoEl.preload = 'auto';
        videoEl.muted = true;
        videoEl.playsInline = true;
        videoEl.hidden = true;
        videoEl.style.display = 'none';
        preloadContainerRef.current.appendChild(videoEl);
        preloadVideoMapRef.current.set(nextFilename, videoEl);
      }

      const onReady = () => {
        loadingFilesRef.current.delete(nextFilename);
        loadedFilesRef.current.add(nextFilename);
        pumpPreloadQueue();
      };

      videoEl.oncanplaythrough = onReady;
      videoEl.onloadeddata = onReady;
      videoEl.onerror = () => {
        loadingFilesRef.current.delete(nextFilename);
        pumpPreloadQueue();
      };

      videoEl.src = `/api/camera/dvr/video/${nextFilename}?token=${token}`;
    }
  }, [token]);

  // Atualiza a janela deslizante quando activeSegment mudar
  useEffect(() => {
    if (!activeSegment?.filename) {
      for (const fname of Array.from(preloadVideoMapRef.current.keys())) {
        discardPreloadVideo(fname);
      }
      lastActiveFilenameRef.current = null;
      return;
    }

    // Só refaça a janela quando o bloco ativo mudar (evita loops de refetch)
    if (lastActiveFilenameRef.current === activeSegment.filename) {
      return;
    }
    lastActiveFilenameRef.current = activeSegment.filename;

    const curDate = activeSegment.start_time.substring(0, 10);
    const wAhead = getWAhead();
    const wBehind = DVR_WINDOW_CONFIG.W_BEHIND;

    let isMounted = true;

    async function buildWindow() {
      const curTimeline = await fetchTimelineForDate(curDate);
      if (!isMounted) return;

      setDayTimeline(curTimeline);

      const curIdx = curTimeline.findIndex((s) => s.filename === activeSegment?.filename);
      const isRecording = activeSegment?.status === 'recording';

      // Cruza dia: busca timeline do dia anterior se início, e do seguinte se fim do dia
      const needPrevDay = curIdx === -1 || curIdx < wBehind;
      const needNextDay = !isRecording && (curIdx === -1 || curIdx >= curTimeline.length - wAhead);

      const datesToLoad = [curDate];
      if (needPrevDay) datesToLoad.unshift(addDays(curDate, -1));
      if (needNextDay) datesToLoad.push(addDays(curDate, 1));

      const multiDayTimelines = await Promise.all(datesToLoad.map(fetchTimelineForDate));
      if (!isMounted) return;

      const combinedMap = new Map<string, TimelineSeg>();
      for (const list of multiDayTimelines) {
        for (const seg of list) {
          combinedMap.set(seg.filename, seg);
        }
      }

      if (activeSegment && !combinedMap.has(activeSegment.filename)) {
        combinedMap.set(activeSegment.filename, {
          filename: activeSegment.filename,
          start_time: activeSegment.start_time,
          end_time: activeSegment.end_time,
          duration_seconds: activeSegment.duration_seconds,
          status: activeSegment.status,
        });
      }

      const fullPlaylist = Array.from(combinedMap.values()).sort(
        (a, b) => parseTs(a.start_time) - parseTs(b.start_time)
      );

      setPlaylist(fullPlaylist);

      const activeIdx = fullPlaylist.findIndex((s) => s.filename === activeSegment?.filename);
      if (activeIdx === -1) return;

      const behindCount = Math.min(wBehind, activeIdx);
      // Se o bloco ativo for o que está gravando (status 'recording') não pré-carregue além dele
      const aheadCount = isRecording ? 0 : Math.min(wAhead, fullPlaylist.length - 1 - activeIdx);

      const behindSegs = fullPlaylist.slice(activeIdx - behindCount, activeIdx);
      const aheadSegs = isRecording ? [] : fullPlaylist.slice(activeIdx + 1, activeIdx + 1 + aheadCount);

      // Descarte dos vídeos que saíram da janela
      const windowFilenames = new Set<string>();
      behindSegs.forEach((s) => windowFilenames.add(s.filename));
      aheadSegs.forEach((s) => windowFilenames.add(s.filename));

      for (const fname of Array.from(preloadVideoMapRef.current.keys())) {
        if (!windowFilenames.has(fname)) {
          discardPreloadVideo(fname);
        }
      }

      // Ordem de proximidade: próximos primeiro (+1, -1, +2, -2...)
      const priorityQueue: string[] = [];
      const maxDist = Math.max(aheadSegs.length, behindSegs.length);
      for (let d = 1; d <= maxDist; d++) {
        if (d <= aheadSegs.length) {
          priorityQueue.push(aheadSegs[d - 1].filename);
        }
        if (d <= behindSegs.length) {
          priorityQueue.push(behindSegs[behindSegs.length - d].filename);
        }
      }

      priorityQueueRef.current = priorityQueue;
      pumpPreloadQueue();
    }

    buildWindow();

    return () => {
      isMounted = false;
    };
  }, [
    activeSegment?.filename,
    activeSegment?.start_time,
    activeSegment?.status,
    fetchTimelineForDate,
    discardPreloadVideo,
    pumpPreloadQueue,
  ]);

  // Limpeza completa ao desmontar o componente
  useEffect(() => {
    return () => {
      for (const fname of Array.from(preloadVideoMapRef.current.keys())) {
        discardPreloadVideo(fname);
      }
    };
  }, [discardPreloadVideo]);

  // Próximo bloco da janela (ou API adjacent se no final)
  const playNext = useCallback(async () => {
    if (!token || !activeSegment) return;
    const curIdx = playlist.findIndex((s) => s.filename === activeSegment.filename);
    if (curIdx !== -1 && curIdx + 1 < playlist.length) {
      const nextSeg = playlist[curIdx + 1];
      pendingOffsetRef.current = 0;
      setActiveSegment(nextSeg as DvrSegment);
      return;
    }

    // Se estiver no final da playlist local, busca se há algo além
    try {
      const res = await fetch(
        `/api/camera/dvr/adjacent?filename=${encodeURIComponent(activeSegment.filename)}&direction=next`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.ok) {
        const text = await res.text();
        const data = text ? JSON.parse(text) : {};
        if (data.found && data.segment) {
          pendingOffsetRef.current = 0;
          setActiveSegment(data.segment);
          return;
        }
      }
    } catch {}

    // Fim das gravações disponíveis (não volta ao vivo)
    if (onFeedback) {
      onFeedback({ text: 'Fim das gravações disponíveis.', type: 'success' });
    }
    if (onEndReached) {
      onEndReached();
    }
  }, [token, activeSegment, playlist, setActiveSegment, pendingOffsetRef, onFeedback, onEndReached]);

  // Bloco anterior da janela
  const playPrev = useCallback(async () => {
    if (!token || !activeSegment) return;
    const curIdx = playlist.findIndex((s) => s.filename === activeSegment.filename);
    if (curIdx > 0) {
      const prevSeg = playlist[curIdx - 1];
      pendingOffsetRef.current = 0;
      setActiveSegment(prevSeg as DvrSegment);
      return;
    }

    try {
      const res = await fetch(
        `/api/camera/dvr/adjacent?filename=${encodeURIComponent(activeSegment.filename)}&direction=prev`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.ok) {
        const text = await res.text();
        const data = text ? JSON.parse(text) : {};
        if (data.found && data.segment) {
          pendingOffsetRef.current = 0;
          setActiveSegment(data.segment);
          return;
        }
      }
    } catch {}

    if (onFeedback) {
      onFeedback({ text: 'Não há gravação anterior.', type: 'error' });
    }
  }, [token, activeSegment, playlist, setActiveSegment, pendingOffsetRef, onFeedback]);

  // Avanço/retrocesso em segundos atravessando blocos (-15s, +15s, -1m, +1m, -5m, +5m)
  const seekRelative = useCallback(
    async (deltaSeconds: number) => {
      if (!activeSegment) return;
      const v = videoRef.current;
      const curTime = v ? v.currentTime : 0;
      const curDuration =
        v && Number.isFinite(v.duration) && v.duration > 0
          ? v.duration
          : activeSegment.duration_seconds || 60;

      const targetInCur = curTime + deltaSeconds;

      // 1. Permanece no mesmo bloco
      if (targetInCur >= 0 && targetInCur < curDuration) {
        if (v) {
          v.currentTime = targetInCur;
          v.play().catch(() => {});
        }
        return;
      }

      const curIdx = playlist.findIndex((s) => s.filename === activeSegment.filename);

      // 2. Salto para trás (atravessa bloco anterior)
      if (targetInCur < 0) {
        let remainingBack = -targetInCur;
        let p = curIdx !== -1 ? curIdx - 1 : -1;

        while (p >= 0) {
          const prevSeg = playlist[p];
          const segDur = prevSeg.duration_seconds || 60;
          if (remainingBack <= segDur) {
            const targetOffset = Math.max(0, segDur - remainingBack);
            pendingOffsetRef.current = targetOffset;
            setActiveSegment(prevSeg as DvrSegment);
            return;
          }
          remainingBack -= segDur;
          p--;
        }

        // Ultrapassou os blocos carregados: busca por horário absoluto via API seek
        const curStartMs = parseTs(activeSegment.start_time);
        if (!isNaN(curStartMs)) {
          const targetDt = new Date(curStartMs + Math.floor(targetInCur) * 1000);
          const p2 = (n: number) => String(n).padStart(2, '0');
          const targetStr = `${targetDt.getFullYear()}-${p2(targetDt.getMonth() + 1)}-${p2(targetDt.getDate())} ${p2(
            targetDt.getHours()
          )}:${p2(targetDt.getMinutes())}:${p2(targetDt.getSeconds())}`;

          try {
            const res = await fetch(`/api/camera/dvr/seek?target_time=${encodeURIComponent(targetStr)}`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) {
              const text = await res.text();
              const data = text ? JSON.parse(text) : {};
              if (data.found && data.segment) {
                pendingOffsetRef.current = data.seek_offset || 0;
                setActiveSegment(data.segment);
                if (data.gap && onFeedback) {
                  onFeedback({
                    text: `Salto com intervalo. Ponto mais próximo: ${data.segment.filename}`,
                    type: 'error',
                  });
                }
                return;
              }
            }
          } catch {}
        }

        if (v) v.currentTime = 0;
        return;
      }

      // 3. Salto para frente (atravessa bloco seguinte)
      if (targetInCur >= curDuration) {
        if (activeSegment.status === 'recording') {
          if (v) v.currentTime = Math.max(0, curDuration - 1);
          return;
        }

        let remainingForward = targetInCur - curDuration;
        let p = curIdx !== -1 ? curIdx + 1 : -1;

        while (p >= 0 && p < playlist.length) {
          const nextSeg = playlist[p];
          const segDur = nextSeg.duration_seconds || 60;
          if (remainingForward < segDur) {
            const targetOffset = Math.max(0, remainingForward);
            pendingOffsetRef.current = targetOffset;
            setActiveSegment(nextSeg as DvrSegment);
            return;
          }
          remainingForward -= segDur;
          p++;
        }

        // Ultrapassou os blocos carregados: busca por horário absoluto via API seek
        const curStartMs = parseTs(activeSegment.start_time);
        if (!isNaN(curStartMs)) {
          const targetDt = new Date(curStartMs + Math.floor(targetInCur) * 1000);
          const p2 = (n: number) => String(n).padStart(2, '0');
          const targetStr = `${targetDt.getFullYear()}-${p2(targetDt.getMonth() + 1)}-${p2(targetDt.getDate())} ${p2(
            targetDt.getHours()
          )}:${p2(targetDt.getMinutes())}:${p2(targetDt.getSeconds())}`;

          try {
            const res = await fetch(`/api/camera/dvr/seek?target_time=${encodeURIComponent(targetStr)}`, {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) {
              const text = await res.text();
              const data = text ? JSON.parse(text) : {};
              if (data.found && data.segment) {
                pendingOffsetRef.current = data.seek_offset || 0;
                setActiveSegment(data.segment);
                if (data.gap && onFeedback) {
                  onFeedback({
                    text: `Salto com intervalo. Ponto mais próximo: ${data.segment.filename}`,
                    type: 'error',
                  });
                }
                return;
              }
            }
          } catch {}
        }

        if (onFeedback) {
          onFeedback({ text: 'Fim das gravações disponíveis.', type: 'success' });
        }
      }
    },
    [activeSegment, playlist, token, videoRef, pendingOffsetRef, setActiveSegment, onFeedback]
  );

  return {
    playlist,
    dayTimeline,
    preloadContainerRef,
    playNext,
    playPrev,
    seekRelative,
    refreshDateTimeline,
  };
}
