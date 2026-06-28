# echo $1 >> /mnt/bw_log.txt
ssh n6saha@transitvm -- sudo sh ./srsran_open5gs/onos/scripts/update_queue.sh $1
