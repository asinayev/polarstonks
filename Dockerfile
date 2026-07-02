# Use official python base image
FROM python:3.14-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    out_dir="/tmp/polarstonks"

# Set working directory
WORKDIR /app

# Copy requirements
# Assuming we generate a clean requirements.txt or use pip install directly
# I'll create a minimal one or just install packages in RUN
# Let's copy requirements.txt if exists, else install manually
COPY requirements.txt .

# Install dependencies
# Need build-essential for some compiled extensions if wheels missing, but slim usually ok for wheels
RUN pip install --no-cache-dir -r requirements.txt || \
    pip install --no-cache-dir polars polygon-api-client numpy pandas scipy requests google-genai pyarrow

# Copy source code
COPY src/ src/
COPY other_datasources/ other_datasources/
COPY run.sh .

# Make run executable
RUN chmod +x run.sh

# Create output dir
RUN mkdir -p /tmp/polarstonks

# Entrypoint
# Maps to run.sh
ENTRYPOINT ["./run.sh"]

# Default command (can be overridden)
CMD ["--run", "am"]
