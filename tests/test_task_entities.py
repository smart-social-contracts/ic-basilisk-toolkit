"""Task entities under CPython, without a canister (issue #16)."""

import pytest
from ic_python_db import Database, MemoryStorage

from ic_basilisk_toolkit.entities import Task, TaskExecution
from ic_basilisk_toolkit.shell import _task_run_code, _task_start_code
from ic_basilisk_toolkit.status import TaskExecutionStatus, TaskStatus


@pytest.fixture(autouse=True)
def database():
    try:
        db = Database.get_instance()
    except Exception:
        db = Database.init(db_storage=MemoryStorage())
    db.clear()
    yield db
    db.clear()


def test_statuses_are_plain_strings():
    """They are stored in String fields, which refuse an Enum member."""
    for status in (TaskStatus, TaskExecutionStatus):
        values = [v for k, v in vars(status).items() if not k.startswith("_")]
        assert values and all(type(v) is str for v in values), status


def test_new_task_execution_stores_its_status():
    task = Task(name="nightly")
    execution = task.new_task_execution()
    assert execution.status == "idle"
    assert execution.task._id == task._id
    task.status = TaskStatus.RUNNING
    assert Database.get_instance().load("Task", task._id)["status"] == "running"


def test_each_execution_of_a_task_has_its_own_name():
    """The name is the alias: a lookup by it finds that execution, not the
    latest one of the task."""
    task = Task(name="nightly")
    first, second = task.new_task_execution(), task.new_task_execution()
    assert first.name != second.name
    assert TaskExecution[first.name]._id == first._id
    assert TaskExecution[second.name]._id == second._id
    assert first.name == f"taskexec_{first._id}"


def test_the_shell_names_executions_the_same_way():
    """Its code runs in the canister, where it may define the entities
    itself, so it builds the name inline: after the execution's id, never
    after the task and the step (the same for every run)."""
    for code in (_task_run_code("1"), _task_start_code("1")):
        assert "_exec_name = f'taskexec_{TaskExecution.max_id() + 1}'" in code
        assert "_si}'" not in code
