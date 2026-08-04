#!/usr/bin/env python3
"""Small Windows GUI for the GrimTools character save generator."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from generator import GenerationError, generate_save, validate_character_name
from grimtools import GrimToolsError, fetch_build
from save_format import CharacterSave, SaveFormatError


APP_TITLE = "Grim Dawn 存档生成器"


def resource_directory() -> Path:
    """Directory containing bundled read-only resources."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def writable_directory() -> Path:
    """Directory next to the executable, used for generated characters."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class SaveGeneratorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self.running = False

        root.title(APP_TITLE)
        root.minsize(620, 410)
        root.geometry("700x470")
        root.protocol("WM_DELETE_WINDOW", self._close)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text=APP_TITLE, font=("Microsoft YaHei UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 18)
        )

        ttk.Label(frame, text="模拟器链接：").grid(row=1, column=0, sticky=tk.W, pady=6)
        self.link_var = tk.StringVar(value="https://www.grimtools.com/calc/")
        self.link_entry = ttk.Entry(frame, textvariable=self.link_var)
        self.link_entry.grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=6)

        ttk.Label(frame, text="角色名称：").grid(row=2, column=0, sticky=tk.W, pady=6)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var, width=32)
        self.name_entry.grid(row=2, column=1, sticky=tk.EW, pady=6)
        self.name_entry.bind("<Return>", lambda _event: self.start_generation())

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(12, 12))
        self.generate_button = ttk.Button(
            button_frame, text="生成角色存档", command=self.start_generation
        )
        self.generate_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(
            button_frame,
            text="打开输出目录",
            command=self.open_output,
            state=tk.DISABLED,
        )
        self.open_button.pack(side=tk.LEFT, padx=(10, 0))
        self.progress = ttk.Progressbar(button_frame, mode="indeterminate", length=180)
        self.progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(20, 0))

        log_frame = ttk.LabelFrame(frame, text="生成日志", padding=8)
        log_frame.grid(row=4, column=0, columnspan=3, sticky=tk.NSEW)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame,
            height=13,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Microsoft YaHei UI", 9),
        )
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)

        ttk.Label(
            frame,
            text="生成结果保存在程序同目录的 output 文件夹中。",
            foreground="#555555",
        ).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))

        root.after(100, self._process_events)
        self.link_entry.selection_range(0, tk.END)
        self.link_entry.focus_set()

    def _append_log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text.rstrip() + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.generate_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.link_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.name_entry.configure(state=tk.DISABLED if running else tk.NORMAL)
        if running:
            self.open_button.configure(state=tk.DISABLED)
            self.progress.start(12)
        else:
            self.progress.stop()
            if self.last_output and self.last_output.is_dir():
                self.open_button.configure(state=tk.NORMAL)

    def start_generation(self) -> None:
        if self.running:
            return
        link = self.link_var.get().strip()
        try:
            name = validate_character_name(self.name_var.get())
        except GenerationError as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self.root)
            self.name_entry.focus_set()
            return
        if not link or link == "https://www.grimtools.com/calc/":
            messagebox.showwarning(APP_TITLE, "请输入完整的 GrimTools 构筑链接。", parent=self.root)
            self.link_entry.focus_set()
            return

        output_root = writable_directory() / "output"
        output_directory = output_root / f"_{name}"
        overwrite = False
        if output_directory.exists():
            overwrite = messagebox.askyesno(
                APP_TITLE,
                f"角色目录已经存在：\n{output_directory}\n\n是否覆盖？",
                parent=self.root,
            )
            if not overwrite:
                return

        self.last_output = None
        self._clear_log()
        self._set_running(True)
        worker = threading.Thread(
            target=self._generate,
            args=(link, name, output_root, overwrite),
            daemon=True,
        )
        worker.start()

    def _generate(
        self, link: str, name: str, output_root: Path, overwrite: bool
    ) -> None:
        try:
            self.events.put(("log", "[1/4] 正在读取 GrimTools 构筑……"))
            build = fetch_build(link)
            self.events.put(
                ("log", f"      构筑 ID: {build.build_id}；游戏版本: {build.game_version}")
            )
            self.events.put(("log", "[2/4] 正在复制模板并写入角色数据……"))
            result = generate_save(
                build,
                name,
                resource_directory() / "_template",
                output_root,
                overwrite=overwrite,
            )
            self.events.put(("log", "[3/4] 已完成解密后回读校验。"))
            self.events.put(("log", "[4/4] 角色存档生成成功："))
            self.events.put(("log", f"      角色名称: {result.character_name}"))
            self.events.put(("log", f"      职业标记: {result.class_tag}"))
            self.events.put(("log", f"      等级: {result.level}"))
            self.events.put(
                (
                    "log",
                    f"      装备: {result.equipment_count}；技能: {result.skill_count}；"
                    f"星座节点: {result.devotion_count}",
                )
            )
            self.events.put(("log", f"      输出目录: {result.output_directory}"))
            for warning in result.warnings:
                self.events.put(("log", f"[注意] {warning}"))
            self.events.put(("success", result.output_directory))
        except (GenerationError, GrimToolsError, SaveFormatError, OSError) as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # Keep the packaged GUI from exiting silently.
            self.events.put(("error", f"未预期错误：{exc}"))

    def _process_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "success":
                    self.last_output = Path(value)
                    self._set_running(False)
                    messagebox.showinfo(
                        APP_TITLE,
                        f"角色存档生成成功！\n\n{self.last_output}",
                        parent=self.root,
                    )
                elif kind == "error":
                    self._append_log(f"[失败] {value}")
                    self._set_running(False)
                    messagebox.showerror(APP_TITLE, str(value), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def open_output(self) -> None:
        if self.last_output and self.last_output.is_dir():
            os.startfile(self.last_output)  # type: ignore[attr-defined]

    def _close(self) -> None:
        if self.running and not messagebox.askyesno(
            APP_TITLE, "角色存档仍在生成，确定要退出吗？", parent=self.root
        ):
            return
        self.root.destroy()


def main() -> int:
    if "--self-test" in sys.argv:
        template = resource_directory() / "_template"
        player_file = template / "player.gdc"
        if not player_file.is_file():
            return 2
        CharacterSave.load(player_file)
        if sum(1 for path in template.rglob("*") if path.is_file()) < 1:
            return 3
        return 0

    root = tk.Tk()
    SaveGeneratorApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
