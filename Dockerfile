FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Works whether the repo root is the bot folder itself (app/, requirements.txt)
# or a parent folder containing quiz_duel_bot/app and quiz_duel_bot/requirements.txt.
COPY . .

RUN if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    elif [ -f quiz_duel_bot/requirements.txt ]; then \
        pip install --no-cache-dir -r quiz_duel_bot/requirements.txt; \
    else \
        echo "requirements.txt not found" && exit 1; \
    fi

RUN mkdir -p /data

CMD ["sh", "-c", "if [ -d app ]; then python -m app.main; elif [ -d quiz_duel_bot/app ]; then cd quiz_duel_bot && python -m app.main; else echo 'app directory not found' && exit 1; fi"]
