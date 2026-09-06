<!-- FOR AI AGENTS - Human readability is a side effect, not a goal -->
<!-- Managed by agent: opencode | Last updated: 2026-09-02 -->

# AGENTS.md (talos-linux-setup)

Tooling to install and manage the `home-cluster-1` Talos Linux cluster — the entry point for node bring-up, worker joins, upgrades, and cluster spin up/down. The scripts may be run against the live cluster when the user asks.

## Default cluster (connecting)

**Unless the user specifies otherwise, the default cluster for `talosctl` and `kubectl` is always `home-cluster-1-config/`** (workspace root — `talosconfig` + `kubeconfig`, gitignored). Run both CLIs via Docker, mounting the config dir read-only:

```sh
docker run --rm -v "$PWD/home-cluster-1-config:/cfg:ro" \
  ghcr.io/siderolabs/talosctl:v1.13.9 \
  --talosconfig /cfg/talosconfig <args>

docker run --rm -v "$PWD/home-cluster-1-config:/cfg:ro" \
  registry.k8s.io/kubectl:v1.36.4 \
  --kubeconfig /cfg/kubeconfig <args>
```

`$PWD` assumes the workspace root; use the absolute path otherwise. Scripts default to `./talosconfig`/`./worker.yaml` — point `TALOSCONFIG`/`WORKER_CONFIG` at `home-cluster-1-config/` when running from this repo dir (or run from there).

**The cluster is LIVE and always in use (production-like) — never assume it is idle.** Read-only commands (`get`, `list`, `describe`, `logs`, `events`, `version`, `health`) are always allowed. **Every other command — any write/mutating action** (`apply-config`, `upgrade`, `reset`, `remove-member`, `drain`, `cordon`, `uncordon`, `reboot`, `shutdown`, `kubectl apply/delete/scale/exec`, etc.) **must be confirmed with the user first**, even if it looks harmless.

## Cluster facts (fixed, do not invent)

| Node | Role | IP |
|------|------|----|
| home-cluster-1 (talos-pve-g5b) | control plane | 192.168.1.107 |
| home-cluster-1 (talos-szo-afm) | worker — **CI node** (Tekton PipelineRuns) | 192.168.1.108 |
| home-cluster-1 (talos-llr-9ky) | worker — Lenovo | 192.168.1.106 |
| home-cluster-1 (talos-9an-o2z) | worker — Lenovo | 192.168.1.109 |

- Versions: Talos `v1.13.9`, Kubernetes `v1.36.4`. Update only after verifying current stable upstream.
- Node roles: set via `designate-node-roles.yaml` (control plane stays kube-system only; CI node is tainted `node-role.kubernetes.io/ci:NoSchedule`).
- Connect flow (kubeconfig / TALOSCONFIG): `devops-wiki/resources/instructions/kubernetes.md` — not in this repo.

## Files

| File | What it is |
|------|-----------|
| `README.md` | Main usage guide: install, add worker, spin up/down. |
| `install-talos-linux.md` | Versioned install steps: flash ISO, bootstrap, join worker. |
| `install-talosctl.sh` | Downloads + checksum-validates talosctl; replaces an older installed version automatically (`VERSION=v1.13.9 ./install-talosctl.sh`). |
| `add-worker-node.yaml` | **Bash script** (despite `.yaml`): lists disks, auto-detects the internal (sata/nvme) disk, applies `worker.yaml` with `install.disk` patched per node, joins it. |
| `upgrade-talos.yaml` | **Bash script**: upgrade all nodes to a target Talos version — etcd snapshot, CP first (single CP = brief API downtime), then workers one at a time with drain/uncordon. Workers use the Image Factory schematic installer (`WORKER_INSTALLER_IMAGE`) — it carries the iscsi-tools extension Longhorn needs; the plain installer drops it. Env: `TALOSCONFIG`, `CONTROL_PLANE_IP`, `WORKER_IPS`, `WORKER_INSTALLER_IMAGE` (default: kubectl discovery). |
| `upgrade-k8s.yaml` | **Bash script**: `check` mode prints supported K8s versions, current cluster kubelet version, and the latest patch of the max minor; upgrades via `talosctl upgrade-k8s` (dry-run first). `<CLUSTER_CONFIG_DIR>` arg is **required** (syncs stored `controlplane.yaml`/`worker.yaml` component images afterwards); optional `[ROOT_HCL]` arg syncs `k8s_version` too. |
| `startup-worker-nodes.yaml` | **Bash script**: wait for Ready + uncordon workers (needs kubectl). |
| `designate-node-roles.yaml` | **Bash script**: label/taint nodes by role (CI node = Dell .108, workers = Lenovos .106/.109); idempotent, re-run after node re-joins. |
| `drain-worker-nodes.yaml` | **Bash script**: cordon + drain workers before shutdown (needs kubectl). |

## Rules

| Rule | Detail |
|------|--------|
| `.yaml` = bash | All `*.yaml` scripts are executable shell scripts with `#!/usr/bin/env bash` — run them directly, never `kubectl apply`. |
| Secrets stay local | `talosconfig` and `worker.yaml` live in this dir but are **not committed** (node join secrets). Never add them, or real IPs of future nodes, to git. |
| Default cluster | `talosctl`/`kubectl` always target `home-cluster-1-config/` (workspace root) unless the user says otherwise — see "Default cluster" above. The cluster is live/production: read-only commands are always allowed; **every non-read command must be user-approved first**. |
| Static IP first | A new worker needs a router DHCP reservation **before** joining (`install-talos-linux.md`). Never fabricate a node IP. |
| Verify disk before apply | `add-worker-node.yaml` prints the node's disks, auto-detects the internal disk (`transport: sata`/`nvme`, never `usb`/`loop0`) and applies it via `apply-config --config-patch` — `worker.yaml` itself stays generic. Override: `WORKER_DISK=/dev/disk/by-id/...`. |
| Upgrade order | `upgrade-talos.yaml`: control plane → workers one at a time; never parallel upgrades (single CP, no quorum). `upgrade-k8s.yaml` only after Talos is at a supporting version; sync stored configs + `root.hcl` after it. |
| Env overrides | No env vars are required — scripts default to the cluster facts above (CP `192.168.1.107`, workers auto-discovered via kubectl). Optional overrides still honored: `WORKER_IP`, `WORKER_CONFIG`, `TALOSCONFIG`, `WORKER_DISK`, `WORKER_IPS`, `CONTROL_PLANE_IP`, `SNAPSHOT_RETENTION`, `WORKER_INSTALLER_IMAGE`. |
| Worker installer image | Workers run an Image Factory schematic installer (schematic `c9078f94...` = official extensions + `siderolabs/iscsi-tools`) — the plain `ghcr.io/siderolabs/installer` DROPS the extension on upgrade and Longhorn breaks. Never upgrade a worker with the plain installer. |
| Docs sync | Keep `README.md` ↔ `install-talos-linux.md` consistent when steps change (script names, env vars, versions). |

## Commands (verify paths before claiming done)

| Task | Command |
|------|---------|
| Connect to cluster | `docker run --rm -v $PWD/home-cluster-1-config:/cfg:ro ghcr.io/siderolabs/talosctl:v1.13.9 --talosconfig /cfg/talosconfig <args>` (kubectl: `registry.k8s.io/kubectl:v1.36.4 --kubeconfig /cfg/kubeconfig <args>`) |
| Install talosctl | `VERSION=v1.13.9 ./install-talosctl.sh` |
| Add worker node | `./add-worker-node.yaml <NODE_IP>` |
| Upgrade Talos (all nodes) | `./upgrade-talos.yaml <TALOS_VERSION>` |
| Upgrade Kubernetes | `./upgrade-k8s.yaml check` then `./upgrade-k8s.yaml <K8S_VERSION> <CLUSTER_CONFIG_DIR> [ROOT_HCL]` |
| Spin cluster up (workers) | `./startup-worker-nodes.yaml` |
| Designate node roles | `./designate-node-roles.yaml` (after adding/rebuilding a worker) |
| Spin cluster down (workers) | `./drain-worker-nodes.yaml` then shutdown each worker — README shows a manual IP list or kubectl auto-discovery |
| Spin cluster down (control plane, last) | `talosctl shutdown -n $CONTROL_PLANE_IP` |

## Checklist (before finishing any change)

- [ ] No `talosconfig`/`worker.yaml`/node secrets staged
- [ ] Script usage and env vars match README
- [ ] IPs/versions match the cluster-facts table above; no fabricated values