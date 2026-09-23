FROM registry.k8s.io/kubectl:v1.36.4 AS kubectl

FROM python:3.13-slim

COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
RUN pip install --no-cache-dir rich==15.0.0

COPY cluster-capacity.py /usr/local/bin/cluster-capacity.py

ENV KUBECONFIG=/cfg/kubeconfig
ENV TZ=Europe/London

ENTRYPOINT ["python3", "/usr/local/bin/cluster-capacity.py"]
