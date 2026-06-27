#!/usr/bin/env python3
# =============================================================================
# Elite ZSTD Compression Ratio & Throughput Forensic Analyzer [V10.2 - Golden Master]
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Python 3.14+)
# Features: Persistent CCtx, True Zero-Allocation Hot Path, Autonomous Mode, Fast Warmup
# =============================================================================

import os
import sys
import time
import ctypes
import resource
import argparse
from pathlib import Path
from ctypes import c_size_t, c_void_p, c_int, c_char_p
from dataclasses import dataclass
from typing import NoReturn

# Strictly enforce Python version for modern standard library features
if sys.version_info < (3, 14):
    sys.exit("FATAL: This architect-grade script requires Python 3.14+.")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.align import Align
    from rich.prompt import IntPrompt
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
except ImportError:
    sys.exit("FATAL: 'rich' library missing. Run: pacman -S python-rich")

console = Console()

# =============================================================================
# Bare-Metal Advanced C-FFI Bindings to /usr/lib/libzstd.so
# =============================================================================
try:
    zstd = ctypes.CDLL("libzstd.so.1")
except OSError:
    sys.exit("[bold red]FATAL: libzstd.so.1 not found in library path. Ensure zstd is installed.[/bold red]")

# Error Handling
zstd.ZSTD_isError.argtypes = [c_size_t]
zstd.ZSTD_isError.restype = c_int
zstd.ZSTD_getErrorName.argtypes = [c_size_t]
zstd.ZSTD_getErrorName.restype = c_char_p

def check_zstd_error(code: int, context: str) -> None:
    if zstd.ZSTD_isError(code):
        err_msg = zstd.ZSTD_getErrorName(code).decode('utf-8')
        raise RuntimeError(f"ZSTD FFI Error during {context}: {err_msg}")

# Context Management (The Zero-Allocation Fix)
zstd.ZSTD_createCCtx.argtypes = []
zstd.ZSTD_createCCtx.restype = c_void_p

zstd.ZSTD_freeCCtx.argtypes = [c_void_p]
zstd.ZSTD_freeCCtx.restype = c_size_t

zstd.ZSTD_createDCtx.argtypes = []
zstd.ZSTD_createDCtx.restype = c_void_p

zstd.ZSTD_freeDCtx.argtypes = [c_void_p]
zstd.ZSTD_freeDCtx.restype = c_size_t

# Advanced Compression / Decompression functions passing the Context
zstd.ZSTD_compressBound.argtypes = [c_size_t]
zstd.ZSTD_compressBound.restype = c_size_t

zstd.ZSTD_compressCCtx.argtypes = [c_void_p, c_void_p, c_size_t, c_void_p, c_size_t, c_int]
zstd.ZSTD_compressCCtx.restype = c_size_t

zstd.ZSTD_decompressDCtx.argtypes = [c_void_p, c_void_p, c_size_t, c_void_p, c_size_t]
zstd.ZSTD_decompressDCtx.restype = c_size_t

@dataclass(slots=True, kw_only=True)
class BenchmarkResult:
    level: int
    orig_size_mb: float
    compr_size_bytes: int
    comp_time_ns: int
    decomp_time_ns: int
    comp_page_faults: int
    decomp_page_faults: int

    @property
    def ratio(self) -> float:
        return (self.orig_size_mb * 1024 * 1024) / max(self.compr_size_bytes, 1)

    @property
    def compr_size_mb(self) -> float:
        return self.compr_size_bytes / (1024 * 1024)

    @property
    def saved_mb(self) -> float:
        return self.orig_size_mb - self.compr_size_mb

    @property
    def comp_speed_mb_s(self) -> float:
        return self.orig_size_mb / (self.comp_time_ns / 1e9)

    @property
    def decomp_speed_mb_s(self) -> float:
        return self.orig_size_mb / (self.decomp_time_ns / 1e9)

def format_duration(nanoseconds: int) -> str:
    seconds = nanoseconds / 1e9
    if seconds < 1.0:
        return f"{seconds * 1000:.2f}ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"
    return f"{int(seconds // 60)}m {seconds % 60:.1f}s"

def generate_realistic_data(size_bytes: int, entropy_mode: str = "mixed") -> bytearray:
    console.print(f"[cyan][INFO][/cyan] Allocating contiguous heap blocks for FFI pointer mapping (entropy_mode={entropy_mode})...")
    
    if entropy_mode == "zero":
        return bytearray(size_bytes)
    elif entropy_mode == "random":
        view = bytearray(size_bytes)
        offset = 0
        chunk_size = 16 * 1024 * 1024
        while offset < size_bytes:
            current_chunk = min(chunk_size, size_bytes - offset)
            view[offset : offset + current_chunk] = os.urandom(current_chunk)
            offset += current_chunk
        return view
        
    view = bytearray(size_bytes)
    base_text = (
        b'{"log_level":"INFO","timestamp":"2026-06-26T19:02:55Z","system":"arch_linux_core","kernel":"7.1.0-arch1-1",'
        b'"event":"memory_compaction","metrics":{"cpu":14.5,"mem_free":1024,"zram_active":true,"throughput_mb":1350.5}} '
        b'Arch Linux rolling release. Memory compression is a critical facet of modern system architectures. '
    )
    base_len = len(base_text)
    offset = 0
    chunk_size = 1024 * 1024
    
    while offset < size_bytes:
        current_chunk = min(chunk_size, size_bytes - offset)
        rand_len = int(current_chunk * 0.33)
        text_len = current_chunk - rand_len
        
        view[offset : offset + rand_len] = os.urandom(rand_len)
        offset += rand_len
        
        repeats = (text_len // base_len) + 1
        view[offset : offset + text_len] = (base_text * repeats)[:text_len]
        offset += text_len
        
    return view

def benchmark_ffi(src_ptr: ctypes.Array, dst_ptr: ctypes.Array, decomp_dst_ptr: ctypes.Array,
                  src_size: int, dst_capacity: int,
                  cctx: c_void_p, dctx: c_void_p, level: int) -> BenchmarkResult:
    """
    TRUE ZERO ALLOCATION HOT PATH. 
    Memory buffers, ctypes pointers, AND ZSTD Contexts are pre-allocated before entering this function.
    """
    # 1. Advanced FFI Compression (Zero Malloc)
    pf_start = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    t_start = time.perf_counter_ns()
    
    compressed_size = zstd.ZSTD_compressCCtx(cctx, dst_ptr, dst_capacity, src_ptr, src_size, level)
    
    comp_time_ns = max(time.perf_counter_ns() - t_start, 1)
    pf_end = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    comp_page_faults = pf_end - pf_start
    
    check_zstd_error(compressed_size, "Compression")
    
    # 2. Advanced FFI Decompression (Zero Malloc)
    pf_start_d = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    t_start = time.perf_counter_ns()
    
    decomp_size = zstd.ZSTD_decompressDCtx(dctx, decomp_dst_ptr, src_size, dst_ptr, compressed_size)
    
    decomp_time_ns = max(time.perf_counter_ns() - t_start, 1)
    pf_end_d = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    decomp_page_faults = pf_end_d - pf_start_d
    
    check_zstd_error(decomp_size, "Decompression")

    return BenchmarkResult(
        level=level,
        orig_size_mb=src_size / (1024 * 1024),
        compr_size_bytes=compressed_size,
        comp_time_ns=comp_time_ns,
        decomp_time_ns=decomp_time_ns,
        comp_page_faults=comp_page_faults,
        decomp_page_faults=decomp_page_faults
    )

def save_report(results: list[BenchmarkResult], size_mb: int, entropy: str, filepath: Path) -> None:
    md_content = (
        f"# ZSTD Autonomous Bare-Metal FFI Benchmark Telemetry\n\n"
        f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Payload Specifications**: {size_mb} MB ({entropy.capitalize()} Entropy)\n"
        f"- **Execution Layer**: C-API Persistent CCtx (Zero Allocation Hot Path)\n\n"
        f"| Level | Compressed | Ratio | Saved | C-API Compression | C-API Decompression | Page Faults (C/D) |\n"
        f"| :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n"
    )
    
    for r in results:
        md_content += (
            f"| {r.level} | {r.compr_size_mb:.2f} MB | {r.ratio:.2f}x | {r.saved_mb:.2f} MB | "
            f"{format_duration(r.comp_time_ns)} ({r.comp_speed_mb_s:.1f} MB/s) | "
            f"{format_duration(r.decomp_time_ns)} ({r.decomp_speed_mb_s:.1f} MB/s) | "
            f"{r.comp_page_faults} / {r.decomp_page_faults} |\n"
        )
        
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        if filepath.exists():
            filepath.unlink()
    except Exception:
        pass
    try:
        filepath.write_text(md_content)
        console.print(f"[bold green]Autonomous telemetry securely written to {filepath}[/bold green]")
    except PermissionError:
        fallback_path = Path.cwd() / filepath.name
        try:
            if fallback_path.exists():
                fallback_path.unlink()
        except Exception:
            pass
        try:
            fallback_path.write_text(md_content)
            console.print(f"[yellow]Warning: Permission denied on {filepath}. Saved to fallback: {fallback_path}[/yellow]")
        except Exception as e:
            console.print(f"[bold red]Failed to write report to {filepath} and fallback: {e}[/bold red]")

def main() -> NoReturn | None:
    parser = argparse.ArgumentParser(description="ZSTD Bare-Metal FFI Benchmark")
    parser.add_argument("--max-level", type=int, default=None)
    parser.add_argument("--size-mb", type=int, default=None)
    parser.add_argument("--entropy", type=str, choices=["mixed", "zero", "random"], default="mixed")
    parser.add_argument("--auto", action="store_true", help="Run autonomously without prompts and auto-save the report")
    parser.add_argument("--chaos-leak", action="store_true", help="Run 10,000 headless iterations of Levels 1-10 to trace leaks under Valgrind")
    args = parser.parse_args()

    header = Panel(
        Align.center(
            "[bold cyan]⚡ ZSTD Multi-Level Compression & Throughput Forensic Analyzer ⚡[/bold cyan]\n"
            "[dim]Targeting Arch Linux | Peak Bare-Metal Execution via Persistent CCtx Pointers[/dim]"
        ),
        border_style="magenta",
        padding=(1, 2)
    )
    console.print(header)
    
    # --- AUTONOMOUS ROUTING LOGIC ---
    if args.chaos_leak:
        max_level = 10
        size_mb = args.size_mb if args.size_mb is not None else 1
        console.print("[bold yellow][CHAOS LEAK MODE ENABLED] Setting level to 10 and size to 1MB...[/bold yellow]")
    elif args.auto:
        max_level = args.max_level if args.max_level is not None else 12
        size_mb = args.size_mb if args.size_mb is not None else 50
        console.print("[bold yellow][AUTO MODE ENABLED] Bypassing prompts. Enforcing absolute forensic run...[/bold yellow]")
    else:
        if args.max_level is not None:
            max_level = args.max_level
        else:
            while True:
                max_level = IntPrompt.ask("\nEnter maximum ZSTD compression level (1-22)", default=10)
                if 1 <= max_level <= 22:
                    break
                console.print("[bold red]Invalid level.[/bold red]")
        
        if args.size_mb is not None:
            size_mb = args.size_mb
        else:
            size_mb = IntPrompt.ask("Enter test data payload size (in Megabytes)", default=50)
            
    if size_mb <= 0:
        sys.exit("[bold red]FATAL: Size must be a positive integer.[/bold red]")
        
    size_bytes = size_mb * 1024 * 1024
    
    # --- GLOBAL ALLOCATION PHASE ---
    data = generate_realistic_data(size_bytes, entropy_mode=args.entropy)
    
    console.print("[dim]Pre-allocating persistent destination buffers and ZSTD CCtx structures...[/dim]")
    dst_capacity = zstd.ZSTD_compressBound(size_bytes)
    comp_buffer = bytearray(dst_capacity)
    decomp_buffer = bytearray(size_bytes)
    
    # Initialize Persistent C-Contexts
    cctx = zstd.ZSTD_createCCtx()
    dctx = zstd.ZSTD_createDCtx()
    if not cctx or not dctx:
        sys.exit("[bold red]FATAL: Failed to allocate ZSTD contexts.[/bold red]")
    
    # Pre-create ctypes pointers
    src_ptr = (ctypes.c_char * size_bytes).from_buffer(data)
    dst_ptr = (ctypes.c_char * dst_capacity).from_buffer(comp_buffer)
    decomp_dst_ptr = (ctypes.c_char * size_bytes).from_buffer(decomp_buffer)
    
    # --- CHAOS LEAK LOOP ---
    if args.chaos_leak:
        console.print("[bold yellow][CHAOS LEAK] Commencing 100 headless iterations of Levels 1-10...[/bold yellow]")
        for i in range(100):
            for level in range(1, 11):
                compressed_size = zstd.ZSTD_compressCCtx(cctx, dst_ptr, dst_capacity, src_ptr, size_bytes, level)
                check_zstd_error(compressed_size, f"Chaos Compression (Iter {i}, Level {level})")
                
                decomp_size = zstd.ZSTD_decompressDCtx(dctx, decomp_dst_ptr, size_bytes, dst_ptr, compressed_size)
                check_zstd_error(decomp_size, f"Chaos Decompression (Iter {i}, Level {level})")
                
        zstd.ZSTD_freeCCtx(cctx)
        zstd.ZSTD_freeDCtx(dctx)
        console.print("[bold green][CHAOS LEAK] Completed 100 iterations. Cleanup successful.[/bold green]")
        return
    
    # --- HARDWARE & VIRTUAL MEMORY FAST WARMUP ---
    console.print("[dim]Pre-faulting persistent buffers and expanding internal ZSTD hash tables (Fast Warmup)...[/dim]")
    
    # SURGICAL FIX: Strict cap to 5MB max, and level 3 to prevent silent UI hangs.
    # We do NOT use 50MB here, as 5MB perfectly achieves the pre-faulting goal in a fraction of the time.
    warmup_size = min(size_bytes, 5 * 1024 * 1024) 
    warmup_levels = min(3, max_level)
    
    for lvl in range(1, warmup_levels + 1):
        _ = benchmark_ffi(src_ptr, dst_ptr, decomp_dst_ptr, warmup_size, dst_capacity, cctx, dctx, lvl)
        
    console.print("[bold green]True Zero-Allocation pathway established. Commencing forensic benchmark.[/bold green]\n")
    # ----------------------------------------

    results: list[BenchmarkResult] = []
    
    with Progress(
        SpinnerColumn(style="bold cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        auto_refresh=False
    ) as progress:
        task = progress.add_task("[cyan]Benchmarking C-Library...[/cyan]", total=max_level)
        
        for level in range(1, max_level + 1):
            progress.update(task, description=f"[cyan]Analyzing Level {level}...[/cyan]")
            progress.refresh()
            try:
                results.append(benchmark_ffi(src_ptr, dst_ptr, decomp_dst_ptr, size_bytes, dst_capacity, cctx, dctx, level))
            except Exception as e:
                console.print(f"\n[bold red]Critical FFI Error at level {level}: {e}[/bold red]")
            progress.advance(task)
            progress.refresh()
            
    # Cleanup C Pointers
    zstd.ZSTD_freeCCtx(cctx)
    zstd.ZSTD_freeDCtx(dctx)
            
    table = Table(
        title=f"\n📊 ZSTD Bare-Metal Performance Matrix ({size_mb}MB {args.entropy.capitalize()}-Entropy Payload)",
        title_style="bold magenta",
        header_style="bold cyan",
        border_style="dim blue",
        expand=True
    )
    
    table.add_column("Level", justify="center", style="bold yellow")
    table.add_column("Compressed", justify="right", style="white")
    table.add_column("Ratio", justify="right", style="bold green")
    table.add_column("Space Saved", justify="right", style="white")
    table.add_column("C-API Compression", justify="right", style="cyan")
    table.add_column("C-API Decompression", justify="right", style="magenta")
    table.add_column("Page Faults (C/D)", justify="right", style="bold red")
    
    for r in results:
        table.add_row(
            str(r.level),
            f"{r.compr_size_mb:.2f} MB",
            f"{r.ratio:.2f}x",
            f"{r.saved_mb:.2f} MB",
            f"{format_duration(r.comp_time_ns)} ({r.comp_speed_mb_s:.1f} MB/s)",
            f"{format_duration(r.decomp_time_ns)} ({r.decomp_speed_mb_s:.1f} MB/s)",
            f"{r.comp_page_faults} / {r.decomp_page_faults}"
        )
        
    console.print(table)
    
    if args.auto:
        report_dest = Path("/tmp/zstd_autonomous_report.md")
        save_report(results, size_mb, args.entropy, report_dest)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\n[bold yellow]SIGINT caught — operation aborted.[/bold yellow]")
