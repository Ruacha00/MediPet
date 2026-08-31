FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

ENV COREPACK_HOME=/opt/corepack \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable \
    && corepack install --global pnpm@11.24.0 \
    && groupadd --gid 10001 medipet \
    && useradd --uid 10001 --gid medipet --create-home medipet \
    && mkdir /app \
    && chown medipet:medipet /app

WORKDIR /app

ARG PNPM_REGISTRY=https://registry.npmmirror.com

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=medipet-web-pnpm-v11,target=/root/.local/share/pnpm/store/v11 \
    corepack pnpm \
      --config.registry="${PNPM_REGISTRY}" \
      install --frozen-lockfile --trust-lockfile

COPY --chown=medipet:medipet . .
RUN mkdir -p .next && chown -R medipet:medipet .next

USER medipet

EXPOSE 3000

CMD ["corepack", "pnpm", "run", "dev", "--hostname", "0.0.0.0", "--port", "3000"]
