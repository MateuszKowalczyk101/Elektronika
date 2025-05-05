# Elektronika

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import threading
import time
import csv
import os
import random
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import nidaqmx
    from nidaqmx.constants import AcquisitionType
    from nidaqmx.system import System
    DAQ_AVAILABLE = True
except ImportError:
    DAQ_AVAILABLE = False

class DAQApp:
    def __init__(self, master):
        self.master = master
        self.master.title("DAQ Pomiar")
        self.master.configure(bg='#ffc0cb')
        self.running = False
        self.task_thread = None
        self.data = []
        self.save_folder = os.getcwd()

        tk.Label(master, text="Urzadzenie:", bg='#ffc0cb').grid(row=0, column=0, sticky="e")
        self.device_var = tk.StringVar()
        self.device_menu = ttk.Combobox(master, textvariable=self.device_var, state="readonly")
        self.refresh_devices()
        self.device_menu.grid(row=0, column=1)

        tk.Label(master, text="Tryb pomiaru:", bg='#ffc0cb').grid(row=1, column=0, sticky="e")
        self.mode_var = tk.StringVar(value="dual")
        tk.Radiobutton(master, text="1 kanal (AI0)", variable=self.mode_var, value="single", bg='#ffc0cb').grid(row=1, column=1, sticky="w")
        tk.Radiobutton(master, text="2 kanaly (AI0 + AI1)", variable=self.mode_var, value="dual", bg='#ffc0cb').grid(row=2, column=1, sticky="w")

        tk.Label(master, text="Sample rate (Hz):", bg='#ffc0cb').grid(row=3, column=0, sticky="e")
        self.sample_entry = tk.Entry(master)
        self.sample_entry.insert(0, "1000")
        self.sample_entry.grid(row=3, column=1)

        self.test_mode = tk.BooleanVar()
        tk.Checkbutton(master, text="Tryb testowy (symulacja)", variable=self.test_mode, bg='#ffc0cb').grid(row=5, column=0, columnspan=2)

        tk.Button(master, text="Start", command=self.start_measurement).grid(row=6, column=0, pady=10)
        tk.Button(master, text="Stop", command=self.stop_measurement).grid(row=6, column=1)
        tk.Button(master, text="Zapisz wykres", command=self.save_plot).grid(row=7, column=0, columnspan=2)

        self.fig, self.ax = plt.subplots(figsize=(6, 3))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.master)
        self.canvas.get_tk_widget().grid(row=8, column=0, columnspan=2)
        self.line_ai0, = self.ax.plot([], [], label="AI0")
        self.line_ai1, = self.ax.plot([], [], label="AI1")
        self.ax.set_xlabel("Czas (s)")
        self.ax.set_ylabel("Napiecie (V)")
        self.ax.grid(True)
        self.ax.legend()

        self.master.after(100, self.update_plot)

    def refresh_devices(self):
        if DAQ_AVAILABLE:
            devices = System.local().devices.device_names
            self.device_menu['values'] = devices if devices else ["Brak"]
            if devices:
                self.device_menu.current(0)
            else:
                self.device_menu.set("Brak")
        else:
            self.device_menu['values'] = ["Brak"]
            self.device_menu.set("Brak")

    def start_measurement(self):
        if self.running:
            return
        self.running = True
        self.data = []
        self.task_thread = threading.Thread(target=self.run_task, daemon=True)
        self.task_thread.start()

    def stop_measurement(self):
        self.running = False
        if self.task_thread:
            self.task_thread.join()
        self.prompt_save_to_file()
        messagebox.showinfo("Info", "Pomiar zakonczony i zapisany.")

    def run_task(self):
        device = self.device_var.get()
        mode = self.mode_var.get()
        rate = int(self.sample_entry.get())
        samples_to_read = rate // 10
        channels = ["AI0"] if mode == "single" else ["AI0", "AI1"]
        start_time = time.time()

        if self.test_mode.get() or not DAQ_AVAILABLE or device == "Brak":
            while self.running:
                current_time = time.time() - start_time
                values = [random.uniform(-1, 1)]
                if mode == "dual":
                    values.append(random.uniform(-1, 1))
                self.data.append([current_time] + values)
                time.sleep(1 / rate)
        else:
            with nidaqmx.Task() as task:
                for ch in channels:
                    task.ai_channels.add_ai_voltage_chan(f"{device}/{ch}", min_val=-10.0, max_val=10.0)
                task.timing.cfg_samp_clk_timing(rate=rate, sample_mode=AcquisitionType.CONTINUOUS)
                task.in_stream.input_buf_size = rate * 10

                while self.running:
                    try:
                        readings = task.read(number_of_samples_per_channel=samples_to_read)
                        if isinstance(readings[0], float):
                            readings = [readings]
                        timestamps = [time.time() - start_time] * len(readings[0])
                        for i in range(len(readings[0])):
                            row = [timestamps[i]] + [readings[ch][i] for ch in range(len(channels))]
                            self.data.append(row)
                        time.sleep(samples_to_read / rate)
                    except Exception as e:
                        print("Blad podczas pomiaru:", e)
                        break

    def prompt_save_to_file(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode='w', newline='') as file:
            writer = csv.writer(file)
            headers = ["Czas (s)", "AI0"] if self.mode_var.get() == "single" else ["Czas (s)", "AI0", "AI1"]
            writer.writerow(headers)
            writer.writerows(self.data)

    def update_plot(self):
        try:
            data_copy = self.data.copy()
            if not data_copy:
                self.master.after(100, self.update_plot)
                return
            mode = self.mode_var.get()
            times = [row[0] for row in data_copy if len(row) > 1]
            ai0_vals = [row[1] for row in data_copy if len(row) > 1]
            self.line_ai0.set_data(times, ai0_vals)

            if mode == "dual" and all(len(row) > 2 for row in data_copy):
                ai1_vals = [row[2] for row in data_copy if len(row) > 2]
                self.line_ai1.set_data(times, ai1_vals)
            else:
                self.line_ai1.set_data([], [])

            if times:
                self.ax.set_xlim(times[0], times[-1])
            self.ax.relim()
            self.ax.autoscale_view()
            self.canvas.draw()
        except Exception as e:
            print("Blad podczas rysowania wykresu:", e)

        self.master.after(100, self.update_plot)

    def save_plot(self):
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG files", "*.png")])
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fig.savefig(path)
        messagebox.showinfo("Zapisano", f"Wykres zapisany do pliku:\n{path}")

if __name__ == "__main__":
    root = tk.Tk()
    app = DAQApp(root)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
