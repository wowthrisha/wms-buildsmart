import requests
import os
from PIL import Image
import io

# Config
BASE_URL = "http://127.0.0.1:5001"
LOGIN_URL = f"{BASE_URL}/login"
ARCHITECT_EMAIL = "arch@qa.com"
ARCHITECT_PASS = "qapass123"

def verify_upload():
    session = requests.Session()
    
    # 1. Login
    print("Logging in...")
    
    # First, GET the login page to get the CSRF token
    import re
    login_page = session.get(LOGIN_URL)
    csrf_match = re.search(r'name="csrf_token" value="(.*?)"', login_page.text)
    if not csrf_match:
        print("Could not find CSRF token on login page!")
        return False
    csrf_token = csrf_match.group(1)
    
    login_data = {
        "email": ARCHITECT_EMAIL, 
        "password": ARCHITECT_PASS,
        "csrf_token": csrf_token
    }
    resp = session.post(LOGIN_URL, data=login_data)
    if "Logout" not in resp.text:
        print("Login failed!")
        return False
    
    # 2. Get a Project ID
    print("Finding project...")
    resp = session.get(f"{BASE_URL}/dashboard")
    
    # Extract CSRF token from dashboard for subsequent requests
    csrf_match = re.search(r'name="csrf_token" value="(.*?)"', resp.text)
    csrf_token = csrf_match.group(1) if csrf_match else csrf_token # Fallback to login token
    
    import re
    project_match = re.search(r'/projects/(\d+)', resp.text)
    if not project_match:
        print("No projects found!")
        return False
    project_id = project_match.group(1)
    print(f"Using Project ID: {project_id}")
    
    # 3. Create dummy image
    img = Image.new('RGB', (100, 100), color=(73, 109, 137))
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)
    
    # 4. Upload
    UPLOAD_URL = f"{BASE_URL}/projects/{project_id}/images/upload"
    print(f"Uploading to {UPLOAD_URL}...")
    files = {'image': ('test_upload.jpg', img_byte_arr, 'image/jpeg')}
    data = {'tag': 'Reference', 'csrf_token': csrf_token}
    
    resp = session.post(UPLOAD_URL, files=files, data=data, allow_redirects=True)
    
    if resp.status_code == 200 and "Image uploaded" in resp.text:
        print("Upload Successful!")
        return True
    else:
        print(f"Upload Failed! Status: {resp.status_code}")
        # print(resp.text[:500])
        return False

if __name__ == "__main__":
    if verify_upload():
        exit(0)
    else:
        exit(1)
