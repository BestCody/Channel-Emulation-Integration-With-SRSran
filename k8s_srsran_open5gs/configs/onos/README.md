# SDN-based Transport Network Switch Deployment (OpenvSwitch) and ONOS

This guide provides step-by-step instructions for setting up a Software-Defined Networking (SDN)-based transport network using OpenvSwitch (OvS) and ONOS. **Note**: Set up the transport network on a separate machine from the RAN and Core, and the Core and RAN should also be on separate machines.

## Step 0: Setup Repository and Environment

1. Clone the repository and add the `bin` directory to your `PATH`:

    ```bash
    git clone https://github.com/sulaimanalmani/k8s_srsran_open5gs.git
    echo 'export PATH="/path/to/k8s_srsran_open5gs/bin:$PATH"' >> ~/.bashrc
    source ~/.bashrc
    cd /path/to/k8s_srsran_open5gs/
    git clone https://github.com/sulaimanalmani/testbed-automator.git
    ```

## Step 1: Install Kubernetes and Required Components

Navigate to the `testbed_automator` directory and run the installation script:

    ```bash
    cd /path/to/k8s_srsran_open5gs/testbed_automator/
    ./install_k8s.sh
    ```

Verify that Kubernetes is up and running:

    ```bash
    kubectl get nodes
    kubectl get pods -A
    ```

Remove the `control-plane` taint from the node:

    ```bash
    kubectl taint nodes <node-name> node-role.kubernetes.io/control-plane-
    ```

## Step 2: Set up VXLAN Tunnels Between RAN <--> Transit <--> Core Nodes

1. **On the Core and RAN nodes**, add the following VXLAN tunnels:

    ```bash
    sudo ovs-vsctl add-port n3br vx_transit -- set Interface vx_transit type=vxlan options:remote_ip=<ip of the transit node>
    ```

2. **On the Transit node**, add the following VXLAN tunnels:

    ```bash
    sudo ovs-vsctl add-port br-int vx_ran -- set Interface vx_ran type=vxlan options:remote_ip=<ip of the RAN node>
    sudo ovs-vsctl add-port br-int vx_core -- set Interface vx_core type=vxlan options:remote_ip=<ip of the Core node>
    ```

## Step 3: Add Physical Interface to OVS Bridge

This step connects the physical interface to the OVS bridge, allowing ONOS to control traffic through the interface.

> **Caution**: Modifying the network configuration might lead to a loss of SSH access. Ensure you have physical access to the machine or the ability to restore access without SSH. If access is lost, restore the original netplan file as follows:

    ```bash
    sudo netplan apply /etc/netplan/01-network-manager-all.yaml
    ```

1. Modify `local_netplan.yaml`:

    ```bash
    cd /path/to/k8s_srsran_open5gs/configs/onos
    vim ./local_netplan.yaml
    ```

    Update `<interface_ip>`, `<interface_name>`, and `<default_gateway>` as per your setup. Use the `route -n` command to find your default gateway. **If using an Azure VM, use `vm_netplan.yaml` instead.**

2. Apply the modified netplan configuration:

    ```bash
    sudo cp ./local_netplan.yaml /etc/netplan/
    sudo chmod 600 /etc/netplan/local_netplan.yaml
    sudo netplan try /etc/netplan/local_netplan.yaml
    ```

3. Verify that the interface is added to the OVS bridge:

    ```bash
    sudo ovs-vsctl show
    ```

    You should see output similar to:

    ```bash
    Bridge n4br
        Port n4br
            Interface n4br
                type: internal
    Bridge n3br
        Controller "tcp:<interface_ip>:31653"
        fail_mode: standalone
        Port vx_core
            Interface vx_core
                type: vxlan
                options: {remote_ip="<ip of the core node>"}
        Port vx_ran
            Interface vx_ran
                type: vxlan
                options: {remote_ip="<ip of the RAN node>"}
    ```

## Step 4: Deploy ONOS

1. Add the necessary Helm repositories:

    ```bash
    helm repo add cord https://charts.opencord.org
    helm repo add atomix https://charts.atomix.io
    helm repo add onosproject https://charts.onosproject.org
    helm repo update
    ```

2. Create the namespace:

    ```bash
    kubectl create namespace micro-onos
    ```

3. Deploy ONOS Operator and Atomix:

    ```bash
    helm -n kube-system install atomix atomix/atomix
    helm install -n kube-system onos-operator onosproject/onos-operator
    ```

4. Deploy ONOS Classic:

    ```bash
    mkdir ~/onos/
    helm pull onosproject/onos-classic --untar
    vim ./onos-classic/charts/atomix/templates/pdb.yaml
    # Change `policy/v1beta1` to `policy/v1`
    cp ./values.yaml ~/onos/onos-classic/values.yaml
    helm install -n micro-onos onos-classic onos-classic/
    ```

5. Install required packages:

    ```bash
    kubectl exec -it onos-classic-onos-classic-0 -n micro-onos -- apt-get update -y
    kubectl exec -it onos-classic-onos-classic-0 -n micro-onos -- apt-get install ssh -y
    ```

6. To access ONOS, use the following command (password is `rocks`):

    ```bash
    ssh -p 30082 onos@localhost
    ```

## Step 5: Configure ONOS

1. Add a queue to the OvS switch (e.g., a 10Mbps queue):

    ```bash
    cd /path/to/k8s_srsran_open5gs/configs/onos/scripts
    sudo sh ./add_queue.sh 10
    sudo sh ./list_queue.sh
    ```

    Note the queue ID and update it in `update_queue.sh`.

2. Install flow rules for slice traffic:

    ```bash
    sh ./install_rule.sh
    ```

    This installs the rules specified in `qos_rule.json` in the ONOS controller. **To view the flow rule** in ONOS:

    ```bash
    ssh -p 30082 onos@localhost
    flows
    ```

3. Update the queue throughput assigned to the slice:

    ```bash
    sh ./update_queue.sh <new_throughput>
    ```
