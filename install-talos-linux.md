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
2. Find the disk to use:

```sh
talosctl --talosconfig=./talosconfig get disks --insecure -n $WORKER_IP[1] -o yaml
```

3. Update the `install:disk` in `worker.yaml` and then apply:

```sh
talosctl apply-config --insecure --nodes $WORKER_IP[1] --file worker.yaml
```
