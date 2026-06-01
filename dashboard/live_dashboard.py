#!/usr/bin/env python3
"""
live_dashboard.py — Real-time terminal dashboard using Rich.
Polls the API every 5 seconds and displays live store metrics.

Usage:
    python dashboard/live_dashboard.py \
        --api-url http://localhost:8000 \
        --store-id STORE_BLR_002
"""

import os
import time
import argparse
from datetime import datetime, timezone

try:
    import httpx
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    print("ERROR: Install dependencies: pip install rich httpx")
    raise

API_URL = os.getenv("API_URL", "http://localhost:8000")
STORE_ID = os.getenv("STORE_ID", "STORE_BLR_002")
POLL_INTERVAL = 5


def fetch(client: httpx.Client, url: str) -> dict | None:
    try:
        resp = client.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def severity_color(severity: str) -> str:
    return {"INFO": "cyan", "WARN": "yellow", "CRITICAL": "red"}.get(severity, "white")


def build_dashboard(metrics: dict | None, funnel: dict | None, anomalies: dict | None) -> Table:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    root = Table.grid(padding=1)
    root.add_column()

    # ── Header ───────────────────────────────────────────────────────────────
    root.add_row(
        Panel(
            f"[bold cyan]Store Intelligence — {STORE_ID}[/bold cyan]  "
            f"[dim]Live · refreshes every {POLL_INTERVAL}s · {now}[/dim]",
            box=box.ROUNDED,
        )
    )

    if not metrics:
        root.add_row("[red]⚠ API unavailable — retrying...[/red]")
        return root

    # ── Key metrics ──────────────────────────────────────────────────────────
    kpis = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold magenta")
    kpis.add_column("Metric")
    kpis.add_column("Value", justify="right")
    kpis.add_column("Status")

    uv = metrics.get("unique_visitors", 0)
    cr = metrics.get("conversion_rate", 0)
    qd = metrics.get("queue_depth", 0)
    ar = metrics.get("abandonment_rate", 0)

    cr_color = "green" if cr >= 0.15 else ("yellow" if cr >= 0.07 else "red")
    qd_color = "red" if qd >= 8 else ("yellow" if qd >= 5 else "green")

    kpis.add_row("👥 Unique Visitors Today", str(uv), "")
    kpis.add_row("💳 Conversion Rate", f"{cr:.1%}", f"[{cr_color}]{'▲' if cr >= 0.10 else '▼'}[/{cr_color}]")
    kpis.add_row("🧾 Queue Depth", str(qd), f"[{qd_color}]{'⚠' if qd >= 5 else '✓'}[/{qd_color}]")
    kpis.add_row("🚶 Abandonment Rate", f"{ar:.1%}", "")

    root.add_row(Panel(kpis, title="[bold]Key Metrics[/bold]", box=box.ROUNDED))

    # ── Zone dwell heatmap ───────────────────────────────────────────────────
    zone_data = metrics.get("avg_dwell_per_zone", [])
    if zone_data:
        zone_table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold blue")
        zone_table.add_column("Zone")
        zone_table.add_column("Avg Dwell", justify="right")
        zone_table.add_column("Visits", justify="right")

        for zone in sorted(zone_data, key=lambda z: z["avg_dwell_ms"], reverse=True):
            dwell_sec = zone["avg_dwell_ms"] / 1000
            bar_len = min(int(dwell_sec / 3), 20)
            bar = "█" * bar_len
            zone_table.add_row(
                zone["zone_id"],
                f"{dwell_sec:.1f}s  [cyan]{bar}[/cyan]",
                str(zone["visit_count"]),
            )

        root.add_row(Panel(zone_table, title="[bold]Zone Dwell[/bold]", box=box.ROUNDED))

    # ── Funnel ───────────────────────────────────────────────────────────────
    if funnel and funnel.get("stages"):
        funnel_table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold green")
        funnel_table.add_column("Stage")
        funnel_table.add_column("Visitors", justify="right")
        funnel_table.add_column("Drop-off", justify="right")

        for stage in funnel["stages"]:
            drop = stage["drop_off_pct"]
            drop_color = "red" if drop > 50 else ("yellow" if drop > 25 else "green")
            funnel_table.add_row(
                stage["stage"],
                str(stage["count"]),
                f"[{drop_color}]{drop:.1f}%[/{drop_color}]" if drop > 0 else "-",
            )

        root.add_row(Panel(funnel_table, title="[bold]Conversion Funnel[/bold]", box=box.ROUNDED))

    # ── Anomalies ────────────────────────────────────────────────────────────
    if anomalies and anomalies.get("anomalies"):
        anom_table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold red")
        anom_table.add_column("Severity")
        anom_table.add_column("Type")
        anom_table.add_column("Description")
        anom_table.add_column("Action")

        for a in anomalies["anomalies"]:
            color = severity_color(a["severity"])
            anom_table.add_row(
                f"[{color}]{a['severity']}[/{color}]",
                a["anomaly_type"],
                a["description"][:60],
                a["suggested_action"][:50],
            )

        root.add_row(Panel(anom_table, title="[bold red]⚠ Active Anomalies[/bold red]", box=box.ROUNDED))
    else:
        root.add_row(Panel("[green]✓ No active anomalies[/green]", box=box.ROUNDED))

    return root


def main():
    global API_URL, STORE_ID
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--store-id", default=STORE_ID)
    args = parser.parse_args()

    API_URL = args.api_url
    STORE_ID = args.store_id

    console = Console()

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        with httpx.Client() as client:
            while True:
                metrics = fetch(client, f"{API_URL}/stores/{STORE_ID}/metrics")
                funnel = fetch(client, f"{API_URL}/stores/{STORE_ID}/funnel")
                anomalies = fetch(client, f"{API_URL}/stores/{STORE_ID}/anomalies")

                dashboard = build_dashboard(metrics, funnel, anomalies)
                live.update(dashboard)
                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
