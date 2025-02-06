import os
import time
from flask import Flask, render_template, Response, request, redirect, url_for, session, send_file
from ultralytics import YOLO
import smtplib
from email.mime.text import MIMEText
from telegram import Bot, InputFile
from datetime import datetime
from threading import Thread
import cv2
import asyncio # Import asyncio for asynchronous operations

animal_counts = {} # Global dictionary to track detected animal names and quantities
video_filename = None   # Initialize video_filename as None

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your_secret_key")  # Secure secret key

# Load YOLO model
model = YOLO(r'C:\Users\darni\Downloads\yolo11s_segment.pt')

# User credentials
USER_CREDENTIALS = {"admin": "password123"}

# Email and Telegram settings
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "22dm11@psgpolytech.ac.in")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "darnishcnpm@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "kxml fjog ipep nays")  # Gmail App Password
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8165959940:AAFF4ZkA6gL5Hm0JCGaNY2sPfheMoIeocP8")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "1028492554")

# ESP32 stream URL
esp32_stream_url = "http://192.168.0.103:81/stream" # Replace with your ESP32 stream URL

cap = None
video_writer = None
clip_duration = 60  # Duration of each video clip in seconds
clip_start_time = None
animal_detected = False
last_email_time = 0
last_video_filename = None

def reconnect_stream(retries=5, delay=2):
    global cap
    for attempt in range(retries):
        if cap is not None:
            cap.release()
        cap = cv2.VideoCapture(esp32_stream_url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            print("Reconnected to the stream successfully.")
            return
        print(f"Retrying connection... ({attempt + 1}/{retries})")
        time.sleep(delay)
    print("Failed to reconnect to the video stream.")

def send_email_notification(animal_counts):
    global last_email_time
    current_time = time.time()

    # Send email only if at least 1 minute has passed since the last email
    if current_time - last_email_time >= 60:
        # Create a detailed message with animal counts
        animal_details = "\n".join([f"{animal}: {count}" for animal, count in animal_counts.items()])
        email_body = f"Animal detected in the video stream:\n\n{animal_details}\n\nCheck Telegram for video details."

        msg = MIMEText(email_body)
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECEIVER
        msg['Subject'] = 'Animal Detected Notification'

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
            server.quit()
            last_email_time = current_time
            print("Email notification sent successfully.")
        except Exception as e:
            print(f"Failed to send email: {e}")

def send_to_telegram(video_path):
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        with open(video_path, 'rb') as video_file:
            bot.send_video(chat_id=TELEGRAM_CHAT_ID, video=InputFile(video_file, filename=os.path.basename(video_path)))
        print(f"Video sent to Telegram: {video_path}")
    except Exception as e:
        print(f"Failed to send video: {e}")

def save_and_notify(video_path, animal_counts):
    global video_writer, last_video_filename

    if video_writer is not None:
        video_writer.release()
        video_writer = None

    last_video_filename = video_path

    # Send the video to Telegram asynchronously
    async def send_to_telegram_async():
        try:
            bot = Bot(token=TELEGRAM_TOKEN)
            await bot.send_video(chat_id=TELEGRAM_CHAT_ID, video=InputFile(video_path, filename=os.path.basename(video_path)))
            print(f"Video sent to Telegram: {os.path.basename(video_path)}")
        except Exception as e:
            print(f"Failed to send video to Telegram: {e}")

    asyncio.run(send_to_telegram_async())

    # Send an email notification
    send_email_notification(animal_counts)
    print(f"Video saved: {video_path}")

def generate_frames():
    global cap, video_writer, clip_start_time, animal_detected, video_filename
    animal_counts = {}  # Initialize animal_counts dictionary

    while True:
        if cap is None or not cap.isOpened():
            reconnect_stream()

        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame, reconnecting...")
            reconnect_stream()
            continue

        # Add timestamp to the frame
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        frame_height, frame_width, _ = frame.shape
        cv2.putText(frame, timestamp, (frame_width - 350, frame_height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        
        # Reset detection flag for the current frame
        animal_detected = False
        animal_counts.clear()  # Clear the dictionary for the new frame

        # Perform object detection on the current frame
        results = model(frame)
        for result in results:
            boxes = result.boxes
            for box in boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])  # Bounding box coordinates
                cls = int(box.cls[0])  # Class index
                conf = box.conf[0].item()  # Confidence score

                # Check if the detected object is an animal (adjust class indices as needed)
                if cls in [0, 16, 17]:  # Replace with relevant class indices for animals
                    animal_name = model.names[cls]  # Get the class name
                    label = f"{animal_name} {conf:.2f}"
                    color = (0, 255, 0) # Green color for bounding box

                    # Draw bounding box and label on the frame
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                   
                    # Update detection flag and count
                    animal_detected = True
                    animal_counts[animal_name] = animal_counts.get(animal_name, 0) + 1

        # If animals are detected, print their counts
        if animal_detected:
            print("Animals detected:", animal_counts)

        # Start a new video clip if needed
        if video_writer is None or (time.time() - clip_start_time) >= clip_duration:
            if video_writer is not None:
                save_and_notify(video_filename, animal_counts)
            clip_start_time = time.time()
            video_filename = f"animal_detection_{int(clip_start_time)}.mp4"
            video_writer = cv2.VideoWriter(video_filename, cv2.VideoWriter_fourcc(*'mp4v'), 20.0, (frame_width, frame_height))
            print(f"Started new video clip: {video_filename}")

        if video_writer is not None:
            video_writer.write(frame)

        # Encode frame for streaming
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            print("Error: Failed to encode frame")
            break

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n\r\n')

@app.route('/')
def welcome():
    return render_template('welcome.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
            session['user'] = username
            return redirect(url_for('dashboard'))
        else:
            return "Invalid credentials. Please try again."
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('welcome'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/videos')
def list_videos():
    video_files = [f for f in os.listdir('.') if f.startswith("animal_detection_") and f.endswith(".mp4")]
    return render_template('videos.html', videos=video_files)

@app.route('/download/<filename>')
def download_video(filename):
    return send_file(filename, as_attachment=True)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    reconnect_stream()
    app.run(debug=True, host="0.0.0.0", port=5000)
