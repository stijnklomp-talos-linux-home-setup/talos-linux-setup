# talos-linux-setup

Tooling to install and manage the `home-cluster-1` Talos cluster.

| Node | Role | IP |
|------|------|----|
| home-cluster-1 (talos-pve-g5b) | control plane | 192.168.1.107 |
| home-cluster-1 (talos-szo-afm) | worker | 192.168.1.108 |

Versions: Talos `v1.x.x`, Kubernetes `v1.x.x`.

## Prerequisites

- `talosctl` installed: see [install-talos-linux.md](./install-talos-linux.md#talosctl)
- How to connect: https://github.com/stijnklomp-talos-linux-home-setup/devops-wiki (instructions in `resources/instructions/kubernetes.md`)

## One-time install

1. See [install-talos-linux.md](./install-talos-linux.md#cluster-setup). This flashes the Talos ISO and bootstraps the control plane.

## Add new worker node

1. See [install-talos-linux.md](./install-talos-linux.md#add-worker-node-to-cluster). This joins the worker node(s).

## Spin cluster UP

1. Power on the machines.
2. The control plane boots and the workers join automatically.
3. Wait for the worker to be Ready and uncordon it:

```sh
./startup-worker-nodes.yaml # requires kubectl
```

## Spin cluster DOWN

1. Move workloads off the workers (cordon + drain):

```sh
./drain-worker-nodes.yaml # requires kubectl
```

2. Shut down the worker nodes:

```sh
for ip in 192.168.1.x 192.168.1.x
    talosctl shutdown -n $ip
end
```

Or automatically — finds all **Ready** worker IPs via kubectl (already-off nodes are skipped):

```sh
kubectl get nodes -o json \
  | jq -r '.items[] | select(.metadata.labels["node-role.kubernetes.io/control-plane"] == null and any(.status.conditions[]; .type == "Ready" and .status == "True")) | .status.addresses[] | select(.type == "InternalIP") | .address' \
  | xargs -n 1 talosctl shutdown -n
```

3. Once the workers are down, shut down the control plane:

```sh
talosctl shutdown -n 192.168.1.107
```

## Upgrade Talos Linux

Check the latest stable Talos version:

```sh
curl -s https://api.github.com/repos/siderolabs/talos/releases/latest | jq -r .tag_name
```

1. **Upgrade `talosctl` on your machine first** — it must be at least the version you're upgrading to (check with: `talosctl version --client`):

```sh
VERSION=v1.13.9 ./install-talosctl.sh
```

2. Run the upgrade script — it handles everything: etcd snapshot, control plane upgrade, health check, then each worker (cordon → drain → upgrade → uncordon):

```sh
./upgrade-talos.yaml v1.13.9
```

- Expected: a few minutes of cluster-API downtime while the single control plane reboots; workloads on workers keep running.
- Rollback (emergency only, if a node fails to boot): `talosctl rollback -n <node-ip>`.

## Upgrade Kubernetes

Separate step, run after the Talos upgrade. Check the latest stable Kubernetes version:

```sh
curl -s https://api.github.com/repos/kubernetes/kubernetes/releases/latest | jq -r .tag_name
```

Check which Kubernetes versions **your current Talos release supports**, what version the cluster is on now, and the latest patch of the highest supported minor:

```sh
./upgrade-k8s.yaml check
```

> The latest stable K8s may be newer than what your Talos version supports, and upgrades are one minor version at a time (1.35 → 1.36 → 1.37). Check the Talos/K8s support matrix before choosing.

A minor-only version auto-resolves to its latest patch:

```sh
./upgrade-k8s.yaml <K8S_VERSION> <CLUSTER_CONFIG_DIR> [ROOT_HCL]
# e.g. ./upgrade-k8s.yaml v1.36 cluster-config/ ./root.hcl # (resolves to latest patch version e.g. v1.36.4)
```

- `<CLUSTER_CONFIG_DIR>` — **required** (the script errors if omitted): the directory holding your stored `controlplane.yaml`/`worker.yaml` (shown above as `cluster-config/`; here: `home-cluster-1-config/`). After the upgrade the script syncs their component image versions (kube-apiserver/controller-manager/scheduler/kube-proxy/kubelet) to the new version.
- `[ROOT_HCL]` — optional: pass the `root.hcl` path (shown above as `./root.hcl`) to also sync its `k8s_version`. Omit to skip.

Runs a dry run first, then the real upgrade against the control plane.