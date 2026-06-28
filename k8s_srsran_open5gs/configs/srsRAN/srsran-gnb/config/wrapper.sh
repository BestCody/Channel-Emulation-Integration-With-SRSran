#!/bin/bash 
apt-get -y install pip iftop
pip install prometheus_client

while true; do sleep 1000; done

