#!/usr/bin/env python3
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import re
import csv
import time
import os
import threading
import queue
import shutil   # for shutil.which
import shlex    # for splitting additional params
from datetime import datetime
from collections import deque

# Additional imports for plotting
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# Regex patterns to capture data
FPS_PATTERN            = re.compile(r"fps=\s*([\d\.]+)")
SPEED_PATTERN          = re.compile(r"speed=\s*([\d\.]+)x")
MISSED_PACKETS_PATTERN = re.compile(r"missed (\d+) packets")
MAX_DELAY_PATTERN      = re.compile(r"max delay reached")
DECODE_ERROR_PATTERN   = re.compile(r"concealing (\d+) DC,\s*(\d+) AC,\s*(\d+) MV errors")

class CameraMonitorGUI:
    def __init__(self, master):
        self.master = master
        self.master.title("RTSP Camera Monitor")

        # Variables for entries
        self.ffmpeg_path_var      = tk.StringVar()  # Path to ffmpeg.exe (optional)
        self.rtsp_url_var         = tk.StringVar()
        self.output_csv_var       = tk.StringVar(value="stream_log.csv")
        self.ffmpeg_params_var    = tk.StringVar()  # Additional FFmpeg parameters

        # FFmpeg process and thread handling
        self.ffmpeg_process = None
        self.monitor_thread = None
        self.running_event = threading.Event()

        # Queue for log lines and status messages
        self.log_queue = queue.Queue()

        # Stats tracking
        self.total_frames = 0
        self.sum_fps = 0.0
        self.speed_values = deque(maxlen=50)
        self.missed_packets_count = 0

        # Data for live graphing (shared x-axis and timestamps)
        self.start_time = None
        self.graph_time_data = []
        self.graph_timestamp_labels = []
        self.graph_avg_fps_data = []
        self.graph_avg_speed_data = []
        self.graph_missed_packets_data = []  # New for missed packets

        # Build UI
        self.create_menu()
        self.create_widgets()

        # Bind window close event
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_menu(self):
        """Create a menu bar with File and Help menus."""
        menu_bar = tk.Menu(self.master)
        self.master.config(menu=menu_bar)
        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Exit", command=self.on_closing)
        menu_bar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)

    def create_widgets(self):
        """Create and place all UI widgets."""
        # --- Input Frame ---
        input_frame = tk.Frame(self.master)
        input_frame.pack(padx=10, pady=5, fill=tk.X)

        # FFmpeg Path
        tk.Label(input_frame, text="FFmpeg Path:").grid(row=0, column=0, sticky=tk.E)
        tk.Entry(input_frame, textvariable=self.ffmpeg_path_var, width=50).grid(row=0, column=1, padx=5, pady=5)
        tk.Button(input_frame, text="Browse FFmpeg...", command=self.browse_ffmpeg).grid(row=0, column=2, padx=5, pady=5)

        # RTSP URL
        tk.Label(input_frame, text="RTSP URL:").grid(row=1, column=0, sticky=tk.E)
        tk.Entry(input_frame, textvariable=self.rtsp_url_var, width=50).grid(row=1, column=1, padx=5, pady=5)
        
        # Output CSV
        tk.Label(input_frame, text="Output CSV:").grid(row=2, column=0, sticky=tk.E)
        tk.Entry(input_frame, textvariable=self.output_csv_var, width=50).grid(row=2, column=1, padx=5, pady=5)
        tk.Button(input_frame, text="Browse CSV...", command=self.browse_csv).grid(row=2, column=2, padx=5, pady=5)

        # Additional FFmpeg Parameters
        tk.Label(input_frame, text="Additional FFmpeg Params:").grid(row=3, column=0, sticky=tk.E)
        tk.Entry(input_frame, textvariable=self.ffmpeg_params_var, width=50).grid(row=3, column=1, padx=5, pady=5)

        # --- Control Buttons ---
        button_frame = tk.Frame(self.master)
        button_frame.pack(padx=10, pady=5)
        self.start_button = tk.Button(button_frame, text="Start Monitoring", command=self.start_monitoring)
        self.start_button.grid(row=0, column=0, padx=10)
        self.stop_button = tk.Button(button_frame, text="Stop Monitoring", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=10)

        # --- Graphs Frame (Three Graphs in one row) ---
        graphs_frame = tk.Frame(self.master)
        graphs_frame.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)

        # Avg FPS Graph
        fps_frame = tk.Frame(graphs_frame)
        fps_frame.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.BOTH, expand=True)
        self.fps_fig, self.fps_ax = plt.subplots(figsize=(4,3))
        self.fps_canvas = FigureCanvasTkAgg(self.fps_fig, master=fps_frame)
        self.fps_canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        NavigationToolbar2Tk(self.fps_canvas, fps_frame)
        self.fps_canvas.mpl_connect('pick_event', self.on_pick)

        # Avg Speed Graph
        speed_frame = tk.Frame(graphs_frame)
        speed_frame.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.BOTH, expand=True)
        self.speed_fig, self.speed_ax = plt.subplots(figsize=(4,3))
        self.speed_canvas = FigureCanvasTkAgg(self.speed_fig, master=speed_frame)
        self.speed_canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        NavigationToolbar2Tk(self.speed_canvas, speed_frame)
        self.speed_canvas.mpl_connect('pick_event', self.on_pick)

        # Missed Packets Graph
        missed_frame = tk.Frame(graphs_frame)
        missed_frame.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.BOTH, expand=True)
        self.missed_fig, self.missed_ax = plt.subplots(figsize=(4,3))
        self.missed_canvas = FigureCanvasTkAgg(self.missed_fig, master=missed_frame)
        self.missed_canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        NavigationToolbar2Tk(self.missed_canvas, missed_frame)
        self.missed_canvas.mpl_connect('pick_event', self.on_pick)

        # --- Log Display Frame ---
        log_frame = tk.Frame(self.master)
        log_frame.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, wrap="none", height=15)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll = tk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scroll.set)

        # Configure log coloring
        self.log_text.tag_config("error", foreground="red")
        self.log_text.tag_config("warning", foreground="orange")
        self.log_text.tag_config("fps", foreground="green")
        self.log_text.tag_config("speed", foreground="blue")
        self.log_text.tag_config("decode", foreground="purple")
        self.log_text.tag_config("highlight", background="yellow")

        # --- Log Management Buttons ---
        log_button_frame = tk.Frame(self.master)
        log_button_frame.pack(padx=10, pady=5)
        tk.Button(log_button_frame, text="Clear Log", command=self.clear_log).grid(row=0, column=0, padx=5)
        tk.Button(log_button_frame, text="Save Log", command=self.save_log).grid(row=0, column=1, padx=5)

        # --- Status Bar ---
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(self.master, textvariable=self.status_var, anchor="w").pack(side=tk.BOTTOM, fill=tk.X)

    def browse_ffmpeg(self):
        filename = filedialog.askopenfilename(
            title="Select ffmpeg executable",
            filetypes=[("FFmpeg Executable", "ffmpeg.exe"), ("All Files", "*.*")]
        )
        if filename:
            self.ffmpeg_path_var.set(filename)

    def browse_csv(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if filename:
            self.output_csv_var.set(filename)

    def clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def save_log(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self.log_text.get("1.0", tk.END))
            messagebox.showinfo("Log Saved", f"Log saved to {filename}")

    def show_about(self):
        about_text = ("RTSP Camera Monitor\n"
                      "Version 2.0\n\n"
                      "This tool monitors an RTSP stream using FFmpeg and logs key encoding metrics.\n"
                      "It now displays live graphs for Avg FPS, Avg Speed, and Missed Packets on a single row.\n"
                      "Click a data point in any graph to jump to its corresponding log entry.")
        messagebox.showinfo("About", about_text)

    def start_monitoring(self):
        rtsp_url = self.rtsp_url_var.get().strip()
        output_csv = self.output_csv_var.get().strip()
        custom_ffmpeg_path = self.ffmpeg_path_var.get().strip()
        additional_params = self.ffmpeg_params_var.get().strip()

        if not rtsp_url:
            messagebox.showerror("Error", "Please enter an RTSP URL.")
            return

        # Determine ffmpeg executable
        if custom_ffmpeg_path:
            ffmpeg_exe = custom_ffmpeg_path
            if not os.path.isfile(ffmpeg_exe):
                messagebox.showerror("FFmpeg Error", f"Provided path not found:\n{ffmpeg_exe}")
                return
        else:
            found = shutil.which("ffmpeg")
            if found:
                ffmpeg_exe = found
            else:
                messagebox.showerror("FFmpeg Not Found", "ffmpeg is not in PATH. Please install it or specify its full path.")
                return

        # Stop any existing monitoring
        self.stop_monitoring()

        # Reset stats and graph data
        self.total_frames = 0
        self.sum_fps = 0.0
        self.speed_values.clear()
        self.missed_packets_count = 0

        self.start_time = time.time()
        self.graph_time_data.clear()
        self.graph_timestamp_labels.clear()
        self.graph_avg_fps_data.clear()
        self.graph_avg_speed_data.clear()
        self.graph_missed_packets_data.clear()

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.clear_log()

        self.running_event.set()
        self.monitor_thread = threading.Thread(
            target=self.run_ffmpeg_monitor,
            args=(rtsp_url, output_csv, ffmpeg_exe, additional_params),
            daemon=True
        )
        self.monitor_thread.start()

        # Schedule recurring updates
        self.master.after(200, self.update_log_display)
        self.update_graphs()
        self.status_var.set("Monitoring...")

    def stop_monitoring(self):
        self.running_event.clear()
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
        self.ffmpeg_process = None
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Stopped")

    def run_ffmpeg_monitor(self, rtsp_url, output_csv, ffmpeg_exe, additional_params):
        # Build ffmpeg command
        cmd = [ffmpeg_exe, "-i", rtsp_url]
        if additional_params:
            cmd.extend(shlex.split(additional_params))
        cmd.extend(["-f", "null", "-"])

        try:
            self.ffmpeg_process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
        except FileNotFoundError:
            self.log_queue.put("Error: ffmpeg not found. Check installation or path.")
            self.running_event.clear()
            return

        time.sleep(1)
        if self.ffmpeg_process.poll() is not None:
            leftover = self.ffmpeg_process.stderr.read()
            self.log_queue.put("FFmpeg failed to start:\n" + leftover)
            self.running_event.clear()
            return

        csv_exists = os.path.isfile(output_csv)
        try:
            with open(output_csv, mode="a", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                if not csv_exists:
                    writer.writerow(["timestamp", "fps", "speed", "missed_packets",
                                     "max_delay_reached", "decode_errors", "raw_log_line"])
                while self.running_event.is_set():
                    line = self.ffmpeg_process.stderr.readline()
                    if not line:
                        break
                    line = line.strip()
                    parsed = self.parse_ffmpeg_line(line)
                    timestamp_str = datetime.now().isoformat()

                    # Update stats
                    if parsed["fps"] is not None:
                        self.total_frames += 1
                        self.sum_fps += parsed["fps"]
                    if parsed["speed"] is not None:
                        self.speed_values.append(parsed["speed"])
                    if parsed["missed_packets"] is not None:
                        self.missed_packets_count += parsed["missed_packets"]

                    writer.writerow([
                        timestamp_str,
                        parsed["fps"] if parsed["fps"] is not None else "",
                        parsed["speed"] if parsed["speed"] is not None else "",
                        parsed["missed_packets"] if parsed["missed_packets"] else "",
                        1 if parsed["max_delay"] else 0,
                        parsed["decode_errors"] if parsed["decode_errors"] else "",
                        line
                    ])
                    csvfile.flush()
                    self.log_queue.put(f"{timestamp_str} -> {line}")
        except Exception as e:
            self.log_queue.put(f"Error writing CSV: {e}")

        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
        self.ffmpeg_process = None
        self.log_queue.put("Monitoring stopped.\n")

    def parse_ffmpeg_line(self, line):
        result = {"fps": None, "speed": None, "missed_packets": None, "max_delay": False, "decode_errors": None}
        fps_match = FPS_PATTERN.search(line)
        if fps_match:
            try:
                result["fps"] = float(fps_match.group(1))
            except ValueError:
                result["fps"] = None
        speed_match = SPEED_PATTERN.search(line)
        if speed_match:
            try:
                result["speed"] = float(speed_match.group(1))
            except ValueError:
                result["speed"] = None
        missed_match = MISSED_PACKETS_PATTERN.search(line)
        if missed_match:
            try:
                result["missed_packets"] = int(missed_match.group(1))
            except ValueError:
                result["missed_packets"] = None
        if MAX_DELAY_PATTERN.search(line):
            result["max_delay"] = True
        decode_match = DECODE_ERROR_PATTERN.search(line)
        if decode_match:
            dc_errs = decode_match.group(1)
            ac_errs = decode_match.group(2)
            mv_errs = decode_match.group(3)
            result["decode_errors"] = f"DC={dc_errs},AC={ac_errs},MV={mv_errs}"
        return result

    def update_log_display(self):
        while not self.log_queue.empty():
            log_line = self.log_queue.get_nowait()
            lower_line = log_line.lower()
            if "max delay reached" in lower_line:
                tag = "error"
            elif "missed" in lower_line:
                tag = "warning"
            elif "fps=" in lower_line:
                tag = "fps"
            elif "speed=" in lower_line:
                tag = "speed"
            elif "concealing" in lower_line or "decode" in lower_line:
                tag = "decode"
            else:
                tag = None
            if tag:
                self.log_text.insert(tk.END, log_line + "\n", tag)
            else:
                self.log_text.insert(tk.END, log_line + "\n")
            self.log_text.see(tk.END)

        avg_fps = (self.sum_fps / self.total_frames) if self.total_frames > 0 else 0.0
        avg_speed = (sum(self.speed_values) / len(self.speed_values)) if self.speed_values else 0.0
        status_text = (f"Monitoring... Avg FPS: {avg_fps:.2f}, Avg Speed: {avg_speed:.2f}x, "
                       f"Missed Packets: {self.missed_packets_count}")
        if self.running_event.is_set():
            self.status_var.set(status_text)
        if self.running_event.is_set():
            self.master.after(200, self.update_log_display)

    def update_graphs(self):
        """Update the three separate graphs for Avg FPS, Avg Speed, and Missed Packets."""
        if self.running_event.is_set():
            current_time = time.time() - self.start_time
            avg_fps = (self.sum_fps / self.total_frames) if self.total_frames > 0 else 0.0
            avg_speed = (sum(self.speed_values) / len(self.speed_values)) if self.speed_values else 0.0

            # Append current data point for all graphs
            self.graph_time_data.append(current_time)
            self.graph_avg_fps_data.append(avg_fps)
            self.graph_avg_speed_data.append(avg_speed)
            self.graph_missed_packets_data.append(self.missed_packets_count)
            self.graph_timestamp_labels.append(datetime.now().isoformat())

            # Update Avg FPS graph (green)
            self.fps_ax.clear()
            line_fps, = self.fps_ax.plot(self.graph_time_data, self.graph_avg_fps_data,
                                         marker='o', color="green", label="Avg FPS", picker=5)
            if len(self.graph_time_data) > 1:
                z = np.polyfit(self.graph_time_data, self.graph_avg_fps_data, 1)
                p = np.poly1d(z)
                self.fps_ax.plot(self.graph_time_data, p(np.array(self.graph_time_data)), "--",
                                 color="green", alpha=0.5)
            self.fps_ax.legend()
            self.fps_ax.set_xlabel("Time (s)")
            self.fps_ax.set_title("Average FPS")
            self.fps_canvas.draw()

            # Update Avg Speed graph (blue)
            self.speed_ax.clear()
            line_speed, = self.speed_ax.plot(self.graph_time_data, self.graph_avg_speed_data,
                                             marker='o', color="blue", label="Avg Speed", picker=5)
            if len(self.graph_time_data) > 1:
                z = np.polyfit(self.graph_time_data, self.graph_avg_speed_data, 1)
                p = np.poly1d(z)
                self.speed_ax.plot(self.graph_time_data, p(np.array(self.graph_time_data)), "--",
                                   color="blue", alpha=0.5)
            self.speed_ax.legend()
            self.speed_ax.set_xlabel("Time (s)")
            self.speed_ax.set_title("Average Speed")
            self.speed_canvas.draw()

            # Update Missed Packets graph (red)
            self.missed_ax.clear()
            line_missed, = self.missed_ax.plot(self.graph_time_data, self.graph_missed_packets_data,
                                               marker='o', color="red", label="Missed Packets", picker=5)
            if len(self.graph_time_data) > 1:
                z = np.polyfit(self.graph_time_data, self.graph_missed_packets_data, 1)
                p = np.poly1d(z)
                self.missed_ax.plot(self.graph_time_data, p(np.array(self.graph_time_data)), "--",
                                    color="red", alpha=0.5)
            self.missed_ax.legend()
            self.missed_ax.set_xlabel("Time (s)")
            self.missed_ax.set_title("Missed Packets")
            self.missed_canvas.draw()

            self.master.after(1000, self.update_graphs)

    def on_pick(self, event):
        """When a data point is clicked, scroll the log view to its timestamp."""
        if event.ind:
            index = event.ind[0]
            if index < len(self.graph_timestamp_labels):
                timestamp = self.graph_timestamp_labels[index]
                pos = self.log_text.search(timestamp, "1.0", tk.END)
                if pos:
                    self.log_text.see(pos)
                    self.log_text.tag_remove("highlight", "1.0", tk.END)
                    self.log_text.tag_add("highlight", pos, f"{pos} lineend")

    def on_closing(self):
        self.stop_monitoring()
        self.master.destroy()

def main():
    root = tk.Tk()
    app = CameraMonitorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
