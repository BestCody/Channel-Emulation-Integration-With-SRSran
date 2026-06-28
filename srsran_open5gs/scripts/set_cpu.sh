#!/bin/bash

# Get pod name with $2 in it
POD_NAME=$(kubectl get pods -n open5gs | grep $1 | awk '{print $1}')

# Get cpu value from first argument
CPU=$2

echo "Setting CPU to $CPU for pod $POD_NAME"

#Patch cpu limits only
# kubectl patch pod $POD_NAME --patch '{"spec":{"containers":[{"name":"'$1'","resources":{"limits":{"cpu":"'$CPU'"}}}]}}' -n open5gs

# Patch the pod with updated CPU requests and limits
kubectl patch -n open5gs pod $POD_NAME --type='json' -p='[{"op": "replace", "path": "/spec/containers/0/resources/requests/cpu", "value": "'$CPU'm"},{"op": "replace", "path": "/spec/containers/0/resources/limits/cpu", "value": "'$CPU'm"}]'
# kubectl patch pod $POD_NAME --patch '{"spec":{"containers":[{"name":"'$1'","resources":{"limits":{"cpu":"'$CPU'"}, "requests":{"cpu":"'$CPU'"}}}]}}' -n open5gs