FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libgeos-dev libproj-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# The model registry serves these over /models/{id}/artifact, which is how
# another node in the federation obtains this one's disease model. Without them
# in the image that endpoint 404s and the federation demo is a slide.
COPY models ./models
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Do not run as root. A container that only needs to read its own code and talk
# to Postgres has no business owning the filesystem it runs on.
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --create-home --uid 10001 agrin \
    && chown -R agrin:agrin /srv
USER agrin

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# No --port here on purpose: the entrypoint appends it from $PORT, falling
# back to 8000. EXPOSE stays as documentation of that default.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
