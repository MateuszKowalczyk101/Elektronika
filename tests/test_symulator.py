"""Testy calej aplikacji w trybie symulatora (bez karty NI)."""
import math
import time

import geiger_gui
from conftest import needs_display, run_until_idle

pytestmark = needs_display


def _single(app, seconds=1.0, rate=50.0):
    app.mode.set("time")
    app.time_value.set(seconds)
    app.time_unit.set("s")
    app.sim_rate_cps.set(rate)
    app.start_measurement()
    run_until_idle(app)


def test_pomiar_na_czas_daje_wynik_z_niepewnoscia(app):
    _single(app, 1.0)
    assert app._last_counts > 0 and app._last_t >= 1.0
    assert "±" in app.result_text.get()
    assert "uplynal czas" in app.status.get()


def test_jednostki_czasu(app):
    app.time_value.set(1500); app.time_unit.set("ms")
    assert abs(app._run_duration_seconds() - 1.5) < 1e-9
    app.time_value.set(0.5); app.time_unit.set("min")
    assert abs(app._run_duration_seconds() - 30.0) < 1e-9


def test_pomiar_do_n_zliczen(app):
    app.mode.set("counts"); app.target_counts.set(30); app.sim_rate_cps.set(100.0)
    app.start_measurement()
    run_until_idle(app)
    assert app._last_counts >= 30 and "osiagnieto N" in app.status.get()


def test_tlo_i_wynik_netto(app):
    _single(app, 1.0, rate=20.0)
    app.save_background()
    assert app._bg is not None and abs(app.decay_bg_cps.get() - round(app._bg[0], 4)) < 1e-9
    _single(app, 1.0, rate=400.0)
    assert "netto" in app.net_text.get()
    app.copy_result()
    assert "netto" in app.clipboard_get()


def test_stop_i_reset_w_trakcie(app):
    app.time_value.set(30.0); app.start_measurement()
    app.after(400, app.stop_measurement)
    run_until_idle(app)
    assert "Zatrzymano" in app.status.get() and str(app.btn_start.cget("state")) == "normal"
    app.start_measurement()
    app.after(300, app.reset_measurement)
    run_until_idle(app)
    assert app.current_counts.get() == 0 and app.result_text.get() == "Wynik: -"


def test_seria(app):
    app.series_runs.set(3); app.series_pause_s.set(0.1)
    app.mode.set("time"); app.time_value.set(0.3); app.sim_rate_cps.set(60.0)
    app.start_series()
    run_until_idle(app)
    assert len(app.series_results) == 3 and len(app.series_table.get_children()) == 3
    assert "Suma serii" in app.result_text.get()
    assert app.series_table.item(app.series_table.get_children()[0])["values"][1] == "czas"


def test_zanik_przez_start_odtwarza_t_polowkowe(app):
    orig_next = app._start_next_series_run

    def decaying_next():   # symulator "zanika" z t1/2 = 4 s
        tt = time.perf_counter() - app._series_start_perf
        app.sim_rate_cps.set(300.0 * math.exp(-math.log(2) / 4.0 * tt) + 5.0)
        orig_next()

    app._start_next_series_run = decaying_next
    app.mode.set("counts")   # zanik ma wlasne parametry - ustawienia zakladki Pomiar go nie dotycza
    app.decay_runs.set(8); app.decay_time_s.set(0.8); app.decay_pause_s.set(0.0); app.decay_bg_cps.set(5.0)
    app.start_decay()
    run_until_idle(app, timeout=40)
    lam, u_lam, t_half, u_half = app._decay_fit
    assert abs(t_half - 4.0) < 4 * u_half + 0.4
    assert app.mode.get() == "counts"


def test_zanik_bez_rozpadu_mowi_to_wprost(app):
    app._decay_t = [10, 40, 70, 100]; app._decay_n = [1000, 1010, 990, 1005]
    app._decay_dt = [20.0] * 4; app._decay_cps = [n / 20 for n in app._decay_n]
    app.decay_bg_cps.set(1.0)
    app._fit_and_plot_decay()
    assert app._decay_fit is None and "Zaniku nie widac" in app.lbl_decay_info.cget("text")


def test_plateau_krok_i_tryb_pomiaru(app):
    app.mode.set("counts")
    app.plateau_voltage.set(400); app.plateau_step_v.set(20); app.plateau_time_s.set(0.4)
    for _ in range(3):
        app.sim_rate_cps.set(80.0)
        app.start_plateau_point()
        run_until_idle(app)
    assert [p[0] for p in app.plateau_points] == [400, 420, 440]
    assert app.plateau_voltage.get() == 460          # pole przesunelo sie o krok
    assert app.mode.get() == "counts"                # plateau nie przestawia zakladki Pomiar
    assert "Nachylenie" in app.lbl_plateau_info.cget("text")


def test_plateau_pomija_prog_i_wyladowanie(app):
    # prog (0 -> 15 -> 52), plateau 52..57, wyladowanie 75; pomiary po 60 s
    for v, c in ((360, 0.5), (380, 15), (400, 52), (420, 54), (440, 55), (460, 56), (480, 57), (500, 75)):
        t = 60.0
        n = int(c * t)
        app.plateau_points.append((v, n, t, n / t, n ** 0.5 / t))
    app._redraw_plateau()
    f = app._plateau_fit
    assert (f["od"], f["do"]) == (400, 480)
    assert 5 < f["pct"] < 20                          # ok. 11 %/100 V, a nie ~100
    app.plateau_from_v.set("420"); app.plateau_to_v.set("480")
    app._redraw_plateau()
    assert app._plateau_fit["od"] == 420 and "recznie" in app._plateau_fit["how"]


def test_histogram(app):
    app.sim_rate_cps.set(40.0); app.hist_intervals.set(30); app.hist_interval_s.set(0.2)
    app.mode.set("counts")
    app.start_histogram()
    assert app._hist_after_id is not None   # zaplanowany odczyt na granicy przedzialu
    run_until_idle(app)
    assert len(app._hist_counts) + app._hist_skipped == 30
    assert app._hist_skipped <= 3
    assert app.mode.get() == "counts"
    assert "chi" in app.lbl_hist_info.cget("text")


def test_histogram_walidacja(app, dialogs):
    app.hist_intervals.set(5); app.start_histogram()
    assert not app.running and "od 10" in dialogs[-1][2]
    app.hist_intervals.set(30); app.hist_interval_s.set(0.1); app.start_histogram()
    assert not app.running and "0,2" in dialogs[-1][2]


def test_czas_martwy_w_symulatorze_to_100_us(app):
    for key, rate in (("r1", 1500.0), ("r12", 3000.0), ("r2", 1500.0)):
        app.sim_rate_cps.set(rate); app.dt_meas_s.set(1.5)
        app.start_deadtime_step(key)
        run_until_idle(app)
    tau, u = app._dt_result
    assert abs(tau - geiger_gui.SIM_DEAD_TIME_S) < 4 * u + 5e-6
    app.use_deadtime()
    assert app.dead_time_us.get() > 0


def test_csv_numeruje_pomiary_i_zapisuje_wynik(app, tmp_path):
    app.pick_csv_file()
    _single(app, 0.5)
    _single(app, 0.5)
    app.plateau_time_s.set(0.3); app.start_plateau_point(); run_until_idle(app)
    lines = (tmp_path / "geiger.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "sep=;"
    assert sum(1 for l in lines if l == "pomiar;nr_w_serii;t_s;N;sqrtN") == 1
    assert sum(1 for l in lines if l.startswith("# --- pomiar")) == 3
    assert sum(1 for l in lines if l.startswith("# wynik")) == 3
    assert any("zrodlo=SYMULATOR" in l for l in lines)
    rows = [l.split(";") for l in lines if l and l[0].isdigit()]
    assert rows and all(len(r) == 5 for r in rows)
    assert {r[0] for r in rows} == {"1", "2", "3"}
    assert "\u00b1" not in "".join(lines)            # Excel bez BOM zle czyta znaki spoza ASCII


def test_csv_otwarty_w_excelu(app, dialogs, monkeypatch):
    import geiger_gui

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied")
    app.pick_csv_file()
    monkeypatch.setattr(geiger_gui, "open", locked, raising=False)
    app.start_measurement()
    assert not app.running and "Excel" in dialogs[-1][2]


def test_stop_w_przerwie_serii_nie_dopisuje_wiersza(app, tmp_path):
    app.pick_csv_file()
    app.series_runs.set(3); app.series_pause_s.set(5.0); app.mode.set("time"); app.time_value.set(0.3)
    app.start_series()
    import time as _t
    end = _t.time() + 5
    while app.running and _t.time() < end:
        app.update(); _t.sleep(0.005)
    assert app.series_active and not app.running      # trwa przerwa po pierwszym pomiarze
    app.stop_measurement()
    assert not app.series_active and str(app.btn_start.cget("state")) == "normal"
    rows = [l for l in (tmp_path / "geiger.csv").read_text(encoding="utf-8").splitlines() if l[:1].isdigit()]
    assert all(r.split(";")[1] == "1" for r in rows)  # tylko pomiar nr 1 serii, bez dodatkowego wiersza


def test_netto_ponizej_tla(app):
    app._bg = (10.0, 0.5, 1000, 100.0)
    app._refresh_result_lines(200, 100.0)              # 2 CPS przy tle 10 CPS
    assert "MNIEJ niz tlo" in app.net_text.get()


def test_start_zalezy_od_zakladki(app):
    for tab, label in (("measure", "pomiar"), ("series", "seria"), ("stats", "histogram"),
                       ("plateau", "plateau"), ("decay", "zanik"), ("dead", "zrodlo 1"), ("csv", "pomiar")):
        app.nb.select(app._tabs[tab]); app.update()
        assert label in app.btn_start.cget("text"), (tab, app.btn_start.cget("text"))
    app.nb.select(app._tabs["series"]); app.update()
    app.series_runs.set(2); app.series_pause_s.set(0.0); app.time_value.set(0.2)
    app.start_selected()
    run_until_idle(app)
    assert len(app.series_results) == 2


def test_baner_symulatora(app):
    app.use_simulator.set(True); app._on_sim_toggle(); app.update()
    assert app.sim_banner.winfo_ismapped() and "SYMULATOR" in app.title()


def test_eksporty_csv_i_png(app, tmp_path):
    app.plateau_points = [(400, 100, 10.0, 10.0, 1.0), (420, 105, 10.0, 10.5, 1.0)]
    app._hist_T = 1.0; app._hist_counts = [5, 7, 6, 8, 4, 6, 7, 5, 6, 6, 9, 3]
    app._decay_t = [0, 10, 20]; app._decay_n = [400, 300, 220]; app._decay_dt = [10.0] * 3
    app._decay_cps = [40.0, 30.0, 22.0]
    app._fit_and_plot_decay()
    app.export_plateau_csv(); app.export_hist_csv(); app.export_decay_csv()
    for fig, name in ((app.fig, "wykres"), (app.pl_fig, "plateau"), (app.hist_fig, "histogram"), (app.dec_fig, "zanik")):
        app._save_figure_png(fig, name)
    names = {p.name for p in tmp_path.iterdir()}
    assert {"plateau.csv", "histogram.csv", "zanik.csv",
            "wykres.png", "plateau.png", "histogram.png", "zanik.png"} <= names


def test_bledne_dane_i_przecinek(app, dialogs):
    app.setvar(str(app.series_pause_s), "abc")
    app.start_series()
    assert not app.running and "Przerwa" in dialogs[-1][2]
    app.series_pause_s.set(0.2)
    app.setvar(str(app.time_value), "2,5")
    assert app._normalize_inputs() and app.time_value.get() == 2.5


def test_spacja(app):
    app.time_value.set(20.0)
    app.focus_force(); app.update()
    app.event_generate("<space>")
    assert app.running
    app.event_generate("<space>")
    assert not app.running
    # przycisk z fokusem: spacja wciska tylko przycisk (bez natychmiastowego STOP)
    app.btn_start.focus_force(); app.update()
    app.btn_start.event_generate("<space>"); app.update()
    assert app.running
    app.stop_measurement()


def test_wszystkie_pozycje_menu(app):
    menubar = app.nametowidget(app["menu"])
    for i in range(menubar.index("end") + 1):
        if menubar.type(i) != "cascade":
            continue
        sub = app.nametowidget(menubar.entrycget(i, "menu"))
        for j in range((sub.index("end") or 0) + 1):
            if sub.type(j) in ("command", "checkbutton") and sub.entrycget(j, "label") != "Zakoncz":
                sub.invoke(j)
                if sub.entrycget(j, "label") == "Pelny ekran":
                    sub.invoke(j)
    app.update()


def test_zanik_przerwany_stopem_dopasowuje_zebrane_punkty(app):
    orig_next = app._start_next_series_run

    def decaying_next():
        tt = time.perf_counter() - app._series_start_perf
        app.sim_rate_cps.set(400.0 * math.exp(-math.log(2) / 1.5 * tt) + 2.0)
        orig_next()

    app._start_next_series_run = decaying_next
    app.decay_runs.set(20); app.decay_time_s.set(0.4); app.decay_pause_s.set(0.0); app.decay_bg_cps.set(2.0)
    app.start_decay()
    end = time.time() + 15
    while len(app._decay_t) < 5 and time.time() < end:
        app.update(); time.sleep(0.005)
    app.stop_measurement()
    run_until_idle(app)
    assert len(app._decay_t) >= 5
    assert not app.lbl_decay_info.cget("text").startswith("Zanik: zmierzono")   # dopasowanie wykonane
