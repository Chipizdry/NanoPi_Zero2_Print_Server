
FROM debian:12-slim

RUN apt-get update && apt-get install -y \
    cups \
    cups-client \
    && rm -rf /var/lib/apt/lists/*

COPY cupsd.conf /etc/cups/cupsd.conf

EXPOSE 631

CMD ["cupsd", "-f"]
