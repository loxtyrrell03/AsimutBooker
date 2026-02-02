"""AsimutBooker GUI - Desktop control panel for managing practice room bookings."""

import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime, timedelta
import json
import csv
import io
import calendar as cal

# Constants
APP_DIR = Path(__file__).parent
STATE_FILE = APP_DIR / "data" / "browser_state" / "state.json"
LOGS_DIR = APP_DIR / "logs"
CONFIG_FILE = APP_DIR / "config" / "config.yaml"
HISTORY_FILE = APP_DIR / "data" / "booking_history.json"
SETTINGS_FILE = APP_DIR / "data" / "settings.json"


class AsimutBookerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AsimutBooker Control Panel")
        self.root.geometry("1400x950")
        self.root.minsize(1100, 800)

        # Configure default font sizes - larger for better readability
        default_font = ("Segoe UI", 13)
        heading_font = ("Segoe UI", 14, "bold")

        # Apply to all ttk widgets
        style = ttk.Style()
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font, padding=8)
        style.configure("TLabelframe.Label", font=heading_font)
        style.configure("TCheckbutton", font=default_font)
        style.configure("TCombobox", font=default_font)
        style.configure("TEntry", font=default_font)

        # Set icon if available
        try:
            icon_path = APP_DIR / "assets" / "icon.ico"
            if icon_path.exists():
                self.root.iconbitmap(str(icon_path))
        except:
            pass

        # Variables
        self.running_process = None
        self.is_running = False

        # Create UI
        self.create_menu()
        self.create_main_layout()
        self.refresh_status()

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Config", command=self.open_config)
        file_menu.add_command(label="Open Logs Folder", command=self.open_logs_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Clean Up Old Files", command=self.show_cleanup_dialog)
        tools_menu.add_command(label="View Scheduled Tasks", command=self.view_scheduled_tasks)
        tools_menu.add_separator()
        tools_menu.add_command(label="Re-run Setup Script (Admin)", command=self.run_setup_script)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def create_main_layout(self):
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top section - Status
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="15")
        status_frame.pack(fill=tk.X, pady=(0, 15))

        # Status indicators
        status_grid = ttk.Frame(status_frame)
        status_grid.pack(fill=tk.X)

        # Login status
        ttk.Label(status_grid, text="Login Status:", font=("Segoe UI", 12)).grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.login_status_var = tk.StringVar(value="Checking...")
        self.login_status_label = ttk.Label(status_grid, textvariable=self.login_status_var, font=("Segoe UI", 12, "bold"))
        self.login_status_label.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        # Scheduled tasks
        ttk.Label(status_grid, text="Scheduled Tasks:", font=("Segoe UI", 12)).grid(row=0, column=2, sticky=tk.W, padx=(30, 5), pady=3)
        self.tasks_status_var = tk.StringVar(value="Checking...")
        ttk.Label(status_grid, textvariable=self.tasks_status_var, font=("Segoe UI", 12, "bold")).grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

        # Last run
        ttk.Label(status_grid, text="Last Run:", font=("Segoe UI", 12)).grid(row=1, column=0, sticky=tk.W, padx=5, pady=(8, 3))
        self.last_run_var = tk.StringVar(value="Checking...")
        ttk.Label(status_grid, textvariable=self.last_run_var, font=("Segoe UI", 12)).grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=5, pady=(8, 3))

        # Refresh button
        ttk.Button(status_grid, text="Refresh", command=self.refresh_status).grid(row=0, column=4, rowspan=2, padx=(30, 0), pady=3)

        # Middle section - Run Controls
        controls_frame = ttk.LabelFrame(main_frame, text="Run Booker", padding="15")
        controls_frame.pack(fill=tk.X, pady=(0, 15))

        controls_inner = ttk.Frame(controls_frame)
        controls_inner.pack(fill=tk.X)

        # Run buttons
        self.run_visible_btn = ttk.Button(
            controls_inner,
            text="▶ Run with Visible Browser",
            command=lambda: self.run_booker(headless=False),
            width=28
        )
        self.run_visible_btn.pack(side=tk.LEFT, padx=8, pady=5)

        self.run_headless_btn = ttk.Button(
            controls_inner,
            text="▶ Run Headless (Background)",
            command=lambda: self.run_booker(headless=True),
            width=28
        )
        self.run_headless_btn.pack(side=tk.LEFT, padx=8, pady=5)

        self.stop_btn = ttk.Button(
            controls_inner,
            text="⏹ Stop",
            command=self.stop_booker,
            width=12,
            state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=8, pady=5)

        # Setup button
        ttk.Button(
            controls_inner,
            text="🔐 Login Setup",
            command=self.run_login_setup,
            width=18
        ).pack(side=tk.RIGHT, padx=8, pady=5)

        # Scheduled Tasks button
        ttk.Button(
            controls_inner,
            text="⏰ Scheduled Tasks",
            command=self.view_scheduled_tasks,
            width=18
        ).pack(side=tk.RIGHT, padx=8, pady=5)

        # View Logs button
        ttk.Button(
            controls_inner,
            text="📋 View Logs",
            command=self.show_logs_viewer,
            width=14
        ).pack(side=tk.RIGHT, padx=8, pady=5)

        # Progress indicator
        self.progress_var = tk.StringVar(value="")
        ttk.Label(controls_frame, textvariable=self.progress_var, foreground="blue", font=("Segoe UI", 11)).pack(anchor=tk.W, pady=(8, 0))

        # Booking Days section - collapsible calendar
        days_frame = ttk.LabelFrame(main_frame, text="Booking Days", padding="15")
        days_frame.pack(fill=tk.X, pady=(0, 15))

        days_inner = ttk.Frame(days_frame)
        days_inner.pack(fill=tk.X)

        # Summary label showing selected days count
        self.days_summary_var = tk.StringVar(value="Loading...")
        ttk.Label(days_inner, textvariable=self.days_summary_var, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=(0, 20))

        # Open calendar button
        ttk.Button(
            days_inner,
            text="📅 Open Calendar",
            command=self.show_calendar_dialog,
            width=18
        ).pack(side=tk.LEFT, padx=5)

        # Initialize day vars (will be populated by load_booking_days)
        self.day_vars = {}  # {date_str: BooleanVar}

        # Load saved settings and update summary
        self.load_booking_days()
        self._update_days_summary()

        # Time Preferences section
        time_prefs_frame = ttk.LabelFrame(main_frame, text="Time Preferences", padding="15")
        time_prefs_frame.pack(fill=tk.X, pady=(0, 15))

        # Row 1: Enable checkbox and preset dropdown
        time_prefs_row1 = ttk.Frame(time_prefs_frame)
        time_prefs_row1.pack(fill=tk.X, pady=(0, 10))

        # Enable checkbox
        self.time_prefs_enabled = tk.BooleanVar(value=False)
        self.time_prefs_enable_cb = ttk.Checkbutton(
            time_prefs_row1,
            text="Enable time preferences",
            variable=self.time_prefs_enabled,
            command=self.on_time_prefs_changed
        )
        self.time_prefs_enable_cb.pack(side=tk.LEFT, padx=(0, 30))

        # Preset dropdown
        ttk.Label(time_prefs_row1, text="Prefer:").pack(side=tk.LEFT, padx=(0, 10))

        self.time_prefs_preset = tk.StringVar(value="afternoon_evening")
        self.time_prefs_presets = {
            "Morning (07:00-12:00)": "morning",
            "Afternoon (12:00-18:00)": "afternoon",
            "Evening (18:00-22:00)": "evening",
            "Afternoon/Evening (14:00-22:00)": "afternoon_evening",
            "Custom...": "custom",
        }
        self.time_prefs_dropdown = ttk.Combobox(
            time_prefs_row1,
            textvariable=self.time_prefs_preset,
            values=list(self.time_prefs_presets.keys()),
            state="disabled",
            width=28,
            font=("Segoe UI", 13)
        )
        self.time_prefs_dropdown.set("Afternoon/Evening (14:00-22:00)")
        self.time_prefs_dropdown.pack(side=tk.LEFT, padx=(0, 30))
        self.time_prefs_dropdown.bind("<<ComboboxSelected>>", lambda e: self.on_time_prefs_changed())

        # Strict mode checkbox (on same row)
        self.time_prefs_strict = tk.BooleanVar(value=False)
        self.time_prefs_strict_cb = ttk.Checkbutton(
            time_prefs_row1,
            text="Strict mode (only book preferred times)",
            variable=self.time_prefs_strict,
            command=self.on_time_prefs_changed,
            state=tk.DISABLED
        )
        self.time_prefs_strict_cb.pack(side=tk.LEFT)

        # Row 2: Custom time range (hidden by default)
        self.custom_time_frame = ttk.Frame(time_prefs_frame)
        # Don't pack yet - will be shown/hidden based on selection

        ttk.Label(self.custom_time_frame, text="From:").pack(side=tk.LEFT, padx=(0, 10))

        # Start hour entry
        self.custom_start_hour = tk.StringVar(value="14")
        self.custom_start_hour_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_start_hour,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_start_hour_entry.pack(side=tk.LEFT)
        self.custom_start_hour_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_start_hour_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text=":").pack(side=tk.LEFT)

        # Start minute entry
        self.custom_start_min = tk.StringVar(value="00")
        self.custom_start_min_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_start_min,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_start_min_entry.pack(side=tk.LEFT)
        self.custom_start_min_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_start_min_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text="To:").pack(side=tk.LEFT, padx=(30, 10))

        # End hour entry
        self.custom_end_hour = tk.StringVar(value="22")
        self.custom_end_hour_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_end_hour,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_end_hour_entry.pack(side=tk.LEFT)
        self.custom_end_hour_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_end_hour_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        ttk.Label(self.custom_time_frame, text=":").pack(side=tk.LEFT)

        # End minute entry
        self.custom_end_min = tk.StringVar(value="00")
        self.custom_end_min_entry = ttk.Entry(
            self.custom_time_frame,
            textvariable=self.custom_end_min,
            width=3,
            font=("Segoe UI", 13),
            justify="center"
        )
        self.custom_end_min_entry.pack(side=tk.LEFT)
        self.custom_end_min_entry.bind("<FocusOut>", lambda e: self.on_time_prefs_changed())
        self.custom_end_min_entry.bind("<Return>", lambda e: self.on_time_prefs_changed())

        # Load saved time preferences
        self.load_time_preferences()

        # Booking Strategy section
        strategy_frame = ttk.LabelFrame(main_frame, text="Booking Strategy", padding="15")
        strategy_frame.pack(fill=tk.X, pady=(0, 15))

        strategy_inner = ttk.Frame(strategy_frame)
        strategy_inner.pack(fill=tk.X)

        # Row 1: Reverse date order toggle
        strategy_row1 = ttk.Frame(strategy_inner)
        strategy_row1.pack(fill=tk.X, pady=(0, 8))

        self.reverse_date_order = tk.BooleanVar(value=False)
        self.reverse_date_order_cb = ttk.Checkbutton(
            strategy_row1,
            text="Book furthest dates first (prioritize newly available rooms)",
            variable=self.reverse_date_order,
            command=self.on_strategy_changed
        )
        self.reverse_date_order_cb.pack(side=tk.LEFT, padx=(0, 20))

        # Explanation label
        ttk.Label(
            strategy_row1,
            text="(Start from 7 days ahead instead of today)",
            foreground="gray",
            font=("Segoe UI", 11)
        ).pack(side=tk.LEFT)

        # Row 2: Smart swap toggle
        # Smart swap feature (disabled for now - kept for future use)
        # strategy_row2 = ttk.Frame(strategy_inner)
        # strategy_row2.pack(fill=tk.X)
        # self.smart_swap_enabled = tk.BooleanVar(value=False)
        # self.smart_swap_cb = ttk.Checkbutton(
        #     strategy_row2,
        #     text="Smart swap (cancel reservations outside preferred times to book better slots)",
        #     variable=self.smart_swap_enabled,
        #     command=self.on_strategy_changed
        # )
        # self.smart_swap_cb.pack(side=tk.LEFT, padx=(0, 20))
        # ttk.Label(
        #     strategy_row2,
        #     text="(Only when < 4 hours quota remaining)",
        #     foreground="gray",
        #     font=("Segoe UI", 11)
        # ).pack(side=tk.LEFT)

        # Load saved strategy settings
        self.load_strategy_settings()

        # Bottom section - Output Log
        log_frame = ttk.LabelFrame(main_frame, text="Output Log", padding="15")
        log_frame.pack(fill=tk.BOTH, expand=True)

        # Log text area - reduced height so buttons are visible on open
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 13),
            height=8
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Configure tags for colored output
        self.log_text.tag_config("success", foreground="green")
        self.log_text.tag_config("error", foreground="red")
        self.log_text.tag_config("warning", foreground="orange")
        self.log_text.tag_config("info", foreground="blue")

        # Log controls
        log_controls = ttk.Frame(log_frame)
        log_controls.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(log_controls, text="Clear Log", command=self.clear_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_controls, text="Open Today's Log File", command=self.open_todays_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_controls, text="View Booking History", command=self.show_history_dialog).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_controls, text="Manage Events", command=self.show_events_dialog).pack(side=tk.LEFT, padx=5)

    def log(self, message, tag=None):
        """Add message to log with optional tag for coloring."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n", tag)
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def refresh_status(self):
        """Refresh all status indicators."""
        # Check login status
        if STATE_FILE.exists():
            try:
                mtime = datetime.fromtimestamp(STATE_FILE.stat().st_mtime)
                age = datetime.now() - mtime
                if age.days > 7:
                    self.login_status_var.set(f"⚠️ May be expired ({age.days} days old)")
                else:
                    self.login_status_var.set(f"✅ Saved ({mtime.strftime('%Y-%m-%d %H:%M')})")
            except:
                self.login_status_var.set("⚠️ Unknown")
        else:
            self.login_status_var.set("❌ Not logged in")

        # Check scheduled tasks
        self.check_scheduled_tasks()

        # Check last run from log
        self.check_last_run()

    def check_scheduled_tasks(self):
        """Check how many AsimutBooker tasks are scheduled."""
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/fo", "CSV"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            count = result.stdout.count("AsimutBooker_")
            if count > 0:
                self.tasks_status_var.set(f"✅ {count} tasks active")
            else:
                self.tasks_status_var.set("❌ No tasks scheduled")
        except Exception as e:
            self.tasks_status_var.set("⚠️ Could not check")

    def check_last_run(self):
        """Check when the booker last ran from history."""
        history = self.load_history()
        runs = history.get("runs", [])

        if runs:
            last_run = runs[0]
            try:
                timestamp = datetime.fromisoformat(last_run.get("timestamp", ""))
                time_str = timestamp.strftime("%Y-%m-%d %H:%M")
                bookings = last_run.get("bookings_made", 0)
                events = last_run.get("events_detected", 0)
                self.last_run_var.set(f"{time_str} - {bookings} bookings, {events} events detected")
                return
            except:
                pass

        # Fallback to scheduler log
        scheduler_log = LOGS_DIR / "scheduler.log"
        if scheduler_log.exists():
            try:
                with open(scheduler_log, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        self.last_run_var.set(last_line)
                        return
            except:
                pass
        self.last_run_var.set("No runs recorded yet")

    def run_booker(self, headless=False):
        """Run the booker script."""
        if self.is_running:
            messagebox.showwarning("Already Running", "The booker is already running.")
            return

        self.is_running = True
        self.run_visible_btn.config(state=tk.DISABLED)
        self.run_headless_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        mode = "headless" if headless else "visible browser"
        self.log(f"Starting booker in {mode} mode...", "info")
        self.progress_var.set(f"Running in {mode} mode...")

        # Run in thread to keep UI responsive
        thread = threading.Thread(target=self._run_booker_thread, args=(headless,))
        thread.daemon = True
        thread.start()

    def _run_booker_thread(self, headless):
        """Thread function to run booker."""
        bookings_made = 0
        events_detected = 0
        booking_details = []

        try:
            cmd = [sys.executable, str(APP_DIR / "book_week.py")]
            if headless:
                cmd.append("--headless")

            self.running_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(APP_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW if headless else 0
            )

            # Read output line by line
            for line in iter(self.running_process.stdout.readline, ''):
                if line:
                    line = line.strip()

                    # Track statistics
                    if "SUCCESS:" in line or "Booked" in line:
                        bookings_made += 1
                        # Extract booking details
                        booking_details.append(line)
                        self.root.after(0, lambda l=line: self.log(l, "success"))
                    elif "Found" in line and "unique events" in line:
                        # Parse "Found X unique events in agenda:"
                        try:
                            events_detected = int(line.split("Found")[1].split("unique")[0].strip())
                        except:
                            pass
                        self.root.after(0, lambda l=line: self.log(l, "info"))
                    elif "Total bookings made:" in line:
                        # Parse final count
                        try:
                            bookings_made = int(line.split(":")[1].strip())
                        except:
                            pass
                        self.root.after(0, lambda l=line: self.log(l, "success"))
                    elif "ERROR" in line or "Error" in line or "failed" in line.lower():
                        self.root.after(0, lambda l=line: self.log(l, "error"))
                    elif "WARNING" in line or "Skip" in line:
                        self.root.after(0, lambda l=line: self.log(l, "warning"))
                    else:
                        self.root.after(0, lambda l=line: self.log(l))

            self.running_process.wait()
            exit_code = self.running_process.returncode

            if exit_code == 0:
                self.root.after(0, lambda: self.log("Booker finished successfully.", "success"))
            else:
                self.root.after(0, lambda: self.log(f"Booker finished with exit code {exit_code}", "warning"))

            # Save to history
            details = "; ".join(booking_details[:5])  # First 5 booking details
            if bookings_made == 0:
                details = "No bookings made"
            self.root.after(0, lambda: self.add_history_entry(bookings_made, events_detected, details))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error running booker: {e}", "error"))
            self.root.after(0, lambda: self.add_history_entry(0, 0, f"Error: {str(e)[:50]}"))
        finally:
            self.root.after(0, self._on_booker_finished)

    def _on_booker_finished(self):
        """Called when booker finishes."""
        self.is_running = False
        self.running_process = None
        self.run_visible_btn.config(state=tk.NORMAL)
        self.run_headless_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_var.set("")
        self.refresh_status()

    def stop_booker(self):
        """Stop the running booker process."""
        if self.running_process:
            self.running_process.terminate()
            self.log("Booker stopped by user.", "warning")

    def run_login_setup(self):
        """Run the login setup (opens browser for SSO)."""
        self.log("Starting login setup - browser will open for SSO login...", "info")

        # Run in visible mode for login
        thread = threading.Thread(target=self._run_setup_thread)
        thread.daemon = True
        thread.start()

    def _run_setup_thread(self):
        """Thread function to run login setup."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main", "--setup", "--headed"],
                cwd=str(APP_DIR),
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                self.root.after(0, lambda: self.log("Login setup completed successfully!", "success"))
            else:
                self.root.after(0, lambda: self.log(f"Setup output: {result.stdout}\n{result.stderr}", "error"))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error during setup: {e}", "error"))
        finally:
            self.root.after(0, self.refresh_status)

    def show_cleanup_dialog(self):
        """Show dialog to clean up old/unused files."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Clean Up Old Files")
        dialog.geometry("500x400")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Select files to delete:", font=("", 11, "bold")).pack(pady=10)

        # Find potentially removable files
        removable_files = []

        # Old Python files that might be test versions
        for pattern in ["test_*.py", "old_*.py", "*_backup.py", "*_old.py"]:
            removable_files.extend(APP_DIR.glob(pattern))

        # Old log files (older than 7 days)
        if LOGS_DIR.exists():
            for log_file in LOGS_DIR.glob("*.log"):
                if log_file.name != "scheduler.log":
                    try:
                        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                        if (datetime.now() - mtime).days > 7:
                            removable_files.append(log_file)
                    except:
                        pass

        # Checkboxes for each file
        frame = ttk.Frame(dialog)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        canvas = tk.Canvas(frame)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        file_vars = {}
        if removable_files:
            for f in removable_files:
                var = tk.BooleanVar(value=False)
                file_vars[f] = var
                rel_path = f.relative_to(APP_DIR) if f.is_relative_to(APP_DIR) else f
                cb = ttk.Checkbutton(scrollable, text=str(rel_path), variable=var)
                cb.pack(anchor=tk.W, pady=2)
        else:
            ttk.Label(scrollable, text="No old files found to clean up.").pack()

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=10, padx=10)

        def delete_selected():
            deleted = 0
            for f, var in file_vars.items():
                if var.get():
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception as e:
                        messagebox.showerror("Error", f"Could not delete {f}: {e}")
            if deleted > 0:
                messagebox.showinfo("Done", f"Deleted {deleted} file(s).")
                dialog.destroy()

        ttk.Button(btn_frame, text="Delete Selected", command=delete_selected).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

    def view_scheduled_tasks(self):
        """Show scheduled tasks manager dialog."""
        self._show_scheduled_tasks_dialog()

    def _show_scheduled_tasks_dialog(self):
        """Show dialog for managing scheduled tasks."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Scheduled Tasks Manager")
        dialog.geometry("900x600")
        dialog.transient(self.root)

        # Main container
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header_frame, text="AsimutBooker Scheduled Tasks", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)

        # Task count label
        self.task_count_var = tk.StringVar(value="Loading...")
        ttk.Label(header_frame, textvariable=self.task_count_var, foreground="gray").pack(side=tk.RIGHT)

        # Treeview for tasks
        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Scrollbars
        tree_scroll_y = ttk.Scrollbar(tree_frame)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        tree_scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # Treeview with columns
        columns = ("task_name", "trigger_time", "target_time", "status", "next_run")
        self.tasks_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set
        )

        # Configure columns
        self.tasks_tree.heading("task_name", text="Task Name")
        self.tasks_tree.heading("trigger_time", text="Trigger Time")
        self.tasks_tree.heading("target_time", text="Booking Time")
        self.tasks_tree.heading("status", text="Status")
        self.tasks_tree.heading("next_run", text="Next Run")

        self.tasks_tree.column("task_name", width=180, minwidth=120)
        self.tasks_tree.column("trigger_time", width=100, minwidth=80)
        self.tasks_tree.column("target_time", width=100, minwidth=80)
        self.tasks_tree.column("status", width=80, minwidth=60)
        self.tasks_tree.column("next_run", width=180, minwidth=120)

        self.tasks_tree.pack(fill=tk.BOTH, expand=True)

        tree_scroll_y.config(command=self.tasks_tree.yview)
        tree_scroll_x.config(command=self.tasks_tree.xview)

        # Button frame
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        # Left side buttons
        left_btns = ttk.Frame(btn_frame)
        left_btns.pack(side=tk.LEFT)

        ttk.Button(left_btns, text="➕ Add Task", command=lambda: self._add_scheduled_task(dialog)).pack(side=tk.LEFT, padx=5)
        ttk.Button(left_btns, text="✏️ Edit Selected", command=lambda: self._edit_scheduled_task(dialog)).pack(side=tk.LEFT, padx=5)
        ttk.Button(left_btns, text="🗑️ Delete Selected", command=lambda: self._delete_scheduled_task(dialog)).pack(side=tk.LEFT, padx=5)

        # Right side buttons
        right_btns = ttk.Frame(btn_frame)
        right_btns.pack(side=tk.RIGHT)

        ttk.Button(right_btns, text="🔄 Refresh", command=self._refresh_tasks_list).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_btns, text="Open Task Scheduler", command=lambda: subprocess.Popen(["taskschd.msc"], shell=True)).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_btns, text="Close", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

        # Store dialog reference
        self.tasks_dialog = dialog

        # Populate the list
        self._refresh_tasks_list()

    def _refresh_tasks_list(self):
        """Refresh the scheduled tasks list."""
        # Clear existing items
        for item in self.tasks_tree.get_children():
            self.tasks_tree.delete(item)

        try:
            # Use schtasks command which is more reliable
            result = subprocess.run(
                ["schtasks", "/query", "/fo", "CSV", "/v"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            tasks = []
            lines = result.stdout.strip().split('\n')

            # Parse CSV output - find AsimutBooker tasks
            # CSV columns: HostName, TaskName, Next Run Time, Status, ... (many more)
            for line in lines[1:]:  # Skip header
                if 'AsimutBooker' not in line:
                    continue

                # Parse CSV (handle quoted fields)
                reader = csv.reader(io.StringIO(line))
                try:
                    fields = next(reader)
                    if len(fields) < 4:
                        continue

                    task_name = fields[1].replace('\\', '')  # Remove leading backslash
                    next_run = fields[2]
                    status = fields[3]

                    # Extract trigger time from task name (e.g., AsimutBooker_1000 -> calculate 09:58)
                    trigger_time = ""
                    target_time = ""
                    if '_' in task_name:
                        time_part = task_name.split('_')[-1]
                        if len(time_part) == 4 and time_part.isdigit():
                            target_hour = int(time_part[:2])
                            target_min = int(time_part[2:])
                            target_time = f"{target_hour:02d}:{target_min:02d}"

                            # Calculate trigger time (2 min before)
                            trigger_min = target_min - 2
                            trigger_hour = target_hour
                            if trigger_min < 0:
                                trigger_min += 60
                                trigger_hour -= 1
                            trigger_time = f"{trigger_hour:02d}:{trigger_min:02d}"

                    tasks.append((task_name, trigger_time, target_time, status, next_run))
                except:
                    continue

            # Sort by task name
            tasks.sort(key=lambda x: x[0])

            for task in tasks:
                self.tasks_tree.insert("", tk.END, values=task)

            self.task_count_var.set(f"{len(tasks)} task(s)")

        except subprocess.TimeoutExpired:
            self.task_count_var.set("Error: Timeout")
        except Exception as e:
            self.task_count_var.set(f"Error: {str(e)[:30]}")

    def _add_scheduled_task(self, parent_dialog):
        """Show dialog to add a new scheduled task."""
        dialog = tk.Toplevel(parent_dialog)
        dialog.title("Add Scheduled Task")
        dialog.geometry("400x250")
        dialog.transient(parent_dialog)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Add New Scheduled Task", font=("Segoe UI", 12, "bold")).pack(pady=(0, 20))

        # Booking time input
        time_frame = ttk.Frame(frame)
        time_frame.pack(fill=tk.X, pady=10)

        ttk.Label(time_frame, text="Booking Time (HH:MM):").pack(side=tk.LEFT)

        # Hour dropdown
        hour_var = tk.StringVar(value="10")
        hour_combo = ttk.Combobox(time_frame, textvariable=hour_var,
                                   values=[f"{h:02d}" for h in range(7, 23)],
                                   width=4, state="readonly")
        hour_combo.pack(side=tk.LEFT, padx=(10, 2))

        ttk.Label(time_frame, text=":").pack(side=tk.LEFT)

        # Minute dropdown (00 or 30)
        minute_var = tk.StringVar(value="00")
        minute_combo = ttk.Combobox(time_frame, textvariable=minute_var,
                                     values=["00", "30"],
                                     width=4, state="readonly")
        minute_combo.pack(side=tk.LEFT, padx=2)

        # Info label
        info_label = ttk.Label(frame, text="Task will trigger 2 minutes before this time\nto prepare for booking.",
                               foreground="gray", justify=tk.CENTER)
        info_label.pack(pady=20)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(20, 0))

        def create_task():
            booking_time = f"{hour_var.get()}:{minute_var.get()}"
            task_name = f"AsimutBooker_{hour_var.get()}{minute_var.get()}"

            # Check if task already exists
            result = subprocess.run(
                ["powershell", "-Command", f"Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue"],
                capture_output=True, text=True
            )
            if task_name in result.stdout:
                messagebox.showerror("Error", f"Task '{task_name}' already exists!", parent=dialog)
                return

            # Calculate trigger time (2 minutes before)
            trigger_hour = int(hour_var.get())
            trigger_minute = int(minute_var.get()) - 2
            if trigger_minute < 0:
                trigger_minute += 60
                trigger_hour -= 1
            trigger_time = f"{trigger_hour:02d}:{trigger_minute:02d}"

            # Create the task
            script_path = APP_DIR / "run_booker.bat"
            ps_command = f'''
                $Action = New-ScheduledTaskAction -Execute '{script_path}' -Argument '--target-time {booking_time}' -WorkingDirectory '{APP_DIR}'
                $Trigger = New-ScheduledTaskTrigger -Daily -At '{trigger_time}'
                $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
                $Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
                Register-ScheduledTask -TaskName '{task_name}' -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'AsimutBooker automatic room booking at {booking_time} (starts at {trigger_time})'
            '''

            try:
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    messagebox.showinfo("Success", f"Task '{task_name}' created successfully!\n\nTriggers at {trigger_time}, books at {booking_time}", parent=dialog)
                    dialog.destroy()
                    self._refresh_tasks_list()
                else:
                    messagebox.showerror("Error", f"Failed to create task:\n{result.stderr}", parent=dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create task: {e}", parent=dialog)

        ttk.Button(btn_frame, text="Create Task", command=create_task).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def _edit_scheduled_task(self, parent_dialog):
        """Edit the selected scheduled task."""
        selection = self.tasks_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a task to edit.", parent=parent_dialog)
            return

        item = selection[0]
        values = self.tasks_tree.item(item, "values")
        task_name = values[0]
        current_trigger = values[1]
        current_target = values[2]

        dialog = tk.Toplevel(parent_dialog)
        dialog.title(f"Edit Task: {task_name}")
        dialog.geometry("400x250")
        dialog.transient(parent_dialog)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Edit: {task_name}", font=("Segoe UI", 12, "bold")).pack(pady=(0, 20))

        # Booking time input
        time_frame = ttk.Frame(frame)
        time_frame.pack(fill=tk.X, pady=10)

        ttk.Label(time_frame, text="Booking Time (HH:MM):").pack(side=tk.LEFT)

        # Parse current target time
        current_hour = current_target.split(':')[0] if current_target else "10"
        current_minute = current_target.split(':')[1] if current_target and ':' in current_target else "00"

        # Hour dropdown
        hour_var = tk.StringVar(value=current_hour)
        hour_combo = ttk.Combobox(time_frame, textvariable=hour_var,
                                   values=[f"{h:02d}" for h in range(7, 23)],
                                   width=4, state="readonly")
        hour_combo.pack(side=tk.LEFT, padx=(10, 2))

        ttk.Label(time_frame, text=":").pack(side=tk.LEFT)

        # Minute dropdown
        minute_var = tk.StringVar(value=current_minute)
        minute_combo = ttk.Combobox(time_frame, textvariable=minute_var,
                                     values=["00", "30"],
                                     width=4, state="readonly")
        minute_combo.pack(side=tk.LEFT, padx=2)

        # Info label
        info_label = ttk.Label(frame, text=f"Current: triggers at {current_trigger}, books at {current_target}",
                               foreground="gray")
        info_label.pack(pady=20)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(20, 0))

        def update_task():
            new_booking_time = f"{hour_var.get()}:{minute_var.get()}"
            new_task_name = f"AsimutBooker_{hour_var.get()}{minute_var.get()}"

            # Calculate new trigger time
            trigger_hour = int(hour_var.get())
            trigger_minute = int(minute_var.get()) - 2
            if trigger_minute < 0:
                trigger_minute += 60
                trigger_hour -= 1
            new_trigger_time = f"{trigger_hour:02d}:{trigger_minute:02d}"

            script_path = APP_DIR / "run_booker.bat"

            # Delete old task and create new one
            ps_command = f'''
                Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false -ErrorAction SilentlyContinue
                $Action = New-ScheduledTaskAction -Execute '{script_path}' -Argument '--target-time {new_booking_time}' -WorkingDirectory '{APP_DIR}'
                $Trigger = New-ScheduledTaskTrigger -Daily -At '{new_trigger_time}'
                $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
                $Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
                Register-ScheduledTask -TaskName '{new_task_name}' -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'AsimutBooker automatic room booking at {new_booking_time} (starts at {new_trigger_time})'
            '''

            try:
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    messagebox.showinfo("Success", f"Task updated!\n\nNew schedule: triggers at {new_trigger_time}, books at {new_booking_time}", parent=dialog)
                    dialog.destroy()
                    self._refresh_tasks_list()
                else:
                    messagebox.showerror("Error", f"Failed to update task:\n{result.stderr}", parent=dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to update task: {e}", parent=dialog)

        ttk.Button(btn_frame, text="Update Task", command=update_task).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

    def _delete_scheduled_task(self, parent_dialog):
        """Delete the selected scheduled task(s)."""
        selection = self.tasks_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select task(s) to delete.", parent=parent_dialog)
            return

        # Get task names
        task_names = [self.tasks_tree.item(item, "values")[0] for item in selection]

        if len(task_names) == 1:
            msg = f"Are you sure you want to delete '{task_names[0]}'?"
        else:
            msg = f"Are you sure you want to delete {len(task_names)} tasks?\n\n" + "\n".join(task_names[:5])
            if len(task_names) > 5:
                msg += f"\n... and {len(task_names) - 5} more"

        if not messagebox.askyesno("Confirm Delete", msg, parent=parent_dialog):
            return

        # Delete tasks
        deleted = 0
        errors = []
        for task_name in task_names:
            try:
                result = subprocess.run(
                    ["powershell", "-Command", f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false"],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode == 0:
                    deleted += 1
                else:
                    errors.append(f"{task_name}: {result.stderr}")
            except Exception as e:
                errors.append(f"{task_name}: {e}")

        if errors:
            messagebox.showwarning("Partial Success",
                                   f"Deleted {deleted} task(s).\n\nErrors:\n" + "\n".join(errors[:3]),
                                   parent=parent_dialog)
        else:
            messagebox.showinfo("Success", f"Deleted {deleted} task(s).", parent=parent_dialog)

        self._refresh_tasks_list()

    def run_setup_script(self):
        """Run the PowerShell setup script (requires admin)."""
        script_path = APP_DIR / "setup_scheduled_tasks.ps1"
        if not script_path.exists():
            messagebox.showerror("Error", "setup_scheduled_tasks.ps1 not found!")
            return

        msg = "This will run the setup script as Administrator.\n\nIt will:\n"
        msg += "• Remove existing scheduled tasks\n"
        msg += "• Create new scheduled tasks (every 30 min)\n"
        msg += "• Enable wake timers\n"
        msg += "• Configure lid close action\n\n"
        msg += "Continue?"

        if messagebox.askyesno("Run Setup Script", msg):
            try:
                # Run PowerShell as admin
                subprocess.Popen(
                    ["powershell", "-Command",
                     f"Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"{script_path}\"' -Verb RunAs"],
                    shell=True
                )
                self.log("Setup script launched (check admin PowerShell window).", "info")
            except Exception as e:
                messagebox.showerror("Error", f"Could not launch setup script: {e}")

    def open_config(self):
        """Open config file in default editor."""
        if CONFIG_FILE.exists():
            os.startfile(str(CONFIG_FILE))
        else:
            messagebox.showerror("Error", "Config file not found!")

    def open_logs_folder(self):
        """Open logs folder in Explorer."""
        if LOGS_DIR.exists():
            os.startfile(str(LOGS_DIR))
        else:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(LOGS_DIR))

    def open_todays_log(self):
        """Open today's log file."""
        today_log = LOGS_DIR / f"asimut_{datetime.now().strftime('%Y%m%d')}.log"
        if today_log.exists():
            os.startfile(str(today_log))
        else:
            messagebox.showinfo("No Log", "No log file for today yet.")

    def show_logs_viewer(self):
        """Show log viewer dialog with date folders and log files."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Log Viewer")
        dialog.geometry("1000x700")
        dialog.transient(self.root)

        # Main container
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left panel - date/file tree
        left_frame = ttk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        ttk.Label(left_frame, text="Log Files", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))

        # Treeview for dates and log files
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(tree_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_tree = ttk.Treeview(tree_frame, yscrollcommand=tree_scroll.set, show="tree")
        self.log_tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.log_tree.yview)

        # Right panel - log content
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Header with current file name
        header_frame = ttk.Frame(right_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header_frame, text="Log Content", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        self.current_log_var = tk.StringVar(value="Select a log file")
        ttk.Label(header_frame, textvariable=self.current_log_var, foreground="gray").pack(side=tk.RIGHT)

        # Log content text area
        text_frame = ttk.Frame(right_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            text_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Bottom buttons
        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text="Open in Explorer", command=self.open_logs_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Refresh", command=lambda: self._populate_log_tree()).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)

        # Bind tree selection
        self.log_tree.bind("<<TreeviewSelect>>", self._on_log_select)

        # Populate tree
        self._populate_log_tree()

    def _populate_log_tree(self):
        """Populate the log tree with date folders and log files."""
        # Clear existing items
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)

        if not LOGS_DIR.exists():
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            return

        # Get all date folders (format: YYYY-MM-DD) and individual log files
        items = []

        for item in LOGS_DIR.iterdir():
            if item.is_dir() and len(item.name) == 10 and item.name[4] == '-':
                # Date folder
                items.append(('folder', item.name, item))
            elif item.is_file() and item.suffix == '.log':
                # Individual log file at root level
                items.append(('file', item.name, item))

        # Sort by name descending (newest first)
        items.sort(key=lambda x: x[1], reverse=True)

        for item_type, name, path in items:
            if item_type == 'folder':
                # Format date nicely: 2026-02-02 -> "Sun 2 Feb 2026"
                try:
                    date_obj = datetime.strptime(name, "%Y-%m-%d")
                    display_name = date_obj.strftime("%a %d %b %Y")
                except:
                    display_name = name

                folder_id = self.log_tree.insert("", tk.END, text=f"📁 {display_name}", values=(str(path),), open=False)

                # Add log files in this folder
                log_files = sorted(path.glob("*.log"), reverse=True)
                for log_file in log_files:
                    # Format time: 07-30-00.log -> "07:30:00"
                    time_name = log_file.stem.replace("-", ":")
                    self.log_tree.insert(folder_id, tk.END, text=f"📄 {time_name}", values=(str(log_file),))

            else:
                # Root-level log file
                self.log_tree.insert("", tk.END, text=f"📄 {name}", values=(str(path),))

    def _on_log_select(self, event):
        """Handle log tree selection."""
        selection = self.log_tree.selection()
        if not selection:
            return

        item = selection[0]
        values = self.log_tree.item(item, "values")

        if not values:
            return

        file_path = Path(values[0])

        # Only load if it's a file, not a folder
        if file_path.is_file():
            self._load_log_content(file_path)

    def _load_log_content(self, log_path):
        """Load and display log file content."""
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, content)
            self.log_text.config(state=tk.DISABLED)

            # Update header with file info
            rel_path = log_path.relative_to(LOGS_DIR) if LOGS_DIR in log_path.parents or log_path.parent == LOGS_DIR else log_path.name
            self.current_log_var.set(str(rel_path))

        except Exception as e:
            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, f"Error loading log: {e}")
            self.log_text.config(state=tk.DISABLED)
            self.current_log_var.set("Error")

    def show_about(self):
        """Show about dialog."""
        msg = "AsimutBooker Control Panel\n\n"
        msg += "Automated practice room booking for RWCMD.\n\n"
        msg += "Features:\n"
        msg += "• Auto-books rooms as they become available\n"
        msg += "• Respects all booking rules (quota, peak hours, etc.)\n"
        msg += "• Runs on schedule with wake-from-sleep\n\n"
        msg += f"App Directory: {APP_DIR}"

        messagebox.showinfo("About AsimutBooker", msg)

    def load_history(self):
        """Load booking history from file."""
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {"runs": []}

    def save_history(self, history):
        """Save booking history to file."""
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)

    def load_settings(self):
        """Load settings from file."""
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_settings(self, settings):
        """Save settings to file."""
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)

    def load_booking_days(self):
        """Load enabled booking days from settings."""
        settings = self.load_settings()
        disabled_dates = set(settings.get("disabled_dates", []))

        # Initialize day_vars for next 60 days (to cover month view)
        today = datetime.now().date()
        self.day_vars = {}
        for i in range(60):
            target_date = today + timedelta(days=i)
            date_str = target_date.strftime('%Y-%m-%d')
            # Default to enabled unless in disabled list
            var = tk.BooleanVar(value=(date_str not in disabled_dates))
            self.day_vars[date_str] = var

    def save_booking_days(self):
        """Save enabled booking days to settings."""
        settings = self.load_settings()

        # Get list of disabled dates
        disabled_dates = []
        for date_str, var in self.day_vars.items():
            if not var.get():
                disabled_dates.append(date_str)

        settings["disabled_dates"] = disabled_dates
        self.save_settings(settings)
        self._update_days_summary()

    def _update_days_summary(self):
        """Update the days summary label."""
        today = datetime.now().date()
        enabled_count = 0
        total_count = 8  # Today + 7 days

        for i in range(total_count):
            target_date = today + timedelta(days=i)
            date_str = target_date.strftime('%Y-%m-%d')
            if date_str in self.day_vars and self.day_vars[date_str].get():
                enabled_count += 1

        if enabled_count == total_count:
            self.days_summary_var.set(f"All {total_count} days enabled for booking")
        elif enabled_count == 0:
            self.days_summary_var.set("No days enabled for booking")
        else:
            self.days_summary_var.set(f"{enabled_count}/{total_count} days enabled for booking")

    def load_time_preferences(self):
        """Load time preferences from settings."""
        settings = self.load_settings()
        prefs = settings.get("time_preferences", {})

        # Set enabled state
        enabled = prefs.get("enabled", False)
        self.time_prefs_enabled.set(enabled)

        # Set preset
        preset = prefs.get("preset", "afternoon_evening")
        # Find the display name for this preset
        for display_name, preset_key in self.time_prefs_presets.items():
            if preset_key == preset:
                self.time_prefs_dropdown.set(display_name)
                break

        # Set custom hours and minutes
        self.custom_start_hour.set(str(prefs.get("custom_start_hour", 14)))
        self.custom_start_min.set(str(prefs.get("custom_start_min", 0)).zfill(2))
        self.custom_end_hour.set(str(prefs.get("custom_end_hour", 22)))
        self.custom_end_min.set(str(prefs.get("custom_end_min", 0)).zfill(2))

        # Set strict mode
        self.time_prefs_strict.set(prefs.get("strict_mode", False))

        # Update UI state
        self._update_time_prefs_ui_state()

    def save_time_preferences(self):
        """Save time preferences to settings."""
        settings = self.load_settings()

        # Get the preset key from the display name
        display_name = self.time_prefs_dropdown.get()
        preset_key = self.time_prefs_presets.get(display_name, "afternoon_evening")

        # Get custom hours and minutes (validate)
        try:
            start_hour = max(0, min(23, int(self.custom_start_hour.get())))
            start_min = max(0, min(59, int(self.custom_start_min.get())))
            end_hour = max(0, min(24, int(self.custom_end_hour.get())))
            end_min = max(0, min(59, int(self.custom_end_min.get())))

            # Ensure end > start
            start_total = start_hour * 60 + start_min
            end_total = end_hour * 60 + end_min
            if end_total <= start_total:
                end_hour = min(start_hour + 1, 24)
                end_min = start_min
                self.custom_end_hour.set(str(end_hour))
                self.custom_end_min.set(str(end_min).zfill(2))

            # Update display with validated values
            self.custom_start_hour.set(str(start_hour))
            self.custom_start_min.set(str(start_min).zfill(2))
            self.custom_end_hour.set(str(end_hour))
            self.custom_end_min.set(str(end_min).zfill(2))
        except ValueError:
            start_hour, start_min = 14, 0
            end_hour, end_min = 22, 0

        settings["time_preferences"] = {
            "enabled": self.time_prefs_enabled.get(),
            "preset": preset_key,
            "custom_start_hour": start_hour,
            "custom_start_min": start_min,
            "custom_end_hour": end_hour,
            "custom_end_min": end_min,
            "strict_mode": self.time_prefs_strict.get()
        }

        self.save_settings(settings)

    def on_time_prefs_changed(self):
        """Handle changes to time preferences."""
        self._update_time_prefs_ui_state()
        self.save_time_preferences()

    def _update_time_prefs_ui_state(self):
        """Update enabled/disabled state of time preference controls."""
        enabled = self.time_prefs_enabled.get()
        is_custom = self.time_prefs_dropdown.get() == "Custom..."

        if enabled:
            self.time_prefs_dropdown.config(state="readonly")
            self.time_prefs_strict_cb.config(state=tk.NORMAL)
        else:
            self.time_prefs_dropdown.config(state="disabled")
            self.time_prefs_strict_cb.config(state=tk.DISABLED)

        # Show/hide custom time frame (it's on a separate row now)
        if enabled and is_custom:
            self.custom_time_frame.pack(fill=tk.X, pady=(5, 0))
        else:
            self.custom_time_frame.pack_forget()

    def load_strategy_settings(self):
        """Load booking strategy settings from settings file."""
        settings = self.load_settings()
        strategy = settings.get("booking_strategy", {})
        self.reverse_date_order.set(strategy.get("reverse_date_order", False))
        # Smart swap disabled for now
        # self.smart_swap_enabled.set(strategy.get("smart_swap_enabled", False))

    def save_strategy_settings(self):
        """Save booking strategy settings to settings file."""
        settings = self.load_settings()
        settings["booking_strategy"] = {
            "reverse_date_order": self.reverse_date_order.get()
            # Smart swap disabled for now
            # "smart_swap_enabled": self.smart_swap_enabled.get()
        }
        self.save_settings(settings)

    def on_strategy_changed(self):
        """Handle changes to booking strategy settings."""
        self.save_strategy_settings()

    def add_history_entry(self, bookings_made, events_detected, details=""):
        """Add a new entry to booking history."""
        history = self.load_history()

        entry = {
            "timestamp": datetime.now().isoformat(),
            "bookings_made": bookings_made,
            "events_detected": events_detected,
            "details": details
        }

        history["runs"].insert(0, entry)  # Add to beginning

        # Keep only last 100 entries
        history["runs"] = history["runs"][:100]

        self.save_history(history)

    def show_history_dialog(self):
        """Show dialog with booking history."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Booking History")
        dialog.geometry("800x600")
        dialog.transient(self.root)

        # Header
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(header_frame, text="Booking History", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header_frame, text="Refresh", command=lambda: self._refresh_history_list(tree)).pack(side=tk.RIGHT)
        ttk.Button(header_frame, text="Clear History", command=lambda: self._clear_history(tree)).pack(side=tk.RIGHT, padx=5)

        # Treeview for history
        tree_frame = ttk.Frame(dialog, padding="10")
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("Date/Time", "Bookings", "Events", "Details")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=20)

        tree.heading("Date/Time", text="Date/Time")
        tree.heading("Bookings", text="Bookings Made")
        tree.heading("Events", text="Events Detected")
        tree.heading("Details", text="Details")

        tree.column("Date/Time", width=180)
        tree.column("Bookings", width=100, anchor=tk.CENTER)
        tree.column("Events", width=120, anchor=tk.CENTER)
        tree.column("Details", width=350)

        # Scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Load history
        self._refresh_history_list(tree)

        # Summary at bottom
        summary_frame = ttk.Frame(dialog, padding="10")
        summary_frame.pack(fill=tk.X)

        history = self.load_history()
        total_bookings = sum(r.get("bookings_made", 0) for r in history.get("runs", []))
        total_runs = len(history.get("runs", []))

        ttk.Label(summary_frame, text=f"Total runs: {total_runs}  |  Total bookings made: {total_bookings}",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT)

        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)

    def _refresh_history_list(self, tree):
        """Refresh the history treeview."""
        # Clear existing items
        for item in tree.get_children():
            tree.delete(item)

        history = self.load_history()

        for entry in history.get("runs", []):
            try:
                timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
                date_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            except:
                date_str = entry.get("timestamp", "Unknown")

            bookings = entry.get("bookings_made", 0)
            events = entry.get("events_detected", 0)
            details = entry.get("details", "")[:50]  # Truncate long details

            # Color based on success
            tag = "success" if bookings > 0 else "normal"
            tree.insert("", tk.END, values=(date_str, bookings, events, details), tags=(tag,))

        tree.tag_configure("success", foreground="green")
        tree.tag_configure("normal", foreground="black")

    def _clear_history(self, tree):
        """Clear all booking history."""
        if messagebox.askyesno("Clear History", "Are you sure you want to clear all booking history?"):
            self.save_history({"runs": []})
            self._refresh_history_list(tree)
            self.log("Booking history cleared.", "info")

    def show_calendar_dialog(self):
        """Show calendar dialog for selecting booking days."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Booking Calendar")
        dialog.geometry("1100x750")
        dialog.transient(self.root)
        dialog.grab_set()

        # Store dialog reference for updates
        self.calendar_dialog = dialog

        # Calendar state
        self.calendar_view = tk.StringVar(value="month")  # month, fortnight, week, 3days
        self.calendar_start_date = datetime.now().date()
        self.calendar_events = {}  # {date_str: [events]}

        # Header frame
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Booking Calendar",
            font=("Segoe UI", 16, "bold")
        ).pack(side=tk.LEFT)

        # View selector
        view_frame = ttk.Frame(header_frame)
        view_frame.pack(side=tk.RIGHT)

        ttk.Label(view_frame, text="View:", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(0, 10))

        views = [("Month", "month"), ("Fortnight", "fortnight"), ("Week", "week"), ("3 Days", "3days")]
        for text, value in views:
            rb = ttk.Radiobutton(
                view_frame,
                text=text,
                variable=self.calendar_view,
                value=value,
                command=self._refresh_calendar
            )
            rb.pack(side=tk.LEFT, padx=5)

        # Navigation frame
        nav_frame = ttk.Frame(dialog, padding="10")
        nav_frame.pack(fill=tk.X)

        ttk.Button(
            nav_frame,
            text="◀ Previous",
            command=lambda: self._navigate_calendar(-1),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            nav_frame,
            text="Today",
            command=self._go_to_today,
            width=10
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            nav_frame,
            text="Next ▶",
            command=lambda: self._navigate_calendar(1),
            width=12
        ).pack(side=tk.LEFT, padx=5)

        # Current period label
        self.calendar_period_var = tk.StringVar(value="")
        ttk.Label(
            nav_frame,
            textvariable=self.calendar_period_var,
            font=("Segoe UI", 14, "bold")
        ).pack(side=tk.LEFT, padx=30)

        # Selection buttons
        ttk.Button(
            nav_frame,
            text="Select All Visible",
            command=lambda: self._select_visible_days(True),
            width=16
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            nav_frame,
            text="Deselect All Visible",
            command=lambda: self._select_visible_days(False),
            width=18
        ).pack(side=tk.RIGHT, padx=5)

        # Scan status
        self.calendar_scan_var = tk.StringVar(value="Scanning events...")
        ttk.Label(
            nav_frame,
            textvariable=self.calendar_scan_var,
            foreground="blue"
        ).pack(side=tk.RIGHT, padx=20)

        # Legend
        legend_frame = ttk.Frame(dialog, padding="5")
        legend_frame.pack(fill=tk.X)

        ttk.Label(legend_frame, text="Legend:", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(10, 15))

        # Blue = selected
        legend_selected = tk.Frame(legend_frame, bg="#4a90d9", width=20, height=20)
        legend_selected.pack(side=tk.LEFT, padx=(0, 5))
        legend_selected.pack_propagate(False)
        ttk.Label(legend_frame, text="= Selected for booking", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 20))

        # White = deselected
        legend_deselected = tk.Frame(legend_frame, bg="white", width=20, height=20, highlightbackground="gray", highlightthickness=1)
        legend_deselected.pack(side=tk.LEFT, padx=(0, 5))
        legend_deselected.pack_propagate(False)
        ttk.Label(legend_frame, text="= Not selected", font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 20))

        # Event indicator
        ttk.Label(legend_frame, text="•", font=("Segoe UI", 14), foreground="#e67e22").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(legend_frame, text="= Has events", font=("Segoe UI", 10)).pack(side=tk.LEFT)

        # Calendar container with scrollbar
        calendar_container = ttk.Frame(dialog, padding="10")
        calendar_container.pack(fill=tk.BOTH, expand=True)

        # Canvas for calendar grid
        self.calendar_canvas = tk.Canvas(calendar_container, bg="white", highlightthickness=1, highlightbackground="gray")
        calendar_scrollbar_y = ttk.Scrollbar(calendar_container, orient="vertical", command=self.calendar_canvas.yview)
        calendar_scrollbar_x = ttk.Scrollbar(calendar_container, orient="horizontal", command=self.calendar_canvas.xview)

        self.calendar_canvas.configure(yscrollcommand=calendar_scrollbar_y.set, xscrollcommand=calendar_scrollbar_x.set)

        calendar_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        calendar_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.calendar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Frame inside canvas for calendar content - use tk.Frame for bg color support
        self.calendar_frame = tk.Frame(self.calendar_canvas, bg="white")
        self.calendar_canvas_window = self.calendar_canvas.create_window((0, 0), window=self.calendar_frame, anchor="nw")

        # Update scroll region when frame changes
        def on_frame_configure(e):
            self.calendar_canvas.configure(scrollregion=self.calendar_canvas.bbox("all"))
        self.calendar_frame.bind("<Configure>", on_frame_configure)

        # Resize calendar frame to match canvas size
        def on_canvas_configure(e):
            # Make the frame fill the canvas
            canvas_width = e.width
            canvas_height = e.height
            self.calendar_canvas.itemconfig(self.calendar_canvas_window, width=canvas_width, height=canvas_height)
            # Store canvas height for cell sizing and refresh
            if hasattr(self, '_last_canvas_height') and self._last_canvas_height != canvas_height:
                self._last_canvas_height = canvas_height
                self._refresh_calendar()
            else:
                self._last_canvas_height = canvas_height
        self.calendar_canvas.bind("<Configure>", on_canvas_configure)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            self.calendar_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.calendar_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Bottom buttons
        bottom_frame = ttk.Frame(dialog, padding="10")
        bottom_frame.pack(fill=tk.X)

        ttk.Button(
            bottom_frame,
            text="Save & Close",
            command=lambda: self._save_calendar_and_close(dialog),
            width=15
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            bottom_frame,
            text="Cancel",
            command=dialog.destroy,
            width=10
        ).pack(side=tk.RIGHT, padx=5)

        # Cleanup on close
        def on_close():
            self.calendar_canvas.unbind_all("<MouseWheel>")
            dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW", on_close)

        # Load cached events first (instant display)
        self._load_cached_events()

        # Initial render with cached data
        self._refresh_calendar()

        # Scan events in background (will update when complete)
        self._scan_calendar_events()

    def _load_cached_events(self):
        """Load cached events from settings for instant display."""
        settings = self.load_settings()
        cached_events = settings.get("cached_events", [])
        cache_timestamp = settings.get("cached_events_timestamp")

        if cached_events:
            # Group events by date
            events_by_date = {}
            for event in cached_events:
                date = event.get('date')
                if date:
                    if date not in events_by_date:
                        events_by_date[date] = []
                    events_by_date[date].append(event)

            self.calendar_events = events_by_date
            total_events = sum(len(events) for events in events_by_date.values())

            # Format cache age for display
            cache_info = ""
            if cache_timestamp:
                try:
                    cache_time = datetime.fromisoformat(cache_timestamp)
                    age = datetime.now() - cache_time
                    if age.total_seconds() < 60:
                        cache_info = " (from just now)"
                    elif age.total_seconds() < 3600:
                        mins = int(age.total_seconds() / 60)
                        cache_info = f" (from {mins}m ago)"
                    elif age.total_seconds() < 86400:
                        hours = int(age.total_seconds() / 3600)
                        cache_info = f" (from {hours}h ago)"
                    else:
                        days = int(age.total_seconds() / 86400)
                        cache_info = f" (from {days}d ago)"
                except:
                    pass

            self.calendar_scan_var.set(f"Showing {total_events} cached events{cache_info} - updating...")
        else:
            self.calendar_events = {}
            self.calendar_scan_var.set("No cached events. Scanning...")

    def _navigate_calendar(self, direction):
        """Navigate calendar forward or backward."""
        view = self.calendar_view.get()
        if view == "month":
            # Move by ~30 days
            self.calendar_start_date += timedelta(days=30 * direction)
            # Snap to first of month
            self.calendar_start_date = self.calendar_start_date.replace(day=1)
        elif view == "fortnight":
            self.calendar_start_date += timedelta(days=14 * direction)
        elif view == "week":
            self.calendar_start_date += timedelta(days=7 * direction)
        else:  # 3days
            self.calendar_start_date += timedelta(days=3 * direction)
        self._refresh_calendar()

    def _go_to_today(self):
        """Go to today's date."""
        self.calendar_start_date = datetime.now().date()
        if self.calendar_view.get() == "month":
            self.calendar_start_date = self.calendar_start_date.replace(day=1)
        self._refresh_calendar()

    def _select_visible_days(self, select: bool):
        """Select or deselect all visible days."""
        view = self.calendar_view.get()
        today = datetime.now().date()

        if view == "month":
            # Get all days in the month
            year = self.calendar_start_date.year
            month = self.calendar_start_date.month
            _, num_days = cal.monthrange(year, month)
            dates = [datetime(year, month, d).date() for d in range(1, num_days + 1)]
        elif view == "fortnight":
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(14)]
        elif view == "week":
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(7)]
        else:  # 3days
            dates = [self.calendar_start_date + timedelta(days=i) for i in range(3)]

        for d in dates:
            date_str = d.strftime('%Y-%m-%d')
            if date_str in self.day_vars:
                self.day_vars[date_str].set(select)

        self._refresh_calendar()

    def _refresh_calendar(self):
        """Refresh the calendar display."""
        # Clear existing content
        for widget in self.calendar_frame.winfo_children():
            widget.destroy()

        view = self.calendar_view.get()
        today = datetime.now().date()

        # Get available height for calendar content
        self.calendar_canvas.update_idletasks()
        available_height = getattr(self, '_last_canvas_height', None)
        if not available_height or available_height < 100:
            available_height = self.calendar_canvas.winfo_height()
        if available_height < 100:
            available_height = 450  # Default fallback

        # Set the frame to fill canvas and not shrink
        self.calendar_frame.grid_propagate(False)

        if view == "month":
            self._render_month_view(today, available_height)
        elif view == "fortnight":
            self._render_days_view(14, today, available_height)
        elif view == "week":
            self._render_days_view(7, today, available_height)
        else:  # 3days
            self._render_days_view(3, today, available_height)

    def _render_month_view(self, today, available_height):
        """Render month view calendar."""
        start_date = self.calendar_start_date
        year = start_date.year
        month = start_date.month

        # Update period label
        month_name = cal.month_name[month]
        self.calendar_period_var.set(f"{month_name} {year}")

        # Day headers
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        header_height = 30
        for col, day in enumerate(days):
            lbl = tk.Label(
                self.calendar_frame,
                text=day,
                font=("Segoe UI", 12, "bold"),
                width=14,
                bg="#f0f0f0",
                relief="solid",
                borderwidth=1
            )
            lbl.grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

        # Get first day of month and number of days
        first_day_weekday = cal.monthrange(year, month)[0]  # 0=Monday
        num_days = cal.monthrange(year, month)[1]

        # Calculate number of rows needed
        total_cells = first_day_weekday + num_days
        num_rows = (total_cells + 6) // 7  # Ceiling division

        # Calculate cell height based on available space
        cell_height = max(80, (available_height - header_height - 20) // num_rows)

        # Render days
        row = 1
        col = first_day_weekday

        for day in range(1, num_days + 1):
            current_date = datetime(year, month, day).date()
            date_str = current_date.strftime('%Y-%m-%d')

            self._render_day_cell(row, col, current_date, date_str, today, cell_height=cell_height)

            col += 1
            if col > 6:
                col = 0
                row += 1

        # Configure grid weights and row heights
        self.calendar_frame.rowconfigure(0, weight=0, minsize=header_height)  # Header row fixed
        for c in range(7):
            self.calendar_frame.columnconfigure(c, weight=1)
        for r in range(1, num_rows + 1):
            self.calendar_frame.rowconfigure(r, weight=1, minsize=cell_height)

    def _render_days_view(self, num_days, today, available_height):
        """Render days view (week, fortnight, 3days)."""
        start_date = self.calendar_start_date
        end_date = start_date + timedelta(days=num_days - 1)

        # Update period label
        start_str = start_date.strftime("%d %b")
        end_str = end_date.strftime("%d %b %Y")
        self.calendar_period_var.set(f"{start_str} - {end_str}")

        # Determine columns (max 7 per row for readability)
        cols_per_row = min(num_days, 7)
        rows_needed = (num_days + cols_per_row - 1) // cols_per_row

        # Calculate cell height based on available space
        cell_height = max(100, (available_height - 20) // rows_needed)

        day_index = 0
        for row in range(rows_needed):
            for col in range(cols_per_row):
                if day_index >= num_days:
                    break

                current_date = start_date + timedelta(days=day_index)
                date_str = current_date.strftime('%Y-%m-%d')

                self._render_day_cell(row, col, current_date, date_str, today, show_day_name=True, cell_height=cell_height)

                day_index += 1

        # Configure grid weights and row heights
        for c in range(cols_per_row):
            self.calendar_frame.columnconfigure(c, weight=1)
        for r in range(rows_needed):
            self.calendar_frame.rowconfigure(r, weight=1, minsize=cell_height)

    def _render_day_cell(self, row, col, current_date, date_str, today, show_day_name=False, cell_height=100):
        """Render a single day cell."""
        # Check if selected
        is_selected = date_str in self.day_vars and self.day_vars[date_str].get()
        is_today = current_date == today
        is_past = current_date < today

        # Get events for this day
        events = self.calendar_events.get(date_str, [])

        # Background color
        if is_selected:
            bg_color = "#4a90d9"  # Blue for selected
            fg_color = "white"
        else:
            bg_color = "white"
            fg_color = "black"

        if is_past:
            bg_color = "#e0e0e0"  # Gray for past days
            fg_color = "#888888"

        # Create cell frame - use sticky="nsew" to fill grid cell
        cell = tk.Frame(
            self.calendar_frame,
            bg=bg_color,
            relief="solid",
            borderwidth=1,
            cursor="hand2" if not is_past else ""
        )
        cell.grid(row=row, column=col, sticky="nsew", padx=1, pady=1)

        # Day number and name
        day_num = current_date.day
        header_text = str(day_num)
        if show_day_name:
            header_text = f"{current_date.strftime('%a')} {day_num}"

        if is_today:
            header_text += " (Today)"

        day_label = tk.Label(
            cell,
            text=header_text,
            font=("Segoe UI", 12, "bold" if is_today else "normal"),
            bg=bg_color,
            fg=fg_color,
            anchor="w"
        )
        day_label.pack(fill=tk.X, padx=5, pady=(5, 2))

        # Calculate how many events can fit based on cell height
        # Header takes ~25px, each event line ~18px, "+more" line ~16px
        header_space = 30
        event_line_height = 18
        available_for_events = cell_height - header_space
        max_events_to_show = max(1, (available_for_events - 16) // event_line_height)

        # Events display
        if events:
            events_frame = tk.Frame(cell, bg=bg_color)
            events_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

            events_to_display = min(len(events), max_events_to_show)
            for event in events[:events_to_display]:
                time_str = f"{event['startTime']}-{event['endTime']}"
                title = event.get('title', 'Event')
                if len(title) > 15:
                    title = title[:14] + "…"

                is_reservation = event.get('isReservation', False)
                # Use lighter colors on blue background for better contrast
                if is_selected:
                    event_color = "#ffcc80" if not is_reservation else "#a5d6a7"  # Light orange/green on blue
                else:
                    event_color = "#e67e22" if not is_reservation else "#27ae60"  # Standard orange/green

                event_lbl = tk.Label(
                    events_frame,
                    text=f"• {time_str} {title}",
                    font=("Segoe UI", 9),
                    bg=bg_color,
                    fg=event_color,
                    anchor="w"
                )
                event_lbl.pack(fill=tk.X)

            if len(events) > events_to_display:
                more_lbl = tk.Label(
                    events_frame,
                    text=f"  +{len(events) - events_to_display} more",
                    font=("Segoe UI", 8, "italic"),
                    bg=bg_color,
                    fg=fg_color,
                    anchor="w"
                )
                more_lbl.pack(fill=tk.X)

        # Click handler for day selection (only for future days)
        if not is_past:
            def toggle_day(ds=date_str, c=cell):
                if ds in self.day_vars:
                    current = self.day_vars[ds].get()
                    self.day_vars[ds].set(not current)
                    self._refresh_calendar()

            cell.bind("<Button-1>", lambda e: toggle_day())
            day_label.bind("<Button-1>", lambda e: toggle_day())
            for child in cell.winfo_children():
                child.bind("<Button-1>", lambda e, ds=date_str: toggle_day())

    def _scan_calendar_events(self):
        """Scan events for calendar display in background."""
        self.calendar_scan_var.set("Scanning events...")

        thread = threading.Thread(target=self._scan_calendar_events_thread)
        thread.daemon = True
        thread.start()

    def _scan_calendar_events_thread(self):
        """Background thread to scan events for calendar."""
        try:
            from playwright.sync_api import sync_playwright

            today = datetime.now().date()

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(storage_state=str(STATE_FILE)) if STATE_FILE.exists() else browser.new_context()
                page = context.new_page()

                page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
                page.wait_for_timeout(3000)

                # Scroll to load events
                target_date = today + timedelta(days=45)
                target_date_str = target_date.strftime("%d %B %Y").lstrip("0")

                max_scrolls = 60
                scroll_count = 0
                last_height = 0
                stalled_count = 0

                while scroll_count < max_scrolls:
                    page_text = page.evaluate("() => document.body.innerText")
                    if target_date_str in page_text:
                        break

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(800)

                    new_height = page.evaluate("() => document.body.scrollHeight")
                    if new_height == last_height:
                        stalled_count += 1
                        if stalled_count >= 3:
                            break
                    else:
                        stalled_count = 0
                    last_height = new_height
                    scroll_count += 1

                # Final pass
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
                for _ in range(max(scroll_count + 5, 15)):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(300)

                # Extract events (same JS as before)
                events_data = page.evaluate("""() => {
                    const events = [];
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);

                    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                      'July', 'August', 'September', 'October', 'November', 'December'];

                    function isCancelled(element) {
                        let el = element;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.textDecoration.includes('line-through') ||
                                style.textDecorationLine.includes('line-through')) return true;
                            el = el.parentElement;
                        }
                        return false;
                    }

                    function getEventTitle(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel')) {
                                const displayName = container.querySelector('[data-cy="event-display-name"]');
                                if (displayName) {
                                    const nameText = (displayName.textContent || '').trim();
                                    return nameText === 'Reservation' ? 'Reservation' : nameText;
                                }
                                return 'Event';
                            }
                            container = container.parentElement;
                        }
                        return 'Event';
                    }

                    function getEventRoom(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel')) {
                                const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                                if (locationLink) {
                                    const roomMatch = (locationLink.textContent || '').match(/B[01]\\.[0-9]{2}/);
                                    if (roomMatch) return roomMatch[0];
                                }
                                return null;
                            }
                            container = container.parentElement;
                        }
                        return null;
                    }

                    const allElements = document.querySelectorAll('*');
                    let currentDate = null;

                    for (const element of allElements) {
                        if (element.children.length > 0) {
                            const hasTextChild = Array.from(element.children).some(child =>
                                child.textContent.trim().length > 0);
                            if (hasTextChild) continue;
                        }

                        const text = element.textContent.trim();

                        const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                        if (dateMatch) {
                            const day = parseInt(dateMatch[2]);
                            const month = monthNames.indexOf(dateMatch[3]);
                            const year = parseInt(dateMatch[4]);
                            currentDate = new Date(year, month, day);
                            continue;
                        }

                        const timeMatch = text.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
                        if (timeMatch && currentDate) {
                            if (isCancelled(element)) continue;

                            const daysDiff = Math.floor((currentDate - today) / (1000 * 60 * 60 * 24));
                            if (daysDiff >= 0 && daysDiff <= 60) {
                                events.push({
                                    date: currentDate.toISOString().split('T')[0],
                                    startTime: timeMatch[1],
                                    endTime: timeMatch[2],
                                    title: getEventTitle(element),
                                    isReservation: getEventTitle(element) === 'Reservation',
                                    room: getEventRoom(element)
                                });
                            }
                        }
                    }
                    return events;
                }""")

                browser.close()

            # Group events by date
            events_by_date = {}
            for event in events_data:
                date = event['date']
                if date not in events_by_date:
                    events_by_date[date] = []
                events_by_date[date].append(event)

            self.calendar_events = events_by_date

            # Also update cached_events in settings
            settings = self.load_settings()
            seen = set()
            unique_events = []
            for event in events_data:
                key = f"{event['date']}_{event['startTime']}_{event['endTime']}"
                if key not in seen:
                    seen.add(key)
                    unique_events.append(event)
            settings["cached_events"] = unique_events
            settings["cached_events_timestamp"] = datetime.now().isoformat()
            self.save_settings(settings)

            self.root.after(0, self._on_calendar_scan_complete)

        except Exception as e:
            self.root.after(0, lambda: self._on_calendar_scan_error(str(e)))

    def _on_calendar_scan_complete(self):
        """Called when calendar event scan completes."""
        total_events = sum(len(events) for events in self.calendar_events.values())
        self.calendar_scan_var.set(f"✓ {total_events} events (updated just now)")
        self._refresh_calendar()

    def _on_calendar_scan_error(self, error_msg):
        """Called when calendar event scan fails."""
        self.calendar_scan_var.set(f"⚠ Scan failed: {error_msg[:30]}")
        self.log(f"Calendar scan error: {error_msg}", "error")

    def _save_calendar_and_close(self, dialog):
        """Save calendar selections and close."""
        self.save_booking_days()
        self.calendar_canvas.unbind_all("<MouseWheel>")
        dialog.destroy()
        self.log("Calendar settings saved", "info")

    def show_events_dialog(self):
        """Show dialog to manage events (ignore/include for booking conflicts)."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage Events")
        dialog.geometry("900x650")
        dialog.transient(self.root)

        # Header
        header_frame = ttk.Frame(dialog, padding="10")
        header_frame.pack(fill=tk.X)

        ttk.Label(
            header_frame,
            text="Manage Events",
            font=("Segoe UI", 14, "bold")
        ).pack(side=tk.LEFT)

        ttk.Label(
            header_frame,
            text="Uncheck events to ignore them (allow booking over them)",
            font=("Segoe UI", 10),
            foreground="gray"
        ).pack(side=tk.LEFT, padx=(20, 0))

        # Button frame
        btn_frame = ttk.Frame(dialog, padding="10")
        btn_frame.pack(fill=tk.X)

        self.scan_btn = ttk.Button(
            btn_frame,
            text="🔄 Scan My Agenda",
            command=lambda: self._scan_events(dialog, events_frame)
        )
        self.scan_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Select All",
            command=lambda: self._select_all_events(True)
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="Deselect All",
            command=lambda: self._select_all_events(False)
        ).pack(side=tk.LEFT, padx=5)

        # Progress label
        self.events_progress_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=self.events_progress_var, foreground="blue").pack(side=tk.LEFT, padx=20)

        # Scrollable frame for events
        container = ttk.Frame(dialog, padding="10")
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        events_frame = ttk.Frame(canvas)

        events_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=events_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Store reference to events frame and checkboxes
        self.events_frame = events_frame
        self.event_vars = {}  # {event_key: BooleanVar}

        # Load existing ignored events and display them
        self._load_and_display_events(events_frame)

        # Bottom buttons
        bottom_frame = ttk.Frame(dialog, padding="10")
        bottom_frame.pack(fill=tk.X)

        ttk.Button(
            bottom_frame,
            text="Save",
            command=lambda: self._save_events_and_close(dialog)
        ).pack(side=tk.RIGHT, padx=5)

        ttk.Button(
            bottom_frame,
            text="Cancel",
            command=dialog.destroy
        ).pack(side=tk.RIGHT, padx=5)

        # Cleanup mousewheel binding when dialog closes
        def on_close():
            canvas.unbind_all("<MouseWheel>")
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

    def _load_and_display_events(self, events_frame):
        """Load cached events and ignored events from settings, display in frame."""
        settings = self.load_settings()
        cached_events = settings.get("cached_events", [])
        ignored_events = set(settings.get("ignored_events", []))

        # Clear existing widgets
        for widget in events_frame.winfo_children():
            widget.destroy()
        self.event_vars = {}

        if not cached_events:
            ttk.Label(
                events_frame,
                text="No events cached. Click 'Scan My Agenda' to load events.",
                font=("Segoe UI", 11),
                foreground="gray"
            ).pack(pady=20)
            return

        # Group events by date
        events_by_date = {}
        for event in cached_events:
            date = event.get("date", "Unknown")
            if date not in events_by_date:
                events_by_date[date] = []
            events_by_date[date].append(event)

        # Display events grouped by date
        for date in sorted(events_by_date.keys()):
            # Date header
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                day_name = date_obj.strftime("%A")
                date_display = f"{day_name}, {date_obj.strftime('%d %B %Y')}"
            except:
                date_display = date

            date_frame = ttk.LabelFrame(events_frame, text=date_display, padding="10")
            date_frame.pack(fill=tk.X, pady=5, padx=5)

            for event in events_by_date[date]:
                event_key = f"{event['date']}_{event['startTime']}_{event['endTime']}"
                is_reservation = event.get("isReservation", False)
                room = event.get("room", "")
                title = event.get("title", "Event")

                # Create checkbox - default checked unless in ignored list
                var = tk.BooleanVar(value=(event_key not in ignored_events))
                self.event_vars[event_key] = var

                # Format display text
                time_str = f"{event['startTime']} - {event['endTime']}"
                if is_reservation:
                    display_text = f"[Reservation] {time_str}"
                    if room:
                        display_text += f" in {room}"
                else:
                    display_text = f"{time_str} - {title}"
                    if room:
                        display_text += f" in {room}"

                cb = ttk.Checkbutton(
                    date_frame,
                    text=display_text,
                    variable=var
                )
                cb.pack(anchor=tk.W, pady=2)

    def _select_all_events(self, select: bool):
        """Select or deselect all event checkboxes."""
        for var in self.event_vars.values():
            var.set(select)

    def _scan_events(self, dialog, events_frame):
        """Scan the agenda for events using Playwright."""
        self.scan_btn.config(state=tk.DISABLED)
        self.events_progress_var.set("Scanning agenda (this may take a moment)...")

        # Run scan in background thread
        thread = threading.Thread(target=self._scan_events_thread, args=(dialog, events_frame))
        thread.daemon = True
        thread.start()

    def _scan_events_thread(self, dialog, events_frame):
        """Background thread to scan events."""
        try:
            # Import here to avoid startup delay
            from playwright.sync_api import sync_playwright

            events = []
            today = datetime.now().date()

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(storage_state=str(STATE_FILE)) if STATE_FILE.exists() else browser.new_context()
                page = context.new_page()

                # Navigate to agenda
                self.root.after(0, lambda: self.events_progress_var.set("Loading agenda page..."))
                page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
                page.wait_for_timeout(3000)

                # Scroll to load all events for the next 7 days
                target_date = today + timedelta(days=7)
                target_date_str = target_date.strftime("%d %B %Y").lstrip("0")

                self.root.after(0, lambda: self.events_progress_var.set("Scrolling to load events..."))

                max_scrolls = 50
                scroll_count = 0
                last_height = 0
                stalled_count = 0

                while scroll_count < max_scrolls:
                    page_text = page.evaluate("() => document.body.innerText")
                    if target_date_str in page_text:
                        break

                    alt_date_str = target_date.strftime("%d %B %Y")
                    if alt_date_str in page_text:
                        break

                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1000)

                    new_height = page.evaluate("() => document.body.scrollHeight")
                    if new_height == last_height:
                        stalled_count += 1
                        if stalled_count >= 3:
                            break
                        page.wait_for_timeout(500)
                    else:
                        stalled_count = 0
                    last_height = new_height
                    scroll_count += 1

                # Scroll back to top and do final pass
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)

                min_scrolls = max(scroll_count + 5, 10)
                for i in range(min_scrolls):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(400)

                page.wait_for_timeout(1000)

                self.root.after(0, lambda: self.events_progress_var.set("Extracting events..."))

                # Extract events using same JavaScript as book_week.py
                events_data = page.evaluate("""() => {
                    const events = [];
                    const today = new Date();
                    today.setHours(0, 0, 0, 0);

                    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                      'July', 'August', 'September', 'October', 'November', 'December'];

                    function isCancelled(element) {
                        let el = element;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.textDecoration.includes('line-through') ||
                                style.textDecorationLine.includes('line-through')) {
                                return true;
                            }
                            const className = el.className || '';
                            if (className.includes('cancelled') || className.includes('canceled') ||
                                className.includes('deleted') || className.includes('removed')) {
                                return true;
                            }
                            el = el.parentElement;
                        }
                        return false;
                    }

                    function getEventTitle(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel') ||
                                container.classList.contains('event-details-expanded')) {

                                const displayName = container.querySelector('[data-cy="event-display-name"]');
                                if (displayName) {
                                    const nameText = (displayName.textContent || '').trim();
                                    if (nameText === 'Reservation' || nameText.includes('Reservation')) {
                                        return 'Reservation';
                                    }
                                    return nameText;
                                }
                                return 'Event';
                            }
                            container = container.parentElement;
                        }
                        return 'Event';
                    }

                    function getEventRoom(element) {
                        let container = element;
                        for (let i = 0; i < 10 && container; i++) {
                            if (container.tagName === 'APP-AS-EVENT' ||
                                container.classList.contains('as-event-panel') ||
                                container.classList.contains('event-details-expanded')) {

                                const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                                if (locationLink) {
                                    const locText = (locationLink.textContent || '').trim();
                                    const roomMatch = locText.match(/B[01]\\.[0-9]{2}/);
                                    if (roomMatch) {
                                        return roomMatch[0];
                                    }
                                }
                                return null;
                            }
                            container = container.parentElement;
                        }
                        return null;
                    }

                    const allElements = document.querySelectorAll('*');
                    let currentDate = null;

                    for (const element of allElements) {
                        if (element.children.length > 0) {
                            const hasTextChild = Array.from(element.children).some(child =>
                                child.textContent.trim().length > 0
                            );
                            if (hasTextChild) continue;
                        }

                        const text = element.textContent.trim();

                        const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                        if (dateMatch) {
                            const day = parseInt(dateMatch[2]);
                            const month = monthNames.indexOf(dateMatch[3]);
                            const year = parseInt(dateMatch[4]);
                            currentDate = new Date(year, month, day);
                            continue;
                        }

                        const timeMatch = text.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
                        if (timeMatch && currentDate) {
                            if (isCancelled(element)) {
                                continue;
                            }

                            const startTime = timeMatch[1];
                            const endTime = timeMatch[2];
                            const eventTitle = getEventTitle(element);
                            const eventRoom = getEventRoom(element);

                            const daysDiff = Math.floor((currentDate - today) / (1000 * 60 * 60 * 24));
                            if (daysDiff >= 0 && daysDiff <= 7) {
                                events.push({
                                    date: currentDate.toISOString().split('T')[0],
                                    daysDiff: daysDiff,
                                    startTime: startTime,
                                    endTime: endTime,
                                    title: eventTitle,
                                    isReservation: eventTitle === 'Reservation',
                                    room: eventRoom
                                });
                            }
                        }
                    }

                    return events;
                }""")

                browser.close()

            # Deduplicate events
            seen = set()
            unique_events = []
            for event in events_data:
                key = f"{event['date']}_{event['startTime']}_{event['endTime']}"
                if key not in seen:
                    seen.add(key)
                    unique_events.append(event)

            # Save to settings
            settings = self.load_settings()
            settings["cached_events"] = unique_events
            self.save_settings(settings)

            # Update UI
            self.root.after(0, lambda: self._on_scan_complete(events_frame, len(unique_events)))

        except Exception as e:
            self.root.after(0, lambda: self._on_scan_error(str(e)))

    def _on_scan_complete(self, events_frame, count):
        """Called when event scan completes."""
        self.scan_btn.config(state=tk.NORMAL)
        self.events_progress_var.set(f"Found {count} events")
        self._load_and_display_events(events_frame)
        self.log(f"Event scan complete: found {count} events", "info")

    def _on_scan_error(self, error_msg):
        """Called when event scan fails."""
        self.scan_btn.config(state=tk.NORMAL)
        self.events_progress_var.set(f"Error: {error_msg[:50]}")
        self.log(f"Event scan error: {error_msg}", "error")
        messagebox.showerror("Scan Error", f"Failed to scan events:\n{error_msg}")

    def _save_events_and_close(self, dialog):
        """Save ignored events to settings and close dialog."""
        ignored_events = []
        for event_key, var in self.event_vars.items():
            if not var.get():  # Unchecked = ignored
                ignored_events.append(event_key)

        settings = self.load_settings()
        settings["ignored_events"] = ignored_events
        self.save_settings(settings)

        self.log(f"Saved {len(ignored_events)} ignored events", "info")
        dialog.destroy()


def main():
    root = tk.Tk()

    # Set DPI awareness for Windows
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass

    app = AsimutBookerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
