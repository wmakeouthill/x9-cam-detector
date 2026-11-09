import { useState, useEffect, useCallback } from 'react';
import { useAuthStore } from '@/stores/authStore';
import { LoginPage } from '@/features/auth/LoginPage';
import { Header } from '@/shared/components/Layout/Header';
import { Sidebar, NavTab } from '@/shared/components/Layout/Sidebar';
import { LiveMonitorView } from '@/features/monitor/LiveMonitorView';
import { DvrRecordingsView } from '@/features/dvr/DvrRecordingsView';
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
  detection_mode?: 'hybrid' | 'camera' | 'yolo';
  yolo_awake?: boolean;
  camera_event_active?: boolean;
  last_camera_event_code?: string;
  person_detected?: boolean;
  people_count?: number;
  last_confidence?: number;
  detections_count?: number;
  test_mode?: boolean;
  model_type?: string;
  image_profile_mode?: 'auto' | 'day' | 'night';
  night_start_time?: string;
  night_end_time?: string;
  is_night_active?: boolean;
  clip_min_duration?: number;
  clip_max_duration?: number;
  target_overlay?: boolean;
  continuous_light?: boolean;
  flash_active?: boolean;
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
              currentSubtype={camStatus.subtype}
              detectorActive={camStatus.detector_active}
              detectionMode={camStatus.detection_mode}
              yoloAwake={camStatus.yolo_awake}
              cameraEventActive={camStatus.camera_event_active}
              lastCameraEventCode={camStatus.last_camera_event_code}
              personDetected={camStatus.person_detected}
              peopleCount={camStatus.people_count}
              lastConfidence={camStatus.last_confidence}
              detectionsCount={camStatus.detections_count}
              testMode={camStatus.test_mode}
              imageProfileMode={camStatus.image_profile_mode}
              nightStartTime={camStatus.night_start_time}
              nightEndTime={camStatus.night_end_time}
              isNightActive={camStatus.is_night_active}
              continuousLight={camStatus.continuous_light}
              flashActive={camStatus.flash_active}
              onRefreshStatus={fetchStatus}
            />
          )}

          {activeTab === 'dvr' && <DvrRecordingsView />}

          {activeTab === 'rules' && <NightWatchView />}

          {activeTab === 'snapshots' && <SnapshotsView />}

          {activeTab === 'config' && (
            <CameraConfigView
              currentIp={camStatus.ip}
              currentPort={camStatus.port}
              currentHttpPort={camStatus.http_port}
              currentUser={camStatus.user}
              currentSubtype={camStatus.subtype}
              currentImageProfileMode={camStatus.image_profile_mode}
              currentNightStartTime={camStatus.night_start_time}
              currentNightEndTime={camStatus.night_end_time}
              currentClipMinDuration={camStatus.clip_min_duration}
              currentClipMaxDuration={camStatus.clip_max_duration}
              currentTargetOverlay={camStatus.target_overlay}
              onConfigSaved={fetchStatus}
            />
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
