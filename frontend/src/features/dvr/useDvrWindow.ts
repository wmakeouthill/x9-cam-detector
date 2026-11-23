import { useState, useEffect, useRef, useCallback, useMemo } from 'react';

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
  // Playlist unificada contínua (pode cruzar fronteira de dias)
  const [playlist, setPlaylist] = useState<TimelineSeg[]>([]);
  // Timeline do dia atual (para a régua de 60 minutos)
  const [dayTimeline, setDayTimeline] = useState<TimelineSeg[]>([]);

  // Cache de timelines por data YYYY-MM-DD
  const timelineCacheRef = useRef<Map<string, TimelineSeg[]>>(new Map());
  const lastActiveFilenameRef = useRef<string | null>(null);

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

  // Mantém a playlist cronológica ao redor do bloco ativo (sem criar elementos <video> ocultos em massa)
  useEffect(() => {
    if (!activeSegment?.filename) {
      lastActiveFilenameRef.current = null;
      return;
    }

    if (lastActiveFilenameRef.current === activeSegment.filename) {
      return;
    }
    lastActiveFilenameRef.current = activeSegment.filename;

    const curDate = activeSegment.start_time.substring(0, 10);
    let isMounted = true;

    async function buildPlaylist() {
      const curTimeline = await fetchTimelineForDate(curDate);
      if (!isMounted) return;

      setDayTimeline(curTimeline);

      const curIdx = curTimeline.findIndex((s) => s.filename === activeSegment?.filename);
      const isRecording = activeSegment?.status === 'recording';

      // Cruza fronteira de dia apenas quando próximo do início ou do fim do dia
      const needPrevDay = curIdx === -1 || curIdx < 2;
      const needNextDay = !isRecording && (curIdx === -1 || curIdx >= curTimeline.length - 2);

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
    }

    buildPlaylist();

    return () => {
      isMounted = false;
    };
  }, [activeSegment?.filename, activeSegment?.start_time, activeSegment?.status, fetchTimelineForDate]);

  // Próximo bloco imediato para o player duplo A/B (apenas 1 bloco de standby, sem tempestade de preload)
  const nextSegment = useMemo<TimelineSeg | null>(() => {
    if (!activeSegment?.filename || activeSegment.status === 'recording') return null;
    const idx = playlist.findIndex((s) => s.filename === activeSegment.filename);
    if (idx === -1 || idx + 1 >= playlist.length) return null;
    return playlist[idx + 1];
  }, [playlist, activeSegment?.filename, activeSegment?.status]);

  // Próximo bloco da playlist (ou API adjacent se estiver no final)
  const playNext = useCallback(async () => {
    if (!token || !activeSegment) return;
    const curIdx = playlist.findIndex((s) => s.filename === activeSegment.filename);
    if (curIdx !== -1 && curIdx + 1 < playlist.length) {
      const nextSeg = playlist[curIdx + 1];
      pendingOffsetRef.current = 0;
      setActiveSegment(nextSeg as DvrSegment);
      return;
    }

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

    if (onFeedback) {
      onFeedback({ text: 'Fim das gravações disponíveis.', type: 'success' });
    }
    if (onEndReached) {
      onEndReached();
    }
  }, [token, activeSegment, playlist, setActiveSegment, pendingOffsetRef, onFeedback, onEndReached]);

  // Bloco anterior da playlist
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
    nextSegment,
    playNext,
    playPrev,
    seekRelative,
    refreshDateTimeline,
  };
}
