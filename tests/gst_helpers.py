"""
GStreamer helpers for the `gst` level of the test harness (see tests/conftest.py).

An ordinary module the tests import (`from gst_helpers import ...`): pytest's default
import mode puts `tests/` on `sys.path`. Nothing here needs a device. It needs the REAL
PyGObject bindings for GStreamer, which exist in `my_hailo_env` and not in the Poetry
environment, so:

* importing this module never fails: without the bindings it simply cannot build
  anything, and `gst_unavailable_reason()` says why in plain words;
* every function that needs GStreamer calls `load_gst()`, which raises `GstUnavailable`
  (also with the plain-words reason) when the bindings are missing.

Facts these helpers rely on, checked against the GStreamer documentation
(https://gstreamer.freedesktop.org/documentation/gstreamer/gstmessage.html and
.../gstbus.html) and by running them on this Pi (GStreamer 1.26.2):

* `Gst.Message.new_error(src, gerror, debug)` and `Gst.Message.new_eos(src)` build a
  message whose `src` is the element given; `Gst.Bus.post(message)` queues it and
  returns False only when the bus is flushing.
* `bus.add_signal_watch()` attaches a source to the DEFAULT main context, and the bus's
  "message" signal is emitted from that source, so it fires only while a GLib main loop
  is running on the default context. That is the same wiring as
  `GStreamerApp.create_pipeline` / `bus_call` in hailo_apps.
* `Gst.init_check(None)` may be called more than once.

No helper starts a pipeline unless asked, and none touches the Hailo chip or the camera.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Any, Callable, Iterator

# Upper bound for any single run of the GLib main loop made by these helpers. A test
# that asks for more is mistaken, so it gets an error instead of a silent clamp.
MAX_LOOP_SECONDS = 30.0

_UNSET = object()
_bindings: Any = _UNSET            # (Gst, GLib) once loaded, or None once known missing
_unavailable_reason: str | None = None


class GstUnavailable(RuntimeError):
    """The real GStreamer bindings cannot be used here; the message says why."""


# ============================================================
# Loading the real bindings
# ============================================================
def probe_gst(importer: Callable[[str], Any] = importlib.import_module):
    """
    Try to import and initialise the real GStreamer bindings.

    Returns `((Gst, GLib), None)` on success or `(None, reason)` on failure, where the
    reason is a sentence for a person. `importer` is injectable so the decision can be
    unit-tested without touching the real modules.

    A hand-made stand-in for `gi` (as tests/test_pipeline_modes.py installs when the
    real one is absent) is recognised and refused: it has no `Gst.init_check`.
    """
    try:
        gi = importer("gi")
        gi.require_version("Gst", "1.0")
        # Import the typelib modules by their full names: `gi.repository` only grows the
        # attributes `Gst` / `GLib` once they have been imported.
        Gst = importer("gi.repository.Gst")
        GLib = importer("gi.repository.GLib")
    except ImportError as exc:
        return None, (
            f"the GStreamer Python bindings (gi / Gst) are not installed in this "
            f"environment ({exc}); run the gst level in my_hailo_env"
        )
    except Exception as exc:  # e.g. ValueError: Namespace Gst not available
        return None, (
            f"the GStreamer Python bindings could not be loaded "
            f"({type(exc).__name__}: {exc})"
        )
    init_check = getattr(Gst, "init_check", None)
    if not callable(init_check):
        return None, (
            "'gi.repository.Gst' here is a test stand-in, not the real GStreamer "
            "binding (it has no init_check)"
        )
    try:
        ok, _argv = init_check(None)
    except Exception as exc:
        return None, f"GStreamer failed to initialise ({type(exc).__name__}: {exc})"
    if not ok:
        return None, "GStreamer failed to initialise (Gst.init_check returned False)"
    return (Gst, GLib), None


def load_gst():
    """
    Return `(Gst, GLib)` from the real bindings, initialising GStreamer once.

    Idempotent and cached. Raises `GstUnavailable` (with the reason in words) when the
    bindings are missing. `Gst.init_check(None)` is safe to repeat, and the first call
    happens in the importing (main) thread.
    """
    global _bindings, _unavailable_reason
    if _bindings is _UNSET:
        found, reason = probe_gst()
        _bindings, _unavailable_reason = found, reason
    if _bindings is None:
        raise GstUnavailable(_unavailable_reason)
    return _bindings


def gst_unavailable_reason() -> str | None:
    """None when the real bindings load and initialise; otherwise the reason, in words."""
    try:
        load_gst()
    except GstUnavailable as exc:
        return str(exc)
    return None


def loaded_bindings():
    """`(Gst, GLib)` if `load_gst()` already succeeded, else None. Never imports."""
    return None if _bindings is _UNSET else _bindings


# ============================================================
# Pipelines
# ============================================================
def make_test_pipeline(source: str = "fakesrc", sink: str = "fakesink"):
    """
    Build a minimal, NOT started pipeline: `<source> name=source ! <sink> name=sink`.

    The source element is named `source` (the name the app's own error handling looks
    for), the sink `sink`. Elements are created with `Gst.ElementFactory.make`, so a
    missing plugin is a clear error rather than a parse failure. The pipeline stays in
    the NULL state; hand it to `teardown_pipeline` when done (the `gst_pipeline` fixture
    in conftest does both).
    """
    Gst, _GLib = load_gst()
    pipeline = Gst.Pipeline.new("test_pipeline")
    src = Gst.ElementFactory.make(source, "source")
    dst = Gst.ElementFactory.make(sink, "sink")
    if src is None or dst is None:
        missing = [name for name, el in ((source, src), (sink, dst)) if el is None]
        raise GstUnavailable(
            f"GStreamer element(s) {missing} are not installed, so the test pipeline "
            "cannot be built"
        )
    pipeline.add(src)
    pipeline.add(dst)
    if not src.link(dst):
        raise RuntimeError(f"could not link {source} to {sink} in the test pipeline")
    return pipeline


def teardown_pipeline(pipeline, timeout: float = 5.0) -> bool:
    """
    Stop a pipeline without hanging: set NULL, then wait at most `timeout` seconds.

    Returns True when the pipeline reached NULL. `Gst.Element.get_state` takes a timeout
    in nanoseconds, so this cannot wait unbounded the way a bare `get_state(CLOCK_TIME_NONE)`
    would.
    """
    Gst, _GLib = load_gst()
    pipeline.set_state(Gst.State.NULL)
    change, current, _pending = pipeline.get_state(int(timeout * Gst.SECOND))
    return current == Gst.State.NULL


# ============================================================
# Posting bus messages as a named element
# ============================================================
def _element(pipeline, element):
    """The Gst element for `element` (an element, or the name of one in `pipeline`)."""
    if not isinstance(element, str):
        return element
    found = pipeline.get_by_name(element)
    if found is None:
        raise LookupError(f"no element named {element!r} in the pipeline")
    return found


def post_error(pipeline, element, text: str = "simulated error from the test harness",
               debug: str = "posted by tests/gst_helpers.post_error") -> bool:
    """
    Post an ERROR message on `pipeline`'s bus, as `element` (or the element named so).

    The message is built with `Gst.Message.new_error(element, gerror, debug)`, so a
    handler that reads `message.src` sees that element and `message.parse_error()`
    returns `text` and `debug`. It is delivered by the bus's main-context source, so a
    watch only sees it while a GLib main loop runs (`run_main_loop`). Returns the result
    of `Gst.Bus.post` (False only if the bus is flushing).
    """
    Gst, GLib = load_gst()
    gerror = GLib.Error.new_literal(Gst.StreamError.quark(), text, Gst.StreamError.FAILED)
    message = Gst.Message.new_error(_element(pipeline, element), gerror, debug)
    return pipeline.get_bus().post(message)


def post_eos(pipeline, element) -> bool:
    """Post an end-of-stream message on `pipeline`'s bus, as `element` (or its name)."""
    Gst, _GLib = load_gst()
    return pipeline.get_bus().post(Gst.Message.new_eos(_element(pipeline, element)))


@contextmanager
def bus_watch(pipeline, callback: Callable, *user_data) -> Iterator[Any]:
    """
    Watch `pipeline`'s bus the way hailo_apps does, and undo it on exit.

    Equivalent to `bus.add_signal_watch(); bus.connect("message", callback, *user_data)`
    (GStreamerApp.create_pipeline), so `callback(bus, message, *user_data)` can be the
    app's own `bus_call`. Yields the bus. The watch fires only while a GLib main loop is
    running on the default context.
    """
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    handler_id = bus.connect("message", callback, *user_data)
    try:
        yield bus
    finally:
        bus.disconnect(handler_id)
        bus.remove_signal_watch()


# ============================================================
# Running the GLib main loop, always bounded
# ============================================================
def run_main_loop(until: Callable[[], bool] | None = None, *, seconds: float = 5.0,
                  poll_interval: float = 0.01) -> bool:
    """
    Run the default GLib main loop for a bounded time. Never waits unbounded.

    * `until=None`: run for exactly `seconds`, then return True. Use it to let queued
      bus messages and `GLib.idle_add` callbacks run.
    * `until=<predicate>`: run until the predicate is true or `seconds` have passed,
      whichever is first. Returns True if the predicate became true, False if time ran
      out. The predicate is checked every `poll_interval` seconds, on the loop.

    `seconds` must be in (0, MAX_LOOP_SECONDS]; anything else is a `ValueError`, so a
    typo cannot turn into a long wait. An exception raised by the predicate stops the
    loop and is re-raised here (PyGObject would otherwise print and swallow it).
    """
    if not 0 < seconds <= MAX_LOOP_SECONDS:
        raise ValueError(f"seconds must be above 0 and at most {MAX_LOOP_SECONDS}, got {seconds!r}")
    _Gst, GLib = load_gst()
    loop = GLib.MainLoop()
    state = {"met": False, "error": None}
    attached = {"deadline": True, "poll": until is not None}   # sources still on the context

    def on_deadline() -> bool:
        attached["deadline"] = False
        loop.quit()
        return GLib.SOURCE_REMOVE

    def on_poll() -> bool:
        try:
            met = bool(until())
        except BaseException as exc:  # noqa: BLE001 - re-raised after the loop stops
            state["error"] = exc
            met = True
        if met:
            state["met"] = state["error"] is None
            attached["poll"] = False
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    deadline_id = GLib.timeout_add(max(int(seconds * 1000), 1), on_deadline)
    poll_id = None
    if until is not None:
        poll_id = GLib.timeout_add(max(int(poll_interval * 1000), 1), on_poll)
    try:
        loop.run()
    finally:
        # Remove whichever of our two sources is still attached; a source that already
        # returned SOURCE_REMOVE must not be removed a second time.
        if attached["deadline"]:
            GLib.source_remove(deadline_id)
        if attached["poll"] and poll_id is not None:
            GLib.source_remove(poll_id)
    if state["error"] is not None:
        raise state["error"]
    return True if until is None else state["met"]
