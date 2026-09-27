# koshad only: MCP tools continue to run on the developer host, where they can safely
# operate on the selected workspace. The container provides the durable ledger and UI.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY kosha ./kosha
COPY config/price_table.m1.json ./config/price_table.m1.json
RUN pip install --no-cache-dir .
ENV KOSHAD_HOST=0.0.0.0 \
    KOSHAD_PORT=8765 \
    KOSHA_DB=/data/kosha.db \
    KOSHA_PRICE_TABLE=/app/config/price_table.m1.json
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["koshad"]
