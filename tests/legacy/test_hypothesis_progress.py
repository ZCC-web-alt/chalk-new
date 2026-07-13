import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hypothesis_progress import (
    HypothesisProgressTracker,
    build_hypothesis_progress_steps,
)


def test_progress_steps_expand_with_iteration_count():
    one_round = build_hypothesis_progress_steps(max_iterations=1, hitl_enabled=True)
    five_rounds = build_hypothesis_progress_steps(max_iterations=5, hitl_enabled=True)

    one_titles = [step["title"] for step in one_round]
    five_titles = [step["title"] for step in five_rounds]

    assert "第 1 轮多角色辩论" in one_titles
    assert "第 2 轮多角色辩论" not in one_titles
    assert "第 5 轮多角色辩论" in five_titles
    assert "第 5 轮人工审核" in five_titles
    assert len(five_rounds) > len(one_round)


def test_progress_tracker_does_not_jump_back_during_hitl():
    tracker = HypothesisProgressTracker(max_iterations=3, hitl_enabled=True)

    first_debate = tracker.update("第 1 轮多角色辩论", 5, 16, "running")
    hitl = tracker.update("第 1 轮思辨与辩论审核", 5, 16, "running")
    delayed_literature = tracker.update("文献理解与事实提取", 1, 16, "running")

    titles = [step["title"] for step in tracker.steps]
    assert titles[first_debate["active_index"]] == "第 1 轮多角色辩论"
    assert titles[hitl["active_index"]] == "第 1 轮人工审核"
    assert delayed_literature["active_index"] == hitl["active_index"]
    assert delayed_literature["percent"] >= hitl["percent"]


def test_progress_tracker_maps_later_rounds_forward():
    tracker = HypothesisProgressTracker(max_iterations=4, hitl_enabled=True)

    round2 = tracker.update("第 2 轮思辨", 7, 20, "running")
    round4 = tracker.update("第 4 轮修订", 13, 20, "running")

    titles = [step["title"] for step in tracker.steps]
    assert titles[round2["active_index"]] == "第 2 轮思辨评审"
    assert titles[round4["active_index"]] == "第 4 轮假设修订"
    assert round4["active_index"] > round2["active_index"]


def test_progress_tracker_maps_initial_and_hitl_stages():
    tracker = HypothesisProgressTracker(max_iterations=2, hitl_enabled=True)

    initial = tracker.update("生成初始假设", 3, 12, "running")
    initial_review = tracker.update("初始假设人工审核", 4, 12, "running")
    round_review = tracker.update("第 2 轮人工审核", 9, 12, "running")

    titles = [step["title"] for step in tracker.steps]
    assert titles[initial["active_index"]] == "初始假设生成"
    assert titles[initial_review["active_index"]] == "初始假设人工审核"
    assert titles[round_review["active_index"]] == "第 2 轮人工审核"


if __name__ == "__main__":
    test_progress_steps_expand_with_iteration_count()
    test_progress_tracker_does_not_jump_back_during_hitl()
    test_progress_tracker_maps_later_rounds_forward()
    test_progress_tracker_maps_initial_and_hitl_stages()
    print("hypothesis progress tests passed")
