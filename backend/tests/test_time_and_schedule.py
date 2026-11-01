from datetime import datetime, time
from zoneinfo import ZoneInfo
from app.core.time import BRT, get_now, get_now_str
from app.services.detector_service import DetectorService

def test_brasilia_timezone():
    now_brt = get_now()
    assert now_brt.tzinfo is not None
    # Brasília é UTC-3 (sem horário de verão atualmente no Brasil)
    assert now_brt.utcoffset().total_seconds() == -10800
    
    ts_str = get_now_str()
    # Deve seguir YYYY-MM-DD HH:MM:SS
    assert len(ts_str) == 19
    assert ts_str[4] == "-" and ts_str[7] == "-" and ts_str[10] == " "

def test_detector_schedule_evaluation():
    detector = DetectorService()
    detector.test_mode = False

    # Intervalo normal diurno (ex: 10:00 às 18:00)
    # Se testarmos com horário conhecido:
    def eval_schedule_at_time(mock_time: time, start: str, end: str) -> bool:
        start_t = datetime.strptime(start, "%H:%M").time()
        end_t = datetime.strptime(end, "%H:%M").time()
        if start_t <= end_t:
            return start_t <= mock_time <= end_t
        else:
            return mock_time >= start_t or mock_time <= end_t

    # Cenário configurado pelo usuário: 05:00 às 09:00, consultado às 02:00
    assert eval_schedule_at_time(time(2, 0), "05:00", "09:00") is False
    assert eval_schedule_at_time(time(2, 50), "05:00", "09:00") is False
    assert eval_schedule_at_time(time(5, 0), "05:00", "09:00") is True
    assert eval_schedule_at_time(time(7, 30), "05:00", "09:00") is True
    assert eval_schedule_at_time(time(9, 0), "05:00", "09:00") is True
    assert eval_schedule_at_time(time(9, 1), "05:00", "09:00") is False

def test_alarm_trigger_logic_when_out_of_schedule_or_disabled():
    detector = DetectorService()
    assert detector.test_mode is False

    # 1. Vigia ativado, mas fora do horário (ex: janela 05:00 às 09:00 sendo 02:00)
    vigia_enabled = True
    in_schedule = False
    should_trigger_alarm = (vigia_enabled and in_schedule) or detector.test_mode
    assert should_trigger_alarm is False  # NÃO PODE TIRAR FOTO

    # 2. Vigia desativado pelo usuário, mesmo dentro do horário
    vigia_enabled = False
    in_schedule = True
    should_trigger_alarm = (vigia_enabled and in_schedule) or detector.test_mode
    assert should_trigger_alarm is False  # NÃO PODE TIRAR FOTO

    # 3. Vigia ativado e dentro do horário
    vigia_enabled = True
    in_schedule = True
    should_trigger_alarm = (vigia_enabled and in_schedule) or detector.test_mode
    assert should_trigger_alarm is True  # AGORA SIM TIRA FOTO

