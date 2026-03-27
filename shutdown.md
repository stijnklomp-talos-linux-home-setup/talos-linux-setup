# Shutdown Talos nodes and achines

Drain worker nodes:

```sh
./drain-worker-nodes.yaml
```

Once the cluster is quiescent, shut down worker nodes:

```sh
for ip in $WORKER_IP
    talosctl shutdown -n $ip --talosconfig=./talosconfig
end
```

Once all worker nodes are down, shut down the controller nodes:

```sh
talosctl shutdown -n $CONTROL_PLANE_IP --talosconfig=./talosconfig
```
