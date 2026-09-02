<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: opencode | Last updated: 2026-09-02 -->

# AGENTS.md (talos-linux-setup)

Tooling to install and manage the `home-cluster-1` Talos Linux cluster — the entry point for node bring-up, worker joins, and cluster spin up/down. Source-code only: never run these scripts against the live cluster unless asked.

## Cluster facts (fixed, do not invent)

| Node | Role | IP |
|------|------|----|
| home-cluster-1 (talos-pve-g5b) | control plane | 192.168.1.107 |
| home-cluster-1 (talos-szo-afm) | worker | 192.168.1.108 |

- Versions: Talos `v1.12.6`, Kubernetes `v1.35.2`. Update only after verifying current stable upstream.
- Connect flow (kubeconfig / TALOSCONFIG): `devops-wiki/resources/instructions/kubernetes.md` — not in this repo.

## Files

| File | What it is |
|------|-----------|
| `README.md` | Main usage guide: install, add worker, spin up/down. |
| `install-talos-linux.md` | Versioned install steps: flash ISO, bootstrap, join worker. |
| `install-talosctl.sh` | Downloads + checksum-validates talosctl (`VERSION=v1.12.6 ./install-talosctl.sh`). |
| `add-worker-node.yaml` | **Bash script** (despite `.yaml`): lists disks, auto-detects the internal (sata/nvme) disk, applies `worker.yaml` with `install.disk` patched per node, joins it. |
| `startup-worker-nodes.yaml` | **Bash script**: wait for Ready + uncordon workers (needs kubectl). |
| `drain-worker-nodes.yaml` | **Bash script**: cordon + drain workers before shutdown (needs kubectl). |

## Rules

| Rule | Detail |
|------|--------|
| `.yaml` = bash | All three `*.yaml` worker scripts are executable shell scripts with `#!/usr/bin/env bash` — run them directly, never `kubectl apply`. |
| Secrets stay local | `talosconfig` and `worker.yaml` live in this dir but are **not committed** (node join secrets). Never add them, or real IPs of future nodes, to git. |
| No cluster access | Do not run `talosctl`/`kubectl` against `home-cluster-1` unless explicitly asked — workspace is source-code only. |
| Static IP first | A new worker needs a router DHCP reservation **before** joining (`install-talos-linux.md`). Never fabricate a node IP. |
| Verify disk before apply | `add-worker-node.yaml` prints the node's disks, auto-detects the internal disk (`transport: sata`/`nvme`, never `usb`/`loop0`) and applies it via `apply-config --config-patch` — `worker.yaml` itself stays generic. Override: `WORKER_DISK=/dev/disk/by-id/...`. |
| Env overrides | Scripts honor `WORKER_IP`, `WORKER_CONFIG`, `TALOSCONFIG`, `WORKER_DISK`, `CONTROL_PLANE_IP`; document new ones in README. |
| Docs sync | Keep `README.md` ↔ `install-talos-linux.md` consistent when steps change (script names, env vars, versions). |

## Commands (verify paths before claiming done)

| Task | Command |
|------|---------|
| Install talosctl | `VERSION=v1.12.6 ./install-talosctl.sh` |
| Add worker node | `./add-worker-node.yaml <NODE_IP>` |
| Spin cluster up (workers) | `./startup-worker-nodes.yaml` |
| Spin cluster down (workers) | `./drain-worker-nodes.yaml` then `talosctl shutdown -n $WORKER_IP` |
| Spin cluster down (control plane, last) | `talosctl shutdown -n $CONTROL_PLANE_IP` |

## Checklist (before finishing any change)

- [ ] No `talosconfig`/`worker.yaml`/node secrets staged
- [ ] Script usage and env vars match README
- [ ] IPs/versions match the cluster-facts table above; no fabricated values