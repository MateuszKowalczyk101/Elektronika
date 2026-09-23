"""
Geiger-Muller - licznik impulsow z karta NI USB-6210
=====================================================

GUI do akwizycji i wstepnej analizy zliczen z licznika Geigera-Mullera.

Stanowisko:
    sonda GM  ->  zasilacz WN + wzmacniacz/dyskryminator  ->  NI USB-6210 (PFI0)

Zasada dzialania (fizyka):
    Kazdy jonizujacy kwant/czastka wywoluje w liczniku GM impuls napieciowy.
    Wzmacniacz/dyskryminator formuje z tego impuls logiczny (TTL/CMOS, 0..5 V),
    ktory wchodzi na wejscie PFI0 karty. Licznik sprzetowy ctr0 zlicza zbocza
    narastajace. Program odczytuje narastajaca liczbe zliczen N, mierzy czas
    bramki t i liczy tempo zliczen CPS = N / t oraz niepewnosc Poissona sqrt(N).

Wymagania:
    - Windows (docelowo, z karta NI): pip install nidaqmx  + sterownik NI-DAQmx
    - Mac/Linux (do testow): dziala tryb SYMULATORA (rozklad Poissona), bez sprzetu
    - Zawsze:  pip install matplotlib

Uruchomienie:
    python geiger_gui.py

Uwagi sprzetowe (USB-6210):
    - GM daje impulsy o czasie martwym rzedu ~100 us, wiec software'owe odpytywanie
      co 100 ms w zupelnosci wystarcza - liczymy w sprzecie, nie w Pythonie.
    - Sygnal na PFI0 musi byc logiczny (0..5 V, prog ~1.4 V). Surowy impuls
      analogowy z anody GM trzeba najpierw uformowac dyskryminatorem.
    - Domyslne zrodlo ctr0 to inny PFI, dlatego jawnie ustawiamy termin na PFI0.
"""

import tkinter as tk
from tkinter import messagebox, filedialog, ttk

import math
import time
import csv
import os
import sys
import wave
import struct
import tempfile
import subprocess
from datetime import datetime, timedelta
import random

# Dzwiek: winsound tylko na Windows, w innym wypadku uzywamy dzwonka Tk.
try:
    import winsound
    WINSOUND_OK = True
except Exception:
    winsound = None
    WINSOUND_OK = False

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

print("PYTHON:", sys.executable)

# NI-DAQ import opcjonalny (na Macu / bez sterownika nie istnieje).
try:
    import nidaqmx
    from nidaqmx.constants import Edge
    from nidaqmx.system import System
    NIDAQ_AVAILABLE = True
except Exception:
    nidaqmx = None
    Edge = None
    System = None
    NIDAQ_AVAILABLE = False


def _fmt_pl(x: float, digits: int = 3) -> str:
    """Liczba z przecinkiem dziesietnym (Excel PL)."""
    return f"{x:.{digits}f}".replace(".", ",")


def _default_font_family() -> str:
    """Ladna czcionka natywna per system (Tk sam podmieni, jesli brak)."""
    if sys.platform.startswith("win"):
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return "DejaVu Sans"


class DAQCounterApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Geiger-Muller - NI USB-6210")
        self.geometry("1100x720")
        self.minsize(980, 620)

        self.font_family = _default_font_family()

        # --- MOTYW (tylko GUI) ---
        self.ui = {
            "bg": "#F4F6F5",
            "panel": "#E9EFEB",
            "card": "#FFFFFF",
            "border": "#D1D5DB",
            "text": "#1F2933",
            "muted": "#6B7280",
            "disabled": "#9CA3AF",

            "accent": "#16A34A",
            "accent_hover": "#15803D",
            "accent_soft": "#86EFAC",
            "accent_tint": "#DCFCE7",
            "accent_dark": "#166534",

            "danger": "#DC2626",
            "danger_hover": "#B91C1C",
            "danger_dark": "#991B1B",

            "neutral_btn": "#E5E7EB",
            "neutral_hover": "#D1D5DB",
            "neutral_text": "#374151",
        }

        self.configure(bg=self.ui["bg"])

        # ustawienia urzadzenia
        self.counter_channel = tk.StringVar(value="Dev1/ctr0")
        self.pfi_term = tk.StringVar(value="PFI0")

        # rodzaj pomiaru: pojedynczy / seria
        self.measurement_kind = tk.StringVar(value="Pojedynczy")

        # tryb pomiaru: time / counts
        self.mode = tk.StringVar(value="time")
        self.time_value = tk.DoubleVar(value=10.0)
        self.time_unit = tk.StringVar(value="s")
        self.target_counts = tk.IntVar(value=100)

        # zmienne wynikowe
        self.current_counts = tk.IntVar(value=0)
        self.elapsed_time = tk.DoubleVar(value=0.0)
        self.sqrt_counts = tk.DoubleVar(value=0.0)
        self.cps_value = tk.StringVar(value="0.000")

        # sterowanie zadaniem DAQ
        self.running = False
        self.task = None

        # czas
        self.start_perf = None
        self.target_time_s = None

        # status i blokowanie kontrolek
        self.status = tk.StringVar(value="Gotowe.")
        self._lock_widgets = []

        # CSV
        self.log_to_csv = tk.BooleanVar(value=False)
        self.csv_path = tk.StringVar(value="")
        self.csv_file = None
        self.csv_writer = None
        self.log_interval_ms = 200
        self._last_log_t = 0.0

        # SYMULATOR
        self.use_simulator = tk.BooleanVar(value=not NIDAQ_AVAILABLE)
        self.sim_rate_cps = tk.DoubleVar(value=50.0)
        self._sim_counts = 0
        self._sim_last_t = None

        # WYKRES
        self.plot_live = tk.BooleanVar(value=True)
        self._t_points = []
        self._n_points = []
        self._last_plot_t = 0.0
        self.plot_interval_ms = 200

        # SERIE
        self.series_runs = tk.IntVar(value=5)
        self.series_pause_s = tk.DoubleVar(value=1.0)
        self.series_active = False
        self.series_index = 0
        self.series_results = []

        # ostatnie wartosci surowe (do zapisu runa)
        self._last_counts = 0
        self._last_t = 0.0

        # PLATEAU (CPS vs napiecie)
        self.plateau_voltage = tk.DoubleVar(value=400.0)
        self.plateau_time_s = tk.DoubleVar(value=10.0)
        self.plateau_active = False
        self._plateau_voltage_pending = 0.0
        self.plateau_points = []          # (V, N, t, CPS, err)

        # ZANIK (dopasowanie t polowkowego)
        self.decay_bg_cps = tk.DoubleVar(value=0.0)
        self._is_decay = False
        self._series_start_perf = None
        self._decay_t = []
        self._decay_cps = []
        self._decay_n = []
        self._decay_dt = []
        self._decay_fit = None            # (lambda, t_half) albo None

        # lista wykrytych kart NI
        self.daq_info = tk.StringVar(value="")

        # DZWIEK
        self.sound_enabled = tk.BooleanVar(value=True)

        # KLIK jak w prawdziwym liczniku GM
        self.click_enabled = tk.BooleanVar(value=False)
        self._click_queue = 0
        self._click_after_id = None
        self._click_wav = None            # sciezka do wygenerowanego ticka
        self.click_interval_ms = 40       # pompka klikow ~25/s

        # CZAS MARTWY (dead time) w mikrosekundach + poprawiony CPS
        self.dead_time_us = tk.DoubleVar(value=0.0)
        self.cps_corr_value = tk.StringVar(value="-")

        # PASEK POSTEPU / POZOSTALO / MIERNIK TEMPA
        self.progress_value = tk.DoubleVar(value=0.0)   # 0..100
        self.remaining_text = tk.StringVar(value="")
        self._rate_scale = 10.0

        # GODZINA KONCA
        self.end_time_text = tk.StringVar(value="Koniec: -")
        self.series_end_time_text = tk.StringVar(value="Koniec serii: -")

        # Timery i zamykanie
        self._after_update_id = None
        self._after_series_id = None
        self._closing = False

        self._prepare_click_sound()

        self._apply_theme()
        self._build_gui()
        self._build_menu()

        # skrot: spacja = START/STOP
        self.bind_all("<space>", self._on_space)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # start jako zmaksymalizowane
        self.after(0, self._maximize_window)

    # ---------------------------------------------------------------
    # MENU + OKNO
    # ---------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Wybierz plik CSV...", command=self.pick_csv_file)
        m_file.add_command(label="Wyczysc wyniki serii", command=self.clear_series_results)
        m_file.add_separator()
        m_file.add_command(label="Zapisz wykres N(t) (PNG)...",
                           command=lambda: self._save_figure_png(self.fig, "wykres_N_t"))
        m_file.add_command(label="Zapisz wykres plateau (PNG)...",
                           command=lambda: self._save_figure_png(self.pl_fig, "plateau"))
        m_file.add_command(label="Zapisz wykres zaniku (PNG)...",
                           command=lambda: self._save_figure_png(self.dec_fig, "zanik"))
        m_file.add_separator()
        m_file.add_command(label="Eksport plateau (CSV)...", command=self.export_plateau_csv)
        m_file.add_command(label="Eksport zaniku (CSV)...", command=self.export_decay_csv)
        m_file.add_separator()
        m_file.add_command(label="Zakoncz", command=self.on_close)
        menubar.add_cascade(label="Plik", menu=m_file)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_command(label="Zmaksymalizuj", command=self._maximize_window)
        m_view.add_command(label="Pelny ekran", command=self._toggle_fullscreen)
        m_view.add_separator()
        m_view.add_checkbutton(label="Wykres na zywo", variable=self.plot_live)
        m_view.add_checkbutton(label="Dzwiek (sygnal konca)", variable=self.sound_enabled)
        m_view.add_checkbutton(label="Klik licznika", variable=self.click_enabled)
        menubar.add_cascade(label="Widok", menu=m_view)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="O programie", command=self._about_dialog)
        menubar.add_cascade(label="Pomoc", menu=m_help)

        self.config(menu=menubar)

    def _apply_theme(self):
        style = ttk.Style(self)

        # Motyw bazowy
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        ff = self.font_family

        # Ogolne
        style.configure(".", font=(ff, 10))
        style.configure("TFrame", background=self.ui["bg"])
        style.configure("TLabel", background=self.ui["bg"], foreground=self.ui["text"])
        style.configure("Muted.TLabel", background=self.ui["bg"], foreground=self.ui["muted"])
        style.configure("Big.TLabel", background=self.ui["card"], foreground=self.ui["accent_dark"],
                        font=(ff, 22, "bold"))
        style.configure("BigCap.TLabel", background=self.ui["card"], foreground=self.ui["muted"],
                        font=(ff, 9))

        # Karty / grupy
        style.configure("Card.TLabelframe", background=self.ui["card"],
                        bordercolor=self.ui["border"], relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=self.ui["card"],
                        foreground=self.ui["text"], font=(ff, 10, "bold"))
        style.configure("Card.TFrame", background=self.ui["card"])

        # Notebook (zakladki)
        style.configure("TNotebook", background=self.ui["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6), background=self.ui["panel"])
        style.map("TNotebook.Tab",
                  background=[("selected", self.ui["card"])],
                  foreground=[("selected", self.ui["text"])])

        # Entry / Combobox
        style.configure("TEntry", padding=(6, 4))
        style.configure("TCombobox", padding=(6, 4))

        # Przyciski
        style.configure("Accent.TButton", padding=(12, 8), background=self.ui["accent"],
                        foreground="#FFFFFF", borderwidth=0, focusthickness=1,
                        focuscolor=self.ui["accent_soft"])
        style.map("Accent.TButton",
                  background=[("active", self.ui["accent_hover"]), ("disabled", self.ui["neutral_btn"])],
                  foreground=[("disabled", self.ui["disabled"])])

        style.configure("Danger.TButton", padding=(12, 8), background=self.ui["danger"],
                        foreground="#FFFFFF", borderwidth=0)
        style.map("Danger.TButton",
                  background=[("active", self.ui["danger_hover"]), ("disabled", self.ui["neutral_btn"])],
                  foreground=[("disabled", self.ui["disabled"])])

        style.configure("Neutral.TButton", padding=(12, 8), background=self.ui["neutral_btn"],
                        foreground=self.ui["neutral_text"], borderwidth=0)
        style.map("Neutral.TButton",
                  background=[("active", self.ui["neutral_hover"]), ("disabled", self.ui["neutral_btn"])],
                  foreground=[("disabled", self.ui["disabled"])])

        # Pasek postepu
        style.configure("Geiger.Horizontal.TProgressbar",
                        troughcolor=self.ui["panel"], bordercolor=self.ui["border"],
                        background=self.ui["accent"], lightcolor=self.ui["accent"],
                        darkcolor=self.ui["accent"])

        # Treeview (tabelka serii)
        style.configure("Treeview", background=self.ui["card"], fieldbackground=self.ui["card"],
                        foreground=self.ui["text"], rowheight=26,
                        bordercolor=self.ui["border"], borderwidth=1)
        style.configure("Treeview.Heading", background=self.ui["panel"],
                        foreground=self.ui["text"], font=(ff, 10, "bold"))
        style.map("Treeview",
                  background=[("selected", self.ui["accent_tint"])],
                  foreground=[("selected", self.ui["accent_dark"])])

    def _maximize_window(self):
        # Windows: prawdziwe zmaksymalizowanie
        if sys.platform.startswith("win"):
            try:
                self.state("zoomed")
                return
            except Exception:
                pass
        # macOS: 'zoomed' nie istnieje - dopasuj do ekranu
        if sys.platform == "darwin":
            try:
                self.update_idletasks()
                w = self.winfo_screenwidth()
                h = self.winfo_screenheight()
                self.geometry(f"{w}x{h - 60}+0+0")
                return
            except Exception:
                pass
        # Linux i reszta
        try:
            self.attributes("-zoomed", True)
            return
        except Exception:
            pass
        w = int(self.winfo_screenwidth() * 0.95)
        h = int(self.winfo_screenheight() * 0.95)
        self.geometry(f"{w}x{h}+0+0")

    def _toggle_fullscreen(self):
        try:
            is_full = bool(self.attributes("-fullscreen"))
            self.attributes("-fullscreen", not is_full)
        except Exception:
            pass

    def _about_dialog(self):
        messagebox.showinfo(
            "O programie",
            "Geiger-Muller - NI USB-6210\n\n"
            "Zliczanie impulsow licznika GM przez sprzetowy licznik karty.\n"
            "Tryb NI-DAQ (Windows + sterownik) lub symulator (do testow).\n"
            "Pomiar pojedynczy i serie, wykres N(t), zapis CSV.\n\n"
            f"NI-DAQ w tym Pythonie: {'dostepne' if NIDAQ_AVAILABLE else 'BRAK (symulator)'}"
        )

    # ---------------------------------------------------------------
    # Timery / dzwiek / czas
    # ---------------------------------------------------------------
    def _cancel_update_timer(self):
        if self._after_update_id is not None:
            try:
                self.after_cancel(self._after_update_id)
            except Exception:
                pass
            self._after_update_id = None

    def _cancel_series_timer(self):
        if self._after_series_id is not None:
            try:
                self.after_cancel(self._after_series_id)
            except Exception:
                pass
            self._after_series_id = None

    def _cancel_all_timers(self):
        self._cancel_update_timer()
        self._cancel_series_timer()

    def _beep(self):
        if not self.sound_enabled.get():
            return
        try:
            if WINSOUND_OK:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.bell()  # cross-platform dzwonek Tk (Mac/Linux)
        except Exception:
            try:
                self.bell()
            except Exception:
                pass

    # --- KLIK jak w liczniku GM (cross-platform) ---
    def _prepare_click_sound(self):
        """Generuje raz krotki tick .wav w katalogu tymczasowym."""
        try:
            sr = 44100
            dur = 0.006                      # 6 ms
            n = int(sr * dur)
            path = os.path.join(tempfile.gettempdir(), "geiger_tick.wav")
            with wave.open(path, "w") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                frames = bytearray()
                for i in range(n):
                    env = math.exp(-i / (n * 0.35))          # szybkie wygaszenie
                    s = math.sin(2 * math.pi * 1800 * i / sr) * env
                    frames += struct.pack("<h", int(s * 22000))
                w.writeframes(bytes(frames))
            self._click_wav = path
        except Exception:
            self._click_wav = None

    def _play_click(self):
        if not self._click_wav:
            return
        try:
            if WINSOUND_OK:
                winsound.PlaySound(self._click_wav,
                                   winsound.SND_FILENAME | winsound.SND_ASYNC)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", self._click_wav],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                for player in ("paplay", "aplay"):
                    try:
                        subprocess.Popen([player, self._click_wav],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    def _start_click_pump(self):
        self._click_queue = 0
        if self._click_after_id is None:
            self._click_after_id = self.after(self.click_interval_ms, self._click_pump)

    def _stop_click_pump(self):
        if self._click_after_id is not None:
            try:
                self.after_cancel(self._click_after_id)
            except Exception:
                pass
            self._click_after_id = None
        self._click_queue = 0

    def _click_pump(self):
        self._click_after_id = None
        if self._closing:
            return
        if self.click_enabled.get() and self._click_queue > 0:
            self._click_queue -= 1
            self._play_click()
        if self.running:
            self._click_after_id = self.after(self.click_interval_ms, self._click_pump)

    def _on_space(self, event=None):
        w = self.focus_get()
        try:
            cls = w.winfo_class() if w is not None else ""
        except Exception:
            cls = ""
        if cls in ("TEntry", "Entry", "TCombobox"):
            return  # nie przechwytuj podczas edycji pola
        if self.running or self.series_active:
            self.stop_measurement()
        else:
            self.start_selected()
        return "break"

    def _update_expected_end_times(self, from_series: bool):
        now = datetime.now()
        is_time = (self.mode.get() == "time")
        run_s = float(self.target_time_s or 0.0)

        # Pojedynczy run
        if is_time:
            end_dt = now + timedelta(seconds=run_s)
            self.end_time_text.set("Koniec: " + end_dt.strftime("%H:%M:%S"))
        else:
            self.end_time_text.set("Koniec: - (tryb N)")

        # Cala seria - liczona od biezacego runa do konca (bez rosniecia w czasie)
        if from_series and is_time:
            runs = int(self.series_runs.get())
            pause = float(self.series_pause_s.get())
            runs_left_after = max(0, runs - self.series_index)  # runy po biezacym
            total = run_s + runs_left_after * (run_s + pause)
            series_end_dt = now + timedelta(seconds=total)
            self.series_end_time_text.set("Koniec serii: " + series_end_dt.strftime("%H:%M:%S"))
        elif not from_series:
            self.series_end_time_text.set("Koniec serii: -")
        # from_series w trybie N: zostaw to, co ustawil start_series ("-")

    def _run_duration_seconds(self) -> float:
        t = float(self.time_value.get())
        unit = self.time_unit.get()
        if unit == "ms":
            return t / 1000.0
        if unit == "min":
            return t * 60.0
        return t  # "s" i domyslnie

    # ---------------------------------------------------------------
    # GUI
    # ---------------------------------------------------------------
    def _build_gui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=0)
        paned.add(right, weight=1)

        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # --- WYNIKI NA ZYWO (duze liczby) ---
        frame_results = ttk.LabelFrame(left, text="Wyniki (na zywo)", style="Card.TLabelframe")
        frame_results.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 8))
        for c in range(3):
            frame_results.grid_columnconfigure(c, weight=1)

        def _big_cell(col, caption, var):
            cell = ttk.Frame(frame_results, style="Card.TFrame")
            cell.grid(row=0, column=col, sticky="nsew", padx=8, pady=(8, 4))
            ttk.Label(cell, textvariable=var, style="Big.TLabel").pack(anchor="center")
            ttk.Label(cell, text=caption, style="BigCap.TLabel").pack(anchor="center")

        _big_cell(0, "N (zliczenia)", self.current_counts)
        _big_cell(1, "t [s]", self.elapsed_time)
        _big_cell(2, "CPS", self.cps_value)

        ttk.Label(frame_results, text="sqrt(N):", style="BigCap.TLabel").grid(
            row=1, column=0, sticky="e", padx=(8, 2), pady=(0, 6))
        ttk.Label(frame_results, textvariable=self.sqrt_counts, style="BigCap.TLabel").grid(
            row=1, column=1, sticky="w", pady=(0, 6))
        corr = ttk.Frame(frame_results, style="Card.TFrame")
        corr.grid(row=1, column=2, sticky="w", pady=(0, 6))
        ttk.Label(corr, text="CPS popr.: ", style="BigCap.TLabel").pack(side="left")
        ttk.Label(corr, textvariable=self.cps_corr_value, style="BigCap.TLabel").pack(side="left")

        # miernik tempa (autoskalowany)
        rate_row = ttk.Frame(frame_results, style="Card.TFrame")
        rate_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 2))
        rate_row.grid_columnconfigure(1, weight=1)
        ttk.Label(rate_row, text="Tempo:", style="BigCap.TLabel").grid(row=0, column=0, sticky="w")
        self.rate_canvas = tk.Canvas(rate_row, height=18, bg=self.ui["panel"],
                                     highlightthickness=1, highlightbackground=self.ui["border"])
        self.rate_canvas.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # pasek postepu + pozostalo
        prog_row = ttk.Frame(frame_results, style="Card.TFrame")
        prog_row.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 6))
        prog_row.grid_columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog_row, orient="horizontal", mode="determinate",
                                        maximum=100, variable=self.progress_value,
                                        style="Geiger.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(prog_row, textvariable=self.remaining_text, style="BigCap.TLabel").grid(
            row=0, column=1, sticky="e", padx=(8, 0))

        ttk.Label(frame_results, textvariable=self.end_time_text, style="BigCap.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 2))
        ttk.Label(frame_results, textvariable=self.series_end_time_text, style="BigCap.TLabel").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        # --- ZAKLADKI USTAWIEN ---
        nb = ttk.Notebook(left)
        nb.grid(row=1, column=0, sticky="nsew", padx=6, pady=0)

        tab_measure = ttk.Frame(nb)
        tab_series = ttk.Frame(nb)
        tab_plateau = ttk.Frame(nb)
        tab_decay = ttk.Frame(nb)
        tab_device = ttk.Frame(nb)
        tab_csv = ttk.Frame(nb)

        nb.add(tab_measure, text="Pomiar")
        nb.add(tab_series, text="Seria")
        nb.add(tab_plateau, text="Plateau")
        nb.add(tab_decay, text="Zanik")
        nb.add(tab_device, text="Urzadzenie")
        nb.add(tab_csv, text="Zapis")

        # TAB: Pomiar
        tab_measure.grid_columnconfigure(1, weight=1)

        kind_row = ttk.Frame(tab_measure)
        kind_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(10, 6))
        kind_row.grid_columnconfigure(1, weight=1)

        ttk.Label(kind_row, text="Rodzaj:").grid(row=0, column=0, sticky="w")
        self.cb_kind = ttk.Combobox(kind_row, textvariable=self.measurement_kind,
                                    values=["Pojedynczy", "Seria", "Zanik"], state="readonly", width=14)
        self.cb_kind.grid(row=0, column=1, sticky="w", padx=6)
        self.cb_kind.bind("<<ComboboxSelected>>", lambda e: self._sync_start_label())

        frame_mode = ttk.LabelFrame(tab_measure, text="Tryb zliczania", style="Card.TLabelframe")
        frame_mode.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=8)
        frame_mode.grid_columnconfigure(1, weight=1)

        self.rb_time = ttk.Radiobutton(frame_mode, text="Liczenie w zadanym czasie",
                                       variable=self.mode, value="time")
        self.rb_time.grid(row=0, column=0, columnspan=3, sticky="w", pady=(6, 2), padx=8)

        ttk.Label(frame_mode, text="Czas:").grid(row=1, column=0, sticky="e", padx=(8, 4), pady=4)
        self.e_time = ttk.Entry(frame_mode, textvariable=self.time_value, width=10)
        self.e_time.grid(row=1, column=1, sticky="w", pady=4)

        self.time_unit.set(self.time_unit.get() or "s")
        self.om_unit = ttk.Combobox(frame_mode, textvariable=self.time_unit,
                                    values=["ms", "s", "min"], state="readonly", width=6)
        self.om_unit.grid(row=1, column=2, sticky="w", padx=6, pady=4)

        self.rb_counts = ttk.Radiobutton(frame_mode, text="Pomiar czasu do N impulsow",
                                         variable=self.mode, value="counts")
        self.rb_counts.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 2), padx=8)

        ttk.Label(frame_mode, text="N:").grid(row=3, column=0, sticky="e", padx=(8, 4), pady=(0, 8))
        self.e_counts = ttk.Entry(frame_mode, textvariable=self.target_counts, width=10)
        self.e_counts.grid(row=3, column=1, sticky="w", pady=(0, 8))

        # TAB: Seria
        tab_series.grid_columnconfigure(0, weight=1)
        tab_series.grid_rowconfigure(2, weight=1)

        series_top = ttk.LabelFrame(tab_series, text="Parametry serii", style="Card.TLabelframe")
        series_top.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 8))
        series_top.grid_columnconfigure(1, weight=1)
        series_top.grid_columnconfigure(3, weight=1)

        ttk.Label(series_top, text="Liczba runow:").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=6)
        self.e_runs = ttk.Entry(series_top, textvariable=self.series_runs, width=10)
        self.e_runs.grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(series_top, text="Przerwa [s]:").grid(row=0, column=2, sticky="e", padx=(12, 4), pady=6)
        self.e_pause = ttk.Entry(series_top, textvariable=self.series_pause_s, width=10)
        self.e_pause.grid(row=0, column=3, sticky="w", pady=6)

        self.lbl_series_progress = ttk.Label(tab_series, text="Seria: -")
        self.lbl_series_progress.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

        table_frame = ttk.Frame(tab_series)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 10))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        columns = ("run", "mode", "t", "n", "cps")
        self.series_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        self.series_table.heading("run", text="Run")
        self.series_table.heading("mode", text="Tryb")
        self.series_table.heading("t", text="Czas [s]")
        self.series_table.heading("n", text="N")
        self.series_table.heading("cps", text="CPS")
        self.series_table.column("run", width=60, anchor="center")
        self.series_table.column("mode", width=90, anchor="center")
        self.series_table.column("t", width=110, anchor="e")
        self.series_table.column("n", width=90, anchor="e")
        self.series_table.column("cps", width=110, anchor="e")

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.series_table.yview)
        self.series_table.configure(yscrollcommand=yscroll.set)
        self.series_table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        # TAB: Urzadzenie
        tab_device.grid_columnconfigure(1, weight=1)

        dev_box = ttk.LabelFrame(tab_device, text="DAQ / Symulator", style="Card.TLabelframe")
        dev_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 8))
        dev_box.grid_columnconfigure(1, weight=1)

        ttk.Label(dev_box, text="Kanal licznika:").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=6)
        chan_row = ttk.Frame(dev_box)
        chan_row.grid(row=0, column=1, sticky="w", pady=6)
        # edytowalny: lista wykrytych kanalow, ale mozna tez wpisac recznie
        self.e_channel = ttk.Combobox(chan_row, textvariable=self.counter_channel, width=16)
        self.e_channel.pack(side="left")
        self.btn_refresh_dev = ttk.Button(chan_row, text="Odswiez",
                                          command=lambda: self.refresh_devices(show_errors=True))
        self.btn_refresh_dev.pack(side="left", padx=(6, 0))

        ttk.Label(dev_box, text="Wejscie sygnalu (PFI):").grid(row=1, column=0, sticky="e", padx=(8, 4), pady=6)
        self.e_pfi = ttk.Entry(dev_box, textvariable=self.pfi_term, width=18)
        self.e_pfi.grid(row=1, column=1, sticky="w", pady=6)

        self.cb_sim = ttk.Checkbutton(dev_box, text="Tryb symulatora",
                                      variable=self.use_simulator, command=self._on_sim_toggle)
        self.cb_sim.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))

        rate_row = ttk.Frame(dev_box)
        rate_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 8))
        ttk.Label(rate_row, text="Szybkosc (CPS):").grid(row=0, column=0, sticky="w")
        self.e_rate = ttk.Entry(rate_row, textvariable=self.sim_rate_cps, width=10)
        self.e_rate.grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(dev_box, textvariable=self.daq_info, wraplength=320, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 10))

        ttk.Label(dev_box, text="Czas martwy tau [us]:").grid(row=5, column=0, sticky="e", padx=(8, 4), pady=(0, 8))
        self.e_tau = ttk.Entry(dev_box, textvariable=self.dead_time_us, width=10)
        self.e_tau.grid(row=5, column=1, sticky="w", pady=(0, 8))

        # TAB: Zapis
        tab_csv.grid_columnconfigure(0, weight=1)

        csv_box = ttk.LabelFrame(tab_csv, text="CSV", style="Card.TLabelframe")
        csv_box.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 8))
        csv_box.grid_columnconfigure(0, weight=1)

        self.cb_csv = ttk.Checkbutton(csv_box, text="Zapis do CSV",
                                      variable=self.log_to_csv, command=self._refresh_csv_toggle_button)
        self.cb_csv.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))

        self.btn_pick = ttk.Button(csv_box, text="Wybierz plik CSV", command=self.pick_csv_file)
        self.btn_pick.grid(row=0, column=1, sticky="e", padx=8, pady=(8, 4))

        lbl_path = ttk.Label(csv_box, textvariable=self.csv_path, anchor="w", justify="left")
        lbl_path.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 10))

        self._build_plateau_tab(tab_plateau)
        self._build_decay_tab(tab_decay)

        # PRAWA: wykres
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        frame_plot = ttk.LabelFrame(right, text="Wykres N(t)", style="Card.TLabelframe")
        frame_plot.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        frame_plot.grid_rowconfigure(1, weight=1)
        frame_plot.grid_columnconfigure(0, weight=1)

        top_plot = ttk.Frame(frame_plot)
        top_plot.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        ttk.Checkbutton(top_plot, text="Wykres na zywo", variable=self.plot_live).pack(side="left", anchor="w")
        ttk.Checkbutton(top_plot, text="Klik licznika", variable=self.click_enabled).pack(side="left", anchor="w", padx=(16, 0))
        ttk.Button(top_plot, text="Zapisz PNG", style="Neutral.TButton",
                   command=lambda: self._save_figure_png(self.fig, "wykres_N_t")).pack(side="right")

        self.fig = Figure(figsize=(6.5, 4.2), dpi=100)
        self.fig.patch.set_facecolor(self.ui["card"])

        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(self.ui["card"])
        self.ax.set_xlabel("t [s]")
        self.ax.set_ylabel("N")
        self.ax.tick_params(colors=self.ui["muted"])
        for spine in self.ax.spines.values():
            spine.set_color(self.ui["border"])
        self.ax.xaxis.label.set_color(self.ui["text"])
        self.ax.yaxis.label.set_color(self.ui["text"])
        self.ax.grid(True, alpha=0.2)

        self.line, = self.ax.plot([], [], color=self.ui["accent"], linewidth=2)
        try:
            self.fig.tight_layout()
        except Exception:
            pass

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame_plot)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # DOLNY PASEK: przyciski + status
        bottom = ttk.Frame(self)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        bottom.grid_columnconfigure(2, weight=1)

        self.btn_start = ttk.Button(bottom, text="START (pojedynczy)",
                                    command=self.start_selected, style="Accent.TButton")
        self.btn_start.grid(row=0, column=0, padx=(0, 8), pady=6, sticky="w")

        self.btn_stop = ttk.Button(bottom, text="STOP", command=self.stop_measurement,
                                   state="disabled", style="Danger.TButton")
        self.btn_stop.grid(row=0, column=1, padx=(0, 8), pady=6, sticky="w")

        self.btn_reset = ttk.Button(bottom, text="RESET", command=self.reset_measurement,
                                    style="Neutral.TButton")
        self.btn_reset.grid(row=0, column=2, padx=(0, 8), pady=6, sticky="w")

        self.btn_csv_pick_bottom = ttk.Button(bottom, text="Wybierz plik CSV", command=self.pick_csv_file)
        self.btn_csv_pick_bottom.grid(row=0, column=3, padx=(0, 8), pady=6, sticky="w")

        self.btn_csv_toggle_bottom = ttk.Button(bottom, text="Zapis CSV: OFF",
                                                command=self.toggle_csv_logging, style="Neutral.TButton")
        self.btn_csv_toggle_bottom.grid(row=0, column=4, padx=(0, 8), pady=6, sticky="w")

        ttk.Button(bottom, text="Wyczysc serie", command=self.clear_series_results).grid(
            row=0, column=5, padx=(0, 8), pady=6, sticky="w")

        ttk.Button(bottom, text="Zakoncz", command=self.on_close).grid(
            row=0, column=6, padx=(0, 0), pady=6, sticky="e")

        status_line = ttk.Label(bottom, textvariable=self.status, anchor="w", style="Muted.TLabel")
        status_line.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(0, 6))

        # rzeczy blokowane podczas pomiaru
        self._lock_widgets = [
            self.cb_kind,
            self.e_channel, self.btn_refresh_dev, self.e_pfi, self.cb_sim, self.e_rate, self.e_tau,
            self.rb_time, self.e_time, self.om_unit, self.rb_counts, self.e_counts,
            self.cb_csv, self.btn_pick, self.btn_csv_pick_bottom, self.btn_csv_toggle_bottom,
            self.e_runs, self.e_pause,
            self.btn_plateau, self.e_pl_voltage, self.e_pl_time, self.e_decay_bg,
        ]

        self.refresh_devices(show_errors=False)
        self._on_sim_toggle()
        self._sync_start_label()
        self._refresh_csv_toggle_button()

    def _sync_start_label(self):
        kind = self.measurement_kind.get()
        if kind == "Seria":
            self.btn_start.configure(text="START (seria)")
        elif kind == "Zanik":
            self.btn_start.configure(text="START (zanik)")
        else:
            self.btn_start.configure(text="START (pojedynczy)")

    def start_selected(self):
        kind = self.measurement_kind.get()
        if kind == "Seria":
            self.start_series()
        elif kind == "Zanik":
            self.start_series(decay=True)
        else:
            self.start_measurement()

    # ---------------------------------------------------------------
    # ZAKLADKA PLATEAU
    # ---------------------------------------------------------------
    def _build_plateau_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        box = ttk.LabelFrame(parent, text="Punkt plateau", style="Card.TLabelframe")
        box.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 8))
        box.grid_columnconfigure(1, weight=1)

        ttk.Label(box, text="Napiecie WN [V]:").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=6)
        self.e_pl_voltage = ttk.Entry(box, textvariable=self.plateau_voltage, width=10)
        self.e_pl_voltage.grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(box, text="Czas na punkt [s]:").grid(row=1, column=0, sticky="e", padx=(8, 4), pady=6)
        self.e_pl_time = ttk.Entry(box, textvariable=self.plateau_time_s, width=10)
        self.e_pl_time.grid(row=1, column=1, sticky="w", pady=6)

        self.btn_plateau = ttk.Button(box, text="Zmierz punkt", command=self.start_plateau_point,
                                      style="Accent.TButton")
        self.btn_plateau.grid(row=2, column=0, padx=8, pady=(4, 8), sticky="w")
        ttk.Button(box, text="Wyczysc", command=self.clear_plateau).grid(
            row=2, column=1, padx=8, pady=(4, 8), sticky="w")

        pl_export = ttk.Frame(box)
        pl_export.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Button(pl_export, text="Eksport CSV", command=self.export_plateau_csv).pack(side="left")
        ttk.Button(pl_export, text="Zapisz PNG",
                   command=lambda: self._save_figure_png(self.pl_fig, "plateau")).pack(side="left", padx=(8, 0))

        self.lbl_plateau_info = ttk.Label(parent, text="Ustaw napiecie na zasilaczu, potem zmierz punkt.",
                                          style="Muted.TLabel")
        self.lbl_plateau_info.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

        mid = ttk.Frame(parent)
        mid.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        mid.grid_rowconfigure(0, weight=1)
        mid.grid_columnconfigure(0, weight=1)

        cols = ("v", "n", "t", "cps", "err")
        self.plateau_table = ttk.Treeview(mid, columns=cols, show="headings", height=5)
        for c, txt, w in (("v", "V", 70), ("n", "N", 70), ("t", "t [s]", 70),
                          ("cps", "CPS", 90), ("err", "+/-", 70)):
            self.plateau_table.heading(c, text=txt)
            self.plateau_table.column(c, width=w, anchor="e")
        self.plateau_table.grid(row=0, column=0, sticky="nsew")
        pls = ttk.Scrollbar(mid, orient="vertical", command=self.plateau_table.yview)
        self.plateau_table.configure(yscrollcommand=pls.set)
        pls.grid(row=0, column=1, sticky="ns")

        self.pl_fig = Figure(figsize=(3.6, 2.2), dpi=100)
        self.pl_fig.patch.set_facecolor(self.ui["card"])
        self.pl_ax = self.pl_fig.add_subplot(111)
        self._style_ax(self.pl_ax, "Napiecie [V]", "CPS")
        self.pl_canvas = FigureCanvasTkAgg(self.pl_fig, master=mid)
        self.pl_canvas.get_tk_widget().grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        mid.grid_rowconfigure(1, weight=1)

    def clear_plateau(self):
        self.plateau_points = []
        for it in self.plateau_table.get_children():
            self.plateau_table.delete(it)
        self.lbl_plateau_info.configure(text="Ustaw napiecie na zasilaczu, potem zmierz punkt.")
        self._redraw_plateau()

    def start_plateau_point(self):
        if self.running or self.series_active or self.plateau_active or self._closing:
            return
        try:
            V = float(self.plateau_voltage.get())
            tp = float(self.plateau_time_s.get())
            if tp <= 0:
                raise ValueError("Czas na punkt musi byc > 0.")
        except Exception as e:
            messagebox.showerror("Plateau", str(e))
            return

        self.mode.set("time")
        self._plateau_voltage_pending = V
        self.plateau_active = True
        self.stop_task()
        self._close_csv()
        ok = self._start_run_internal(from_series=False, override_time_s=tp)
        if not ok:
            self.plateau_active = False
        else:
            self.status.set(f"Plateau: pomiar przy {V:.0f} V...")

    def _store_plateau_point(self):
        V = float(self._plateau_voltage_pending)
        t = float(self._last_t)
        n = int(self._last_counts)
        cps = (n / t) if t > 0 else 0.0
        err = (math.sqrt(max(n, 0)) / t) if t > 0 else 0.0
        self.plateau_points.append((V, n, t, cps, err))
        self.plateau_table.insert("", "end",
                                  values=(f"{V:.0f}", n, f"{t:.2f}", f"{cps:.2f}", f"{err:.2f}"))
        self._redraw_plateau()

    def _redraw_plateau(self):
        try:
            self.pl_ax.clear()
            self._style_ax(self.pl_ax, "Napiecie [V]", "CPS")
            if self.plateau_points:
                pts = sorted(self.plateau_points, key=lambda p: p[0])
                xs = [p[0] for p in pts]
                ys = [p[3] for p in pts]
                es = [p[4] for p in pts]
                self.pl_ax.errorbar(xs, ys, yerr=es, fmt="o-", color=self.ui["accent"],
                                    ecolor=self.ui["muted"], capsize=3, linewidth=1.5, markersize=5)
                # nachylenie plateau [%/100V] z regresji liniowej
                if len(xs) >= 2:
                    slope = self._lin_slope(xs, ys)
                    mean_cps = sum(ys) / len(ys)
                    if mean_cps > 0:
                        pct = slope / mean_cps * 100.0 * 100.0  # %/100V
                        self.lbl_plateau_info.configure(
                            text=f"Punktow: {len(xs)}   Nachylenie plateau ~ {pct:.1f} %/100V")
            self.pl_fig.tight_layout()
            self.pl_canvas.draw_idle()
        except Exception:
            pass

    # ---------------------------------------------------------------
    # ZAKLADKA ZANIK
    # ---------------------------------------------------------------
    def _build_decay_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        box = ttk.LabelFrame(parent, text="Zanik promieniotworczy", style="Card.TLabelframe")
        box.grid(row=0, column=0, sticky="ew", padx=8, pady=(10, 8))
        box.grid_columnconfigure(1, weight=1)

        ttk.Label(box, text="Tlo [CPS]:").grid(row=0, column=0, sticky="e", padx=(8, 4), pady=6)
        self.e_decay_bg = ttk.Entry(box, textvariable=self.decay_bg_cps, width=10)
        self.e_decay_bg.grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(box, text=("Uzywa parametrow z zakladki Seria:\n"
                             "liczba runow, przerwa i czas pomiaru.\n"
                             "Start przyciskiem START (rodzaj: Zanik)."),
                  style="Muted.TLabel", justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        dec_export = ttk.Frame(box)
        dec_export.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Button(dec_export, text="Eksport CSV", command=self.export_decay_csv).pack(side="left")
        ttk.Button(dec_export, text="Zapisz PNG",
                   command=lambda: self._save_figure_png(self.dec_fig, "zanik")).pack(side="left", padx=(8, 0))

        self.lbl_decay_info = ttk.Label(parent, text="t1/2: -", style="Muted.TLabel")
        self.lbl_decay_info.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

        holder = ttk.Frame(parent)
        holder.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        self.dec_fig = Figure(figsize=(3.6, 2.4), dpi=100)
        self.dec_fig.patch.set_facecolor(self.ui["card"])
        self.dec_ax = self.dec_fig.add_subplot(111)
        self._style_ax(self.dec_ax, "t [s]", "CPS")
        self.dec_canvas = FigureCanvasTkAgg(self.dec_fig, master=holder)
        self.dec_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def _fit_and_plot_decay(self):
        bg = float(self.decay_bg_cps.get())
        xs, ys_ln, ys_raw = [], [], []
        for tt, c in zip(self._decay_t, self._decay_cps):
            val = c - bg
            ys_raw.append(c)
            if val > 0:
                xs.append(tt)
                ys_ln.append(math.log(val))

        a = b = None
        self._decay_fit = None
        if len(xs) >= 2:
            b = self._lin_slope(xs, ys_ln)
            mean_x = sum(xs) / len(xs)
            mean_y = sum(ys_ln) / len(ys_ln)
            a = mean_y - b * mean_x
            lam = -b
            if lam > 0:
                t_half = math.log(2) / lam
                self._decay_fit = (lam, t_half)
                txt = f"t1/2 = {t_half:.4g} s   (lambda = {lam:.4g} /s)"
            else:
                txt = "Dopasowanie nie wskazuje rozpadu (lambda <= 0)."
            self.lbl_decay_info.configure(text=txt)
            self.status.set("Zanik: " + txt)
        else:
            self.lbl_decay_info.configure(text="Za malo punktow > tla do dopasowania.")

        try:
            self.dec_ax.clear()
            self._style_ax(self.dec_ax, "t [s]", "CPS")
            if self._decay_t:
                self.dec_ax.plot(self._decay_t, ys_raw, "o", color=self.ui["accent"], markersize=5,
                                 label="dane")
                if a is not None and b is not None:
                    xmin, xmax = min(self._decay_t), max(self._decay_t)
                    xx = [xmin + (xmax - xmin) * k / 60.0 for k in range(61)]
                    yy = [bg + math.exp(a + b * x) for x in xx]
                    self.dec_ax.plot(xx, yy, "-", color=self.ui["danger"], linewidth=1.5, label="dopasowanie")
                self.dec_ax.legend(fontsize=8, loc="best")
            self.dec_fig.tight_layout()
            self.dec_canvas.draw_idle()
        except Exception:
            pass

    # --- narzedzia wspolne ---
    def _style_ax(self, ax, xlabel, ylabel):
        ax.set_facecolor(self.ui["card"])
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.tick_params(colors=self.ui["muted"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(self.ui["border"])
        ax.xaxis.label.set_color(self.ui["text"])
        ax.yaxis.label.set_color(self.ui["text"])
        ax.grid(True, alpha=0.2)

    def _lin_slope(self, xs, ys):
        n = len(xs)
        sx = sum(xs); sy = sum(ys)
        sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        return 0.0 if denom == 0 else (n * sxy - sx * sy) / denom

    def _on_sim_toggle(self):
        if self.use_simulator.get():
            self.status.set("Tryb symulatora aktywny.")
        else:
            if not NIDAQ_AVAILABLE:
                self.use_simulator.set(True)
                self.status.set("Brak NI-DAQ w tym Pythonie. Zostaje symulator.")
            else:
                self.status.set("Tryb NI-DAQ aktywny.")

    # ---------------------------------------------------------------
    # WYKRYWANIE KART NI
    # ---------------------------------------------------------------
    def _scan_counter_channels(self):
        """Zwraca (lista kanalow licznika np. 'Dev1/ctr0', opis dla uzytkownika, czy_blad)."""
        if not NIDAQ_AVAILABLE:
            return [], "NI-DAQ: brak paczki nidaqmx w tym Pythonie (symulator).", True
        try:
            devices = list(System.local().devices)
        except Exception as e:
            return [], f"NI-DAQ: blad sterownika NI-DAQmx: {e}", True
        if not devices:
            return [], ("NI-DAQ: nidaqmx dziala, ale nie widzi zadnej karty. Sprawdz kabel USB "
                        "i czy NI MAX widzi karte."), True

        channels, names = [], []
        for d in devices:
            try:
                channels.extend(d.ci_physical_chans.channel_names)
            except Exception:
                pass
            try:
                names.append(f"{d.name} ({d.product_type})")
            except Exception:
                names.append(d.name)
        if not channels:
            return [], "NI-DAQ: karty " + ", ".join(names) + " nie maja licznikow (ctr).", True
        return channels, "NI-DAQ: " + ", ".join(names), False

    def refresh_devices(self, show_errors: bool = True):
        channels, info, is_error = self._scan_counter_channels()
        self.e_channel.configure(values=channels)
        if channels and self.counter_channel.get() not in channels:
            self.counter_channel.set(channels[0])
        self.daq_info.set(info)
        if is_error and show_errors:
            messagebox.showwarning("NI-DAQ", info)
        elif not is_error:
            self.status.set(f"Wykryte kanaly licznika: {', '.join(channels)}")

    # ---------------------------------------------------------------
    # PNG / EKSPORT
    # ---------------------------------------------------------------
    def _ask_save_path(self, title: str, prefix: str, ext: str, label: str):
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return filedialog.asksaveasfilename(
            title=title,
            defaultextension=ext,
            initialdir=docs,
            initialfile=f"{prefix}_{ts}{ext}",
            filetypes=[(label, f"*{ext}")]
        )

    def _save_figure_png(self, fig, prefix: str):
        path = self._ask_save_path("Zapisz wykres (PNG)", prefix, ".png", "PNG")
        if not path:
            return
        try:
            fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
            self.status.set(f"Zapisano wykres: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("PNG", f"Nie udalo sie zapisac wykresu.\n{e}")

    def _write_table_csv(self, path: str, comments, header, rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("sep=;\n")
            f.write(f"# created={datetime.now().isoformat(timespec='seconds')}\n")
            for line in comments:
                f.write(f"# {line}\n")
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    def export_plateau_csv(self):
        if not self.plateau_points:
            messagebox.showinfo("Plateau", "Brak punktow plateau do zapisania.")
            return
        path = self._ask_save_path("Eksport plateau (CSV)", "plateau", ".csv", "CSV")
        if not path:
            return

        pts = sorted(self.plateau_points, key=lambda p: p[0])
        comments = ["kind=plateau", f"points={len(pts)}"]
        if len(pts) >= 2:
            xs = [p[0] for p in pts]
            ys = [p[3] for p in pts]
            mean_cps = sum(ys) / len(ys)
            if mean_cps > 0:
                pct = self._lin_slope(xs, ys) / mean_cps * 100.0 * 100.0
                comments.append(f"slope_pct_per_100V={pct:.3f}")
        rows = [[_fmt_pl(V, 1), n, _fmt_pl(t), _fmt_pl(cps), _fmt_pl(err)]
                for (V, n, t, cps, err) in pts]
        try:
            self._write_table_csv(path, comments, ["U_V", "N", "t_s", "CPS", "err_CPS"], rows)
            self.status.set(f"Zapisano plateau: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Plateau", f"Nie udalo sie zapisac pliku.\n{e}")

    def export_decay_csv(self):
        if not self._decay_t:
            messagebox.showinfo("Zanik", "Brak punktow zaniku do zapisania.")
            return
        path = self._ask_save_path("Eksport zaniku (CSV)", "zanik", ".csv", "CSV")
        if not path:
            return

        bg = float(self.decay_bg_cps.get())
        comments = ["kind=decay", f"bg_cps={bg}"]
        if self._decay_fit is not None:
            lam, t_half = self._decay_fit
            comments += [f"lambda_per_s={lam:.6g}", f"t_half_s={t_half:.6g}"]
        else:
            comments.append("fit=brak")

        rows = []
        for tt, n, dt, cps in zip(self._decay_t, self._decay_n, self._decay_dt, self._decay_cps):
            err = math.sqrt(max(n, 0)) / dt if dt > 0 else 0.0
            rows.append([_fmt_pl(tt), n, _fmt_pl(dt), _fmt_pl(cps), _fmt_pl(err), _fmt_pl(cps - bg)])
        try:
            self._write_table_csv(path, comments,
                                  ["t_mid_s", "N", "t_pomiaru_s", "CPS", "err_CPS", "CPS_netto"], rows)
            self.status.set(f"Zapisano zanik: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Zanik", f"Nie udalo sie zapisac pliku.\n{e}")

    # ---------------------------------------------------------------
    # CSV
    # ---------------------------------------------------------------
    def _refresh_csv_toggle_button(self):
        if self.log_to_csv.get():
            self.btn_csv_toggle_bottom.configure(text="Zapis CSV: ON", style="Accent.TButton")
        else:
            self.btn_csv_toggle_bottom.configure(text="Zapis CSV: OFF", style="Neutral.TButton")

    def toggle_csv_logging(self):
        # jesli nie wybrano pliku, to od razu otworz wybor
        if not self.csv_path.get().strip():
            self.pick_csv_file()
            if not self.csv_path.get().strip():
                return

        self.log_to_csv.set(not self.log_to_csv.get())
        self._refresh_csv_toggle_button()
        self.status.set("CSV: zapis wlaczony." if self.log_to_csv.get() else "CSV: zapis wylaczony.")

    def pick_csv_file(self):
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_name = f"geiger_{ts}.csv"

        path = filedialog.asksaveasfilename(
            title="Zapisz CSV",
            defaultextension=".csv",
            initialdir=docs,
            initialfile=default_name,
            filetypes=[("CSV", "*.csv")]
        )
        if path:
            self.csv_path.set(path)
            self.log_to_csv.set(True)
            self._refresh_csv_toggle_button()
            self.status.set("CSV: wybrano plik, zapis wlaczony.")

    def _open_csv_if_needed(self, header_info: str = ""):
        if not self.log_to_csv.get():
            return

        if self.csv_file is not None and self.csv_writer is not None:
            return

        path = self.csv_path.get().strip()
        if not path:
            raise ValueError("Nie wybrano pliku CSV.")

        file_exists = os.path.exists(path)
        file_empty = (not file_exists) or (os.path.getsize(path) == 0)

        # w serii dopisuj, w pojedynczym nadpisuj
        mode = "a" if self.series_active else "w"

        self.csv_file = open(path, mode, newline="", encoding="utf-8", buffering=1)

        # naglowek tylko gdy plik pusty
        if file_empty:
            self.csv_file.write("sep=;\n")
            created = datetime.now().isoformat(timespec="seconds")
            self.csv_file.write(f"# created={created}\n")
            if header_info:
                for line in header_info.splitlines():
                    self.csv_file.write(f"# {line}\n")

        self.csv_writer = csv.writer(self.csv_file, delimiter=";", lineterminator="\n")

        if file_empty:
            self.csv_writer.writerow(["run", "t_s", "N", "sqrtN"])

        self.csv_file.flush()
        try:
            os.fsync(self.csv_file.fileno())
        except Exception:
            pass

        self._last_log_t = 0.0

    def _close_csv(self):
        try:
            if self.csv_file:
                self.csv_file.flush()
                try:
                    os.fsync(self.csv_file.fileno())
                except Exception:
                    pass
                self.csv_file.close()
        except Exception:
            pass
        self.csv_file = None
        self.csv_writer = None

    def _log_row(self, run_index: int, t: float, counts: int):
        if not self.csv_writer or not self.csv_file:
            return

        sqrt_n = math.sqrt(max(int(counts), 0))

        def fmt(x):
            return f"{x:.3f}".replace(".", ",")  # przecinek dziesietny (Excel PL)

        self.csv_writer.writerow([int(run_index), fmt(float(t)), int(counts), fmt(sqrt_n)])

        self.csv_file.flush()
        try:
            os.fsync(self.csv_file.fileno())
        except Exception:
            pass

    # ---------------------------------------------------------------
    # BLOKOWANIE KONTROLEK
    # ---------------------------------------------------------------
    def _set_controls_locked(self, locked: bool):
        state = "disabled" if locked else "normal"
        for w in self._lock_widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

        self.btn_start.configure(state="disabled" if locked else "normal")
        self.btn_stop.configure(state="normal" if locked else "disabled")
        self.btn_reset.configure(state="disabled" if locked else "normal")

    # ---------------------------------------------------------------
    # SYMULATOR (rozklad Poissona)
    # ---------------------------------------------------------------
    def _poisson_sample(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam >= 30:
            # przyblizenie gaussowskie dla duzych lambda
            x = int(round(random.gauss(lam, math.sqrt(lam))))
            return max(0, x)

        # algorytm Knutha
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while p > L:
            k += 1
            p *= random.random()
        return k - 1

    def _sim_read_counts(self) -> int:
        now = time.perf_counter()
        if self._sim_last_t is None:
            self._sim_last_t = now
            return self._sim_counts

        dt = now - self._sim_last_t
        self._sim_last_t = now

        rate = float(self.sim_rate_cps.get())
        if rate < 0:
            rate = 0.0

        inc = self._poisson_sample(rate * dt)
        self._sim_counts += inc
        return self._sim_counts

    # ---------------------------------------------------------------
    # SERIE: UI
    # ---------------------------------------------------------------
    def clear_series_results(self):
        self.series_results = []
        for item in self.series_table.get_children():
            self.series_table.delete(item)
        self.lbl_series_progress.configure(text="Seria: -")
        self.status.set("Wyczyszczono wyniki serii.")

    def start_series(self, decay=False):
        if self.running or self.series_active or self.plateau_active or self._closing:
            return

        try:
            runs = int(self.series_runs.get())
            pause = float(self.series_pause_s.get())
            if runs < 1:
                raise ValueError("Liczba runow musi byc >= 1.")
            if pause < 0:
                raise ValueError("Przerwa musi byc >= 0.")
        except Exception as e:
            messagebox.showerror("Seria", str(e))
            return

        self._is_decay = decay
        if decay:
            self._series_start_perf = time.perf_counter()
            self._decay_t = []
            self._decay_cps = []
            self._decay_n = []
            self._decay_dt = []
            self._decay_fit = None

        self.clear_series_results()
        self.stop_task()
        self._close_csv()

        self.series_active = True
        self.series_index = 0

        try:
            info = (
                "kind=series\n"
                f"runs={runs}\n"
                f"pause_s={pause}\n"
                f"mode={self.mode.get()}\n"
                f"device={'SIM' if self.use_simulator.get() else self.counter_channel.get()}"
            )
            if self.mode.get() == "time":
                info += f"\nset_time={float(self.time_value.get())} {self.time_unit.get()}"
            else:
                info += f"\nset_counts={int(self.target_counts.get())}"
            info += f"\ntau_us={float(self.dead_time_us.get())}"

            self._open_csv_if_needed(header_info=info)
        except Exception as e:
            self.series_active = False
            messagebox.showerror("CSV", str(e))
            self._close_csv()
            return

        self.series_end_time_text.set("Koniec serii: -")  # wlasciwa wartosc ustawi pierwszy run
        self.status.set("Seria: start.")
        self._start_next_series_run()

    def _start_next_series_run(self):
        if not self.series_active or self._closing:
            return

        runs = int(self.series_runs.get())
        if self.series_index >= runs:
            self._finish_series()
            return

        self.series_index += 1
        self.lbl_series_progress.configure(text=f"Seria: {self.series_index}/{runs}")

        ok = self._start_run_internal(from_series=True)
        if not ok:
            self.series_active = False
            self._set_controls_locked(False)
            self._close_csv()
            self.status.set("Seria przerwana (blad startu).")

    def _finish_series(self):
        self.series_active = False
        self._set_controls_locked(False)

        if self._is_decay:
            self._fit_and_plot_decay()
        elif self.series_results:
            cps_list = [r["cps"] for r in self.series_results if r["t"] > 0]
            if cps_list:
                mean = sum(cps_list) / len(cps_list)
                var = sum((x - mean) ** 2 for x in cps_list) / max(1, (len(cps_list) - 1))
                std = math.sqrt(var)
                self.status.set(f"Seria zakonczona. Sredni CPS = {mean:.3f}, odch = {std:.3f}")
            else:
                self.status.set("Seria zakonczona.")
        else:
            self.status.set("Seria zakonczona.")

        self._is_decay = False
        self.end_time_text.set("Koniec: -")
        self.series_end_time_text.set("Koniec serii: -")
        self._close_csv()
        self._beep()

    # ---------------------------------------------------------------
    # START / STOP / FINALIZE
    # ---------------------------------------------------------------
    def start_measurement(self):
        if self.running or self.series_active or self.plateau_active or self._closing:
            return

        self.series_active = False
        self.series_index = 0
        self._is_decay = False

        self.stop_task()
        self._close_csv()
        try:
            self._open_csv_if_needed(
                header_info=f"kind=single\ntau_us={float(self.dead_time_us.get())}")
        except Exception as e:
            messagebox.showerror("CSV", str(e))
            self._close_csv()
            return

        ok = self._start_run_internal(from_series=False)
        if not ok:
            self._close_csv()

    def _start_run_internal(self, from_series: bool, override_time_s=None) -> bool:
        self._cancel_update_timer()
        self._cancel_series_timer()

        # reset wykresu na kazdy run
        self._t_points = []
        self._n_points = []
        self._last_plot_t = 0.0
        try:
            self.line.set_data([], [])
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()
        except Exception:
            pass

        # walidacja
        try:
            if self.mode.get() == "time":
                if not (float(self.time_value.get()) > 0):
                    raise ValueError("Czas musi byc > 0.")
            else:
                if int(self.target_counts.get()) < 1:
                    raise ValueError("N musi byc >= 1.")

            if self.use_simulator.get() and float(self.sim_rate_cps.get()) < 0:
                raise ValueError("CPS w symulatorze musi byc >= 0.")
        except Exception as e:
            messagebox.showerror("Bledne dane", str(e))
            return False

        # reset wynikow
        self.current_counts.set(0)
        self.elapsed_time.set(0.0)
        self.sqrt_counts.set(0.0)
        self.cps_value.set("0.000")
        self.cps_corr_value.set("-")
        self._rate_scale = 10.0
        self._reset_progress()
        self._last_counts = 0
        self._last_t = 0.0

        # czas docelowy (Plateau podaje override_time_s)
        if self.mode.get() == "time":
            self.target_time_s = override_time_s if override_time_s is not None else self._run_duration_seconds()
        else:
            self.target_time_s = None
        self._update_expected_end_times(from_series=from_series)

        # start zrodla zliczen
        if self.use_simulator.get():
            self._sim_counts = 0
            self._sim_last_t = None
        else:
            if not NIDAQ_AVAILABLE:
                messagebox.showerror("NI-DAQ", "Brak nidaqmx w tym Pythonie. Wlacz tryb symulatora.")
                return False
            try:
                self.task = nidaqmx.Task()
                ch = self.task.ci_channels.add_ci_count_edges_chan(
                    self.counter_channel.get(),
                    edge=Edge.RISING,
                    initial_count=0
                )
                dev = self.counter_channel.get().split("/")[0]
                pfi = self.pfi_term.get().strip().lstrip("/")
                ch.ci_count_edges_term = f"/{dev}/{pfi}"
                self.task.start()
            except Exception as e:
                messagebox.showerror("Blad DAQ", str(e))
                self.stop_task()
                self.status.set("Blad startu DAQ.")
                return False

        self.start_perf = time.perf_counter()
        self.running = True
        self._set_controls_locked(True)
        self._start_click_pump()

        if from_series:
            self.status.set(f"Seria: run {self.series_index} trwa...")
        else:
            self.status.set("Pomiar trwa...")

        self._after_update_id = self.after(100, self._update)
        return True

    def stop_measurement(self):
        if not self.running and not self.series_active:
            return

        # STOP w serii przerywa cala serie
        if self.series_active:
            self.series_active = False
            self._cancel_series_timer()

        self.end_time_text.set("Koniec: -")
        self.series_end_time_text.set("Koniec serii: -")
        self.running = False
        self._finalize_measurement("Zatrzymano pomiar.", ended_normally=False)

    def _finalize_measurement(self, msg: str, ended_normally: bool):
        self._cancel_update_timer()
        self._stop_click_pump()
        self.stop_task()

        if not self.series_active:
            self._set_controls_locked(False)

        self.status.set(msg)

        if (not self.series_active) and ended_normally:
            self._beep()

        # w serii: zapisz wynik runa i zaplanuj kolejny po przerwie
        if self.series_active and ended_normally:
            self._store_series_result()
            if self.csv_writer:
                self._log_row(self.series_index, self._last_t, self._last_counts)
            pause = float(self.series_pause_s.get())
            self._after_series_id = self.after(int(pause * 1000), self._start_next_series_run)
            return

        # plateau: zapisz punkt (CPS vs napiecie)
        if self.plateau_active:
            if ended_normally:
                self._store_plateau_point()
                self.status.set(f"Plateau: zapisano punkt {self._plateau_voltage_pending:.0f} V.")
            self.plateau_active = False

        # pojedynczy pomiar: zapisz wiersz koncowy
        if self.csv_writer and self._last_t > 0:
            run_idx = self.series_index if self.series_active else 0
            self._log_row(run_idx, self._last_t, self._last_counts)

        if not self.series_active:
            self._close_csv()

    def _store_series_result(self):
        run = self.series_index
        t = float(self._last_t)
        n = int(self._last_counts)
        cps = (n / t) if t > 0 else 0.0
        mode = self.mode.get()

        self.series_results.append({"run": run, "mode": mode, "t": t, "n": n, "cps": cps})
        self.series_table.insert("", "end", values=(run, mode, f"{t:.3f}", n, f"{cps:.3f}"))

        if self._is_decay and self._series_start_perf is not None:
            midpoint = (time.perf_counter() - self._series_start_perf) - t / 2.0
            self._decay_t.append(midpoint)
            self._decay_cps.append(cps)
            self._decay_n.append(n)
            self._decay_dt.append(t)

    # ---------------------------------------------------------------
    # UPDATE (petla odpytujaca)
    # ---------------------------------------------------------------
    def _update_rate_bar(self, cps: float):
        c = getattr(self, "rate_canvas", None)
        if c is None:
            return
        # autoskalowanie maksimum: rosnie szybko, opada powoli
        if cps > self._rate_scale:
            self._rate_scale = cps * 1.2
        else:
            self._rate_scale = max(10.0, self._rate_scale * 0.999)
        try:
            w = c.winfo_width()
            h = c.winfo_height()
            if w <= 1:
                return
            frac = 0.0 if self._rate_scale <= 0 else min(1.0, cps / self._rate_scale)
            c.delete("bar")
            c.create_rectangle(0, 0, int(w * frac), h, fill=self.ui["accent"], width=0, tags="bar")
            c.create_text(w - 5, h // 2, text=f"{cps:.1f} cps", anchor="e",
                          fill=self.ui["text"], tags="bar")
        except Exception:
            pass

    def _update_progress(self, t: float, counts: int):
        if self.mode.get() == "time" and self.target_time_s:
            frac = max(0.0, min(1.0, t / self.target_time_s))
            self.progress_value.set(frac * 100.0)
            self.remaining_text.set(f"Pozostalo: {max(0.0, self.target_time_s - t):.1f} s")
        elif self.mode.get() == "counts":
            tgt = max(1, int(self.target_counts.get()))
            self.progress_value.set(max(0.0, min(1.0, counts / tgt)) * 100.0)
            self.remaining_text.set(f"N: {counts} / {tgt}")
        else:
            self.progress_value.set(0.0)
            self.remaining_text.set("")

    def _reset_progress(self):
        self.progress_value.set(0.0)
        self.remaining_text.set("")
        try:
            if getattr(self, "rate_canvas", None) is not None:
                self.rate_canvas.delete("bar")
        except Exception:
            pass

    def _read_counts(self) -> int:
        if self.use_simulator.get():
            return int(self._sim_read_counts())
        return int(self.task.read())

    def _update(self):
        if self._closing or (not self.running):
            return

        try:
            counts = self._read_counts()
        except Exception as e:
            messagebox.showerror("Blad", f"Blad odczytu:\n{e}")
            self.running = False
            if self.series_active:
                self.series_active = False
            self._finalize_measurement("Blad odczytu.", ended_normally=False)
            return

        t = time.perf_counter() - self.start_perf
        sqrt_n = math.sqrt(max(counts, 0))
        cps = (counts / t) if t > 0 else 0.0

        # klik proporcjonalny do przyrostu zliczen (kolejka ograniczona)
        delta = counts - self._last_counts
        if self.click_enabled.get() and delta > 0:
            self._click_queue = min(self._click_queue + delta, 8)

        self._last_counts = counts
        self._last_t = t

        if self.plot_live.get():
            if (t - self._last_plot_t) >= (self.plot_interval_ms / 1000.0):
                self._t_points.append(t)
                self._n_points.append(counts)
                try:
                    self.line.set_data(self._t_points, self._n_points)
                    self.ax.relim()
                    self.ax.autoscale_view()
                    self.canvas.draw_idle()
                except Exception:
                    pass
                self._last_plot_t = t

        self.current_counts.set(counts)
        self.elapsed_time.set(round(t, 3))
        self.sqrt_counts.set(round(sqrt_n, 3))
        self.cps_value.set(f"{cps:.3f}")

        # korekta czasu martwego (dead time)
        tau = float(self.dead_time_us.get()) * 1e-6
        if tau > 0 and (1.0 - cps * tau) > 0:
            self.cps_corr_value.set(f"{cps / (1.0 - cps * tau):.3f}")
        elif tau > 0:
            self.cps_corr_value.set("przeciazenie")
        else:
            self.cps_corr_value.set("-")

        self._update_rate_bar(cps)
        self._update_progress(t, counts)

        if self.csv_writer:
            if (t - self._last_log_t) >= (self.log_interval_ms / 1000.0):
                run_idx = self.series_index if self.series_active else 0
                self._log_row(run_idx, t, counts)
                self._last_log_t = t

        # warunki zakonczenia runa
        if self.mode.get() == "time" and self.target_time_s and t >= self.target_time_s:
            self.running = False
            self._finalize_measurement("Zakonczono (uplynal czas).", ended_normally=True)
            return

        if self.mode.get() == "counts" and counts >= self.target_counts.get():
            self.running = False
            self._finalize_measurement("Zakonczono (osiagnieto N).", ended_normally=True)
            return

        self._after_update_id = self.after(100, self._update)

    # ---------------------------------------------------------------
    # RESET / TASK / CLOSE
    # ---------------------------------------------------------------
    def reset_measurement(self):
        self.series_active = False
        self.running = False
        self.plateau_active = False
        self._is_decay = False

        self._cancel_all_timers()
        self._stop_click_pump()

        self.current_counts.set(0)
        self.elapsed_time.set(0.0)
        self.sqrt_counts.set(0.0)
        self.cps_value.set("0.000")
        self.cps_corr_value.set("-")
        self._reset_progress()

        self._t_points = []
        self._n_points = []
        try:
            self.line.set_data([], [])
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw_idle()
        except Exception:
            pass

        self.stop_task()
        self._close_csv()
        self._set_controls_locked(False)

        self.status.set("Zresetowano.")
        self.end_time_text.set("Koniec: -")
        self.series_end_time_text.set("Koniec serii: -")

    def stop_task(self):
        if self.task is not None:
            try:
                try:
                    self.task.stop()
                except Exception:
                    pass
                self.task.close()
            except Exception:
                pass
            self.task = None

    def on_close(self):
        if self._closing:
            return
        self._closing = True

        self.series_active = False
        self.running = False

        self._cancel_all_timers()
        self._stop_click_pump()
        self.stop_task()
        self._close_csv()

        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    app = DAQCounterApp()
    app.mainloop()
