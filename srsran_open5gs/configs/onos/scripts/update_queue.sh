ovs-vsctl set queue cd8ef39f-cf2e-41c8-9ca6-4cd758fc6737 other-config:min-rate=${1}000000 other-config:max-rate=${1}000000

sleep 1
ovs-vsctl list queue
sleep 1
ovs-vsctl list qos
