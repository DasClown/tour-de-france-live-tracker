#!/usr/bin/env python3
"""
TdF-Console – Terminal-Client für den TDF-Live-Server.

Verbindet sich mit /state (oder /ws) eines laufenden ``tdf.server`` und zeigt
die Virtual-GC, Top-Favoriten und Live-Gruppen an. Optional mit ``rich`` für
eine farbige, live aktualisierende Tabelle; ohne rich als Plain-Text.

Usage:
    python3 tdf_console.py                      # http://localhost:8000
    python3 tdf_console.py --url http://host:8000
    python3 tdf_console.py --top 20 --interval 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


# --------------------------------------------------------------------------- #
# Formatierung
# --------------------------------------------------------------------------- #
def fmt_gap(s) -> str:
    if s is None:
        return "   -   "
    s = int(s)
    if s <= 0:
        return "leader "
    h, rem = divmod(s, 3600)
    mm, ss = divmod(rem, 60)
    return f"+{h}:{mm:02d}:{ss:02d}" if h else f"+{mm:02d}:{ss:02d}"


def fmt_kph(v) -> str:
    return f"{float(v):5.1f}" if v is not None else "  -  "


# --------------------------------------------------------------------------- #
# Plain-Text-Renderer
# --------------------------------------------------------------------------- #
def render_plain(d: dict) -> str:
    lines = []
    lines.append(f"=== TdF {d.get('year')} | Etappe {d.get('stage')} | "
                 f"{d.get('status')} ({d.get('freshness')}) ===")
    lines.append("")

    jerseys = d.get("jerseys") or {}
    if jerseys:
        js = "  ".join(f"{v.get('label','?')}: {v.get('name','?')}"
                       for v in jerseys.values())
        lines.append(f"Trikots: {js}")
        lines.append("")

    lines.append("── Virtual GC ──")
    lines.append(f"{'#':>3} {'BIB':>4}  {'NAME':<28}{'TEAM':<22}{'GAP':>10}")
    for e in (d.get("gc") or [])[:20]:
        extras = []
        if e.get("bonus_s"):
            extras.append(f"B{e['bonus_s']}s")
        if e.get("pen_s"):
            extras.append(f"P{e['pen_s']}s")
        lines.append(f"{e['pos']:>3} {e['bib']:>4}  {e['name'][:28]:<28}"
                     f"{(e.get('team') or '')[:22]:<22}{fmt_gap(e.get('rel_s')):>10}"
                     + (f"  {' '.join(extras)}" if extras else ""))
    lines.append("")

    if d.get("top_n"):
        lines.append("── Top-Favoriten ──")
        for r in d["top_n"]:
            extras = []
            if r.get("gradient") is not None:
                extras.append(f"{float(r['gradient']):+.0f}%")
            if r.get("deg_c") is not None:
                extras.append(f"{float(r['deg_c']):.0f}°C")
            rank = f"#{r['rank']}" if r.get("rank") else "  "
            ktf = (f"{float(r['km_to_finish']):.1f}km"
                   if r.get("km_to_finish") is not None else "")
            lines.append(f"  {rank:<3} {r['name'][:24]:<24}"
                         f"{fmt_kph(r.get('kph'))} km/h  {ktf}  "
                         f"{' '.join(extras)}")
        lines.append("")

    if d.get("groups"):
        lines.append("── Live-Gruppen ──")
        for g in d["groups"]:
            spd = f"{float(g['speed_kph']):.1f}km/h" if g.get("speed_kph") is not None else " - "
            rem = f"{float(g['remaining_km']):.0f}km" if g.get("remaining_km") is not None else ""
            lines.append(f"  [{g['order']}] {g['name']} ({g['size']} F.)  "
                         f"{spd} {rem}  {fmt_gap(g.get('gap_s'))}")
            if g.get("sample_names"):
                lines.append("       " + ", ".join(g["sample_names"])
                             + (f"  +{g['extra']}" if g.get("extra") else ""))

    if d.get("withdrawals"):
        lines.append("")
        lines.append(f"── Aufgaben ({len(d['withdrawals'])}) ──")
        lines.append("  bibs: " + ",".join(str(b) for b in d["withdrawals"]))

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Rich-Renderer (optional)
# --------------------------------------------------------------------------- #
def render_rich(d: dict, console):  # pragma: no cover - optional
    from rich.table import Table
    from rich.text import Text
    from rich.panel import Panel
    from rich.columns import Columns

    console.clear()
    head = (f"Tour de France {d.get('year')}  ·  Etappe {d.get('stage')}  ·  "
            f"[bold]{d.get('status')}[/bold]  ({d.get('freshness')})")
    console.print(head, style="yellow")
    console.print()

    jerseys = d.get("jerseys") or {}
    if jerseys:
        jc = []
        for v in jerseys.values():
            jc.append(Text(f"{v.get('label','?')}: {v.get('name','?')}",
                           style="bold"))
        console.print(Columns(jc, padding=(0, 2)))
        console.print()

    t = Table(title="Virtual GC", show_lines=False)
    t.add_column("#", justify="right", style="cyan", no_wrap=True)
    t.add_column("Bib", justify="right")
    t.add_column("Name", style="bold")
    t.add_column("Team", style="dim")
    t.add_column("Gap", justify="right", style="yellow")
    t.add_column("B/P", justify="right")
    for e in (d.get("gc") or [])[:20]:
        extras = []
        if e.get("bonus_s"):
            extras.append(f"+{e['bonus_s']}s")
        if e.get("pen_s"):
            extras.append(f"-{e['pen_s']}s")
        gap_style = "green" if e["pos"] == 1 else "yellow"
        t.add_row(str(e["pos"]), str(e["bib"]), e["name"][:28],
                  (e.get("team") or "")[:22],
                  Text(fmt_gap(e.get("rel_s")), style=gap_style),
                  " ".join(extras))
    console.print(t)

    if d.get("top_n"):
        tt = Table(title="Top-Favoriten", show_lines=False)
        tt.add_column("#", justify="right")
        tt.add_column("Name", style="bold")
        tt.add_column("km/h", justify="right")
        tt.add_column("km→Ziel", justify="right")
        tt.add_column("Steig.", justify="right")
        for r in d["top_n"]:
            tt.add_row(f"#{r.get('rank','?')}", r["name"][:24],
                       fmt_kph(r.get("kph")),
                       f"{float(r['km_to_finish']):.1f}" if r.get("km_to_finish") is not None else "-",
                       f"{float(r['gradient']):+.0f}%" if r.get("gradient") is not None else "-")
        console.print(tt)

    if d.get("groups"):
        gt = Table(title="Live-Gruppen", show_lines=False)
        gt.add_column("Gruppe")
        gt.add_column("Größe", justify="right")
        gt.add_column("km/h", justify="right")
        gt.add_column("km→Ziel", justify="right")
        gt.add_column("Gap", justify="right", style="yellow")
        for g in d["groups"]:
            gt.add_row(f"[{g['order']}] {g['name']}", str(g["size"]),
                       fmt_kph(g.get("speed_kph")),
                       f"{float(g['remaining_km']):.0f}" if g.get("remaining_km") is not None else "-",
                       fmt_gap(g.get("gap_s")))
        console.print(gt)


# --------------------------------------------------------------------------- #
# Fetch + Loop
# --------------------------------------------------------------------------- #
def fetch_state(base_url: str, top: int | None) -> dict | None:
    url = base_url.rstrip("/") + "/state"
    if top:
        url += f"?top={top}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tdf_console"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[error] {url}: {e}", file=sys.stderr)
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TdF Terminal-Client")
    p.add_argument("--url", default="http://localhost:8000",
                   help="Server-URL (default: http://localhost:8000)")
    p.add_argument("--top", type=int, default=20,
                   help="Anzahl GC-Zeilen (default 20)")
    p.add_argument("--interval", type=float, default=2.0,
                   help="Poll-Intervall in Sekunden (default 2.0)")
    p.add_argument("--plain", action="store_true",
                   help="Keine rich-Formatierung erzwingen")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    use_rich = not args.plain
    console = None
    if use_rich:
        try:
            from rich.console import Console
            console = Console()
        except ImportError:
            use_rich = False

    try:
        while True:
            d = fetch_state(args.url, top=args.top)
            if d is None:
                pass
            elif use_rich and console is not None:
                render_rich(d, console)
            else:
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.write(render_plain(d))
                sys.stdout.write("\n")
                sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[stop] Beende Console.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
