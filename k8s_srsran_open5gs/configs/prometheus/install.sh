helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
kubectl create namespace monitoring
helm install kube-prometheus-stack  prometheus-community/kube-prometheus-stack -f debug_kube-prometheus-values-nuc.yaml -n monitoring

