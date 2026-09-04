# For hosts that give the app a real machine with a real disk, which is
# what Sibyl Memory needs — see README, "The hosted sandbox".
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY troth ./troth
RUN pip install --no-cache-dir .

COPY troth-v2-lethe-blue.html ./
COPY examples ./examples
COPY demo ./demo

# The memory lives on the mounted volume, not in the image layer.
ENV TROTH_DB=/data/memory.db \
    HOST=0.0.0.0 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

# Seed the volume on first boot only; never overwrite a real memory.
CMD ["sh", "-c", "[ -f \"$TROTH_DB\" ] || cp demo/demo-seed.db \"$TROTH_DB\"; exec troth serve"]
