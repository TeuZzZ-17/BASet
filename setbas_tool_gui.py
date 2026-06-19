#!/usr/bin/env python3
"""Tkinter front-end for SET.BAS EMRS extraction and VBMP to PNG conversion."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional, Tuple

import setbas_extract
import vbmp_to_png
import vbmp_to_ilbm
import base_kids_export
import png_to_ilbm


TITLE = "BASet - Urban Assault SET.BAS Tool"
PILLOW_HELP = "Missing dependency: Pillow. Install it with:\npython -m pip install pillow"

COLOR_BG = "#15121f"
COLOR_PANEL = "#201a2d"
COLOR_ENTRY = "#120f19"
COLOR_TEXT = "#eee8ff"
COLOR_MUTED = "#b8abc9"
COLOR_ACCENT = "#8f5cff"
COLOR_ACCENT_DARK = "#5e35b1"
COLOR_LOG = "#0f0d16"


class LogSink(io.TextIOBase):
    def __init__(self, callback):
        self.callback = callback
        self.buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line:
                self.callback(line)
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.callback(self.buffer)
            self.buffer = ""


class SetBasToolGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(TITLE)
        self.root.minsize(860, 540)
        self.root.configure(bg=COLOR_BG)

        self.setbas_var = tk.StringVar()
        self.raw_out_var = tk.StringVar()
        self.export_leaf_chunks_var = tk.BooleanVar(value=False)

        self._setup_style()
        self._build_ui()

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL)
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_BG, foreground=COLOR_MUTED)
        style.configure("TEntry", fieldbackground=COLOR_ENTRY, foreground=COLOR_TEXT, insertcolor=COLOR_TEXT)
        style.map("TEntry", fieldbackground=[("disabled", COLOR_PANEL)])
        style.configure("TButton", background=COLOR_PANEL, foreground=COLOR_TEXT, padding=(8, 5), borderwidth=1)
        style.configure("TCheckbutton", background=COLOR_BG, foreground=COLOR_TEXT)
        style.map("TCheckbutton", background=[("active", COLOR_BG)], foreground=[("active", COLOR_TEXT)])
        style.map(
            "TButton",
            background=[("active", COLOR_ACCENT_DARK), ("pressed", COLOR_ACCENT)],
            foreground=[("active", COLOR_TEXT), ("pressed", COLOR_TEXT)],
        )
        style.configure("Accent.TButton", background=COLOR_ACCENT_DARK, foreground=COLOR_TEXT, padding=(8, 5))
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT), ("pressed", COLOR_ACCENT)])

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=10, style="TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)

        self._field(frame, 0, "SET.BAS input path", self.setbas_var, self.browse_setbas, "Browse File")
        self._field(frame, 1, "Extraction output folder", self.raw_out_var, self.browse_raw_out, "Browse Folder")

        options = ttk.Frame(frame)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(2, 4))
        ttk.Checkbutton(
            options,
            text="Developer: export BASE/KIDS leaf chunks (slow, many small files)",
            variable=self.export_leaf_chunks_var,
            command=self.on_leaf_chunks_toggle,
        ).pack(side="left")
        ttk.Button(options, text="Info", command=self.show_leaf_chunks_info).pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 6))

        ttk.Button(buttons, text="Extract", command=self.extract_all, style="Accent.TButton").pack(side="left", padx=(0, 5))
        ttk.Button(buttons, text="Texture ILBM Conversion", command=self.convert_extracted_ilbm, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(buttons, text="Texture PNG Conversion", command=self.convert_extracted, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(buttons, text="PNG to ILBM Conversion", command=self.convert_png_to_ilbm, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(buttons, text="Export Metadata", command=self.export_metadata).pack(side="left", padx=5)

        buttons2 = ttk.Frame(frame)
        buttons2.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Button(buttons2, text="Open Output Folder", command=lambda: self.open_folder(self.raw_out_var.get())).pack(side="left", padx=(0, 5))
        ttk.Button(buttons2, text="Clear Log", command=self.clear_log).pack(side="left", padx=5)

        self.log_text = scrolledtext.ScrolledText(frame, height=20, wrap="word", state="disabled")
        self.log_text.grid(row=3, column=0, columnspan=3, sticky="nsew")
        self.log_text.configure(
            background=COLOR_LOG,
            foreground=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT_DARK,
            selectforeground=COLOR_TEXT,
            relief="flat",
            borderwidth=1,
        )

    def _field(self, parent, row: int, label: str, var: tk.StringVar, command, button_text: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(parent, text=button_text, command=command).grid(row=row, column=2, pady=4)

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.root.update_idletasks()
        self.write_persistent_log(message)

    def write_persistent_log(self, message: str) -> None:
        raw_out_text = self.raw_out_var.get().strip()
        if not raw_out_text:
            return
        try:
            log_path = Path(raw_out_text) / "BASet_log.txt"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(message + "\n")
        except OSError:
            pass

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def show_error(self, message: str) -> None:
        self.log("[ERROR] " + message)
        messagebox.showerror(TITLE, message)

    def leaf_chunks_warning_text(self) -> str:
        return (
            "This developer option exports CLID/NAME/NAM2/STRC/ATTS/OLPL/OTL2 "
            "as thousands of tiny files inside raw\\BASE_KIDS\\chunks.\n\n"
            "It is useful for reverse engineering and SKLtron research, but it can make "
            "Extract much slower and Windows Explorer may become sluggish.\n\n"
            "Leave it disabled for normal BASet extraction."
        )

    def show_leaf_chunks_info(self) -> None:
        messagebox.showinfo(TITLE, self.leaf_chunks_warning_text())

    def on_leaf_chunks_toggle(self) -> None:
        if not self.export_leaf_chunks_var.get():
            self.log("[OPTIONS] BASE/KIDS leaf chunk dump disabled")
            return
        proceed = messagebox.askyesno(TITLE, self.leaf_chunks_warning_text() + "\n\nEnable it anyway?")
        if not proceed:
            self.export_leaf_chunks_var.set(False)
            self.log("[OPTIONS] BASE/KIDS leaf chunk dump left disabled")
            return
        self.log("[OPTIONS] BASE/KIDS leaf chunk dump enabled: Extract will be slower")

    def browse_setbas(self) -> None:
        path = filedialog.askopenfilename(title="Select SET.BAS", filetypes=[("SET.BAS", "*.BAS *.bas"), ("All files", "*.*")])
        if path:
            self.setbas_var.set(path)

    def browse_raw_out(self) -> None:
        path = filedialog.askdirectory(title="Select extraction output folder")
        if path:
            self.raw_out_var.set(path)

    def validate_extract_paths(self) -> Optional[Tuple[Path, Path]]:
        setbas_text = self.setbas_var.get().strip()
        raw_out_text = self.raw_out_var.get().strip()
        if not setbas_text:
            self.show_error("SET.BAS input path is required.")
            return None
        if not raw_out_text:
            self.show_error("Extraction output folder is required.")
            return None
        setbas = Path(setbas_text)
        raw_out = Path(raw_out_text)
        if not setbas.is_file():
            self.show_error(f"SET.BAS file not found: {setbas}")
            return None
        return setbas, raw_out

    def texture_vbmp_dir(self) -> Path:
        return Path(self.raw_out_var.get().strip()) / "raw" / "VBMP"

    def texture_ilbm_dir(self) -> Path:
        return Path(self.raw_out_var.get().strip()) / "textures_ilbm"

    def texture_png_dir(self) -> Path:
        return Path(self.raw_out_var.get().strip()) / "textures_png"

    def validate_convert_paths(self) -> Optional[Tuple[Path, Path]]:
        raw_out_text = self.raw_out_var.get().strip()
        if not raw_out_text:
            self.show_error("Extraction output folder is required.")
            return None
        input_dir = self.texture_vbmp_dir()
        png_out = self.texture_png_dir()

        if not input_dir.is_dir():
            self.show_error(f"Extracted VBMP folder not found: {input_dir}")
            return None
        return input_dir, png_out

    def run_extract(self, all_classes: bool) -> bool:
        paths = self.validate_extract_paths()
        if paths is None:
            return False
        setbas, raw_out = paths

        self.log("[EXTRACT] Starting")
        self.log(f"[EXTRACT] SET.BAS: {setbas}")
        self.log(f"[EXTRACT] Output: {raw_out}")
        if all_classes:
            self.log("[EXTRACT] Mode: all classes")
        else:
            self.log("[EXTRACT] Mode: ilbm.class")

        args = argparse.Namespace(
            setbas=str(setbas),
            out=str(raw_out),
            class_name="ilbm.class",
            all_classes=all_classes,
            manifest_json="manifest.json",
            manifest_csv="",
            dry_run=False,
            verbose=False,
        )

        sink = LogSink(lambda line: self.log("[EXTRACT] " + line))
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = setbas_extract.extract(args)
        sink.flush()

        if result == 0:
            self.remove_text_manifest(raw_out)
            export_leaf_chunks = self.export_leaf_chunks_var.get()
            self.log("[EXTRACT] BASE/KIDS raw export started")
            if export_leaf_chunks:
                self.log("[EXTRACT] BASE/KIDS leaf chunk dump: enabled, this may be slow")
            else:
                self.log("[EXTRACT] BASE/KIDS leaf chunk dump: disabled, compact output")
            try:
                base_kids_summary = base_kids_export.write_raw_base_kids(
                    setbas,
                    raw_out / "raw" / "BASE_KIDS",
                    export_leaf_chunks=export_leaf_chunks,
                )
            except (base_kids_export.ExportError, OSError) as exc:
                self.log(f"[ERROR] BASE/KIDS raw export failed: {exc}")
                messagebox.showerror(TITLE, "BASE/KIDS raw export failed. See log for details.")
                return False
            self.log(f"[EXTRACT] BASE/KIDS raw manifest: {base_kids_summary['raw_manifest_path']}")
            self.log(f"[EXTRACT] KIDS forms exported: {base_kids_summary['kids_forms_exported']}")
            self.log(f"[EXTRACT] OBJT forms exported: {base_kids_summary['objt_forms_exported']}")
            if base_kids_summary.get("leaf_chunk_files_enabled"):
                for tag in ("CLID", "NAME", "NAM2", "STRC", "ATTS", "OLPL", "OTL2"):
                    count = base_kids_summary["leaf_chunks_exported"].get(tag, 0)
                    self.log(f"[EXTRACT] {tag} chunks exported: {count}")
            else:
                self.log("[EXTRACT] Leaf chunk files skipped by GUI option")
            self.log(f"[EXTRACT] BASE/KIDS warnings/errors: {base_kids_summary['warning_count']}/0")
            self.log(f"[EXTRACT] manifest.json: {raw_out / 'manifest.json'}")
            self.log(f"[EXTRACT] raw output: {raw_out / 'raw'}")
            self.log("[OK] Extraction complete")
            return True

        self.log(f"[ERROR] Extraction failed with code {result}")
        messagebox.showerror(TITLE, "Extraction failed. See log for details.")
        return False

    def remove_text_manifest(self, raw_out: Path) -> None:
        """Remove the optional text/CSV manifest from GUI runs, keeping manifest.json."""
        for name in ("manifest.csv", "manifest.txt"):
            path = raw_out / name
            if path.exists():
                try:
                    path.unlink()
                    self.log(f"[EXTRACT] Removed text manifest: {path}")
                except OSError as exc:
                    self.log(f"[WARNING] Could not remove text manifest {path}: {exc}")

    def load_builtin_palette(self) -> Optional[bytes]:
        try:
            palette = vbmp_to_png.get_builtin_cmap()
        except (vbmp_to_png.ConvertError, OSError) as exc:
            self.show_error(str(exc))
            return None

        if len(palette) >= 3:
            self.log(f"[PALETTE] Using {vbmp_to_png.BUILTIN_PALETTE_SOURCE}")
            self.log(f"[PALETTE] Index 0 RGB = {palette[0]},{palette[1]},{palette[2]}")
        return palette

    def convert_extracted_vbmp_to_ilbm(self, raw_out: Path) -> None:
        input_dir = raw_out / "raw" / "VBMP"
        ilbm_out = raw_out / "textures_ilbm"
        if not input_dir.is_dir():
            self.log(f"[ILBM] Skipped: extracted VBMP folder not found: {input_dir}")
            return

        palette = self.load_builtin_palette()
        if palette is None:
            self.log("[ILBM] Skipped: palette unavailable")
            return

        self.log("[ILBM] Converting extracted VBMP textures to standard ILBM")
        self.log(f"[ILBM] Input: {input_dir}")
        self.log(f"[ILBM] Output: {ilbm_out}")
        ilbm_out.mkdir(parents=True, exist_ok=True)
        self.log(f"[ILBM] Created folder: {ilbm_out}")

        converted = 0
        skipped = 0
        errors = 0
        for vbmp_file in vbmp_to_ilbm.iter_batch_files(input_dir):
            out_file = vbmp_to_ilbm.output_for_batch(input_dir, vbmp_file, ilbm_out)
            try:
                vbmp = vbmp_to_ilbm.convert_vbmp_to_ilbm(vbmp_file, out_file, palette)
            except (vbmp_to_png.ConvertError, vbmp_to_ilbm.IlbmWriteError, OSError) as exc:
                skipped += 1
                self.log(f"[ILBM SKIP] {vbmp_file} {exc}")
                continue
            converted += 1
            self.log(f"[ILBM OK] {vbmp_file} -> {out_file} ({vbmp.width}x{vbmp.height}, unknown={vbmp.unknown})")

        self.log("[ILBM] Summary:")
        self.log(f"[ILBM] converted count: {converted}")
        self.log(f"[ILBM] skipped count: {skipped}")
        self.log(f"[ILBM] error count: {errors}")
        self.log("[OK] Texture ILBM Conversion finished")

    def load_palette_and_pillow(self):
        palette = self.load_builtin_palette()
        if palette is None:
            return None
        try:
            image_module = vbmp_to_png.load_pillow_image()
        except vbmp_to_png.ConvertError as exc:
            message = str(exc)
            if "Pillow" in message:
                message = PILLOW_HELP
            self.show_error(message)
            return None
        except OSError as exc:
            self.show_error(str(exc))
            return None
        return palette, image_module

    def run_convert(self) -> bool:
        paths = self.validate_convert_paths()
        if paths is None:
            return False
        input_dir, png_out = paths

        loaded = self.load_palette_and_pillow()
        if loaded is None:
            return False
        palette, image_module = loaded

        self.log("[CONVERT] Starting")
        self.log(f"[CONVERT] Input: {input_dir}")
        self.log(f"[CONVERT] Palette: {vbmp_to_png.BUILTIN_PALETTE_SOURCE}")
        self.log(f"[CONVERT] Output: {png_out}")
        png_out.mkdir(parents=True, exist_ok=True)
        self.log(f"[CONVERT] Created folder: {png_out}")

        converted = 0
        skipped = 0
        errors = 0

        for vbmp_file in vbmp_to_png.iter_batch_files(input_dir):
            out_file = vbmp_to_png.output_for_batch(input_dir, vbmp_file, png_out)
            try:
                vbmp = vbmp_to_png.convert_vbmp_to_png(vbmp_file, out_file, palette, image_module)
            except (vbmp_to_png.ConvertError, OSError) as exc:
                skipped += 1
                self.log(f"[SKIP] {vbmp_file} {exc}")
                continue

            converted += 1
            self.log(f"[OK] {vbmp_file} -> {out_file} ({vbmp.width}x{vbmp.height}, unknown={vbmp.unknown})")

        self.log("[CONVERT] Summary:")
        self.log(f"[CONVERT] converted count: {converted}")
        self.log(f"[CONVERT] skipped count: {skipped}")
        self.log(f"[CONVERT] error count: {errors}")

        if converted:
            self.log("[OK] Conversion complete")
            return True

        self.log("[ERROR] No VBMP files were converted")
        messagebox.showwarning(TITLE, "No VBMP files were converted. See log for details.")
        return False

    def extract_all(self) -> None:
        if self.run_extract(all_classes=True):
            messagebox.showinfo(TITLE, "Extraction complete. Use Texture ILBM Conversion or Texture PNG Conversion when needed.")

    def convert_extracted_ilbm(self) -> None:
        raw_out_text = self.raw_out_var.get().strip()
        if not raw_out_text:
            self.show_error("Extraction output folder is required.")
            return
        raw_out = Path(raw_out_text)
        self.convert_extracted_vbmp_to_ilbm(raw_out)
        messagebox.showinfo(TITLE, "Texture ILBM conversion complete. See log for details.")

    def convert_extracted(self) -> None:
        if self.run_convert():
            messagebox.showinfo(TITLE, "Texture PNG conversion complete.")

    def validate_png_to_ilbm_paths(self) -> Optional[Tuple[Path, Path, Path]]:
        raw_out_text = self.raw_out_var.get().strip()
        if not raw_out_text:
            self.show_error("Extraction output folder is required.")
            return None
        raw_out = Path(raw_out_text)
        png_dir = raw_out / "textures_png"
        template_dir = raw_out / "textures_ilbm"
        out_dir = raw_out / "textures_ilbm_from_png"
        if not png_dir.is_dir():
            self.show_error(f"PNG folder not found: {png_dir}. Run Texture PNG Conversion first.")
            return None
        if not template_dir.is_dir():
            self.show_error(f"ILBM template folder not found: {template_dir}. Run Texture ILBM Conversion first.")
            return None
        return png_dir, template_dir, out_dir

    def convert_png_to_ilbm(self) -> None:
        paths = self.validate_png_to_ilbm_paths()
        if paths is None:
            return
        png_dir, template_dir, out_dir = paths
        self.log("[PNG->ILBM] Starting")
        self.log(f"[PNG->ILBM] PNG input: {png_dir}")
        self.log(f"[PNG->ILBM] ILBM templates: {template_dir}")
        self.log(f"[PNG->ILBM] Output: {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"[PNG->ILBM] Created folder: {out_dir}")

        try:
            image_module = png_to_ilbm.load_pillow_image()
            sink = LogSink(lambda line: self.log("[PNG->ILBM] " + line))
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                converted, skipped, warnings, errors = png_to_ilbm.run_batch(png_dir, template_dir, out_dir, image_module)
            sink.flush()
        except png_to_ilbm.PngToIlbmError as exc:
            message = str(exc)
            if "Pillow" in message:
                message = PILLOW_HELP
            self.show_error(message)
            return
        except OSError as exc:
            self.show_error(str(exc))
            return

        self.log("[PNG->ILBM] Summary:")
        self.log(f"[PNG->ILBM] converted count: {converted}")
        self.log(f"[PNG->ILBM] skipped count: {skipped}")
        self.log(f"[PNG->ILBM] warning count: {warnings}")
        self.log(f"[PNG->ILBM] error count: {errors}")
        if converted:
            self.log("[OK] PNG to ILBM Conversion complete")
            messagebox.showinfo(TITLE, "PNG to ILBM conversion complete.")
        else:
            self.log("[ERROR] No PNG files were converted")
            messagebox.showwarning(TITLE, "No PNG files were converted. See log for details.")

    def export_metadata(self) -> None:
        paths = self.validate_extract_paths()
        if paths is None:
            return
        setbas, raw_out = paths
        metadata_out = raw_out / "metadata"

        self.log("[METADATA] Parsing BASE/KIDS")
        self.log(f"[METADATA] SET.BAS: {setbas}")
        self.log(f"[METADATA] Output: {metadata_out}")
        try:
            summary = base_kids_export.write_outputs(setbas, metadata_out)
        except (base_kids_export.ExportError, OSError) as exc:
            self.show_error(f"Metadata export failed: {exc}")
            return

        self.log(f"[METADATA] scenegraph.json written: {summary['scenegraph_path']}")
        self.log(f"[METADATA] references.json written: {summary['references_path']}")
        self.log(f"[METADATA] unresolved_refs.txt written: {summary['unresolved_path']}")
        self.log(f"[METADATA] KIDS sections found: {summary['kids_count']}")
        self.log(f"[METADATA] OBJT nodes exported: {summary['node_count']}")
        self.log(f"[METADATA] texture references found: {summary['texture_ref_count']}")
        self.log(f"[METADATA] skeleton references found: {summary['skeleton_ref_count']}")
        self.log(f"[METADATA] animation references found: {summary['animation_ref_count']}")
        self.log(f"[METADATA] unresolved references: {summary['unresolved_count']}")
        messagebox.showinfo(TITLE, "Metadata export complete.")

    def open_folder(self, folder_text: str) -> None:
        folder_text = folder_text.strip()
        if not folder_text:
            self.show_error("Folder path is empty.")
            return
        folder = Path(folder_text)
        if not folder.exists():
            self.show_error(f"Folder does not exist: {folder}")
            return
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self.show_error(str(exc))


def main() -> int:
    root = tk.Tk()
    SetBasToolGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
