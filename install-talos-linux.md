# Talos Linux versioned install instructions

## talosctl

```sh
VERSION=v1.12.6 ./install-talosctl.sh
```

## Cluster setup

### Flash ISO to external drive

1. Unmount drive if mounted

```sh
lsblk # Check drives
sudo umount /dev/sdc1 # Unmount if mounted, `/dev/sdc1` used as example
```

2. Download latest ISO

```sh
curl -fLO https://github.com/siderolabs/talos/releases/download/vx.x.x/metal-amd64.iso
```

3. Flash ISO

```sh
zstdcat metal-amd64.iso | sudo dd of=/dev/sdc bs=4M status=progress oflag=sync # `/dev/sdc` used as example
```

4. Verify

```sh
sudo lsblk -f /dev/sdc # Should show `TALOS_Vx_x_x partitions`, `/dev/sdc` used as example
```

### Follow further controller node instructions

https://docs.siderolabs.com/talos/v1.13/getting-started/getting-started

### Add worker node to cluster

1. Reserve a static IP in the router **before** joining.
2. Run the worker script. It lists the node's disks, auto-detects the internal (sata/nvme) drive, and applies `worker.yaml` with the install disk patched in — **no `worker.yaml` editing needed**:

```sh
WORKER_CONFIG=./worker.yaml ./add-worker-node.yaml 192.168.1.x
```

The install disk is picked per node at apply time. If the detection guesses wrong, override it:

```sh
WORKER_DISK=/dev/disk/by-id/... WORKER_CONFIG=./worker.yaml ./add-worker-node.yaml 192.168.1.x
```

3. Wait for the node to become Ready and uncordon it:

```sh
./startup-worker-nodes.yaml # requires kubectl
```

#### Manual flow (reference — what the script does)

The node lists *every* block device it sees, including the USB installer stick:

```sh
NODE_IP=192.168.1.x
talosctl --talosconfig=./talosconfig get disks --insecure -n $NODE_IP -o yaml
```

Read the output like this:

| Entry | What it is | Use it? |
|---|---|---|
| `id: loop0` / `/rootfs.sqsh`, readonly | the Talos ISO's own squashfs rootfs | ❌ ignore |
| `transport: usb` (label `TALOS_*`) | your installer stick | ❌ never — flashing to it wipes the stick |
| `transport: sata` or `nvme` | the node's internal disk | ✅ this is `install:disk` |

> **Don't take the first by-id in the raw list** — the USB stick also gets an `ata-` by-id (its bridge reports as ATA, e.g. `ata-SanDisk...`). A by-id only belongs to the internal disk when it sits under the `sata`/`nvme` block. This one-liner prints exactly those:

```sh
talosctl --talosconfig=./talosconfig get disks --insecure -n $NODE_IP -o yaml \
  | awk '/^    id: /{disk=$2; internal=""} /transport: sata|transport: nvme/{internal=1} internal && /by-id/{print disk": "$2}'
```

Example output:

```
sdb: /dev/disk/by-id/ata-Crucial_CT275MX300SSD1_17501A1825FB
sdb: /dev/disk/by-id/wwn-0x500a07511a1825fb
```

Apply with the disk patched in (no `worker.yaml` edit) — prefer the **stable by-id path** over `/dev/sdX`, which can shuffle between boots:

```sh
talosctl apply-config --insecure --nodes $NODE_IP --file worker.yaml \
  --config-patch 'machine:
  install:
    disk: /dev/disk/by-id/ata-Crucial_CT275MX300SSD1_17501A1825FB'
```
