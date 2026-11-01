import { useState, useEffect, useCallback } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { LoginPage } from '@/features/auth/LoginPage';
import { Header } from '@/shared/components/Layout/Header';
import { Sidebar, NavTab } from '@/shared/components/Layout/Sidebar';
import { LiveMonitorView } from '@/features/monitor/LiveMonitorView';
import { CameraConfigView } from '@/features/config/CameraConfigView';
import { NightWatchView } from '@/features/rules/NightWatchView';
import { SnapshotsView } from '@/features/snapshots/SnapshotsView';

interface CameraStatus {
  status: string;
  fps: number;
  resolution: string;
  ip: string;
  port: number;
  http_port: number;
  subtype: number;
  user: string;
  detector_active?: boolean;
  person_detected?: boolean;
  last_confidence?: number;
  detections_count?: number;
  test_mode?: boolean;
  model_type?: string;
}

export function App() {
  const { isAuthenticated, initialize, token } = useAuthStore();
  const [activeTab, setActiveTab] = useState<NavTab>('live');

  const [camStatus, setCamStatus] = useState<CameraStatus>({
    status: 'CONNECTING',
    fps: 0,
    resolution: 'N/A',
    ip: '',
    port: 554,
    http_port: 80,
    subtype: 0,
    user: 'admin',
  });

  useEffect(() => {
    initialize();
  }, [initialize]);

  const fetchStatus = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch('/api/camera/status', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setCamStatus(data);
      }
    } catch {
      // offline
    }
  }, [token]);

  useEffect(() => {
    if (!isAuthenticated) return;
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [isAuthenticated, fetchStatus]);

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div className="h-[100dvh] w-screen flex flex-col bg-[#040608] overflow-hidden select-none font-sans">
      {/* Header Fixo */}
      <Header
        status={camStatus.status}
        fps={camStatus.fps}
        resolution={camStatus.resolution}
        cameraIp={camStatus.ip}
      />

      {/* Corpo com Sidebar e Conteúdo Central */}
      <div className="flex-1 flex flex-col md:flex-row overflow-hidden min-h-0">
        <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />

        <main className="flex-1 h-full overflow-y-auto overflow-x-hidden bg-[#070A0F] terminal-grid min-h-0 order-1 md:order-2">
          {activeTab === 'live' && (
            <LiveMonitorView
              status={camStatus.status}
              fps={camStatus.fps}
              resolution={camStatus.resolution}
              cameraIp={camStatus.ip}
              detectorActive={camStatus.detector_active}
              personDetected={camStatus.person_detected}
              lastConfidence={camStatus.last_confidence}
              detectionsCount={camStatus.detections_count}
              testMode={camStatus.test_mode}
              onRefreshStatus={fetchStatus}
            />
          )}

          {activeTab === 'rules' && <NightWatchView />}

          {activeTab === 'snapshots' && <SnapshotsView />}

          {activeTab === 'config' && (
            <CameraConfigView
              currentIp={camStatus.ip}
              currentPort={camStatus.port}
              currentHttpPort={camStatus.http_port}
              currentUser={camStatus.user}
              currentSubtype={camStatus.subtype}
              onConfigSaved={fetchStatus}
            />
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
