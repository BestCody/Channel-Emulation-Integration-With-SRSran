# Live Pilot Preflight

These steps cover the cluster-admin pieces the experiment runner cannot safely do for you.
Run them on the Kubernetes node before starting the live-channel pilot.

## 1. Make the GPU schedulable

The UE deployment requests `nvidia.com/gpu: 1`, so Kubernetes must see at least one allocatable NVIDIA GPU.
Configure the NVIDIA runtime for containerd, restart the node services, then install the NVIDIA device-plugin DaemonSet using the version approved for the cluster.

```sh
sudo nvidia-ctk runtime configure --runtime=containerd --set-as-default
sudo systemctl restart containerd
sudo systemctl restart kubelet
kubectl apply -f <nvidia-device-plugin-daemonset.yaml>
```

Verify the plugin and allocatable resource before applying the live UE overlay:

```sh
kubectl get pods -A | grep nvidia-device-plugin
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}'
```

## 2. Build and import the image tags the overlay uses

The live overlay uses `localhost/srsue-live:gr38-v1` with `imagePullPolicy: Never`, and the live image is built from `localhost/srsue-sparse:gr38-v1`.
Build both plain tags from the current source tree and import them into containerd's `k8s.io` namespace.

Run from `srsran_open5gs/`:

```sh
docker build -t localhost/srsue-sparse:gr38-v1 -f containers/srsue-channel/Dockerfile .
docker build -t localhost/srsue-live:gr38-v1 -f containers/srsue-live/Dockerfile .
docker save localhost/srsue-sparse:gr38-v1 localhost/srsue-live:gr38-v1 -o /tmp/srsue-live-gr38-v1.tar
sudo ctr -n k8s.io images import /tmp/srsue-live-gr38-v1.tar
sudo ctr -n k8s.io images list | grep 'localhost/srsue-.*:gr38-v1'
```

Do not rely on older `stage3` or `stage4` tags for the pilot; the deployment and Dockerfile expect the plain `gr38-v1` tags.

## 3. Confirm both live-channel ports are reachable

The runner forwards both ZeroMQ sockets:

- `5555:5555` for control/status/config REQ/REP
- `5556:5556` for streamed CIR PUSH/PULL updates

Readiness still probes the control port only. If you test manually, use both mappings:

```sh
kubectl port-forward -n open5gs pod/<ue-pod> 5555:5555 5556:5556
```

## 4. Verify the configured virtual-radio IP topology

The flowgraphs and generated radio configs now read the virtual-radio addresses from environment variables.
The deployment defaults still match the current Multus/static IP assignments:

- `SRSRAN_AMF_N3_ADDR=10.10.3.200`
- `SRSRAN_GNB_N3_BIND_ADDR=10.10.3.231`
- `SRSRAN_GNB_ZMQ_ADDR=10.10.3.231`
- `SRSRAN_UE_ZMQ_ADDR=10.10.3.232`
- `SRSRAN_ZMQ_INTERFACE=n3`

Before a pilot run, confirm the configured env values match the Multus annotations and that traffic is routed through the live-channel flowgraph.
For non-default ports or explicit endpoints, use `SRSRAN_ZMQ_GNB_DOWNLINK_ENDPOINT`, `SRSRAN_ZMQ_GNB_UPLINK_ENDPOINT`, `SRSRAN_ZMQ_UE<N>_UPLINK_ENDPOINT`, or `SRSRAN_ZMQ_UE<N>_DOWNLINK_ENDPOINT`.
