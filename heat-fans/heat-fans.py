#!/usr/bin/env python3
"""Read-only heat and fan report for home-cluster-1.

Node sensors from /sys/class/hwmon via the Talos API (talosctl):
CPU (coretemp), board (acpitz/nct6686), disks (drivetemp/nvme) and fans (nct6686).
Unconnected thermistors (chip max reading, no critical temp) are hidden.
Bars show the temperature on a 0-100 °C scale, colored by a smooth blue-to-red
gradient that shifts on every degree.

Env:
  TALOSCONFIG  path to talosconfig (default /cfg/talosconfig)
  TALOSCTL     talosctl binary (default talosctl)
  WATCH        seconds between refreshes; unset or non-TTY = one snapshot
  VIEW         simple (default) = hottest per component, complex = every sensor
  COLUMNS      fallback width when stdout is not a TTY (default 150)
  TZ           timezone for the "updated" timestamp (image default Europe/London)
"""

import colorsys
import concurrent.futures
import copy
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

TALOSCONFIG_PATH = os.environ.get("TALOSCONFIG", "/cfg/talosconfig")
TALOSCTL = os.environ.get("TALOSCTL", "talosctl")
HWMON_ROOT = "/sys/class/hwmon"
TEMP_CHART_MAX = 100.0
MAX_WORKERS = 24

SECTION_BOX = copy.copy(box.SIMPLE_HEAD)
SECTION_BOX.row_horizontal = "─"
SECTION_BOX.row_cross = "─"

COMPONENT_CHIPS = (
    ("CPU", {"coretemp", "k10temp", "zenpower", "cpu_thermal", "x86_pkg_temp", "peci"}),
    ("GPU", {"amdgpu", "i915", "nouveau", "radeon", "nvidia", "xe"}),
    ("RAM", {"jc42", "spd5118", "ee1004"}),
    ("Storage", {"drivetemp", "nvme", "sas"}),
)
COMPONENT_ORDER = ("CPU", "GPU", "RAM", "Storage", "Board")

TEMP_COLOR_MIN = 30.0
TEMP_COLOR_MAX = 90.0


class ClusterError(RuntimeError):
    pass


def run_talosctl(args, timeout=30):
    cmd = [TALOSCTL] + list(args)
    if TALOSCONFIG_PATH and os.path.exists(TALOSCONFIG_PATH):
        cmd += ["--talosconfig", TALOSCONFIG_PATH]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ClusterError(f"talosctl not found ({TALOSCTL})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"talosctl timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise ClusterError(f"talosctl {' '.join(args)} failed: {message}")
    return proc.stdout


def gather_members():
    raw = run_talosctl(["get", "members", "-o", "json"], timeout=30)
    decoder = json.JSONDecoder()
    members, index = [], 0
    while index < len(raw):
        while index < len(raw) and raw[index] in " \t\r\n":
            index += 1
        if index >= len(raw):
            break
        obj, index = decoder.raw_decode(raw, index)
        spec = obj.get("spec") or {}
        addresses = [a for a in spec.get("addresses") or [] if ":" not in a]
        if not addresses:
            continue
        members.append(
            {
                "name": spec.get("hostname") or obj["metadata"]["id"],
                "ip": addresses[0],
                "type": spec.get("machineType") or "worker",
            }
        )
    members.sort(key=lambda m: (m["type"] != "controlplane", m["name"]))
    return members


def cluster_context():
    try:
        for line in run_talosctl(["config", "info"], timeout=15).splitlines():
            if line.startswith("Current context:"):
                return line.split(":", 1)[1].strip() or "cluster"
    except ClusterError:
        pass
    return "cluster"


def list_dir(node, path):
    out = run_talosctl(["--nodes", node, "ls", path], timeout=25)
    names = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] != ".":
            names.append(parts[1])
    return names


def read_file(node, path):
    try:
        return run_talosctl(["--nodes", node, "read", path], timeout=20).strip()
    except ClusterError:
        return None


def list_node(node):
    dirs = {}
    try:
        for entry in list_dir(node["ip"], HWMON_ROOT):
            if entry.startswith("hwmon"):
                base = f"{HWMON_ROOT}/{entry}"
                dirs[base] = list_dir(node["ip"], base)
    except ClusterError:
        return node, {}
    return node, dirs


def wanted_files(files):
    wanted = ["name"]
    for name in files:
        if name.startswith("temp") and name.endswith(("_input", "_label", "_crit")):
            wanted.append(name)
        elif name.startswith("fan") and name.endswith(("_input", "_label")):
            wanted.append(name)
    if "device" in files:
        wanted.append("device/model")
    return wanted


def indices(files, prefix):
    return sorted({int(m.group(1)) for f in files if (m := re.match(rf"{prefix}(\d+)_", f))})


def assemble(member, dirs, values):
    temps, fans = [], []
    for base, files in sorted(dirs.items()):
        chip = values.get((base, "name")) or base.rsplit("/", 1)[-1]
        model = (values.get((base, "device/model")) or "").strip()
        for i in indices(files, "temp"):
            raw = values.get((base, f"temp{i}_input"))
            if not raw:
                continue
            try:
                value = int(raw) / 1000.0
            except ValueError:
                continue
            crit_raw = values.get((base, f"temp{i}_crit")) or ""
            crit = int(crit_raw) / 1000.0 if crit_raw.isdigit() and int(crit_raw) > 0 else None
            if crit is None and value >= 127.0:
                continue
            label = values.get((base, f"temp{i}_label")) or f"temp{i}"
            if chip in ("drivetemp", "nvme") and model:
                sensor = f"{model} · {label}" if not label.startswith("temp") else model
            else:
                sensor = f"{chip} · {label}"
            temps.append({"node": member["name"], "chip": chip, "sensor": sensor, "value": value, "crit": crit})
        for i in indices(files, "fan"):
            raw = values.get((base, f"fan{i}_input"))
            if not raw:
                continue
            try:
                rpm = int(raw)
            except ValueError:
                continue
            label = values.get((base, f"fan{i}_label")) or f"fan{i}"
            fans.append({"node": member["name"], "sensor": f"{chip} · {label}", "rpm": rpm})
    return temps, fans


def gather():
    members = gather_members()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(members)))) as pool:
        listings = list(pool.map(list_node, members))

    jobs = [
        (member, base, name)
        for member, dirs in listings
        for base, files in dirs.items()
        for name in wanted_files(files)
    ]

    def do_read(job):
        member, base, name = job
        return job, read_file(member["ip"], f"{base}/{name}")

    values = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for (member, base, name), text in pool.map(do_read, jobs):
            values[(member["name"], base, name)] = text

    order = {member["name"]: index for index, member in enumerate(members)}
    temps, fans = [], []
    for member, dirs in listings:
        node_values = {key[1:]: value for key, value in values.items() if key[0] == member["name"]}
        node_temps, node_fans = assemble(member, dirs, node_values)
        temps += node_temps
        fans += node_fans
    temps.sort(key=lambda t: (order.get(t["node"], 99), -t["value"]))
    fans.sort(key=lambda f: (order.get(f["node"], 99), f["sensor"]))
    return members, temps, fans


def shorten(text, limit):
    if limit <= 1:
        return "…"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def component_of(chip):
    for component, chips in COMPONENT_CHIPS:
        if chip in chips:
            return component
    return "Board"


def role_labels(members):
    labels, worker = {}, 0
    for member in members:
        if member["type"] == "controlplane":
            labels[member["name"]] = "controller"
        else:
            worker += 1
            labels[member["name"]] = f"worker {worker}"
    return labels


def node_label(name, roles, layout):
    role = roles.get(name, name)
    if layout["width"] >= 130:
        return f"{role} · {name.removeprefix('talos-')}"
    return role


def temp_color(value):
    fraction = (value - TEMP_COLOR_MIN) / (TEMP_COLOR_MAX - TEMP_COLOR_MIN)
    fraction = max(0.0, min(fraction, 1.0))
    hue = 240.0 * (1.0 - fraction)
    red, green, blue = colorsys.hsv_to_rgb(hue / 360.0, 1.0, 1.0)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def make_bar(fraction, width, style):
    fraction = max(0.0, min(fraction, 1.0))
    filled = int(round(fraction * width))
    bar = Text()
    bar.append("█" * filled, style=style)
    bar.append("░" * (width - filled), style="grey35")
    return bar


def layout_for(width):
    return {
        "width": width,
        "role_width": 18 if width >= 130 else 10,
        "show_sensor": width >= 40,
        "show_crit": width >= 96,
        "show_bar": width >= 48,
        "show_fan_label": width >= 44,
        "show_fan_state": width >= 64,
        "sensor_width": 38 if width >= 120 else 28 if width >= 96 else 14,
        "header_columns": 2 if width >= 76 else 1,
    }


def footnote(width):
    tiers = (
        "bar = temperature (0–100 °C) · color = blue→green→yellow→red gradient (30–90 °C, shifts every degree) · 0 RPM = fan stopped",
        "bar = temperature (0–100 °C) · color = blue→red gradient (30–90 °C, every degree) · 0 RPM = fan stopped",
        "bar = temperature (0–100 °C) · color = blue→red by temperature (30–90 °C) · 0 RPM = stopped",
        "bar = temp (0–100 °C) · color = blue→red 30–90 °C · 0 RPM = stopped",
        "bar = temp 0–100 °C · 0 RPM = stopped",
        "0 RPM = stopped",
    )
    for text in tiers:
        if len(text) <= width:
            return Text(text, style="grey50")
    return None


def paint(console, parts, clear):
    if not clear:
        for part in parts:
            console.print(part)
        return
    with console.capture() as capture:
        for part in parts:
            console.print(part)
    frame = capture.get()
    height = console.height or 0
    if height and frame.count("\n") < height:
        console.file.write("\x1b[H" + frame + "\x1b[J")
    else:
        console.clear()
        console.file.write(frame)
    console.file.flush()


def header_panel(context, members, temps, fans, roles, layout):
    local_now = datetime.now().astimezone()
    now = local_now.strftime("%H:%M:%S")
    if layout["width"] >= 48:
        now += f" {local_now.strftime('%Z')}"
    title = f"[bold]{context}[/] · {len(members)} nodes"

    hottest = max(temps, key=lambda t: t["value"]) if temps else None
    stopped = [f for f in fans if f["rpm"] == 0]
    stats = []
    if hottest:
        detail = f"{roles.get(hottest['node'], hottest['node'])} · {component_of(hottest['chip'])}"
        stats.append(
            (
                "TEMP",
                Text(f"{hottest['value']:.0f}°C", style="white"),
                Text(shorten(detail, 32), style="grey62"),
            )
        )
    stats.append(
        (
            "FANS",
            Text(str(len(fans)), style="bold"),
            Text(f"{len(stopped)} stopped · {len(temps)} temp sensors", style="grey62"),
        )
    )
    grid = Table.grid(padding=(0, 3))
    columns = layout["header_columns"]
    for _ in range(columns):
        grid.add_column(vertical="top")
    cells = []
    for label, value, detail in stats:
        text = Text(label, style="bold")
        text.append("  ")
        text.append_text(value)
        if detail.plain:
            text.append(" · ", style="grey62")
            text.append_text(detail)
        cells.append(text)
    for start in range(0, len(cells), columns):
        grid.add_row(*cells[start : start + columns])
    return Panel(grid, title=title, subtitle=f"updated {now}", subtitle_align="right", border_style="grey50")


def temperature_table(temps, roles, layout):
    title = "TEMPERATURES · every sensor" if layout["width"] >= 44 else "TEMPERATURES"
    table = Table(
        title=title,
        box=SECTION_BOX,
        title_justify="left",
        border_style="grey50",
        expand=False,
    )
    columns = [("node", "left")]
    if layout["show_sensor"]:
        columns.append(("sensor", "left"))
    columns.append(("°C", "right"))
    if layout["show_crit"]:
        columns.append(("critical", "right"))
    if layout["show_bar"]:
        columns.append(("temperature", "left"))
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)

    fixed = (
        layout["role_width"]
        + (layout["sensor_width"] if layout["show_sensor"] else 0)
        + 7
        + (9 if layout["show_crit"] else 0)
        + 2 * len(columns)
        + 2
    )
    bar_width = max(8, min(28, layout["width"] - fixed))

    previous = None
    for temp in temps:
        if previous is not None and temp["node"] != previous:
            table.add_section()
        previous = temp["node"]
        row = [Text(node_label(temp["node"], roles, layout))]
        if layout["show_sensor"]:
            row.append(Text(shorten(temp["sensor"], layout["sensor_width"]), style="grey62"))
        row.append(Text(f"{temp['value']:.0f}°C", style="white"))
        if layout["show_crit"]:
            row.append(Text(f"{temp['crit']:.0f}°C" if temp["crit"] else "—", style="grey62"))
        if layout["show_bar"]:
            row.append(make_bar(temp["value"] / TEMP_CHART_MAX, bar_width, temp_color(temp["value"])))
        table.add_row(*row)
    return table


def summary_table(temps, roles, layout):
    title = "TEMPERATURES · hottest per component" if layout["width"] >= 46 else "TEMPERATURES"
    table = Table(
        title=title,
        box=SECTION_BOX,
        title_justify="left",
        border_style="grey50",
        expand=False,
    )
    columns = [("node", "left"), ("component", "left"), ("°C", "right")]
    if layout["show_bar"]:
        columns.append(("temperature", "left"))
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)

    fixed = layout["role_width"] + 10 + 7 + (2 * len(columns)) + 2
    bar_width = max(8, min(32, layout["width"] - fixed))

    node_order, hottest = {}, {}
    for temp in temps:
        node_order.setdefault(temp["node"], len(node_order))
        key = (temp["node"], component_of(temp["chip"]))
        if key not in hottest or temp["value"] > hottest[key]["value"]:
            hottest[key] = temp

    previous = None
    for key in sorted(hottest, key=lambda k: (node_order[k[0]], COMPONENT_ORDER.index(k[1]))):
        temp = hottest[key]
        if previous is not None and key[0] != previous:
            table.add_section()
        previous = key[0]
        row = [
            Text(node_label(temp["node"], roles, layout)),
            Text(key[1], style="grey62"),
            Text(f"{temp['value']:.0f}°C", style="white"),
        ]
        if layout["show_bar"]:
            row.append(make_bar(temp["value"] / TEMP_CHART_MAX, bar_width, temp_color(temp["value"])))
        table.add_row(*row)
    return table


def fan_table(fans, roles, layout):
    table = Table(title="FANS", box=SECTION_BOX, title_justify="left", border_style="grey50", expand=False)
    columns = [("node", "left")]
    if layout["show_fan_label"]:
        columns.append(("fan", "left"))
    columns.append(("rpm", "right"))
    if layout["show_fan_state"]:
        columns.append(("state", "left"))
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)
    previous = None
    for fan in fans:
        if previous is not None and fan["node"] != previous:
            table.add_section()
        previous = fan["node"]
        stopped = fan["rpm"] == 0
        row = [Text(node_label(fan["node"], roles, layout))]
        if layout["show_fan_label"]:
            row.append(Text(shorten(fan["sensor"], layout["sensor_width"]), style="grey62"))
        row.append(Text(str(fan["rpm"]), style="yellow" if stopped else None))
        if layout["show_fan_state"]:
            row.append(Text("stopped" if stopped else "spinning", style="yellow" if stopped else "green"))
        table.add_row(*row)
    return table


def render(console, clear=False, complex_view=False):
    context = cluster_context()
    members, temps, fans = gather()
    layout = layout_for(console.width)
    roles = role_labels(members)

    parts = [header_panel(context, members, temps, fans, roles, layout), ""]
    if temps:
        parts.append(temperature_table(temps, roles, layout) if complex_view else summary_table(temps, roles, layout))
    if fans:
        parts.append(fan_table(fans, roles, layout))
    if not temps and not fans:
        parts.append(Text("no hwmon sensors found on any node", style="yellow"))
    note = footnote(layout["width"])
    if note is not None:
        parts += ["", note]
    paint(console, parts, clear)


def main():
    if sys.stdout.isatty():
        console = Console(highlight=False)
    else:
        console = Console(highlight=False, width=max(24, int(os.environ.get("COLUMNS", "150"))))
    watch = os.environ.get("WATCH", "").strip()
    interval = None
    if watch:
        try:
            interval = max(1.0, float(watch))
        except ValueError:
            console.print(f"[yellow]ignoring invalid WATCH={watch!r}[/]")
    if interval and not sys.stdout.isatty():
        interval = None
    view = os.environ.get("VIEW", "simple").strip().lower()
    if view not in ("simple", "complex", "all", "full"):
        console.print(f"[yellow]ignoring invalid VIEW={view!r} (using simple)[/]")
        view = "simple"
    complex_view = view in ("complex", "all", "full")
    try:
        clear = False
        while True:
            render(console, clear=clear, complex_view=complex_view)
            if not interval:
                break
            time.sleep(interval)
            clear = True
    except ClusterError as exc:
        console.print(Panel(str(exc), title="cluster error", border_style="red"))
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
