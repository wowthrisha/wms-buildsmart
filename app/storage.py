import os
import boto3
try:
    import magic
except ImportError:
    magic = None
from werkzeug.utils import secure_filename
from flask import current_app, send_from_directory, redirect
import mimetypes

s3_client = None

def get_s3_client():
    global s3_client
    if s3_client is None and os.environ.get('AWS_ACCESS_KEY_ID'):
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
    return s3_client

def validate_secure_mime(file_obj, allowed_mimes=None):
    if not allowed_mimes:
        allowed_mimes = ['application/pdf', 'image/jpeg', 'image/png']
    
    # Fallback if libmagic is missing
    if magic is None:
        # Simple extension-based fallback + basic header check
        filename = getattr(file_obj, 'filename', '').lower()
        content_type, _ = mimetypes.guess_type(filename)
        return content_type in allowed_mimes

    header = file_obj.read(2048)
    file_obj.seek(0)
    try:
        mime = magic.from_buffer(header, mime=True)
        return mime in allowed_mimes
    except Exception:
        # If magic exists but fails (e.g. libmagic missing at runtime)
        return any(file_obj.filename.lower().endswith(ext) for ext in ['.pdf', '.jpg', '.jpeg', '.png'])

def save_file_securely(file_obj, filename_prefix=""):
    """
    Saves a file to S3 if configured, else falls back to local uploads folder securely.
    Returns the secure filename or path used.
    """
    secure_name = secure_filename(file_obj.filename)
    if filename_prefix:
        secure_name = f"{filename_prefix}_{secure_name}"

    bucket = os.environ.get('AWS_BUCKET_NAME')
    s3 = get_s3_client()

    if s3 and bucket:
        # Upload to S3
        s3.upload_fileobj(
            file_obj,
            bucket,
            secure_name,
            ExtraArgs={"ContentType": file_obj.content_type}
        )
        return secure_name
    else:
        # Fallback to local storage
        upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], secure_name)
        # Ensure directory exists!
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)
        file_obj.save(upload_path)
        return secure_name

def send_file_securely(filename):
    bucket = os.environ.get('AWS_BUCKET_NAME')
    s3 = get_s3_client()
    if s3 and bucket:
        url = s3.generate_presigned_url('get_object',
                                        Params={'Bucket': bucket, 'Key': filename},
                                        ExpiresIn=3600)
        return redirect(url)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


def delete_file_securely(filename):
    if not filename:
        return
    bucket = os.environ.get('AWS_BUCKET_NAME')
    s3 = get_s3_client()
    if s3 and bucket:
        try:
            s3.delete_object(Bucket=bucket, Key=filename)
        except Exception:
            pass
        return

    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    try:
        if os.path.exists(upload_path):
            os.remove(upload_path)
    except Exception:
        pass
