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
        self.root.geometry("1200x900")
        self.root.minsize(1000, 700)

        # Configure default font sizes
        default_font = ("Segoe UI", 11)
        heading_font = ("Segoe UI", 12, "bold")

        # Apply to all ttk widgets
        style = ttk.Style()
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font, padding=6)
        style.configure("TLabelframe.Label", font=heading_font)
        style.configure("TCheckbutton", font=default_font)

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

        # Progress indicator
        self.progress_var = tk.StringVar(value="")
        ttk.Label(controls_frame, textvariable=self.progress_var, foreground="blue", font=("Segoe UI", 11)).pack(anchor=tk.W, pady=(8, 0))

        # Booking Days section
        days_frame = ttk.LabelFrame(main_frame, text="Booking Days (today + next 7 days)", padding="15")
        days_frame.pack(fill=tk.X, pady=(0, 15))

        days_inner = ttk.Frame(days_frame)
        days_inner.pack(fill=tk.X)

        # Create checkboxes for each of the next 8 days (today through 7 days ahead)
        self.day_vars = {}  # {date_str: BooleanVar}
        self.day_checkboxes = {}

        today = datetime.now().date()
        for i in range(0, 8):  # Days 0-7 (today through 7 days ahead)
            target_date = today + timedelta(days=i)
            date_str = target_date.strftime('%Y-%m-%d')
            day_name = target_date.strftime('%A')
            # Mark today specially
            if i == 0:
                display_text = f"Today ({target_date.strftime('%d/%m')})"
            else:
                display_text = f"{day_name} {target_date.strftime('%d/%m')}"

            var = tk.BooleanVar(value=True)  # Default to enabled
            self.day_vars[date_str] = var

            cb = ttk.Checkbutton(
                days_inner,
                text=display_text,
                variable=var,
                command=self.save_booking_days
            )
            cb.pack(side=tk.LEFT, padx=10, pady=5)
            self.day_checkboxes[date_str] = cb

        # Load saved settings
        self.load_booking_days()

        # Bottom section - Output Log
        log_frame = ttk.LabelFrame(main_frame, text="Output Log", padding="15")
        log_frame.pack(fill=tk.BOTH, expand=True)

        # Log text area
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 12),
            height=25
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
        """Open Task Scheduler to view tasks."""
        try:
            subprocess.Popen(["taskschd.msc"], shell=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open Task Scheduler: {e}")

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
        disabled_dates = settings.get("disabled_dates", [])

        # Update checkboxes based on saved settings
        for date_str, var in self.day_vars.items():
            if date_str in disabled_dates:
                var.set(False)
            else:
                var.set(True)

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
