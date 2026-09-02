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
- Export `CONTROL_PLANE_IP` and `WORKER_IP` env vars

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
for ip in $WORKER_IP
    talosctl shutdown -n $ip
end
```

3. Once the workers are down, shut down the control plane:

```sh
talosctl shutdown -n $CONTROL_PLANE_IP
```