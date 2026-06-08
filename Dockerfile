# 🐳 Dockerfile для бота Bimbo Planner

# 1. Берём официальный образ Python (лёгкая версия)
FROM python:3.12-slim

# 2. Рабочая папка внутри контейнера
WORKDIR /app

# 3. Копируем список зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Копируем сам код бота
COPY planner_bot.py .

# 5. Запускаем бота
CMD ["python", "-u", "planner_bot.py"]
