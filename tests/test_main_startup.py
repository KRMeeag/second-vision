"""
T-03b: main() never starts mock mode by itself (REQ-CON-08: "It must never fall back to the testing
mock mode"; decision D-13: mock mode is reachable only by --mock).

Unit level, hardware-free: the three things main() could start (a worker thread, the mock runner,
the pipeline runner) are replaced by recorders, so nothing runs. Runs in the Poetry environment and
in my_hailo_env alike: the flag that says whether hailo_apps imported is set by each test.

    ~/.cache/pypoetry/virtualenvs/second-vision-repo-wwT_Rtha-py3.13/bin/python -m pytest tests/test_main_startup.py -v

Each test file puts the `src/` of its own tree first on the import path, so a copy of this file
placed in a scratch copy of the baseline tests the baseline's code (red before green).
"""

import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# The two hailo_apps modules main.py imports to decide HAILO_AVAILABLE.
HAILO_IMPORTS = ("hailo_apps.python.core.common.hailo_logger",
                 "hailo_apps.python.core.gstreamer.gstreamer_app")


def load_main(monkeypatch):
    """
    A fresh copy of main.py, imported as if hailo_apps were not installed. Not `import
    second_vision.main`: other test files put stand-ins for hailo_apps (and a callbacks module
    without user_app_callback_class) into sys.modules, and in my_hailo_env the real library is
    there, so what a plain import gives depends on which tests ran before. Here the two imports
    main.py tries are made to fail, whatever else is loaded; every test then sets
    HAILO_AVAILABLE itself.
    """
    for name in HAILO_IMPORTS:
        monkeypatch.setitem(sys.modules, name, None)        # `import name` now raises ImportError
    monkeypatch.setenv("GST_PLUGIN_FEATURE_RANK", os.environ.get("GST_PLUGIN_FEATURE_RANK", ""))  # main.py sets it
    path = SRC / "second_vision" / "main.py"
    spec = importlib.util.spec_from_file_location("second_vision_main_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    """What main() started: worker names, and whether the mock or the pipeline runner was called."""

    def __init__(self):
        self.threads = []
        self.mock_runs = 0
        self.pipeline_runs = 0
        self.panel_readers = 0


@pytest.fixture
def main_mod(monkeypatch):
    """second_vision.main with everything that starts something replaced by a recorder."""
    module = load_main(monkeypatch)
    assert Path(module.__file__).resolve().is_relative_to(SRC.resolve()), module.__file__
    recorder = Recorder()

    class RecordingThread:
        def __init__(self, target=None, args=(), daemon=None, name=None, **_):
            self.name = name

        def start(self):
            recorder.threads.append(self.name)

        def join(self, timeout=None):
            pass

    # main.py's own `threading` name only: other modules (and pytest) keep the real one.
    monkeypatch.setattr(module, "threading", SimpleNamespace(Thread=RecordingThread, Event=threading.Event))
    monkeypatch.setattr(module, "_run_mock_mode",
                        lambda *a, **k: setattr(recorder, "mock_runs", recorder.mock_runs + 1))
    monkeypatch.setattr(module, "_run_pipeline_mode",
                        lambda *a, **k: setattr(recorder, "pipeline_runs", recorder.pipeline_runs + 1))
    monkeypatch.setattr(module, "_start_config_reader",
                        lambda *a, **k: setattr(recorder, "panel_readers", recorder.panel_readers + 1))
    module.recorder = recorder
    return module


def run_main(module, monkeypatch, argv, *, hailo_available):
    monkeypatch.setattr(module, "HAILO_AVAILABLE", hailo_available)
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])
    module.main()


def test_t03b_con08_no_hailo_and_no_mock_flag_is_an_error_not_mock_mode(main_mod, monkeypatch, capsys):
    """
    REQ-CON-08, D-13. hailo_apps cannot be imported and --mock was not given: main() exits non-zero
    with a message, before any worker exists, and runs neither mock mode nor the pipeline.
    """
    monkeypatch.setattr(main_mod, "HAILO_IMPORT_ERROR", ModuleNotFoundError("No module named 'gi'"),
                        raising=False)
    with pytest.raises(SystemExit) as exited:
        run_main(main_mod, monkeypatch, ["--input", "/dev/video0", "--serial-port", "/dev/serial0"],
                 hailo_available=False)

    assert exited.value.code not in (0, None), "the refusal must be a non-zero exit"
    rec = main_mod.recorder
    assert rec.threads == [], f"workers were started: {rec.threads}"
    assert rec.mock_runs == 0, "mock mode was started without --mock"
    assert rec.pipeline_runs == 0 and rec.panel_readers == 0
    err = capsys.readouterr()
    assert "MOCK mode (no hardware)" not in err.out + err.err
    message = err.err
    assert "hailo_apps" in message and "gi" in message, "the message must say what is missing"
    assert "--mock" in message, "the message must say how to ask for mock mode on purpose"


def test_t03b_con08_mock_flag_still_runs_mock_mode_without_hailo(main_mod, monkeypatch, capsys):
    """--mock behaves as before when hailo_apps cannot be imported: workers, panel reader, mock runner."""
    run_main(main_mod, monkeypatch, ["--mock"], hailo_available=False)

    rec = main_mod.recorder
    assert rec.mock_runs == 1 and rec.pipeline_runs == 0
    # F3 added the earphones watcher to the always-started workers (core/connections.py), and the
    # status display the status worker (core/status_board.py).
    assert rec.threads == ["tts-worker", "alert-scheduler", "serial-writer", "earphones-watch", "status-board"]
    assert rec.panel_readers == 1
    assert "Running in MOCK mode" in capsys.readouterr().out


def test_t03b_con08_mock_flag_still_runs_mock_mode_with_hailo(main_mod, monkeypatch, capsys):
    """--mock with hailo_apps present: mock mode, not the pipeline (unchanged)."""
    run_main(main_mod, monkeypatch, ["--mock"], hailo_available=True)

    rec = main_mod.recorder
    assert rec.mock_runs == 1 and rec.pipeline_runs == 0
    assert "Running in MOCK mode" in capsys.readouterr().out


def test_t03b_con08_hailo_present_and_no_mock_flag_runs_the_pipeline(main_mod, monkeypatch, capsys):
    """The normal start: no --mock, hailo_apps present. The pipeline runner, never mock mode."""
    run_main(main_mod, monkeypatch, ["--input", "/dev/video0", "--serial-port", "/dev/serial0"],
             hailo_available=True)

    rec = main_mod.recorder
    assert rec.pipeline_runs == 1 and rec.mock_runs == 0
    # F3 added the earphones watcher to the always-started workers (core/connections.py), and the
    # status display the status worker (core/status_board.py).
    assert rec.threads == ["tts-worker", "alert-scheduler", "serial-writer", "earphones-watch", "status-board"]
    assert "MOCK" not in capsys.readouterr().out


def test_t03b_con08_the_import_error_is_kept_for_the_message(monkeypatch):
    """main.py records why hailo_apps did not import, so the refusal can say it."""
    module = load_main(monkeypatch)

    assert module.HAILO_AVAILABLE is False
    assert isinstance(getattr(module, "HAILO_IMPORT_ERROR", None), ImportError)
