import uuid
import boto3
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
# 📤 Upload Video (PUBLIC)
# ==============================
@app.post("/upload-video/")
async def upload_video(video: UploadFile = File(...)):
    try:
        # Validate file type
        if not video.filename.endswith(".mp4"):
            raise HTTPException(status_code=400, detail="Only MP4 allowed")

        file_key = f"videos/{uuid.uuid4()}.mp4"

        # Upload to S3 (PRIVATE)
        s3.upload_fileobj(
            video.file,
            S3_BUCKET,
            file_key,
            ExtraArgs={
                "ContentType": "video/mp4"
            }
        )

        return {
            "status": "success",
            "message": "Video uploaded",
            "file_key": file_key
        }

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
        ExpiresIn=3600
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
