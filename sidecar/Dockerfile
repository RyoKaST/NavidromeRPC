FROM debian:bookworm-slim

ADD https://github.com/Vencord/Vesktop/releases/download/v1.6.8/vesktop_1.6.8_amd64.deb /tmp/vesktop.deb

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      /tmp/vesktop.deb ca-certificates fonts-liberation procps python3 \
      novnc openbox websockify x11vnc xvfb \
      libasound2 libatk-bridge2.0-0 libdrm2 libgbm1 libgtk-3-0 libnss3 libsecret-1-0 libxkbcommon0 libxss1 \
 && rm -rf /tmp/vesktop.deb /var/lib/apt/lists/* \
 && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
 && useradd --create-home --uid 1000 vesktop \
 && mkdir -p /run/user/1000 && chown vesktop:vesktop /run/user/1000 && chmod 700 /run/user/1000

COPY entrypoint.sh bridge.py /
USER vesktop
RUN mkdir -p /home/vesktop/.config/vesktop
ENV DISPLAY=:99 XDG_RUNTIME_DIR=/run/user/1000 HOME=/home/vesktop
VOLUME /home/vesktop/.config/vesktop
EXPOSE 6080 6464
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6464/health', timeout=3)"
ENTRYPOINT ["bash", "/entrypoint.sh"]
