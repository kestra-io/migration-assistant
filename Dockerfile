# The Kestra Migration Assistant for vRA as a container image: the wheel in dist/, with the runtime dependencies
# uv.lock pins (and their hashes), which `uv export` writes next to it as dist/requirements.txt. `make image` builds
# it locally; the release workflow builds it from the wheel it attaches to the release.
#
#   docker run --rm -v "$PWD/acme:/work/acme" -e ANTHROPIC_API_KEY ghcr.io/kestra-io/migration-assistant vra run acme
#
# /work is the working folder: mount the export folder inside it, under its own name, so the estate keeps its name.
# A Claude Code login cannot be used in a container: an Anthropic API key, an OpenAI-compatible endpoint, or no model.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ARG VERSION=dev
LABEL org.opencontainers.image.title="Kestra Migration Assistant for vRA" \
      org.opencontainers.image.description="Assess a VMware vRA / vRO estate for migration to Kestra, from its export." \
      org.opencontainers.image.source="https://github.com/kestra-io/migration-assistant" \
      org.opencontainers.image.url="https://kestra.io/resources/migration/vra/assistant" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Kestra Technologies" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KESTRA_MIGRATE_CONTAINER=1

COPY dist/requirements.txt dist/*.whl /tmp/install/
RUN pip install --no-cache-dir --require-hashes -r /tmp/install/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/install/*.whl \
 && pip check \
 && rm -rf /tmp/install \
 && useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin kestra \
 && mkdir /work \
 && chown kestra:kestra /work

USER 1000:1000
WORKDIR /work
ENTRYPOINT ["kestra-migrate"]
CMD ["--help"]
