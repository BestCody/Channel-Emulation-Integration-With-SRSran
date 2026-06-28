fid=$(sh ./get_flows.sh | jq -r '.flows[] | select(.treatment.instructions[]?.type == "QUEUE") | .id')
echo "deleting flow with id: $fid"
curl -X DELETE --header 'Accept: application/json' "http://localhost:30081/onos/v1/flows/of:0000000000000050/$fid" --user onos:rocks
