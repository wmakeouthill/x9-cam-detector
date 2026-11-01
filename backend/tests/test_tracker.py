import time
from app.services.detector_service import calculate_iou, SimpleTracker

def test_calculate_iou():
    box1 = [10, 10, 50, 50]
    box2 = [10, 10, 50, 50]
    # Identical boxes should have IoU = 1.0
    assert abs(calculate_iou(box1, box2) - 1.0) < 1e-4

    # Disjoint boxes should have IoU = 0.0
    box3 = [100, 100, 150, 150]
    assert calculate_iou(box1, box3) == 0.0

    # Half-overlapping box
    box4 = [10, 10, 30, 50] # Area = 20 * 40 = 800; box1 Area = 1600; union = 1600; iou = 0.5
    assert abs(calculate_iou(box1, box4) - 0.5) < 1e-4

def test_tracker_maintains_id_and_follows():
    tracker = SimpleTracker(max_age_seconds=0.8, iou_threshold=0.25)
    
    # Frame 1: Person 1 enters
    det1 = [{"box": [100, 100, 200, 300], "conf": 0.85}]
    tracks1 = tracker.update(det1)
    assert len(tracks1) == 1
    assert tracks1[0]["track_id"] == 1
    assert tracks1[0]["conf"] == 0.85

    # Frame 2: Person 1 moves slightly to the right
    det2 = [{"box": [105, 102, 205, 302], "conf": 0.88}]
    tracks2 = tracker.update(det2)
    assert len(tracks2) == 1
    assert tracks2[0]["track_id"] == 1
    # Check that position smoothed towards new detection
    assert tracks2[0]["box"][0] >= 100

def test_tracker_grace_period_prevents_flicker():
    tracker = SimpleTracker(max_age_seconds=0.5, iou_threshold=0.25)
    
    # Person detected
    tracker.update([{"box": [50, 50, 100, 150], "conf": 0.70}])
    
    # Next cycle: momentary missed detection (empty list)
    empty_tracks = tracker.update([])
    # Must still retain the track within grace period!
    assert len(empty_tracks) == 1
    assert empty_tracks[0]["track_id"] == 1

    # After expiration
    time.sleep(0.55)
    expired_tracks = tracker.update([])
    assert len(expired_tracks) == 0

def test_tracker_multiple_people():
    tracker = SimpleTracker(max_age_seconds=0.8, iou_threshold=0.25)
    
    dets = [
        {"box": [10, 10, 60, 120], "conf": 0.9},
        {"box": [300, 50, 380, 200], "conf": 0.85},
    ]
    tracks = tracker.update(dets)
    assert len(tracks) == 2
    track_ids = {t["track_id"] for t in tracks}
    assert len(track_ids) == 2
