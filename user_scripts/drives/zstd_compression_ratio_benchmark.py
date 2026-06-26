#!/usr/bin/env python3
# =============================================================================
# Elite ZSTD Compression Ratio & Throughput Forensic Analyzer
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Python 3.14+)
# Scope: Platinum Grade. High-fidelity ZSTD performance analytics.
# =============================================================================

from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any

# Verify minimum Python version
if sys.version_info < (3, 14):
    print(f"Warning: This script is optimized for Python 3.14+, running {sys.version.split()[0]}", file=sys.stderr)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.align import Align
    from rich.prompt import Prompt, IntPrompt, Confirm
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
except ImportError:
    print("Error: The 'rich' library is required but not installed.", file=sys.stderr)
    print("Please install it using: pacman -S python-rich", file=sys.stderr)
    sys.exit(1)

console = Console()

# --- Time Formatter ---
def format_duration(seconds: float) -> str:
    """Formats a float duration into human-readable duration strings."""
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60.0:
        return f"{seconds:.2f}s"
    else:
        minutes = int(seconds // 60)
        rem_seconds = seconds % 60
        return f"{minutes}m {rem_seconds:.1f}s"

# --- Realistic Data Generator ---
def generate_realistic_data(size_bytes: int) -> bytes:
    """
    Generates high-fidelity test data simulating real-world system memory/disk.
    Interleaves 33% high-entropy (incompressible) data with 67% structured text.
    Targeting a realistic ~3.0x compression ratio.
    """
    console.print("[cyan][INFO][/cyan] Synthesizing high-fidelity payload with tuned entropy...")
    
    base_text = (
        b'{"log_level":"INFO","timestamp":"2026-06-26T19:02:55Z","system":"arch_linux_core","kernel":"7.1.0-arch1-1",'
        b'"event":"memory_compaction","metrics":{"cpu":14.5,"mem_free":1024,"zram_active":true,"throughput_mb":1350.5}} '
        b'Arch Linux rolling release. Memory compression is a critical facet of modern system architectures. '
        b'By reclaiming cold pages and compressing them in RAM, we prevent disk thrashing and extend hardware lifespan. '
    )
    
    chunks = []
    bytes_left = size_bytes
    
    # Process in 1MB chunks to ensure interleaved entropy across the entire payload
    while bytes_left > 0:
        chunk_size = min(bytes_left, 1024 * 1024)
        rand_len = int(chunk_size * 0.33)  # 33% random noise
        text_len = chunk_size - rand_len   # 67% compressible text
        
        chunks.append(os.urandom(rand_len))
        
        repeats = (text_len // len(base_text)) + 1
        chunks.append((base_text * repeats)[:text_len])
        
        bytes_left -= chunk_size
        
    return b"".join(chunks)

# --- In-Memory Benchmark Engine ---
def benchmark_in_memory(data: bytes, level: int) -> dict[str, Any]:
    """Runs compression and decompression entirely in memory using standard I/O pipes."""
    # Compression
    t_start = time.perf_counter()
    p_comp = subprocess.run(
        ["zstd", f"-{level}", "-c"],
        input=data,
        capture_output=True
    )
    comp_time = max(time.perf_counter() - t_start, 1e-6) # Guard against ZeroDivisionError
    
    if p_comp.returncode != 0:
        raise RuntimeError(f"zstd compression failed: {p_comp.stderr.decode().strip()}")
        
    compressed_data = p_comp.stdout
    compressed_size = max(len(compressed_data), 1) # Guard against ZeroDivisionError for ratio
    
    # Decompression
    t_start = time.perf_counter()
    p_decomp = subprocess.run(
        ["zstd", "-d", "-c"],
        input=compressed_data,
        capture_output=True
    )
    decomp_time = max(time.perf_counter() - t_start, 1e-6) # Guard against ZeroDivisionError
    
    if p_decomp.returncode != 0:
        raise RuntimeError(f"zstd decompression failed: {p_decomp.stderr.decode().strip()}")
        
    return {
        "compressed_size": compressed_size,
        "comp_time": comp_time,
        "decomp_time": decomp_time
    }

# --- File-Based Benchmark Engine ---
def benchmark_file_based(data: bytes, level: int, target_dir: Path) -> dict[str, Any]:
    """Runs compression and decompression using physical/virtual disk staging."""
    input_file = target_dir / "zstd_bench_input.bin"
    output_file = target_dir / "zstd_bench_output.zst"
    decomp_file = target_dir / "zstd_bench_decomp.bin"
    
    try:
        # Stage input payload
        input_file.write_bytes(data)
        
        # Compression
        t_start = time.perf_counter()
        p_comp = subprocess.run(
            ["zstd", f"-{level}", "-f", "-o", str(output_file), str(input_file)],
            capture_output=True,
            text=True
        )
        comp_time = max(time.perf_counter() - t_start, 1e-6)
        
        if p_comp.returncode != 0:
            raise RuntimeError(f"zstd compression failed: {p_comp.stderr.strip()}")
            
        compressed_size = max(output_file.stat().st_size, 1)
        
        # Decompression
        t_start = time.perf_counter()
        p_decomp = subprocess.run(
            ["zstd", "-d", "-f", "-o", str(decomp_file), str(output_file)],
            capture_output=True,
            text=True
        )
        decomp_time = max(time.perf_counter() - t_start, 1e-6)
        
        if p_decomp.returncode != 0:
            raise RuntimeError(f"zstd decompression failed: {p_decomp.stderr.strip()}")
            
        return {
            "compressed_size": compressed_size,
            "comp_time": comp_time,
            "decomp_time": decomp_time
        }
    finally:
        # Atomic, exception-safe cleanup
        input_file.unlink(missing_ok=True)
        output_file.unlink(missing_ok=True)
        decomp_file.unlink(missing_ok=True)

# --- CLI Presentation ---
def main() -> None:
    # Modernized Arch-Grade Header
    header = Panel(
        Align.center(
            "[bold cyan]⚡ ZSTD Multi-Level Compression & Throughput Forensic Analyzer ⚡[/bold cyan]\n"
            "[dim]Targeting Arch Linux (Kernel 7.1+) & High-Performance Storage Architecture[/dim]"
        ),
        border_style="magenta",
        padding=(1, 2)
    )
    console.print(header)
    
    # 1. Parameter Collection
    while True:
        max_level = IntPrompt.ask(
            "\nEnter maximum ZSTD compression level to test (Standard: 1-19, Ultra: 20-22) (1-22)",
            default=10
        )
        if 1 <= max_level <= 22:
            break
        console.print("[bold red]Please enter a strictly valid level between 1 and 22.[/bold red]")
    
    size_mb = IntPrompt.ask(
        "Enter test data payload size (in Megabytes)",
        default=50
    )
    if size_mb <= 0:
        console.print("[bold red]FATAL: Size must be a positive integer.[/bold red]")
        sys.exit(1)
        
    size_bytes = size_mb * 1024 * 1024
    
    # 2. Storage Profile
    console.print("\n[bold]Choose benchmark storage profile:[/bold]")
    console.print("  [cyan]1)[/cyan] [bold]Pure In-Memory[/bold] (Uses stdin/stdout pipes, zero SSD writes/wear)")
    console.print("  [cyan]2)[/cyan] [bold]File-based RAM-disk/ZRAM[/bold] (Writes to a directory like /tmp or /mnt/zram1)")
    
    mode_choice = Prompt.ask("Select profile", choices=["1", "2"], default="1")
    
    target_dir: Path | None = None
    if mode_choice == "2":
        suggestions = ["/tmp", "/mnt/zram1", "/dev/shm"]
        active_suggestions = [s for s in suggestions if Path(s).is_dir() and os.access(s, os.W_OK)]
        
        console.print(f"\nWritable fast-paths detected: [green]{', '.join(active_suggestions)}[/green]")
        target_path_str = Prompt.ask("Enter directory path for tests", default=active_suggestions[0] if active_suggestions else "/tmp")
        target_dir = Path(target_path_str)
        
        if not target_dir.is_dir():
            console.print(f"[bold red]FATAL: Directory {target_dir} does not exist.[/bold red]")
            sys.exit(1)
        if not os.access(target_dir, os.W_OK):
            console.print(f"[bold red]FATAL: Directory {target_dir} is not writable.[/bold red]")
            sys.exit(1)
            
    # Generate Payload
    data = generate_realistic_data(size_bytes)
    results = []
    
    console.print(f"\n[bold green]Initializing forensic benchmark on {size_mb} MB payload...[/bold green]\n")
    
    # Execution & Progress Tracking
    with Progress(
        SpinnerColumn(style="bold cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Benchmarking ZSTD Engine...[/cyan]", total=max_level)
        
        for level in range(1, max_level + 1):
            progress.update(task, description=f"[cyan]Analyzing Level {level}...[/cyan]")
            try:
                if mode_choice == "1":
                    res = benchmark_in_memory(data, level)
                else:
                    assert target_dir is not None
                    res = benchmark_file_based(data, level, target_dir)
                    
                ratio = size_bytes / res["compressed_size"]
                comp_speed = size_mb / res["comp_time"]
                decomp_speed = size_mb / res["decomp_time"]
                saved_mb = (size_bytes - res["compressed_size"]) / (1024 * 1024)
                
                results.append({
                    "level": level,
                    "orig_size": size_mb,
                    "compr_size": res["compressed_size"] / (1024 * 1024),
                    "ratio": ratio,
                    "comp_time": res["comp_time"],
                    "decomp_time": res["decomp_time"],
                    "comp_speed": comp_speed,
                    "decomp_speed": decomp_speed,
                    "saved_mb": saved_mb
                })
            except Exception as e:
                console.print(f"\n[bold red]Forensic Error at level {level}: {e}[/bold red]")
            progress.advance(task)
            
    # Rendering Advanced Table
    table = Table(
        title=f"\n📊 ZSTD Compression Matrix ({size_mb}MB Mixed-Entropy Payload)",
        title_style="bold magenta",
        header_style="bold cyan",
        border_style="dim blue",
        expand=True
    )
    
    table.add_column("Level", justify="center", style="bold yellow")
    table.add_column("Compressed", justify="right", style="white")
    table.add_column("Ratio", justify="right", style="bold green")
    table.add_column("Space Saved", justify="right", style="white")
    table.add_column("Compression (Time / Speed)", justify="right", style="cyan")
    table.add_column("Decompression (Time / Speed)", justify="right", style="magenta")
    
    for r in results:
        comp_time_formatted = format_duration(r["comp_time"])
        decomp_time_formatted = format_duration(r["decomp_time"])
        table.add_row(
            str(r["level"]),
            f"{r['compr_size']:.2f} MB",
            f"{r['ratio']:.2f}x",
            f"{r['saved_mb']:.2f} MB",
            f"{comp_time_formatted} ({r['comp_speed']:.1f} MB/s)",
            f"{decomp_time_formatted} ({r['decomp_speed']:.1f} MB/s)"
        )
        
    console.print(table)
    
    # 4. Report Generation
    if Confirm.ask("\nCommit telemetry to markdown report file?", default=False):
        report_path_str = Prompt.ask(
            "Enter target destination",
            default=str(Path.home() / "zstd_forensic_report.md")
        )
        report_path = Path(report_path_str).expanduser()
        
        try:
            md_content = f"# ZSTD Forensic Benchmark Telemetry\n\n"
            md_content += f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            md_content += f"- **Payload Specifications**: {size_mb} MB (Mixed Entropy 33/67 Split)\n"
            md_content += f"- **Execution Layer**: {'Pure In-Memory' if mode_choice == '1' else f'File-based I/O ({target_dir})'}\n\n"
            
            md_content += "| Level | Compressed Size | Ratio | Space Saved | Compression (Time / Speed) | Decompression (Time / Speed) |\n"
            md_content += "| :---: | :---: | :---: | :---: | :---: | :---: |\n"
            
            for r in results:
                comp_time_formatted = format_duration(r["comp_time"])
                decomp_time_formatted = format_duration(r["decomp_time"])
                md_content += (
                    f"| {r['level']} | {r['compr_size']:.2f} MB | {r['ratio']:.2f}x | "
                    f"{r['saved_mb']:.2f} MB | {comp_time_formatted} ({r['comp_speed']:.1f} MB/s) | "
                    f"{decomp_time_formatted} ({r['decomp_speed']:.1f} MB/s) |\n"
                )
                
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(md_content)
            console.print(f"[bold green]Telemetry securely written to {report_path}[/bold green]")
        except Exception as e:
            console.print(f"[bold red]I/O Exception during write operation: {e}[/bold red]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]SIGINT caught — operation aborted.[/bold yellow]")
        sys.exit(130)
