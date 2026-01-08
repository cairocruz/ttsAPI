import requests
import json
import time
import os

# Configuration
API_URL = "http://127.0.0.1:8000"
VIDEO_PATH = "test_video.mp4"

# Sample Script (matching the user's request format)
SCRIPT = [
    {
      "start": "00:00",
      "end": "00:04",
      "text": "Este é um teste de narração."
    },
    {
      "start": "00:05",
      "end": "00:09",
      "text": "Verificando se o áudio baixa quando eu falo."
    },
    {
      "start": "00:10",
      "end": "00:14",
      "text": "E se a legenda aparece corretamente no vídeo final."
    }
]

def test_workflow():
    print("1. Submitting Job...")

    with open(VIDEO_PATH, "rb") as f:
        files = {"file": f}
        data = {
            "script": json.dumps(SCRIPT),
            "voice": "pt-BR-AntonioNeural",
            "add_subtitles": True
        }

        response = requests.post(f"{API_URL}/narrate", files=files, data=data)

    if response.status_code != 200:
        print(f"Failed to submit job: {response.text}")
        return

    job_id = response.json()["job_id"]
    print(f"   Job ID: {job_id}")

    print("2. Polling Status...")
    while True:
        res = requests.get(f"{API_URL}/status/{job_id}")
        status_data = res.json()
        status = status_data["status"]
        print(f"   Status: {status}")

        if status == "completed":
            break
        elif status == "failed":
            print(f"   Job failed: {status_data.get('message')}")
            return

        time.sleep(2)

    print("3. Downloading Video...")
    res = requests.get(f"{API_URL}/download/{job_id}", stream=True)
    if res.status_code == 200:
        output_file = f"downloaded_{job_id}.mp4"
        with open(output_file, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"   Video saved to {output_file}")

        # Verify file size
        if os.path.getsize(output_file) > 1000:
            print("   SUCCESS: Video seems valid.")
        else:
            print("   FAILURE: Video file too small.")
    else:
        print(f"   Failed to download: {res.text}")

if __name__ == "__main__":
    test_workflow()
