"""Tests for delegate_task_background — async/detached coder spawn (Task 3)."""
from unittest.mock import MagicMock, patch

from tools.delegate_tool import delegate_task_background


def test_returns_immediately_with_handle():
    """Background variant returns a coder_run_id without waiting for the child."""
    parent = MagicMock()
    parent.task_id = "parent-task-1"

    with patch("tools.delegate_tool._spawn_detached_coder") as mock_spawn:
        result = delegate_task_background(
            parent_agent=parent,
            goal="add function X to foo.py",
            context="file at /tmp/foo.py",
        )

    assert isinstance(result, dict)
    assert "coder_run_id" in result
    assert result["coder_run_id"].startswith("coder-")
    assert result["status"] == "spawned"
    assert result["goal"] == "add function X to foo.py"
    mock_spawn.assert_called_once()


def test_records_coder_run_for_thread_routing():
    """The spawned run is registered so gateway can map it to a Discord thread."""
    parent = MagicMock()
    parent.task_id = "parent-task-2"

    with patch("tools.delegate_tool._spawn_detached_coder"), \
         patch("tools.delegate_tool._register_coder_run") as mock_register:
        result = delegate_task_background(
            parent_agent=parent,
            goal="rename Y",
            context="",
        )

    mock_register.assert_called_once()
    call_args_str = str(mock_register.call_args)
    # The registration must link the generated coder_run_id with the parent task
    assert result["coder_run_id"] in call_args_str
    assert "parent-task-2" in call_args_str
