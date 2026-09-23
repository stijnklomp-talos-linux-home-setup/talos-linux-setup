#!/usr/bin/env python3
"""Read-only capacity report for home-cluster-1.

Sources (kubectl only):
  - node capacity/allocatable      kubectl get nodes
  - pod requests/limits            kubectl get pods -A
  - actual CPU/RAM/root-FS usage   kubelet /stats/summary via API proxy
  - Longhorn pool + volumes        longhorn-system CRDs (optional)

Env:
  KUBECONFIG  path to kubeconfig (default /cfg/kubeconfig)
  KUBECTL     kubectl binary (default kubectl)
  WATCH       seconds between refreshes; unset or non-TTY = one snapshot
  COLUMNS     fallback width when stdout is not a TTY (default 150)
  TZ          timezone for the "updated" timestamp (image default Europe/London)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

KUBECONFIG_PATH = os.environ.get("KUBECONFIG", "/cfg/kubeconfig")
KUBECTL = os.environ.get("KUBECTL", "kubectl")

BIN_SUFFIXES = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6}
DEC_SUFFIXES = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
RESOURCES = ("cpu", "memory", "ephemeral-storage")


class ClusterError(RuntimeError):
    pass


def parse_cpu(value):
    if value is None:
        return 0.0
    value = str(value)
    if value.endswith("m"):
        return float(value[:-1]) / 1e3
    if value.endswith("u"):
        return float(value[:-1]) / 1e6
    if value.endswith("n"):
        return float(value[:-1]) / 1e9
    return float(value)


def parse_bytes(value):
    if value is None:
        return 0.0
    value = str(value)
    for suffix, multiplier in BIN_SUFFIXES.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    if value and value[-1] in DEC_SUFFIXES:
        return float(value[:-1]) * DEC_SUFFIXES[value[-1]]
    return float(value)


def parse_resource(name, value):
    return parse_cpu(value) if name == "cpu" else parse_bytes(value)


def run_kubectl(args, timeout=60):
    cmd = [KUBECTL] + list(args)
    if KUBECONFIG_PATH and os.path.exists(KUBECONFIG_PATH):
        cmd += ["--kubeconfig", KUBECONFIG_PATH]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ClusterError(f"kubectl not found ({KUBECTL})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"kubectl timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise ClusterError(f"kubectl {' '.join(args)} failed: {message}")
    return proc.stdout


def kubectl_json(*args, timeout=60):
    return json.loads(run_kubectl(["get", *args, "-o", "json"], timeout=timeout))


def node_roles(labels):
    roles = []
    for role in ("control-plane", "ci", "worker"):
        if f"node-role.kubernetes.io/{role}" in labels:
            roles.append(role)
    return roles or ["worker"]


def fetch_summary(node):
    try:
        raw = run_kubectl(
            ["get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary", "--request-timeout=12s"],
            timeout=20,
        )
        return json.loads(raw)
    except (ClusterError, json.JSONDecodeError):
        return None


def pod_resources(pod, key):
    totals = dict.fromkeys(RESOURCES, 0.0)
    inits = dict.fromkeys(RESOURCES, 0.0)
    for container in pod["spec"].get("containers") or []:
        request = (container.get("resources") or {}).get(key) or {}
        for name, value in request.items():
            if name in totals:
                totals[name] += parse_resource(name, value)
    for container in pod["spec"].get("initContainers") or []:
        request = (container.get("resources") or {}).get(key) or {}
        for name, value in request.items():
            if name in inits:
                inits[name] = max(inits[name], parse_resource(name, value))
    return {name: max(totals[name], inits[name]) for name in RESOURCES}


def gather_nodes():
    nodes = []
    for item in kubectl_json("nodes")["items"]:
        meta, status = item["metadata"], item["status"]
        labels = meta.get("labels") or {}
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions") or [])
        nodes.append(
            {
                "name": meta["name"],
                "roles": node_roles(labels),
                "ready": ready,
                "cpu_cap": parse_cpu(status["capacity"].get("cpu")),
                "cpu_alloc": parse_cpu(status["allocatable"].get("cpu")),
                "mem_cap": parse_bytes(status["capacity"].get("memory")),
                "mem_alloc": parse_bytes(status["allocatable"].get("memory")),
                "fs_cap": parse_bytes(status["capacity"].get("ephemeral-storage")),
                "kubelet": (status.get("nodeInfo") or {}).get("kubeletVersion", "?"),
                "os": (status.get("nodeInfo") or {}).get("osImage", ""),
                "used": dict.fromkeys(RESOURCES, 0.0),
                "req": dict.fromkeys(RESOURCES, 0.0),
                "lim": dict.fromkeys(RESOURCES, 0.0),
                "pods": 0,
                "summary": False,
                "fs_used": None,
            }
        )
    nodes.sort(key=lambda n: (n["roles"], n["name"]))
    return nodes


def gather_pods(nodes):
    by_name = {n["name"]: n for n in nodes}
    totals = {"req": dict.fromkeys(RESOURCES, 0.0), "lim": dict.fromkeys(RESOURCES, 0.0), "pending": 0}
    for pod in kubectl_json("pods", "-A")["items"]:
        if (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed"):
            continue
        requests = pod_resources(pod, "requests")
        limits = pod_resources(pod, "limits")
        for name in RESOURCES:
            totals["req"][name] += requests[name]
            totals["lim"][name] += limits[name]
        node = by_name.get(pod["spec"].get("nodeName"))
        if node is None:
            totals["pending"] += 1
            continue
        node["pods"] += 1
        for name in RESOURCES:
            node["req"][name] += requests[name]
            node["lim"][name] += limits[name]
    return totals


def gather_usage(nodes):
    for node in nodes:
        summary = fetch_summary(node["name"])
        if not summary:
            continue
        node["summary"] = True
        stats = summary.get("node") or {}
        cpu = stats.get("cpu") or {}
        memory = stats.get("memory") or {}
        fs = stats.get("fs") or {}
        node["used"]["cpu"] = (cpu.get("usageNanoCores") or 0.0) / 1e9
        node["used"]["memory"] = memory.get("workingSetBytes") or memory.get("usageBytes") or 0.0
        node["used"]["ephemeral-storage"] = fs.get("usedBytes") or 0.0
        node["fs_used"] = fs.get("usedBytes")
        if fs.get("capacityBytes"):
            node["fs_cap"] = fs["capacityBytes"]
    return nodes


def gather_longhorn():
    try:
        nodes = kubectl_json("-n", "longhorn-system", "nodes.longhorn.io")["items"]
    except ClusterError:
        return None
    pool = {}
    for item in nodes:
        name = item["metadata"]["name"]
        status = item.get("status") or {}
        total = {"max": 0.0, "scheduled": 0.0, "available": 0.0, "model": "", "disk": ""}
        for disk_name, disk in (status.get("diskStatus") or {}).items():
            total["max"] += disk.get("storageMaximum") or 0
            total["scheduled"] += disk.get("storageScheduled") or 0
            total["available"] += disk.get("storageAvailable") or 0
            health = (disk.get("healthData") or {}).get(disk_name) or {}
            total["model"] = health.get("modelName") or disk.get("modelName") or total["model"]
            total["disk"] = (disk_name or "").split("/")[-1] or total["disk"]
        pool[name] = total
    return pool


def gather_volumes():
    try:
        items = kubectl_json("-n", "longhorn-system", "volumes.longhorn.io")["items"]
    except ClusterError:
        return []
    volumes = []
    for item in items:
        status = item.get("status") or {}
        kube = status.get("kubernetesStatus") or {}
        volumes.append(
            {
                "name": item["metadata"]["name"],
                "pvc": f"{kube.get('namespace', '?')}/{kube.get('pvcName') or item['metadata']['name']}",
                "state": status.get("state") or "?",
                "robustness": status.get("robustness") or "?",
                "requested": float((item.get("spec") or {}).get("size") or 0),
                "actual": float(status.get("actualSize") or 0),
            }
        )
    volumes.sort(key=lambda v: v["requested"], reverse=True)
    return volumes


def fmt_bytes(value):
    if value is None:
        return "n/a"
    gib = value / 1024**3
    return f"{gib:,.2f}" if abs(gib) < 10 else f"{gib:,.1f}"


def fmt_cpu(value):
    return "n/a" if value is None else f"{value:,.2f}"


def shorten(text, limit):
    if limit <= 1:
        return "…"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def pressure_style(fraction):
    return "green" if fraction < 0.60 else "yellow" if fraction < 0.85 else "red"


def pct_text(fraction, style=None):
    percent = fraction * 100
    label = f"{percent:,.1f}%" if percent < 10 else f"{percent:,.0f}%"
    return Text(label, style=style or pressure_style(fraction))


def make_bar(fractions, chars, styles, width=36, marker=None):
    clamped, used = [], 0.0
    for fraction in fractions:
        clamped.append(max(0.0, min(fraction, 1.0 - used)))
        used += clamped[-1]
    clamped.append(max(0.0, 1.0 - used))
    chars = list(chars) + ["░"]
    styles = list(styles) + ["grey35"]
    raw = [fraction * width for fraction in clamped]
    counts = [int(value) for value in raw]
    for index in sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)[: width - sum(counts)]:
        counts[index] += 1
    cells = []
    for count, char, style in zip(counts, chars, styles):
        cells += [(char, style)] * count
    if marker and marker >= 0.02 and width:
        position = min(width - 1, max(0, int(round(marker * width)) - 1))
        cells[position] = ("┃", "magenta")
    bar = Text()
    for char, style in cells:
        bar.append(char, style=style)
    return bar


def layout_for(width):
    return {
        "width": width,
        "header_columns": 4 if width >= 120 else 2 if width >= 60 else 1,
        "node_alias": width < 96,
        "show_role": width >= 92,
        "show_limit": width >= 104,
        "show_req": width >= 52,
        "show_alloc": width >= 42,
        "show_pods": width >= 84,
        "show_disk": width >= 118,
        "show_capacity": width >= 96,
        "show_longhorn": width >= 68,
        "show_provisioned": width >= 104,
        "show_free": width >= 78,
        "show_volume_state": width >= 74,
        "show_volume_robustness": width >= 94,
        "show_volume_actual": width >= 44,
        "show_volume_usage": width >= 64,
    }


def role_label(roles):
    role = "+".join(roles)
    if len(role) <= 13:
        return role
    return role.replace("control-plane", "cp").replace("worker", "w")


def node_label(name, layout):
    return name.removeprefix("talos-") if layout["node_alias"] else name


def fit_bar(width, fixed, maximum, minimum=6):
    return max(minimum, min(maximum, width - fixed))


def header_panel(context, nodes, totals, longhorn, layout):
    width = layout["width"]
    kubelet = nodes[0]["kubelet"] if nodes else "?"
    os_image = nodes[0]["os"].replace("Talos (", "").replace(")", "") if nodes else "?"
    local_now = datetime.now().astimezone()
    now = local_now.strftime("%H:%M:%S")
    if width >= 48:
        now += f" {local_now.strftime('%Z')}"
    if width >= 90:
        title = f"[bold]{context}[/] · {len(nodes)} nodes · K8s {kubelet} · {os_image}"
    elif width >= 50:
        title = f"[bold]{context}[/] · {len(nodes)} nodes"
    else:
        title = f"[bold]{context}[/]"

    cpu_alloc = sum(n["cpu_alloc"] for n in nodes)
    mem_alloc = sum(n["mem_alloc"] for n in nodes)
    fs_cap = sum(n["fs_cap"] for n in nodes)
    fs_used = sum(n["fs_used"] or 0 for n in nodes)
    cpu_used = sum(n["used"]["cpu"] for n in nodes)
    mem_used = sum(n["used"]["memory"] for n in nodes)
    lh_max = sum(v["max"] for v in (longhorn or {}).values())
    lh_sched = sum(v["scheduled"] for v in (longhorn or {}).values())

    def suffix(text, label):
        text.append(f" {label}", style="grey62")
        return text

    def stat(label, value, detail):
        text = Text(label, style="bold")
        text.append("  ")
        text.append_text(value)
        if detail is not None:
            text.append(" · ", style="grey62")
            text.append_text(detail)
        return text

    pending = totals["pending"]
    stats = [
        stat("CPU", suffix(pct_text(cpu_used / cpu_alloc if cpu_alloc else 0), "used"), suffix(pct_text(totals["req"]["cpu"] / cpu_alloc if cpu_alloc else 0, "cyan"), "req")),
        stat("MEM", suffix(pct_text(mem_used / mem_alloc if mem_alloc else 0), "used"), suffix(pct_text(totals["req"]["memory"] / mem_alloc if mem_alloc else 0, "cyan"), "req")),
        stat("DISK", suffix(pct_text(fs_used / fs_cap if fs_cap else 0), "used"), suffix(pct_text(lh_sched / lh_max if lh_max else 0, "cyan"), "prov")),
        stat("PODS", Text(f"{sum(n['pods'] for n in nodes)} running", style="bold"), Text(f"{pending} pending", style="grey62") if pending else None),
    ]

    grid = Table.grid(padding=(0, 2))
    columns = layout["header_columns"]
    for _ in range(columns):
        grid.add_column(vertical="top")
    for start in range(0, len(stats), columns):
        grid.add_row(*stats[start : start + columns])

    legend = Text("bar ", style="grey62")
    parts = [("█", "green", "used"), ("▓", "cyan", "req"), ("░", "grey35", "free")]
    if width >= 110:
        parts.append(("┃", "magenta", "limit"))
    for char, style, label in parts:
        legend.append(char, style=style)
        legend.append(f" {label}  ", style="grey62")
    if width >= 60 and layout["show_longhorn"]:
        legend.append("storage: ", style="grey62")
        legend.append("█", style="cyan")
        legend.append(" Longhorn  ", style="grey62")
        legend.append("▒", style="blue")
        legend.append(" other", style="grey62")
    return Panel(grid, title=title, subtitle=f"updated {now}", subtitle_align="right", border_style="grey50"), legend


def resource_table(title, nodes, totals, resource, unit, layout):
    show_role, show_limit, show_pods = layout["show_role"], layout["show_limit"], layout["show_pods"]
    show_req, show_alloc = layout["show_req"], layout["show_alloc"]
    columns = [("node", "left")]
    if show_role:
        columns.append(("role", "left"))
    columns.append(("used", "right"))
    if show_req:
        columns.append(("req", "right"))
    if show_limit:
        columns.append(("limit", "right"))
    if show_alloc:
        columns.append(("alloc", "right"))
    columns.append(("usage", "left"))
    if show_pods:
        columns.append(("pods", "right"))

    table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", border_style="grey50", expand=False)
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)

    def fmt(value):
        return fmt_cpu(value) if unit == "cores" else fmt_bytes(value)

    def add_row(node, used, req, lim, alloc, pods, style=None, bold=False):
        fractions = (used / alloc if alloc else 0, max(0.0, req - used) / alloc if alloc else 0)
        marker = lim / alloc if alloc and lim > used else None
        row = [
            Text(node_label(node["name"], layout) if node else "TOTAL", style="bold" if bold else style),
        ]
        if show_role:
            row.append(Text(role_label(node["roles"]) if node else "", style="bold" if bold else style or "grey62"))
        row.append(Text(fmt(used) if node is None or node["summary"] else "n/a", style="bold" if bold else style))
        if show_req:
            row.append(Text(fmt(req), style="bold" if bold else style))
        if show_limit:
            row.append(Text(fmt(lim), style="bold" if bold else style))
        if show_alloc:
            row.append(Text(fmt(alloc), style="bold" if bold else style or "grey62"))
        row.append(make_bar(fractions, ["█", "▓"], [pressure_style(used / alloc if alloc else 0), "cyan"], width=bar_width, marker=marker))
        if show_pods:
            row.append(Text(str(pods), style="bold" if bold else style))
        table.add_row(*row)

    node_width = 9 if layout["node_alias"] else 15
    fixed = (
        node_width
        + (13 if show_role else 0)
        + 7
        + (7 if show_req else 0)
        + (7 if show_limit else 0)
        + (7 if show_alloc else 0)
        + 2 * len(columns)
        + 4
    )
    bar_width = fit_bar(layout["width"], fixed, 32)

    for node in nodes:
        alloc, used, req, lim = node["alloc"], node["used"][resource], node["req"][resource], node["lim"][resource]
        add_row(node, used, req, lim, alloc, node["pods"], style="dim" if not node["ready"] else None)
    used = sum(n["used"][resource] for n in nodes)
    req, lim = totals["req"][resource], totals["lim"][resource]
    add_row(None, used, req, lim, sum(n["alloc"] for n in nodes), sum(n["pods"] for n in nodes), bold=True)
    return table


def storage_table(nodes, longhorn, layout):
    show_disk = layout["show_disk"]
    show_capacity = layout["show_capacity"]
    show_longhorn = layout["show_longhorn"]
    show_provisioned = layout["show_provisioned"]
    show_free = layout["show_free"]

    columns = [("node", "left")]
    if show_disk:
        columns.append(("disk", "left"))
    if show_capacity:
        columns.append(("capacity", "right"))
    columns.append(("used", "right"))
    if show_longhorn:
        columns.append(("Longhorn", "right"))
    if show_provisioned:
        columns.append(("provisioned", "right"))
    if show_free:
        columns.append(("free", "right"))
    columns.append(("usage", "left"))

    title = "STORAGE · GiB"
    if layout["width"] >= 110:
        title += " · node root filesystem (Longhorn data lives inside it)"
    elif layout["width"] >= 60:
        title += " · node root filesystem"
    table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", border_style="grey50", expand=False)
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)

    def add_row(node, pool, cap, used, style=None, bold=False):
        lh_used = min(max(0.0, pool["max"] - pool["available"]), used or 0.0) if pool else 0.0
        other = max(0.0, (used or 0.0) - lh_used)
        used_frac = (used or 0.0) / cap if cap else 0
        row = [Text(node_label(node["name"], layout) if node else "TOTAL", style="bold" if bold else style)]
        if show_disk:
            model = (pool or {}).get("model")
            label = shorten(model, 26) if model else ("—" if node else "")
            row.append(Text(label, style="bold" if bold else style or "grey62"))
        if show_capacity:
            row.append(Text(fmt_bytes(cap), style="bold" if bold else style or "grey62"))
        row.append(Text(fmt_bytes(used), style="bold" if bold else style))
        if show_longhorn:
            row.append(Text(fmt_bytes(lh_used), style="bold" if bold else style or "cyan") if pool else Text("—", style="grey62"))
        if show_provisioned:
            row.append(Text(fmt_bytes(pool["scheduled"]), style="bold" if bold else style) if pool else Text("—", style="grey62"))
        if show_free:
            row.append(Text(fmt_bytes(pool["available"]), style="bold" if bold else style) if pool else Text("—", style="grey62"))
        if show_longhorn:
            bar = make_bar((lh_used / cap if cap else 0, other / cap if cap else 0), ["█", "▒"], ["cyan", "blue"], width=bar_width)
        else:
            bar = make_bar((used_frac,), ["█"], [pressure_style(used_frac)], width=bar_width)
        row.append(bar + Text(f" {used_frac * 100:,.0f}%", style="bold" if bold else pressure_style(used_frac)))
        table.add_row(*row)

    node_width = 9 if layout["node_alias"] else 15
    fixed = (
        node_width
        + (27 if show_disk else 0)
        + (9 if show_capacity else 0)
        + 7
        + (9 if show_longhorn else 0)
        + (12 if show_provisioned else 0)
        + (7 if show_free else 0)
        + 2 * len(columns)
        + 6
    )
    bar_width = fit_bar(layout["width"], fixed, 28)

    for node in nodes:
        pool = (longhorn or {}).get(node["name"])
        add_row(node, pool, node["fs_cap"], node["fs_used"], style="dim" if not node["ready"] else None)
    cap = sum(n["fs_cap"] for n in nodes)
    used = sum(n["fs_used"] or 0 for n in nodes)
    pools = list((longhorn or {}).values())
    lh_used = min(sum(max(0.0, p["max"] - p["available"]) for p in pools), used)
    total_pool = {
        "max": sum(p["max"] for p in pools),
        "available": sum(p["available"] for p in pools),
        "scheduled": sum(p["scheduled"] for p in pools),
    }
    add_row(None, total_pool if pools else None, cap, used, bold=True)
    return table


def volumes_table(volumes, layout):
    if not volumes:
        return None
    show_state, show_robustness, show_usage, show_actual = (
        layout["show_volume_state"],
        layout["show_volume_robustness"],
        layout["show_volume_usage"],
        layout["show_volume_actual"],
    )
    columns = [("pvc", "left")]
    if show_state:
        columns.append(("state", "left"))
    if show_robustness:
        columns.append(("robustness", "left"))
    columns.append(("requested", "right"))
    if show_actual:
        columns.append(("actual", "right"))
    if show_usage:
        columns.append(("usage", "left"))
    title = "LONGHORN VOLUMES"
    if layout["width"] >= 60:
        title += " · requested vs actually used"
    table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", border_style="grey50", expand=False)
    for name, justify in columns:
        table.add_column(name, justify=justify, no_wrap=True)
    other = (
        (10 if show_state else 0)
        + (11 if show_robustness else 0)
        + 11
        + (10 if show_actual else 0)
        + (5 if show_usage else 0)
        + 2 * len(columns)
        + 2
    )
    pvc_limit = max(11, layout["width"] - other)
    for volume in volumes:
        fraction = volume["actual"] / volume["requested"] if volume["requested"] else 0
        style = "green" if volume["robustness"] in ("healthy", "unknown") else "yellow"
        row = [Text(shorten(volume["pvc"], pvc_limit))]
        if show_state:
            row.append(Text(volume["state"], style=style))
        if show_robustness:
            row.append(Text(volume["robustness"], style=style))
        row.append(Text(f"{fmt_bytes(volume['requested'])} GiB", justify="right"))
        if show_actual:
            row.append(Text(f"{fmt_bytes(volume['actual'])} GiB", justify="right"))
        if show_usage:
            row.append(pct_text(fraction, "cyan"))
        table.add_row(*row)
    return table


def footnote(width):
    tiers = (
        "req = guaranteed minimum · limit = max burst, when set (past it: CPU throttled, memory killed)",
        "req = guaranteed · limit = max burst, when set (past it: CPU throttled, memory killed)",
        "req = guaranteed · limit = max burst, when set (over: CPU throttles, memory kills)",
        "req = guaranteed · limit = max burst, when set",
        "req = guaranteed · limit = max, if set",
        "req = min · limit = max, if set",
        "req=min limit=max if set",
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


def render(console, clear=False):
    nodes = gather_nodes()
    totals = gather_pods(nodes)
    gather_usage(nodes)
    longhorn = gather_longhorn()
    volumes = gather_volumes()

    try:
        context = run_kubectl(["config", "current-context"], timeout=15).strip() or "cluster"
    except ClusterError:
        context = "cluster"

    layout = layout_for(console.width)
    panel, legend = header_panel(context, nodes, totals, longhorn, layout)
    width = layout["width"]
    cpu_alloc, cpu_cap = sum(n["cpu_alloc"] for n in nodes), sum(n["cpu_cap"] for n in nodes)
    mem_alloc, mem_cap = sum(n["mem_alloc"] for n in nodes), sum(n["mem_cap"] for n in nodes)
    cpu_title = "CPU"
    if width >= 32:
        cpu_title = f"CPU · {cpu_alloc:,.2f}/{cpu_cap:,.2f} cores"
    if width >= 42:
        cpu_title = f"CPU · {cpu_alloc:,.2f} cores allocatable"
    if width >= 62:
        cpu_title = f"CPU · {cpu_alloc:,.2f} of {cpu_cap:,.2f} cores allocatable"
    mem_title = "MEMORY"
    if width >= 32:
        mem_title = f"MEMORY · {fmt_bytes(mem_alloc)}/{fmt_bytes(mem_cap)} GiB"
    if width >= 46:
        mem_title = f"MEMORY · {fmt_bytes(mem_alloc)} GiB allocatable"
    if width >= 68:
        mem_title = f"MEMORY · {fmt_bytes(mem_alloc)} of {fmt_bytes(mem_cap)} GiB allocatable"
    cpu_view = resource_table(
        cpu_title,
        [dict(n, alloc=n["cpu_alloc"]) for n in nodes],
        totals,
        "cpu",
        "cores",
        layout,
    )
    mem_view = resource_table(
        mem_title,
        [dict(n, alloc=n["mem_alloc"]) for n in nodes],
        totals,
        "memory",
        "bytes",
        layout,
    )
    storage_view = storage_table(nodes, longhorn, layout)
    volumes_view = volumes_table(volumes, layout)

    parts = [panel, legend, "", cpu_view, mem_view, storage_view]
    if volumes_view:
        parts.append(volumes_view)
    note = footnote(width)
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
    try:
        clear = False
        while True:
            render(console, clear=clear)
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
