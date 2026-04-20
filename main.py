import uuid
import boto3
import subprocess
import firebase_admin
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import credentials, auth
from botocore.exceptions import NoCredentialsError
from dotenv import load_dotenv
import os

load_dotenv()


app = FastAPI()
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_SIZE_MB = 15

# ==============================
# 🔐 Firebase Setup
# ==============================
cred = credentials.Certificate("firebase-service-account.json")
firebase_admin.initialize_app(cred)

# ==============================
# ☁️ AWS S3 Setup
# ==============================
S3_BUCKET = os.getenv("S3_BUCKET")
AWS_REGION = "ap-south-1"

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("ACCESS_KEY"),
    aws_secret_access_key=os.getenv("SECRET_KEY"),
    region_name=AWS_REGION
)

# ==============================
# 🔐 Auth Helpers
# ==============================
def verify_token(token: str):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")

# ==============================
# Video Converter
# ==============================
def get_size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)


def run_ffmpeg(input_path, output_path, crf, scale=None):
    command = [
        "/usr/bin/ffmpeg",
        "-i", input_path,
        "-vcodec", "libx264",
        "-crf", str(crf),
        "-preset", "medium",
        "-acodec", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart"
    ]

    # Optional scaling (last resort)
    if scale:
        command.extend(["-vf", scale])

    command.append(output_path)

    subprocess.run(command, check=True)


# ==============================
# 📤 Upload Video (PUBLIC)
# ==============================
@app.post("/upload-video/")
async def upload_video(video: UploadFile = File(...)):
    try:
        # ✅ Validate
        if not (video.filename.endswith(".mp4") or video.filename.endswith(".webm")):
            raise HTTPException(status_code=400, detail="Only MP4 or WEBM allowed")

        # Paths
        input_ext = ".webm" if video.filename.endswith(".webm") else ".mp4"
        input_path = f"/tmp/{uuid.uuid4()}{input_ext}"
        output_path = f"/tmp/{uuid.uuid4()}.mp4"

        # ✅ Stream upload (safe)
        with open(input_path, "wb") as f:
            while chunk := await video.read(1024 * 1024):
                f.write(chunk)

        # 🎯 Step 1: Try BEST quality
        run_ffmpeg(input_path, output_path, crf=23)

        size = get_size_mb(output_path)

        # 🎯 Step 2: Medium compression
        if size > MAX_SIZE_MB:
            run_ffmpeg(input_path, output_path, crf=26)
            size = get_size_mb(output_path)

        # 🎯 Step 3: Strong compression
        if size > MAX_SIZE_MB:
            run_ffmpeg(input_path, output_path, crf=28)
            size = get_size_mb(output_path)

        # 🎯 Step 4: Last resort → reduce resolution (720p)
        if size > MAX_SIZE_MB:
            run_ffmpeg(input_path, output_path, crf=28, scale="scale=1280:-2")
            size = get_size_mb(output_path)

        # ❌ Still too large
        if size > MAX_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail="Video too large even after optimization. Please upload shorter video."
            )

        # Upload to S3
        file_key = f"videos/{uuid.uuid4()}.mp4"

        with open(output_path, "rb") as f:
            s3.upload_fileobj(
                f,
                S3_BUCKET,
                file_key,
                ExtraArgs={"ContentType": "video/mp4"}
            )

        # Cleanup
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)
            
        print(f"Video Uploaded (optimized to {round(size, 2)} MB)")
        print("file key:", file_key)

        return {
            "status": "success",
            "message": f"Video Uploaded (optimized to {round(size, 2)} MB)",
            "file_key": file_key
        }

    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Video processing failed")

    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials error")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==============================
# 🔒 Get Private Video URL (ADMIN ONLY)
# ==============================
@app.get("/get-video-url/")
def get_video_url(
    file_key: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    user = verify_token(token)

    if user:
        user_id = user.get("uid")
    else:
        raise HTTPException(status_code=403, detail="Admin access only")

    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": S3_BUCKET,
            "Key": file_key
        },
        # ExpiresIn=3600
        ExpiresIn=129600
    )

    return {
        "user_id": user_id,
        "file_key": file_key,
        "video_url": url
    }

# ==============================
# ❤️ Health Check
# ==============================
@app.get("/")
def root():
    return {"message": "Backend running 🚀"}