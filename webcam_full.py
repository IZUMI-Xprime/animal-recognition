import os
import time
from flask import Flask, render_template, Response, request, redirect, url_for, session, send_file
from ultralytics import YOLO
import smtplib
import numpy as np
import seaborn as sns
from email.mime.text import MIMEText
from telegram import Bot, InputFile
from datetime import datetime
from threading import Thread
import cv2
import matplotlib
import matplotlib.pyplot as plt
import io
import asyncio  # Import asyncio for asynchronous operations

animal_counts = {}  # Global dictionary to track detected animal names and quantities
video_filename = None  # Initialize video_filename as None
matplotlib.use('Agg')  # Use a non-interactive backend
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your_secret_key")  # Secure secret key

# Load YOLO model
model = YOLO(r'C:\Users\darni\Downloads\yolo11s_segment.pt')

# User credentials
USER_CREDENTIALS = {"admin": "password123"}

# Email and Telegram settings
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "#enter email")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "#enter mail")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "app pass")  # Gmail App Password
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "token")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "chat id")

# WEBCAM
esp32_stream_url = cv2.VideoCapture(0)

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
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)  # Ensure DirectShow is used

        if cap.isOpened():
            print("✅ Reconnected to webcam successfully.")
            return
        print(f"🔄 Retrying webcam connection... ({attempt + 1}/{retries})")
        time.sleep(delay)
    print("❌ Failed to reconnect to the webcam.")


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
    global video_writer

    if video_writer is not None:
        video_writer.release()
        video_writer = None

    # Add a short delay to ensure the file is fully written
    time.sleep(1)

    # Verify the video file is valid
    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        print(f"Error: Video file {video_path} is corrupted or empty. Aborting send.")
        return

    print(f"Video saved successfully: {video_path}")

    # Send the video to Telegram
    asyncio.run(send_to_telegram_async(video_path))

    # Send an email notification
    send_email_notification(animal_counts)


async def send_to_telegram_async(video_path):
    bot = Bot(token=TELEGRAM_TOKEN)
    try:
        with open(video_path, 'rb') as video_file:
            await bot.send_video(chat_id=TELEGRAM_CHAT_ID, video=InputFile(video_file, filename=os.path.basename(video_path)))
        print(f"Video sent to Telegram: {os.path.basename(video_path)}")
    except Exception as e:
        print(f"Failed to send video to Telegram: {e}")


def generate_frames():
    global cap
    while True:
        if cap is None or not cap.isOpened():
            print("⚠️ Camera not open. Trying to reconnect...")
            reconnect_stream()

        ret, frame = cap.read()
        if not ret:
            print("❌ Failed to read frame, retrying...")
            reconnect_stream()
            continue  # Skip to next loop iteration

        # Add timestamp overlay
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        frame_height, frame_width, _ = frame.shape
        cv2.putText(frame, timestamp, (frame_width - 350, frame_height - 20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        # Perform YOLO detection
        results = model(frame)
        bboxes = [(int(box.xyxy[0][0]), int(box.xyxy[0][1]), 
                   int(box.xyxy[0][2]), int(box.xyxy[0][3])) 
                  for result in results for box in result.boxes] if results else []

        # Draw bounding boxes
        for (x1, y1, x2, y2) in bboxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, "Detected", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Encode the frame for streaming
        ret, jpeg = cv2.imencode('.jpg', frame)
        if not ret:
            print("❌ Encoding error, skipping frame")
            continue

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n\r\n')

        
# Heatmap generation function
def generate_heatmap(frame, bboxes):
    heatmap = np.zeros((frame.shape[0], frame.shape[1]), dtype=np.float32)
    # Generate the heatmap data (this is just an example, replace it with your logic)
    sns.heatmap(heatmap, cmap="jet", alpha=0.6)

    # Save the heatmap image to a file
    heatmap_path = 'path_to_save_heatmap.png'
    plt.savefig(heatmap_path)
    plt.close()  # Close the figure to avoid memory issues

    return heatmap_path

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
    video_files = [f for f in os.listdir('./videos') if f.endswith('.mp4')]
    return render_template('videos.html', videos=video_files)

@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory('uploads', filename)

# Stream video and generate heatmap
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/heatmap')
def heatmap():
    global cap
    if cap is None or not cap.isOpened():
        reconnect_stream()

    ret, frame = cap.read()
    if not ret:
        return "Failed to capture frame", 500

    results = model(frame)
    bboxes = [(int(box.xyxy[0][0]), int(box.xyxy[0][1]), int(box.xyxy[0][2]), int(box.xyxy[0][3])) for result in results for box in result.boxes]

    heatmap_path = generate_heatmap(frame, bboxes)
    return send_file(heatmap_path, mimetype='image/png')

if __name__ == "__main__":
    reconnect_stream()
    app.run(debug=True, host="0.0.0.0", port=5000)
