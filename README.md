# Talos Linux versioned install script

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
