# Use the official Python slim image for a smaller footprint
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies needed for some Python packages (like Pillow)
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Command to run the bot
CMD ["python", "bot.py"]
