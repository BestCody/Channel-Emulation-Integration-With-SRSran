ovs-vsctl -- --all destroy QoS -- --all destroy Queue -- clear Port eno1 qos

sleep 1
ovs-vsctl list queue
ovs-vsctl list qos
