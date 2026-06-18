# পাইথন ৩.১১ ইমেজ ব্যবহার করা হচ্ছে
FROM python:3.11-slim

# লিনাক্সের প্রয়োজনীয় সিস্টেম ডিপেন্ডেন্সি (CMake এবং libzbar) ইন্সটল
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

# প্রজেক্ট ডিরেক্টরি সেট করা
WORKDIR /app

# রিকোয়ারমেন্টস কপি ও ইন্সটল করা
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# বাকি সব কোড কপি করা
COPY . .

# রেন্ডারের পোর্টের সাথে কানেক্ট করা
ENV PORT=5000
EXPOSE 5000

# অ্যাপ রান করার কমান্ড
CMD ["python", "app.py"]
