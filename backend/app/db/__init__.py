from app.db.db import (
    init_db,
    get_camera_config,
    save_camera_config,
    get_night_watch_config,
    save_night_watch_config,
    log_detection_event,
    list_detection_events,
    count_detection_events,
    delete_detection_event,
    delete_detection_events_batch,
    cleanup_retention,
)
