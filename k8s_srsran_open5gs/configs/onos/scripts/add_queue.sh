ovs-vsctl -- set port eno1 qos=@newqos -- --id=@newqos create qos type=linux-htb other-config:max-rate=1000000000 queues:123=@newqueue -- --id=@newqueue create queue other-config:min-rate=${1}000000 other-config:max-rate=${1}000000

sleep 1
ovs-vsctl list queue
sleep 1
ovs-vsctl list qos
