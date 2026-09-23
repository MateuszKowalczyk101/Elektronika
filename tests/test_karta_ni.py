"""Tryb NI-DAQ na atrapie paczki nidaqmx (tests/atrapa_nidaqmx) - bez karty i sterownika."""
import importlib
import os
import sys

import pytest

from conftest import make_app, needs_display, run_until_idle

pytestmark = needs_display

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atrapa_nidaqmx")


@pytest.fixture
def ni(dialogs):
    """Laduje geiger_gui od nowa z atrapa nidaqmx zamiast prawdziwej paczki."""
    saved = {k: v for k, v in sys.modules.items() if k == "geiger_gui" or k.split(".")[0] == "nidaqmx"}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, FAKE)
    try:
        fake = importlib.import_module("nidaqmx")
        fake.reset()
        module = importlib.import_module("geiger_gui")
        app = make_app(module)
        app._csv_no_save_ok = True
        yield module, app, fake.LOG, dialogs
        errors = app._test_errors
        app.on_close()
        assert not errors, f"wyjatki w callbackach Tk: {errors}"
    finally:
        sys.path.remove(FAKE)
        for k in [k for k in sys.modules if k == "geiger_gui" or k.split(".")[0] == "nidaqmx"]:
            del sys.modules[k]
        sys.modules.update(saved)


def _measure(app, seconds=0.8):
    app.measurement_kind.set("Pojedynczy"); app.mode.set("time"); app.time_value.set(seconds)
    app.start_selected()
    run_until_idle(app)


def test_karta_wykryta_i_lista_licznikow(ni):
    module, app, log, _ = ni
    assert module.NIDAQ_AVAILABLE and not app.use_simulator.get()
    assert tuple(app.e_channel.cget("values")) == ("Dev1/ctr0", "Dev1/ctr1")
    assert "USB-6210" in app.daq_info.get()


def test_pomiar_konfiguruje_licznik_i_zamyka_zadanie(ni):
    module, app, log, _ = ni
    _measure(app)
    assert log["chans"][-1] == ("Dev1/ctr0", module.Edge.RISING, 0)
    assert log["terms"][-1] == "/Dev1/PFI0"
    assert app._last_counts > 50
    assert log["created"] == log["closed"] == 1


def test_inny_licznik_i_wejscie_pfi(ni):
    module, app, log, _ = ni
    app.counter_channel.set("Dev1/ctr1"); app.pfi_term.set("/PFI3")
    _measure(app, 0.3)
    assert log["chans"][-1][0] == "Dev1/ctr1" and log["terms"][-1] == "/Dev1/PFI3"


def test_seria_i_histogram_na_karcie(ni):
    module, app, log, _ = ni
    app.measurement_kind.set("Seria"); app.series_runs.set(3); app.series_pause_s.set(0.1)
    app.mode.set("time"); app.time_value.set(0.3)
    app.start_selected()
    run_until_idle(app)
    assert len(app.series_results) == 3
    app.hist_intervals.set(10); app.hist_interval_s.set(0.2)
    app.start_histogram()
    run_until_idle(app)
    assert len(app._hist_counts) + app._hist_skipped == 10
    assert log["created"] == log["closed"]


def test_zly_kanal_pokazuje_blad(ni):
    module, app, log, dialogs = ni
    app.counter_channel.set("Dev9/ctr0")
    app.start_selected()
    assert not app.running and dialogs[-1][1] == "Blad DAQ"
    assert log["created"] == log["closed"]
    assert str(app.btn_start.cget("state")) == "normal"


def test_odlaczenie_karty_w_trakcie(ni):
    module, app, log, dialogs = ni
    log["fail_read_after"] = 3
    app.time_value.set(5.0); app.start_selected()
    run_until_idle(app)
    assert "device removed" in dialogs[-1][2]
    assert log["created"] == log["closed"]
    assert str(app.btn_start.cget("state")) == "normal"


def test_zamkniecie_okna_w_trakcie_zamyka_zadanie(ni):
    module, app, log, _ = ni
    app.time_value.set(10.0); app.start_selected()
    for _ in range(20):
        app.update()
    app.on_close()
    assert log["created"] == log["closed"] == 1
