FROM ghcr.io/siderolabs/talosctl:v1.13.9 AS talosctl

FROM python:3.13-slim

COPY --from=talosctl /talosctl /usr/local/bin/talosctl
RUN pip install --no-cache-dir rich==15.0.0

COPY heat-fans.py /usr/local/bin/heat-fans.py

ENV TALOSCONFIG=/cfg/talosconfig
ENV TZ=Europe/London
ENV TERM=xterm-256color
ENV COLORTERM=truecolor

ENTRYPOINT ["python3", "/usr/local/bin/heat-fans.py"]
