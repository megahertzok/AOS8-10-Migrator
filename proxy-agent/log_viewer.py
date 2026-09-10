"""Standalone log viewer window for the proxy agent -- shows proxy_agent.log live,
color-coded by level (success/info/warning/error).

Runs as its own process (launched from the tray icon's "View Logs" menu item, or
directly with `python3 log_viewer.py`) rather than a thread in the main proxy agent
process: on macOS, both pystray and Tkinter want the main thread for their own event
loop, so they can't share one process without conflict. A separate process sidesteps
that entirely.
"""

import re
import tkinter as tk
from tkinter import font as tkfont

from debug_log import LOG_PATH

LINE_RE = re.compile(r"^(\S+ \S+) \[(\w+)\] \[([^\]]+)\] (.*)$")

COLORS = {
    "bg": "#1d1f27",
    "bg_bar": "#292d3a",
    "border": "#3a3f50",
    "text": "#e8eaed",
    "text_weak": "#97a1b8",
    "success": "#05cc93",
    "warning": "#ec8c25",
    "error": "#fc6161",
    "info": "#97a1b8",
}


class LogViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("AOS8 → AOS10 Migrator — Live Log")
        self.root.geometry("900x520")
        self.root.configure(bg=COLORS["bg"])

        mono = tkfont.nametofont("TkFixedFont").actual("family")

        bar = tk.Frame(root, bg=COLORS["bg_bar"], height=36)
        bar.pack(fill="x", side="top")
        tk.Label(
            bar, text=f"Log file: {LOG_PATH}", bg=COLORS["bg_bar"], fg=COLORS["text_weak"],
            font=(mono, 10), anchor="w", padx=10,
        ).pack(side="left", fill="y")
        self.autoscroll = tk.BooleanVar(value=True)
        tk.Checkbutton(
            bar, text="Auto-scroll", variable=self.autoscroll, bg=COLORS["bg_bar"], fg=COLORS["text"],
            selectcolor=COLORS["bg"], activebackground=COLORS["bg_bar"], activeforeground=COLORS["text"],
            font=(mono, 10), highlightthickness=0,
        ).pack(side="right", padx=10)

        text_frame = tk.Frame(root, bg=COLORS["bg"])
        text_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(
            text_frame, bg=COLORS["bg"], fg=COLORS["text"], insertbackground=COLORS["text"],
            font=(mono, 11), wrap="word", yscrollcommand=scrollbar.set, borderwidth=0, highlightthickness=0,
        )
        self.text.pack(fill="both", expand=True, padx=(10, 0), pady=6)
        scrollbar.config(command=self.text.yview)

        for level in ("success", "warning", "error", "info"):
            self.text.tag_configure(level, foreground=COLORS[level])
        self.text.tag_configure("success", font=(mono, 11, "bold"))
        self.text.tag_configure("error", font=(mono, 11, "bold"))
        self.text.tag_configure("category", foreground=COLORS["text_weak"])

        self._offset = 0
        self._load_existing()
        self.root.after(500, self._poll)

    def _append_line(self, line):
        line = line.rstrip("\n")
        if not line:
            return
        match = LINE_RE.match(line)
        was_at_bottom = self.text.yview()[1] >= 0.999
        if match:
            ts, level, category, message = match.groups()
            level = level.lower() if level.lower() in COLORS else "info"
            self.text.insert("end", f"{ts}  ", ("info",))
            self.text.insert("end", f"[{category}] ", ("category",))
            self.text.insert("end", f"{message}\n", (level,))
        else:
            self.text.insert("end", line + "\n", ("info",))
        if self.autoscroll.get() and was_at_bottom:
            self.text.see("end")

    def _load_existing(self):
        if not LOG_PATH.exists():
            self.text.insert(
                "end",
                "Waiting for the proxy agent to log something...\n"
                "(This file is created on first use -- it'll appear here automatically.)\n",
                ("info",),
            )
            return
        with open(LOG_PATH, errors="replace") as f:
            content = f.read()
            self._offset = f.tell()
        for line in content.splitlines():
            self._append_line(line)
        self.text.see("end")

    def _poll(self):
        if LOG_PATH.exists():
            with open(LOG_PATH, errors="replace") as f:
                f.seek(self._offset)
                new = f.read()
                self._offset = f.tell()
            if new:
                if self.text.get("1.0", "end").strip().startswith("Waiting for the proxy agent"):
                    self.text.delete("1.0", "end")
                for line in new.splitlines():
                    self._append_line(line)
        self.root.after(500, self._poll)


def main():
    root = tk.Tk()
    LogViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
