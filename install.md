# Talos Linux versioned install instructions

## Usage

```sh
VERSION=v1.12.6 ./talos-linux.sh
```

## Flash ISO to external drive

1. Unmount drive if mounted

```sh
lsblk # Check drives
sudo umount /dev/sdc1 # Unmount if mounted, `/dev/sdc1` used as example
```

2. Flash ISO

```sh
zstdcat metal-amd64.iso | sudo dd of=/dev/sdc bs=4M status=progress oflag=sync # `/dev/sdc` used as example
```

## Follow further controller node instructions

https://docs.siderolabs.com/talos/v1.12/getting-started/getting-started

## Add worker node to cluster

Find the disk to use:

```sh
talosctl --talosconfig=./talosconfig get disks --insecure -n $WORKER_IP[1] -o yaml
```

Update the `install:disk` in `worker.yaml` and then apply:

```sh
talosctl apply-config --insecure --nodes $WORKER_IP[1] --file worker.yaml
```
