import os
import sys
import time
import tkinter as tk

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _display_available() -> bool:
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


HAS_DISPLAY = _display_available()
needs_display = pytest.mark.skipif(not HAS_DISPLAY, reason="brak ekranu (Tk) - testy GUI pominiete")


@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    """Zastepuje okienka dialogowe: komunikaty sa zapisywane, pytania dostaja 'Tak',
    a okno 'Zapisz jako' zwraca plik w tmp_path."""
    from tkinter import filedialog, messagebox
    log = []
    for kind in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(messagebox, kind, lambda *a, _k=kind, **kw: log.append((_k, a[0], a[1])))
    for kind in ("askyesno", "askokcancel", "askyesnocancel"):
        monkeypatch.setattr(messagebox, kind, lambda *a, **kw: True)
    monkeypatch.setattr(filedialog, "asksaveasfilename",
                        lambda **kw: str(tmp_path / (kw["initialfile"].split("_")[0] + kw["defaultextension"])))
    return log


def make_app(module):
    app = module.DAQCounterApp()
    errors = []
    app.report_callback_exception = lambda exc, val, tb: errors.append(val)
    app._test_errors = errors
    return app


def run_until_idle(app, timeout: float = 30.0):
    """Kreci petla Tk, az pomiar (albo cala seria) sie skonczy."""
    end = time.time() + timeout
    app.update()
    while (app.running or app.series_active) and time.time() < end:
        app.update()
        time.sleep(0.005)
    assert not app.running and not app.series_active, "pomiar nie skonczyl sie w zadanym czasie"


@pytest.fixture
def app(dialogs):
    import geiger_gui
    a = make_app(geiger_gui)
    a.use_simulator.set(True)
    a._csv_no_save_ok = True
    yield a
    errors = a._test_errors
    a.on_close()
    assert not errors, f"wyjatki w callbackach Tk: {errors}"
